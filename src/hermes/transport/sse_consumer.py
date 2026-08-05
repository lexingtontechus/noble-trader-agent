"""
Microstructure SSE consumer — subscribes to the proxy's /sse/alerts stream.

P3.5 migration target (HIGH #9, audit 2026-07-22). The proxy emits
`event: microstructure` SSE frames every 5 minutes per symbol with a
composite `p_microstructure` value (L1-derived + TVDA TA bias). This
module consumes those frames and exposes them to the agent's
MetaRegimeClassifier via a synchronous in-memory lookup.

Lifecycle:
    consumer = MicrostructureSSEConsumer(config)
    await consumer.start()
    # ... runs forever in background ...
    p = consumer.get_p_microstructure("BTC-PERP")  # -1.0 to +1.0, or None
    await consumer.stop()

Architecture:
    - Single asyncio task maintains a streaming httpx GET to /sse/alerts
    - SSE parser handles "event:" + "data:" lines per the SSE spec
    - Per-symbol MicrostructureSnapshot stored in dict with TTL
      (default 10 min — proxy emits every 5 min; 2x window is safe)
    - Reconnect with exponential backoff (1s → 60s)
    - Plan prefix sourced from the agent's NOBLE_TRADER_PROXY_REDIS_URL username
      (stamped at provisioning as "<prefix>-sub-<hex>"; ps=Signal Scout,
      pp=Precision Pro). Sent to the proxy as the X-Plan-Prefix header —
      no license key, no Edge Function call.
    - Proxy URL sourced from config.upstream.noble_trader.quote_proxy.url
      (same proxy that serves /quotes) — falls back to localhost:8080
    - Symbols filter optional: pass list to subscribe only to specific
      symbols (passed as ?symbols= query param to /sse/alerts)

Error handling:
    - All exceptions in the receive loop are caught + logged at warning
    - Consumer never raises to caller; get_p_microstructure() returns
      None when no fresh data is available
    - A 401/403 from the proxy causes the consumer to stop retrying —
      there's no point reconnecting with a bad plan prefix. The operator
      must fix the Redis credential (re-run the setup wizard) and restart.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from hermes.core.config import HermesConfig

log = structlog.get_logger(__name__)


# ─── Snapshot model ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class MicrostructureSnapshot:
    """Latest p_microstructure value for a symbol, with metadata."""

    symbol: str
    ts_ms: int                      # Unix ms when proxy computed the value
    p_microstructure: float         # Composite directional probability [-1, +1]
    p_micro_l1: float | None = None  # L1-derived sub-signal [-1, +1]
    p_micro_ta: float | None = None  # TVDA TA-derived sub-signal [-1, +1]
    direction: str = "neutral"      # buy / sell / neutral (proxy's discretization)
    ta_vetoed: bool = False         # True if TA vetoed the L1 signal
    received_at: float = field(default_factory=time.time)  # local receipt time


# ─── Consumer ────────────────────────────────────────────────────────────────


class MicrostructureSSEConsumer:
    """Subscribes to the proxy /sse/alerts stream for p_microstructure updates.

    Designed to be wired into SignalSynthesizer.__init__ as an optional
    `microstructure_source` parameter. The synthesizer queries
    `get_p_microstructure(symbol)` on each process_heartbeat call.
    """

    # Default TTL for cached snapshots (10 min; proxy emits every 5 min)
    DEFAULT_TTL_SEC = 600.0
    # Reconnect backoff schedule (seconds)
    BACKOFF_SCHEDULE = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)

    def __init__(
        self,
        config: HermesConfig,
        *,
        symbols: list[str] | None = None,
        ttl_sec: float | None = None,
    ) -> None:
        self._config = config
        self._symbols = symbols
        self._ttl_sec = ttl_sec or self.DEFAULT_TTL_SEC

        # Resolve plan prefix from the agent's Noble Trader Redis URL.
        # The credential username is "<prefix>-sub-<hex>" (prefix stamped at
        # provisioning: ps=Signal Scout, pp=Precision Pro). The agent sends
        # this prefix to the proxy as X-Plan-Prefix so the proxy can filter
        # symbols/alerts for its plan — no license key, no Edge Function.
        nt = config.upstream.get("noble_trader", {})
        redis_cfg = nt.get("redis", {}) or {}

        # Plan prefix resolution (explicit > redis username parse).
        # The agent's proxy Redis credential may use the Railway `default`
        # admin user (no <prefix>-sub-<hex> form), so allow an explicit
        # prefix via env NOBLE_TRADER_PLAN_PREFIX or config plan_prefix
        # (ps=Signal Scout, pp=Precision Pro). Falls back to parsing the
        # Redis URL username only when no explicit value is set.
        explicit_prefix = (
            os.getenv("NOBLE_TRADER_PLAN_PREFIX")
            or nt.get("plan_prefix")
            or ""
        )
        if explicit_prefix:
            self._plan_prefix = explicit_prefix.strip().lower()
        else:
            redis_url = redis_cfg.get("url", "") or ""
            if redis_url.startswith("secret:"):
                # secret:noble_trader.proxy_redis_url -> NOBLE_TRADER_PROXY_REDIS_URL
                redis_url = (
                    os.getenv("NOBLE_TRADER_PROXY_REDIS_URL")
                    or redis_url[7:]
                )
                if redis_url.startswith("secret:"):
                    redis_url = ""
            self._plan_prefix = _plan_prefix_from_redis_url(redis_url)

        # Resolve proxy URL (reuse the same proxy that serves /quotes)
        qp = nt.get("quote_proxy", {})
        self._proxy_url = (qp.get("url") or "http://localhost:8080").rstrip("/")

        # In-memory snapshot cache: symbol → MicrostructureSnapshot
        self._snapshots: dict[str, MicrostructureSnapshot] = {}
        self._snapshots_lock = asyncio.Lock()

        # Lifecycle state
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Stats
        self._stats = {
            "frames_received": 0,
            "microstructure_frames": 0,
            "alerts_ignored": 0,
            "heartbeats_ignored": 0,
            "reconnects": 0,
            "errors": 0,
            "last_frame_at": 0.0,
        }

    # ─── Public API ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Start the background SSE consumer task."""
        if self._running:
            log.warning("sse_consumer_already_running")
            return

        if not self._plan_prefix:
            log.warning(
                "sse_consumer_no_plan_prefix",
                note="Microstructure SSE consumer will not start — plan prefix not resolved "
                     "from NOBLE_TRADER_PROXY_REDIS_URL. p_microstructure will be unavailable; "
                     "MetaRegimeClassifier will run without microstructure input "
                     "(degraded but functional).",
            )
            return  # Don't raise — agent should still run without microstructure

        self._running = True
        self._task = asyncio.create_task(self._run(), name="microstructure-sse-consumer")
        log.info(
            "sse_consumer_started",
            proxy_url=self._proxy_url,
            symbols=self._symbols,
            ttl_sec=self._ttl_sec,
        )

    async def stop(self) -> None:
        """Stop the consumer and clean up."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("sse_consumer_stopped", stats=self._stats)

    def get_p_microstructure(self, symbol: str) -> float | None:
        """Get the latest p_microstructure value for a symbol.

        Returns None if no snapshot exists, or if the snapshot is older
        than ttl_sec (treated as stale — proxy may have stopped emitting).
        """
        snap = self._snapshots.get(symbol)
        if snap is None:
            return None
        if time.time() - snap.received_at > self._ttl_sec:
            return None
        return snap.p_microstructure

    def get_snapshot(self, symbol: str) -> MicrostructureSnapshot | None:
        """Get the full MicrostructureSnapshot for a symbol (or None if stale)."""
        snap = self._snapshots.get(symbol)
        if snap is None:
            return None
        if time.time() - snap.received_at > self._ttl_sec:
            return None
        return snap

    def get_stats(self) -> dict[str, Any]:
        """Return a copy of the consumer's stats dict."""
        return dict(self._stats)

    # ─── Internal: SSE receive loop ──────────────────────────────────────

    async def _run(self) -> None:
        """Main loop with reconnect/backoff."""
        backoff_idx = 0
        while self._running:
            try:
                await self._connect_and_stream()
                # If we exit normally (server closed connection), reset
                # backoff to a small value — server-closed is normal.
                backoff_idx = 0
            except asyncio.CancelledError:
                raise
            except _FatalAuthError as exc:
                # 401/403 — no point retrying with the same key
                log.error(
                    "sse_consumer_fatal_auth",
                    error=str(exc),
                    note="Stopping consumer — fix the license key and restart the agent.",
                )
                self._running = False
                return
            except Exception as exc:
                self._stats["errors"] += 1
                backoff = self.BACKOFF_SCHEDULE[
                    min(backoff_idx, len(self.BACKOFF_SCHEDULE) - 1)
                ]
                log.warning(
                    "sse_consumer_reconnect",
                    error=str(exc)[:200],
                    backoff_sec=backoff,
                    attempt=backoff_idx + 1,
                )
                backoff_idx += 1
                await asyncio.sleep(backoff)

    async def _connect_and_stream(self) -> None:
        """Open the SSE connection and process frames until disconnect."""
        url = f"{self._proxy_url}/sse/alerts"
        headers = {
            "Accept": "text/event-stream",
            "X-Plan-Prefix": self._plan_prefix,
            "Cache-Control": "no-cache",
        }
        params: dict[str, str] = {}
        if self._symbols:
            params["symbols"] = ",".join(self._symbols)

        log.info("sse_consumer_connecting", url=url, symbols=self._symbols)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                # No read timeout — SSE is a long-lived stream. We rely on
                # the proxy's heartbeat frames (every 30s) to detect dead
                # connections. If no frame arrives in 90s, we abort + reconnect.
                read=90.0,
                write=10.0,
                pool=10.0,
            ),
        ) as client:
            async with client.stream(
                "GET", url, headers=headers, params=params
            ) as resp:
                if resp.status_code == 401:
                    raise _FatalAuthError(f"HTTP 401 — plan prefix missing/rejected")
                if resp.status_code == 403:
                    raise _FatalAuthError(
                        f"HTTP 403 — plan prefix unknown (not entitled)"
                    )
                if resp.status_code == 503:
                    raise RuntimeError(
                        f"HTTP 503 — plan service unavailable or SSE at capacity"
                    )
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(
                        f"HTTP {resp.status_code}: {body[:200].decode('utf-8', errors='replace')}"
                    )

                log.info("sse_consumer_connected", status=200)
                self._stats["reconnects"] += 1

                # SSE line parser: accumulates "event:" + "data:" lines into
                # frames, dispatches when a blank line is seen.
                current_event = "message"
                current_data: list[str] = []

                async for line in resp.aiter_lines():
                    if not self._running:
                        break

                    # SSE spec: lines end with \n; blank line dispatches frame
                    line = line.rstrip("\r\n")

                    if line == "":
                        # Dispatch accumulated frame
                        if current_data:
                            await self._handle_frame(current_event, "\n".join(current_data))
                        current_event = "message"
                        current_data = []
                        continue

                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                    elif line.startswith("data:"):
                        current_data.append(line[5:].lstrip())
                    elif line.startswith(":"):
                        # SSE comment — ignore (used for keep-alive)
                        continue
                    # Other SSE fields (id:, retry:) are not currently used

                # Stream ended (server closed connection) — normal exit,
                # _run() will reconnect with backoff reset.

    async def _handle_frame(self, event: str, data: str) -> None:
        """Dispatch a single SSE frame based on its event type."""
        self._stats["frames_received"] += 1
        self._stats["last_frame_at"] = time.time()

        if event == "microstructure":
            await self._handle_microstructure(data)
        elif event == "alert":
            self._stats["alerts_ignored"] += 1
        elif event == "heartbeat":
            self._stats["heartbeats_ignored"] += 1
        elif event == "hello":
            log.info("sse_consumer_hello", data=data[:200])
        else:
            log.debug("sse_consumer_unknown_event", event=event, data=data[:100])

    async def _handle_microstructure(self, data: str) -> None:
        """Parse a microstructure frame and update the snapshot cache."""
        self._stats["microstructure_frames"] += 1
        try:
            payload = json.loads(data)
        except json.JSONDecodeError as exc:
            log.warning("sse_consumer_bad_json", error=str(exc), data=data[:200])
            self._stats["errors"] += 1
            return

        symbol = payload.get("symbol")
        if not symbol:
            log.warning("sse_consumer_no_symbol", data=data[:200])
            return

        try:
            snap = MicrostructureSnapshot(
                symbol=symbol,
                ts_ms=int(payload.get("ts_ms", 0)),
                p_microstructure=float(payload.get("p_microstructure", 0.0)),
                p_micro_l1=_safe_float(payload.get("p_micro_l1")),
                p_micro_ta=_safe_float(payload.get("p_micro_ta")),
                direction=str(payload.get("direction", "neutral")),
                ta_vetoed=bool(payload.get("ta_vetoed", False)),
            )
        except (TypeError, ValueError) as exc:
            log.warning(
                "sse_consumer_bad_payload",
                symbol=symbol,
                error=str(exc),
                data=data[:200],
            )
            self._stats["errors"] += 1
            return

        async with self._snapshots_lock:
            self._snapshots[symbol] = snap

        log.debug(
            "sse_consumer_snapshot",
            symbol=symbol,
            p_microstructure=snap.p_microstructure,
            direction=snap.direction,
            ta_vetoed=snap.ta_vetoed,
        )


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    """Coerce to float or return None."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Plan prefix → slug (must mirror nt_symbol_validator.PLAN_PREFIX_TO_SLUG)
_PLAN_PREFIX_TO_SLUG: dict[str, str] = {
    "ps": "signal_scout",
    "pp": "precision_pro",
}


def _plan_prefix_from_redis_url(redis_url: str) -> str | None:
    """Parse the plan prefix from a rediss:// URL's username.

    Username form: "<prefix>-sub-<hex>" (prefix stamped at provisioning).
    Returns the prefix string (e.g. "pp") or None if unparseable.
    """
    from urllib.parse import urlparse

    if not redis_url:
        return None
    try:
        username = urlparse(redis_url).username or ""
    except (ValueError, AttributeError):
        return None
    if not username:
        return None
    return username.split("-")[0].lower() or None


class _FatalAuthError(Exception):
    """Raised when the SSE consumer gets 401/403 — no point retrying."""
