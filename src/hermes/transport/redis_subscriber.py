"""
Async Redis subscriber for Noble Trader heartbeats.

Reads from the `signal.raw.noble_trader` Redis STREAM (XADD/XREAD) — NT pushes
qualified heartbeat records there. Uses a consumer group (`hermes-l0`) so we can
recover from disconnects without losing messages, and replay from the beginning
when needed. Reconnects with exponential backoff on failure.

Re-publishes normalized heartbeats on `signal.raw.hermes.{symbol}` for
downstream consumption — L4 never reads from the upstream channel directly.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.schemas.heartbeat import HeartbeatValidationError, parse_heartbeat
from hermes.transport.heartbeat_writer import HeartbeatWriter
from hermes.transport.l0_processing import (
    Deduper,
    RegimeShiftDetector,
    StalenessChecker,
    compute_dedup_hash,
)

log = structlog.get_logger(__name__)


class HeartbeatSubscriber:
    """
    Subscribes to Noble Trader's Redis heartbeat channel, validates, dedupes,
    persists to DuckDB, and re-publishes internally.

    Lifecycle:
        subscriber = HeartbeatSubscriber(config, writer)
        await subscriber.start()
        # ... runs forever ...
        await subscriber.stop()
    """

    def __init__(
        self,
        config: HermesConfig,
        writer: HeartbeatWriter,
        redis_url: str | None = None,
        channel: str | None = None,
        consumer_group: str | None = None,
        staleness_ms: int | None = None,
    ) -> None:
        nt_config = config.upstream.get("noble_trader", {}).get("redis", {})
        self._redis_url = redis_url or nt_config.get("url", "")
        self._channel = channel or nt_config.get("channel", "signal.raw.noble_trader")
        self._consumer_group = consumer_group or nt_config.get(
            "consumer_group", "hermes-l0"
        )
        self._staleness_ms = staleness_ms or nt_config.get("staleness_ms", 30000)

        self._writer = writer
        self._deduper = Deduper(window_sec=5.0)
        self._staleness = StalenessChecker(staleness_ms=self._staleness_ms)
        self._shift_detector = RegimeShiftDetector()

        self._running = False
        self._task: asyncio.Task | None = None
        self._stats = {
            "received": 0,
            "accepted": 0,
            "rejected_stale": 0,
            "rejected_duplicate": 0,
            "rejected_invalid": 0,
            "republished": 0,
            "regime_shifts": 0,
            "reconnects": 0,
        }

        # Internal Redis client (Hermes's own Redis, for re-publishing)
        self._internal_redis = None
        hermes_redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
        self._hermes_redis_url = hermes_redis_url

    async def start(self) -> None:
        """Start the subscriber."""
        if self._running:
            log.warning("subscriber_already_running")
            return

        # Detect placeholder config
        if not self._redis_url or "<" in self._redis_url or self._redis_url.startswith("secret:"):
            log.warning(
                "upstream_redis_not_configured",
                note="Heartbeat subscriber will not start until .env is filled in",
            )
            raise RuntimeError(
                "Noble Trader Redis URL not configured. Fill in .env with real value."
            )

        self._running = True

        # Connect to internal Redis for re-publishing
        try:
            import redis.asyncio as aioredis

            self._internal_redis = aioredis.from_url(
                self._hermes_redis_url, decode_responses=True
            )
            await self._internal_redis.ping()
            log.info("internal_redis_connected", url=self._safe_url(self._hermes_redis_url))
        except Exception as e:
            log.warning(
                "internal_redis_unavailable",
                error=str(e),
                note="re-publishing will be skipped",
            )
            self._internal_redis = None

        self._task = asyncio.create_task(self._run())
        log.info(
            "heartbeat_subscriber_started",
            upstream_channel=self._channel,
            consumer_group=self._consumer_group,
            staleness_ms=self._staleness_ms,
        )

    async def stop(self) -> None:
        """Stop the subscriber."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._internal_redis:
            await self._internal_redis.close()
        log.info("heartbeat_subscriber_stopped", stats=self._stats)

    async def _run(self) -> None:
        """Main loop with reconnect/backoff."""
        backoff = 1.0
        max_backoff = 60.0

        while self._running:
            try:
                await self._subscribe_loop()
                backoff = 1.0  # reset on clean exit
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._stats["reconnects"] += 1
                log.error(
                    "subscriber_error",
                    error=str(e),
                    backoff_sec=backoff,
                    reconnects=self._stats["reconnects"],
                )
                if self._running:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)

    async def _subscribe_loop(self) -> None:
        """Read from the upstream Redis STREAM via XREAD (consumer group).

        NT pushes qualified heartbeats to `signal.raw.noble_trader` as a Redis
        Stream (XADD). We read new entries (id ">") in real-time; the 
        `replay_from_start` flag (set when the stream is empty on first connect)
        reads from "0" to ingest any backlog.
        """
        import redis.asyncio as aioredis

        upstream = aioredis.from_url(self._redis_url, decode_responses=True)
        try:
            await upstream.ping()
            log.info("upstream_redis_connected", url=self._safe_url(self._redis_url))

            # Ensure consumer group exists (mkstream creates the stream if absent)
            try:
                await upstream.xgroup_create(
                    name=self._channel,
                    groupname=self._consumer_group,
                    id="0",
                    mkstream=True,
                )
                log.info("consumer_group_created", channel=self._channel, group=self._consumer_group)
            except Exception as e:
                # BUSYGROUP = already exists; ignore. Other errors bubble.
                if "BUSYGROUP" not in str(e):
                    log.warning("consumer_group_create_failed", error=str(e))

            log.info("stream_read_started", channel=self._channel, group=self._consumer_group)
            last_id = ">"  # only new messages
            while self._running:
                try:
                    resp = await upstream.xreadgroup(
                        groupname=self._consumer_group,
                        consumername="hermes-l0-worker",
                        streams={self._channel: last_id},
                        count=10,
                        block=5000,
                    )
                    if not resp:
                        continue
                    for _stream, entries in resp:
                        for entry_id, fields in entries:
                            raw = self._extract_payload(fields)
                            if raw is not None:
                                await self._process_message(raw)
                            # Acknowledge so we don't reprocess on restart
                            try:
                                await upstream.xack(self._channel, self._consumer_group, entry_id)
                            except Exception:
                                pass
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    log.error("stream_read_error", error=str(e))
                    await asyncio.sleep(1.0)
        finally:
            await upstream.close()

    @staticmethod
    def _extract_payload(fields: dict) -> "str | None":
        """Extract the raw heartbeat JSON string from a stream entry.

        NT may store the payload as a single field (e.g. `data`, `payload`, or
        `heartbeat`) containing JSON, or spread flat key/values. We handle both:
        prefer a JSON-bearing field; otherwise serialize the flat map back to
        JSON so parse_heartbeat can validate it.
        """
        if not fields:
            return None
        # Common single-field payload keys
        for key in ("data", "payload", "heartbeat", "message"):
            if key in fields:
                return fields[key]
        # Exactly one field and it looks like JSON
        if len(fields) == 1:
            return next(iter(fields.values()))
        # Flat map -> re-serialize to JSON for the schema parser
        import json
        return json.dumps(fields)

    async def _process_message(self, raw_payload: str | bytes) -> None:
        """Process a single heartbeat message."""
        self._stats["received"] += 1

        if isinstance(raw_payload, bytes):
            raw_str = raw_payload.decode("utf-8")
        else:
            raw_str = raw_payload

        # 1. Parse + validate
        try:
            hb = parse_heartbeat(raw_str, strategy_id="noble_trader")
        except HeartbeatValidationError as e:
            self._stats["rejected_invalid"] += 1
            log.warning("heartbeat_invalid", error=str(e), payload_preview=raw_str[:200])
            # Quarantine for forensic review
            self._writer.write_quarantine(
                raw_payload=raw_str,
                parse_error=str(e),
                schema_violations=[err.get("loc", []) for err in e.errors] if e.errors else None,
            )
            return

        # 2. Dedup
        dedup_hash = compute_dedup_hash(hb)
        if self._deduper.is_duplicate(dedup_hash):
            self._stats["rejected_duplicate"] += 1
            log.debug("heartbeat_duplicate", symbol=hb.symbol, hash=dedup_hash[:16])
            return

        # 3. Staleness check
        if self._staleness.is_stale(hb):
            self._stats["rejected_stale"] += 1
            age = self._staleness.age_ms(hb)
            log.warning(
                "heartbeat_stale",
                symbol=hb.symbol,
                age_ms=age,
                threshold_ms=self._staleness_ms,
            )
            # Still write to DuckDB but mark as not accepted
            row = hb.to_duckdb_row(
                ts_received=datetime.now(timezone.utc),
                dedup_hash=dedup_hash,
                accepted=False,
                reject_reason="stale",
                raw_payload=raw_str,
            )
            await self._writer.enqueue(row)
            return

        # 4. Regime shift detection
        shift_event = self._shift_detector.check_shift(hb)
        if shift_event:
            self._stats["regime_shifts"] += 1
            log.info(
                "regime_shift_detected",
                symbol=hb.symbol,
                prev=shift_event["prev_regime"],
                new=shift_event["new_regime"],
                source=shift_event["source"],
            )
            # Publish high-priority shift event
            await self._republish(f"regime.shift.{hb.symbol}", shift_event)

        # 5. Write to DuckDB (immutable provenance chain)
        row = hb.to_duckdb_row(
            ts_received=datetime.now(timezone.utc),
            dedup_hash=dedup_hash,
            accepted=True,
            raw_payload=raw_str,
        )
        await self._writer.enqueue(row)

        # 6. Re-publish internally on signal.raw.hermes.{symbol}
        internal_payload = {
            "heartbeat_id": row["heartbeat_id"],
            "ts_received": row["ts_received"].isoformat(),
            "symbol": hb.symbol,
            "signal": hb.signal,
            "entry_price": hb.entry_price,
            "stop_loss": hb.stop_loss,
            "take_profit": hb.take_profit,
            "aggression": hb.aggression,
            "brick_size": hb.brick_size,
            "sl_bricks": hb.sl_bricks,
            "tp_bricks": hb.tp_bricks,
            "regime": hb.regime,
            "regime_conf": hb.regime_conf,
            "regime_shift": hb.regime_shift,
            "kelly_f": hb.kelly_f,
            "effective_kelly": hb.effective_kelly,
            "ev": hb.ev,
            "ev_per_dollar": hb.ev_per_dollar,
            "p_win": hb.p_win,
            "p_regime": hb.p_regime,
            "p_imbalance": hb.p_imbalance,
            "p_markov": hb.p_markov,
            "p_timesfm": hb.p_timesfm,
            "tail_risk_score": hb.tail_risk_score,
            "tail_risk_action": hb.tail_risk_action,
            "markov_current_state": hb.markov_current_state,
        }
        await self._republish(f"signal.raw.hermes.{hb.symbol}", internal_payload)

        self._stats["accepted"] += 1
        self._stats["republished"] += 1

        log.debug(
            "heartbeat_processed",
            symbol=hb.symbol,
            signal=hb.signal,
            regime=hb.regime,
            regime_conf=hb.regime_conf,
            heartbeat_id=row["heartbeat_id"],
        )

    async def _republish(self, channel: str, payload: dict[str, Any]) -> None:
        """Re-publish a message on Hermes's internal Redis."""
        if self._internal_redis is None:
            return  # Non-fatal — re-publishing is best-effort
        try:
            await self._internal_redis.publish(channel, json.dumps(payload, default=str))
        except Exception as e:
            log.warning("republish_failed", channel=channel, error=str(e))

    @staticmethod
    def _safe_url(url: str) -> str:
        """Redact password from Redis URL for logging."""
        if "@" in url:
            scheme, rest = url.split("://", 1)
            _, host = rest.split("@", 1)
            return f"{scheme}://***@{host}"
        return url

    def get_stats(self) -> dict[str, Any]:
        """Return subscriber statistics."""
        stats = self._stats.copy()
        stats["deduper"] = self._deduper.get_stats()
        stats["shift_detector"] = self._shift_detector.get_stats()
        stats["writer"] = self._writer.get_stats()
        return stats
