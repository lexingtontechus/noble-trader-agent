"""
Cross-Price Monitor — tracks rolling correlation between assets.

Detects correlation regime shifts that feed into the 7-state meta-regime
classifier (state 5: risk_off when cross-asset correlation > 0.75).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Iterable
from uuid import uuid4

import numpy as np
import structlog

from hermes.schemas.market import PriceMonitorEvent, Tick, Venue

log = structlog.get_logger(__name__)


class CrossPriceMonitor:
    """
    Tracks rolling correlations between all symbol pairs.

    Emits `correlation_shift` events when any pair's 1h correlation
    moves more than `shift_threshold` from its 24h baseline.
    """

    def __init__(
        self,
        correlation_window: int = 60,  # 60 ticks for short-term corr
        baseline_window: int = 1440,   # 1440 ticks for 24h baseline
        shift_threshold: float = 0.3,
    ) -> None:
        self._corr_window = correlation_window
        self._baseline_window = baseline_window
        self._shift_threshold = shift_threshold

        # symbol → deque of (ts, price)
        self._prices: dict[str, deque[tuple[datetime, float]]] = defaultdict(
            lambda: deque(maxlen=baseline_window)
        )
        self._last_correlations: dict[tuple[str, str], float] = {}
        self._stats = {"shifts_detected": 0}

    def on_tick(self, tick: Tick) -> PriceMonitorEvent | None:
        """Record price and check for correlation shifts."""
        self._prices[tick.symbol].append((tick.ts, tick.price))

        # Need at least 2 symbols with enough history
        symbols_with_data = [
            s for s, prices in self._prices.items()
            if len(prices) >= self._corr_window
        ]
        if len(symbols_with_data) < 2:
            return None

        # Check all pairs
        for i, sym_a in enumerate(symbols_with_data):
            for sym_b in symbols_with_data[i + 1:]:
                shift_event = self._check_pair(tick, sym_a, sym_b)
                if shift_event:
                    return shift_event

        return None

    def _check_pair(
        self,
        reference_tick: Tick,
        sym_a: str,
        sym_b: str,
    ) -> PriceMonitorEvent | None:
        """Check correlation shift for a single pair."""
        prices_a = list(self._prices[sym_a])
        prices_b = list(self._prices[sym_b])

        # Align timestamps (simplified: use same window)
        n = min(len(prices_a), len(prices_b), self._corr_window)

        if n < 30:  # need minimum data
            return None

        returns_a = np.diff(np.log([p[1] for p in prices_a[-n:]]))
        returns_b = np.diff(np.log([p[1] for p in prices_b[-n:]]))

        if len(returns_a) < 2 or len(returns_b) < 2:
            return None

        min_len = min(len(returns_a), len(returns_b))
        returns_a = returns_a[-min_len:]
        returns_b = returns_b[-min_len:]

        current_corr = float(np.corrcoef(returns_a, returns_b)[0, 1])

        # Get baseline correlation (longer window)
        baseline_n = min(len(prices_a), len(prices_b), self._baseline_window)
        if baseline_n > n + 30:
            baseline_returns_a = np.diff(np.log([p[1] for p in prices_a[-baseline_n:]]))
            baseline_returns_b = np.diff(np.log([p[1] for p in prices_b[-baseline_n:]]))
            min_base = min(len(baseline_returns_a), len(baseline_returns_b))
            if min_base > 2:
                baseline_corr = float(
                    np.corrcoef(
                        baseline_returns_a[-min_base:],
                        baseline_returns_b[-min_base:],
                    )[0, 1]
                )
            else:
                baseline_corr = current_corr
        else:
            baseline_corr = current_corr

        delta = abs(current_corr - baseline_corr)
        pair_key = tuple(sorted([sym_a, sym_b]))

        if delta >= self._shift_threshold:
            # Check we haven't already reported this shift recently
            last_corr = self._last_correlations.get(pair_key)
            if last_corr is not None and abs(current_corr - last_corr) < 0.1:
                return None  # Already reported, don't spam

            self._last_correlations[pair_key] = current_corr
            self._stats["shifts_detected"] += 1

            return PriceMonitorEvent(
                event_id=str(uuid4()),
                ts=reference_tick.ts,
                symbol=reference_tick.symbol,
                venue=reference_tick.venue,
                event_type="correlation_shift",
                severity="warning",
                last_price=reference_tick.price,
                payload={
                    "pair": list(pair_key),
                    "corr_short": current_corr,
                    "corr_baseline": baseline_corr,
                    "delta": delta,
                    "threshold": self._shift_threshold,
                },
                related_symbols=list(pair_key),
            )

        self._last_correlations[pair_key] = current_corr
        return None

    def get_correlation_matrix(self, symbols: list[str] | None = None) -> dict[str, dict[str, float]]:
        """Get current correlation matrix for all (or specified) symbols."""
        syms = symbols or [s for s, p in self._prices.items() if len(p) >= self._corr_window]
        if len(syms) < 2:
            return {}

        matrix: dict[str, dict[str, float]] = {s: {} for s in syms}

        for i, sym_a in enumerate(syms):
            for sym_b in syms[i:]:
                if sym_a == sym_b:
                    matrix[sym_a][sym_b] = 1.0
                    continue

                prices_a = list(self._prices.get(sym_a, []))
                prices_b = list(self._prices.get(sym_b, []))
                n = min(len(prices_a), len(prices_b), self._corr_window)

                if n < 30:
                    matrix[sym_a][sym_b] = None  # type: ignore
                    matrix[sym_b][sym_a] = None  # type: ignore
                    continue

                returns_a = np.diff(np.log([p[1] for p in prices_a[-n:]]))
                returns_b = np.diff(np.log([p[1] for p in prices_b[-n:]]))
                min_len = min(len(returns_a), len(returns_b))

                if min_len < 2:
                    matrix[sym_a][sym_b] = None  # type: ignore
                    matrix[sym_b][sym_a] = None  # type: ignore
                    continue

                corr = float(np.corrcoef(returns_a[-min_len:], returns_b[-min_len:])[0, 1])
                matrix[sym_a][sym_b] = corr
                matrix[sym_b][sym_a] = corr

        return matrix

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
