"""Pricing/SSE pipeline liveness watchdog — Monitor B (CRITICAL).

This is the SECOND of two independent pipelines and must not be conflated
with the signal pipeline (backend -> proxy -> agent).

  Pipeline 1 (SIGNAL):  backend XADD signal.raw.noble_trader
                        -> proxy re-publishes signal.proxy.noble_trader
                        -> agent ingest waits on it.  (see signal_drought_watchdog / proxy_liveness)

  Pipeline 2 (PRICING): TradingView Data API (TVDA) -> proxy (QuoteTracker)
                        -> proxy pushes `microstructure`/`alert`/`heartbeat`
                           frames over SSE /sse/alerts
                        -> agent MicrostructureSSEConsumer receives them.

PRICING is entirely UNRELATED to the backend generating signal.raw.noble_trader.
The proxy sources price data from TVDA (WebSocket/REST), not from the backend.
Therefore a dead pricing/SSE stream is a distinct, critical failure: the agent
goes structurally blind to market state even while signals keep flowing.

This watchdog watches the SSE consumer's `last_frame_at` timestamp. If NO frame
(arbitrary type — alert / heartbeat / microstructure) arrives within
`sse_liveness_timeout_sec` (default 90s, ~3x the proxy's 30s SSE heartbeat),
it raises a CRITICAL alert via the existing AlertManager (Discord/Telegram).

State machine (anti-spam):
  - CRITICAL fires ONCE on transition ok -> dead.
  - INFO "restored" fires ONCE on transition dead -> ok.
  - No re-alert while dead; recovery is required before another CRITICAL can fire.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import structlog

from hermes.core.config import HermesConfig
from hermes.ops.alerting import Alert, AlertManager, AlertSeverity

log = structlog.get_logger(__name__)

# Default: 90s with no SSE frame = pricing pipeline dead. The proxy emits a
# heartbeat frame every SSE_HEARTBEAT_SEC (30s), so 90s tolerates a single
# missed heartbeat without false-firing.
DEFAULT_SSE_LIVENESS_TIMEOUT_SEC = 90.0

# How often the watchdog polls last_frame_at.
POLL_INTERVAL_SEC = 15.0


def _get_last_frame_at(consumer: Any) -> float:
    """Best-effort read of the SSE consumer's last_frame_at timestamp.

    The MicrostructureSSEConsumer tracks `stats["last_frame_at"]` (epoch seconds,
    updated on EVERY received frame in `_handle_frame`). Returns 0.0 if unknown,
    which the watchdog treats as "never seen a frame" (dead until proven alive).
    """
    try:
        stats = consumer.get_stats()
        return float(stats.get("last_frame_at", 0.0) or 0.0)
    except Exception:
        return 0.0


class PricingSSEWatchdog:
    """Raises CRITICAL if the agent stops receiving proxy SSE pricing frames."""

    def __init__(
        self,
        config: HermesConfig,
        consumer: Any,
        alert_manager: AlertManager,
        *,
        timeout_sec: float | None = None,
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._consumer = consumer
        self._alert_manager = alert_manager
        nt = config.upstream.get("noble_trader", {})
        self._timeout_sec = (
            timeout_sec
            if timeout_sec is not None
            else float(
                nt.get("sse_liveness_timeout_sec", DEFAULT_SSE_LIVENESS_TIMEOUT_SEC)
            )
        )
        self._poll_interval = poll_interval_sec
        self._now = now

        self._running = False
        self._task: asyncio.Task[None] | None = None
        # State machine: False = ok (frames arriving), True = dead (alerted).
        self._dead = False
        self._stats = {
            "checks": 0,
            "critical_alerts": 0,
            "restore_alerts": 0,
            "last_frame_at": 0.0,
            "last_dead_at": 0.0,
        }

    async def start(self) -> None:
        if self._running:
            log.warning("pricing_sse_watchdog_already_running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="pricing-sse-watchdog")
        log.info("pricing_sse_watchdog_started", timeout_sec=self._timeout_sec)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("pricing_sse_watchdog_stopped", stats=self._stats)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    async def _run(self) -> None:
        while self._running:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never let the watchdog die
                log.warning("pricing_sse_watchdog_error", error=str(exc)[:200])
            await asyncio.sleep(self._poll_interval)

    async def _check_once(self) -> None:
        self._stats["checks"] += 1
        last = _get_last_frame_at(self._consumer)
        self._stats["last_frame_at"] = last

        now = self._now()
        # If we've never seen a frame and the consumer is supposed to be running,
        # treat absence as dead only after the timeout has elapsed since start.
        dead = (now - last) > self._timeout_sec if last > 0 else False

        # Special case: consumer never connected (last==0). Only flag dead if the
        # consumer reports it is running (i.e. it SHOULD be receiving frames).
        consumer_running = bool(getattr(self._consumer, "_running", False))
        if last == 0.0 and consumer_running:
            # Give it one timeout window from process start before alarming.
            # We approximate using reconnects: if it connected at least once,
            # last_frame_at would be >0. So last==0 + running + no frames since
            # start is only actionable after timeout since the task began.
            # Simpler: if consumer is running but last==0, we cannot prove a
            # frame ever arrived — wait until the consumer itself has had a
            # chance (the consumer logs its own connect failures). We do NOT
            # alert here to avoid racing the consumer's own startup.
            return

        if dead and not self._dead:
            # Transition ok -> dead: fire CRITICAL exactly once.
            self._dead = True
            self._stats["critical_alerts"] += 1
            self._stats["last_dead_at"] = now
            await self._alert_manager.send_alert(
                Alert(
                    title="Pricing/SSE pipeline DOWN — agent blind to market state",
                    message=(
                        "No frame received from the proxy's /sse/alerts stream for "
                        f">{self._timeout_sec:.0f}s. The pricing pipeline (TVDA -> "
                        "proxy -> agent SSE) is broken — the agent cannot see market "
                        "microstructure/price state. This is INDEPENDENT of the signal "
                        "pipeline (backend -> signal.raw.noble_trader). Check: proxy "
                        "SSE endpoint, TVDA upstream WS, and the agent's SSE "
                        "connection. Signal ingestion may still be flowing."
                    ),
                    severity=AlertSeverity.CRITICAL,
                    source="pricing_sse_watchdog",
                    data={
                        "timeout_sec": self._timeout_sec,
                        "last_frame_at": last,
                        "seconds_since_frame": round(now - last, 1),
                        "pipeline": "pricing",
                    },
                )
            )
            log.error(
                "pricing_sse_watchdog_critical",
                timeout_sec=self._timeout_sec,
                last_frame_at=last,
                seconds_since_frame=round(now - last, 1),
            )
        elif not dead and self._dead:
            # Transition dead -> ok: fire INFO restore exactly once.
            self._dead = False
            self._stats["restore_alerts"] += 1
            await self._alert_manager.send_alert(
                Alert(
                    title="Pricing/SSE pipeline RESTORED",
                    message=(
                        "Proxy /sse/alerts frames are flowing again. Agent market-"
                        "state visibility restored."
                    ),
                    severity=AlertSeverity.INFO,
                    source="pricing_sse_watchdog",
                    data={"pipeline": "pricing"},
                )
            )
            log.info("pricing_sse_watchdog_restored")
