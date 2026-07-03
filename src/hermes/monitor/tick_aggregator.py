"""
Tick Aggregator — builds OHLCV bars from individual ticks in real time.

Maintains rolling windows of bars at multiple timeframes:
1s, 5s, 1m, 5m, 15m, 1h

Each timeframe keeps a configurable rolling window of bars in memory
(default 500 bars per timeframe per symbol).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal

import structlog

from hermes.schemas.market import Bar, Tick, Venue

log = structlog.get_logger(__name__)

Timeframe = Literal["1s", "5s", "1m", "5m", "15m", "1h"]

# Timeframe to seconds mapping
TIMEFRAME_SECONDS: dict[str, int] = {
    "1s": 1,
    "5s": 5,
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
}


class TickAggregator:
    """
    Aggregates ticks into OHLCV bars at multiple timeframes.

    Usage:
        agg = TickAggregator(timeframes=["1s", "1m", "5m"], window_size=500)
        agg.on_tick(tick)
        bars_1m = agg.get_bars("BTC", "1m")  # last 500 1m bars
    """

    def __init__(
        self,
        timeframes: list[str] | None = None,
        window_size: int = 500,
    ) -> None:
        self._timeframes = timeframes or ["1s", "5s", "1m", "5m", "15m", "1h"]
        self._window_size = window_size

        # (symbol, timeframe) -> deque[Bar]
        self._bars: dict[tuple[str, str], deque[Bar]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )

        # (symbol, timeframe) -> current forming bar
        self._current: dict[tuple[str, str], Bar] = {}

        self._stats = {
            "ticks_processed": 0,
            "bars_closed": 0,
        }

    def on_tick(self, tick: Tick) -> list[Bar]:
        """
        Process a tick. Returns list of bars that were closed by this tick
        (across all timeframes for this symbol).
        """
        self._stats["ticks_processed"] += 1
        closed_bars: list[Bar] = []

        for tf in self._timeframes:
            tf_seconds = TIMEFRAME_SECONDS.get(tf, 60)
            bar_start_ts = self._align_ts(tick.ts, tf_seconds)

            key = (tick.symbol, tf)
            current = self._current.get(key)

            if current is None:
                # Start a new bar
                self._current[key] = self._create_bar(tick, bar_start_ts, tf)
            elif self._bar_ts_key(current.ts_open) != self._bar_ts_key(bar_start_ts):
                # Time has moved to a new bar window — close current and start new
                current.closed = True
                current.ts_close = tick.ts
                closed_bars.append(current)
                self._bars[key].append(current)
                self._stats["bars_closed"] += 1

                self._current[key] = self._create_bar(tick, bar_start_ts, tf)
            else:
                # Update current bar
                self._update_bar(current, tick)

        return closed_bars

    def get_bars(self, symbol: str, timeframe: str, n: int | None = None) -> list[Bar]:
        """
        Get the last N closed bars for a symbol/timeframe.
        If n is None, returns all bars in the rolling window.
        """
        key = (symbol, timeframe)
        bars = list(self._bars.get(key, []))
        if n is not None:
            bars = bars[-n:]
        return bars

    def get_current_bar(self, symbol: str, timeframe: str) -> Bar | None:
        """Get the currently forming bar (not yet closed)."""
        return self._current.get((symbol, timeframe))

    def get_last_price(self, symbol: str) -> float | None:
        """Get the last tick price for a symbol (from current 1s bar)."""
        current = self._current.get((symbol, "1s"))
        if current:
            return current.close
        # Fallback: last closed 1s bar
        bars = self._bars.get((symbol, "1s"), [])
        return bars[-1].close if bars else None

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()

    @staticmethod
    def _align_ts(ts: datetime, tf_seconds: int) -> datetime:
        """Align timestamp to the start of its timeframe bucket."""
        epoch = ts.timestamp()
        aligned_epoch = (int(epoch) // tf_seconds) * tf_seconds
        return datetime.fromtimestamp(aligned_epoch, tz=timezone.utc)

    @staticmethod
    def _bar_ts_key(ts: datetime) -> int:
        """Convert timestamp to a comparable int (epoch seconds)."""
        return int(ts.timestamp())

    @staticmethod
    def _create_bar(tick: Tick, bar_start_ts: datetime, timeframe: str) -> Bar:
        """Create a new bar from the first tick in the window."""
        return Bar(
            ts_open=bar_start_ts,
            ts_close=None,
            venue=tick.venue,
            symbol=tick.symbol,
            timeframe=timeframe,
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            volume=tick.size or 0,
            vwap=tick.price,  # initial VWAP = first price
            n_trades=1,
            closed=False,
        )

    @staticmethod
    def _update_bar(bar: Bar, tick: Tick) -> None:
        """Update an existing bar with a new tick."""
        bar.high = max(bar.high, tick.price)
        bar.low = min(bar.low, tick.price)
        bar.close = tick.price
        tick_size = tick.size or 0
        bar.volume += tick_size

        # Update VWAP: volume-weighted average price
        if bar.n_trades and bar.volume > 0:
            old_vwap = bar.vwap or bar.open
            # Simplified: weighted by size
            total_value = old_vwap * (bar.volume - tick_size) + tick.price * tick_size
            bar.vwap = total_value / bar.volume
        else:
            bar.vwap = tick.price

        if bar.n_trades is None:
            bar.n_trades = 1
        else:
            bar.n_trades += 1
