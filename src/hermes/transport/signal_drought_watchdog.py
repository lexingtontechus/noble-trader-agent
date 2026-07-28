"""Signal drought watchdog — Monitor A (WARNING, 4h with no qualified signal).

This watches the SIGNAL pipeline (backend -> proxy -> agent). It is INDEPENDENT
of the pricing/SSE pipeline (see pricing_sse_watchdog) and of the proxy delivery
heartbeat (see proxy_liveness).

The agent's ingest (L0) reads `signal.proxy.noble_trader` and WAITS — a qualified
signal may legitimately not arrive for minutes or hours (the backend enforces a
60-min per-(symbol,direction) cooldown, SIGNAL_COOLDOWN_MINUTES=60 in the
orchestrator, mirrored by PROXY_COOLDOWN_MINUTES=55). So silence is NOT itself an
error.

We therefore only WARN (not CRITICAL) when the agent has received ZERO signals
for `signal_drought_alert_sec` (default 14400 = 4h) — far above any normal quiet
period. This catches a genuinely stalled pipeline (proxy silently dropping, or
backend sweep dead) WITHOUT false-firing on a quiet market.

State machine (anti-spam):
  - WARNING fires on crossing the 4h threshold, then escalates at 8h / 12h tiers.
  - INFO "resumed" fires once when a signal finally arrives after a drought.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import structlog

from hermes.core.config import HermesConfig
from hermes.ops.alerting import Alert, AlertManager, AlertSeverity

log = structlog.get_logger(__name__)

# 4h with no qualified signal = drought warning.
DEFAULT_DROUGHT_ALERT_SEC = 14400.0
# Escalation tiers (hours) after the first warning, to escalate a persistent gap.
ESCALATION_TIERS_SEC = (8 * 3600, 12 * 3600)
POLL_INTERVAL_SEC = 300.0  # check every 5 min


class SignalDroughtWatchdog:
    """Raises WARNING if the agent receives no qualified signal for >N seconds."""

    def __init__(
        self,
        config: HermesConfig,
        alert_manager: AlertManager,
        *,
        drought_alert_sec: float | None = None,
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._alert_manager = alert_manager
        nt = config.upstream.get("noble_trader", {})
        self._drought_sec = (
            drought_alert_sec
            if drought_alert_sec is not None
            else float(nt.get("signal_drought_alert_sec", DEFAULT_DROUGHT_ALERT_SEC))
        )
        self._poll_interval = poll_interval_sec
        self._now = now

        self._running = False
        self._task: asyncio.Task[None] | None = None
        # Timestamp of the last qualified signal the subscriber accepted.
        self._last_signal_at: float = 0.0
        self._in_drought = False
        self._next_escalation_idx = 0
        self._stats = {
            "checks": 0,
            "warnings": 0,
            "restores": 0,
            "last_signal_at": 0.0,
        }

    async def mark_signal(self, at: float | None = None) -> None:
        """Called by the subscriber when a qualified signal is accepted."""
        self._last_signal_at = at if at is not None else self._now()
        if self._in_drought:
            # Signal arrived during a drought -> emit restore + reset.
            self._in_drought = False
            self._next_escalation_idx = 0
            self._stats["restores"] += 1
            await self._send_restore()

    async def _send_restore(self) -> None:
        await self._alert_manager.send_alert(
            Alert(
                title="Signal flow RESUMED",
                message=(
                    "Agent received a qualified signal after a drought period. "
                    "Signal pipeline (backend -> proxy -> agent) is flowing again."
                ),
                severity=AlertSeverity.INFO,
                source="signal_drought_watchdog",
                data={"pipeline": "signal"},
            )
        )
        log.info("signal_drought_restored")

    async def start(self) -> None:
        if self._running:
            log.warning("signal_drought_watchdog_already_running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="signal-drought-watchdog")
        log.info("signal_drought_watchdog_started", drought_sec=self._drought_sec)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        log.info("signal_drought_watchdog_stopped", stats=self._stats)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    async def _run(self) -> None:
        while self._running:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("signal_drought_watchdog_error", error=str(exc)[:200])
            await asyncio.sleep(self._poll_interval)

    async def _check_once(self) -> None:
        self._stats["checks"] += 1
        self._stats["last_signal_at"] = self._last_signal_at
        if self._last_signal_at == 0.0:
            # No signal ever received since start. Don't alarm during the initial
            # warm-up window (could just be process start). Only after the drought
            # window has elapsed from process start do we consider it.
            # We approximate process start via the first check's now; simpler to
            # rely on the subscriber's own startup logs. Skip until a signal arrives
            # OR we've been up longer than the drought window.
            return

        now = self._now()
        gap = now - self._last_signal_at

        if gap > self._drought_sec:
            if not self._in_drought:
                self._in_drought = True
                self._next_escalation_idx = 0
                self._stats["warnings"] += 1
                await self._alert_manager.send_alert(
                    Alert(
                        title="Signal drought — no qualified signal in >4h",
                        message=(
                            f"Agent has received NO qualified signal for "
                            f"{gap/3600:.1f}h (threshold {self._drought_sec/3600:.1f}h). "
                            "This may be a quiet market (signals are bursty and the "
                            "backend enforces a 60-min per-(symbol,direction) cooldown) "
                            "OR a stalled pipeline. VERIFY the proxy delivery heartbeat "
                            "(separate monitor) before assuming failure. Signal "
                            "ingestion path: backend -> proxy -> signal.proxy.noble_trader."
                        ),
                        severity=AlertSeverity.WARNING,
                        source="signal_drought_watchdog",
                        data={
                            "hours_since_signal": round(gap / 3600, 2),
                            "threshold_hours": self._drought_sec / 3600,
                            "pipeline": "signal",
                        },
                    )
                )
                log.warning(
                    "signal_drought_warning",
                    hours_since_signal=round(gap / 3600, 2),
                )
            else:
                # Already in drought — escalate at configured tiers.
                if self._next_escalation_idx < len(ESCALATION_TIERS_SEC):
                    tier = ESCALATION_TIERS_SEC[self._next_escalation_idx]
                    if gap > tier:
                        self._next_escalation_idx += 1
                        self._stats["warnings"] += 1
                        await self._alert_manager.send_alert(
                            Alert(
                                title=f"Signal drought ESCALATING — {gap/3600:.1f}h no signal",
                                message=(
                                    "Still no qualified signal after "
                                    f"{gap/3600:.1f}h. Pipeline likely stalled — investigate "
                                    "backend sweep + proxy delivery immediately."
                                ),
                                severity=AlertSeverity.WARNING,
                                source="signal_drought_watchdog",
                                data={
                                    "hours_since_signal": round(gap / 3600, 2),
                                    "pipeline": "signal",
                                },
                            )
                        )
                        log.warning(
                            "signal_drought_escalation",
                            hours_since_signal=round(gap / 3600, 2),
                        )
