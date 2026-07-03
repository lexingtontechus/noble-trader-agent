"""
Dead Man's Switch — monitors heartbeat health and auto-flattens if Hermes stops responding.

If the heartbeat monitor doesn't receive a "still alive" signal within the
configured timeout, it:
1. Activates the kill switch (halts all new entries)
2. Cancels all open orders
3. Optionally flattens all positions

This protects against:
- Process crashes (Python GIL deadlock, OOM, segfault)
- Network partition (can't reach venue APIs)
- Redis disconnect (can't receive signals)
- Runaway optimization (CPU stuck at 100%)

See roadmap §10 Phase 10.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class DeadMansSwitchState(BaseModel):
    """Current state of the dead man's switch."""

    is_alive: bool = True
    last_heartbeat_ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    timeout_sec: float = 60.0
    auto_flatten: bool = True
    activated: bool = False
    activated_at: datetime | None = None
    activation_reason: str = ""
    checks_performed: int = 0
    activations: int = 0


class DeadMansSwitch:
    """
    Monitors heartbeat health and triggers emergency shutdown if missed.

    Usage:
        dms = DeadMansSwitch(timeout_sec=60, auto_flatten=True)
        dms.start()  # starts background monitor

        # Any component calls this periodically to prove it's alive:
        dms.heartbeat()

        # If no heartbeat within timeout_sec, DMS activates:
        # 1. Calls on_activate callback (which activates kill switch)
        # 2. Logs critical alert
    """

    def __init__(
        self,
        timeout_sec: float = 60.0,
        auto_flatten: bool = True,
        check_interval_sec: float = 5.0,
        on_activate=None,  # async callback called when DMS activates
    ) -> None:
        self._timeout = timeout_sec
        self._auto_flatten = auto_flatten
        self._check_interval = check_interval_sec
        self._on_activate = on_activate

        self._last_heartbeat: float = time.monotonic()
        self._last_heartbeat_ts: datetime = datetime.now(timezone.utc)
        self._running = False
        self._task: asyncio.Task | None = None
        self._activated = False
        self._activated_at: datetime | None = None
        self._activation_reason: str = ""

        self._stats = {
            "checks_performed": 0,
            "activations": 0,
            "heartbeats_received": 0,
        }

    def heartbeat(self, source: str = "unknown") -> None:
        """Signal that the system is alive. Call this periodically from all components."""
        self._last_heartbeat = time.monotonic()
        self._last_heartbeat_ts = datetime.now(timezone.utc)
        self._stats["heartbeats_received"] += 1

        # If previously activated, deactivate
        if self._activated:
            log.info("dms_deactivated_heartbeat_received", source=source)
            self._activated = False
            self._activated_at = None
            self._activation_reason = ""

    async def start(self) -> None:
        """Start the background monitor."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        log.info(
            "dms_started",
            timeout_sec=self._timeout,
            check_interval=self._check_interval,
            auto_flatten=self._auto_flatten,
        )

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("dms_stopped", stats=self._stats)

    async def _monitor_loop(self) -> None:
        """Background loop: check heartbeat health."""
        while self._running:
            await asyncio.sleep(self._check_interval)
            self._stats["checks_performed"] += 1

            elapsed = time.monotonic() - self._last_heartbeat
            if elapsed > self._timeout and not self._activated:
                self._activate(f"heartbeat missed for {elapsed:.1f}s (timeout: {self._timeout}s)")

    def _activate(self, reason: str) -> None:
        """Activate the dead man's switch."""
        self._activated = True
        self._activated_at = datetime.now(timezone.utc)
        self._activation_reason = reason
        self._stats["activations"] += 1

        log.critical(
            "dms_activated",
            reason=reason,
            auto_flatten=self._auto_flatten,
            activated_at=self._activated_at.isoformat(),
            last_heartbeat=self._last_heartbeat_ts.isoformat(),
        )

        if self._on_activate:
            asyncio.create_task(self._on_activate(reason, self._auto_flatten))

    @property
    def is_activated(self) -> bool:
        return self._activated

    @property
    def is_alive(self) -> bool:
        """Returns True if heartbeat is within timeout."""
        elapsed = time.monotonic() - self._last_heartbeat
        return elapsed <= self._timeout and not self._activated

    def get_state(self) -> DeadMansSwitchState:
        """Get current state for dashboard/API."""
        return DeadMansSwitchState(
            is_alive=self.is_alive,
            last_heartbeat_ts=self._last_heartbeat_ts,
            timeout_sec=self._timeout,
            auto_flatten=self._auto_flatten,
            activated=self._activated,
            activated_at=self._activated_at,
            activation_reason=self._activation_reason,
            checks_performed=self._stats["checks_performed"],
            activations=self._stats["activations"],
        )

    def get_stats(self) -> dict[str, Any]:
        return {**self._stats, "is_alive": self.is_alive, "activated": self._activated}
