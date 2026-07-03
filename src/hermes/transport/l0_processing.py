"""
L0 processing utilities: dedup, staleness check, regime shift detection.

These run BEFORE the heartbeat is written to DuckDB or re-published internally.
"""

from __future__ import annotations

import hashlib
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.schemas.heartbeat import NobleTraderHeartbeat

log = structlog.get_logger(__name__)


def compute_dedup_hash(hb: NobleTraderHeartbeat) -> str:
    """
    SHA-256 hash of (symbol, ts, signal, entry_price, stop_loss, take_profit).

    Used to detect duplicate heartbeats within a 5s window.
    """
    payload = f"{hb.symbol}|{hb.ts}|{hb.signal}|{hb.entry_price}|{hb.stop_loss}|{hb.take_profit}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Deduper:
    """
    In-memory dedup with sliding 5s window.

    Keeps track of dedup_hash → ts_received for the last 5 seconds.
    If a hash is seen again within the window, it's a duplicate.

    Thread-safe via asyncio single-threaded model (no lock needed).
    """

    def __init__(self, window_sec: float = 5.0) -> None:
        self._window = window_sec
        self._seen: deque[tuple[str, float]] = deque()
        self._stats = {"checked": 0, "duplicates": 0}

    def is_duplicate(self, dedup_hash: str) -> bool:
        """Check if a hash has been seen in the last window_sec seconds."""
        now = time.monotonic()
        # Evict old entries
        while self._seen and (now - self._seen[0][1]) > self._window:
            self._seen.popleft()

        self._stats["checked"] += 1

        # Check for duplicate
        for h, _ in self._seen:
            if h == dedup_hash:
                self._stats["duplicates"] += 1
                return True

        # Not a duplicate — record it
        self._seen.append((dedup_hash, now))
        return False

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()


class StalenessChecker:
    """
    Rejects heartbeats older than signal_staleness_ms.

    Noble Trader publishes every 5min (crypto/forex) to 15min (stocks/commodities),
    so a 30s staleness threshold gives healthy margin.
    """

    def __init__(self, staleness_ms: int = 30000) -> None:
        self._staleness_ms = staleness_ms

    def is_stale(self, hb: NobleTraderHeartbeat) -> bool:
        """Returns True if the heartbeat is older than staleness_ms."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        age_ms = now_ms - hb.ts
        return age_ms > self._staleness_ms

    def age_ms(self, hb: NobleTraderHeartbeat) -> int:
        """Returns the age of the heartbeat in milliseconds."""
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        return now_ms - hb.ts


class RegimeShiftDetector:
    """
    Detects regime shifts from upstream heartbeats.

    When `regime_shift == "true"` from Noble Trader, emit a high-priority
    `regime.shift.{symbol}` event so L4/L5 can re-evaluate open positions.
    """

    def __init__(self) -> None:
        self._last_regime: dict[str, str] = {}  # symbol → last regime
        self._stats = {"shifts_detected": 0}

    def check_shift(self, hb: NobleTraderHeartbeat) -> dict[str, Any] | None:
        """
        Check if this heartbeat represents a regime shift.

        Returns:
            Shift event dict if shift detected, None otherwise.
            The dict is suitable for publishing on `regime.shift.{symbol}` channel.
        """
        # Primary signal: upstream regime_shift field
        if hb.regime_shift == "true":
            self._stats["shifts_detected"] += 1
            prev = self._last_regime.get(hb.symbol) or hb.prev_regime
            self._last_regime[hb.symbol] = hb.regime
            return {
                "symbol": hb.symbol,
                "prev_regime": prev,
                "new_regime": hb.regime,
                "regime_conf": hb.regime_conf,
                "shift_at": hb.shift_at,
                "shifts_24h": hb.shifts_24h,
                "source": "upstream",
                "ts": hb.ts,
            }

        # Secondary: detect shift ourselves if regime changed since last heartbeat
        prev = self._last_regime.get(hb.symbol)
        if prev is not None and prev != hb.regime:
            self._stats["shifts_detected"] += 1
            self._last_regime[hb.symbol] = hb.regime
            return {
                "symbol": hb.symbol,
                "prev_regime": prev,
                "new_regime": hb.regime,
                "regime_conf": hb.regime_conf,
                "shift_at": hb.ts,
                "shifts_24h": hb.shifts_24h,
                "source": "hermes_detected",
                "ts": hb.ts,
            }

        # No shift — update tracking
        self._last_regime[hb.symbol] = hb.regime
        return None

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
