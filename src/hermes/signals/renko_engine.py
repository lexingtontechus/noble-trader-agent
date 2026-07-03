"""
Renko Bar Constructor + Brick Pattern Analyzer.

Builds renko bricks from venue ticks using Noble Trader's brick_size.
Classifies brick patterns for entry timing decisions.

See roadmap §2.2.3 for full design.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from hermes.schemas.market import Tick, Venue

log = structlog.get_logger(__name__)


class BrickDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    NONE = "none"


class RenkoBrick(BaseModel):
    """A single renko brick."""

    model_config = {"extra": "allow"}

    ts_open: datetime
    ts_close: datetime | None = None
    symbol: str
    venue: Venue
    brick_size: float = Field(..., gt=0)
    direction: BrickDirection
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float = 0.0
    n_ticks: int = 0
    closed: bool = False
    brick_number: int = 0  # sequential number for this symbol


class BrickPattern(str, Enum):
    """Patterns classified from recent brick sequence."""

    BREAKOUT_UP = "breakout_up"
    BREAKOUT_DOWN = "breakout_down"
    PULLBACK_TO_SUPPORT = "pullback_to_support"
    PULLBACK_TO_RESISTANCE = "pullback_to_resistance"
    DOUBLE_TOP = "double_top"
    DOUBLE_BOTTOM = "double_bottom"
    REVERSAL_UP = "reversal_up"
    REVERSAL_DOWN = "reversal_down"
    CONSOLIDATION = "consolidation"
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    UNKNOWN = "unknown"


class RenkoConstructor:
    """
    Builds renko bricks from individual ticks.

    A new brick is formed when price moves by `brick_size` from the last
    brick's close. Supports both fixed and ATR-based brick sizes.

    Usage:
        constructor = RenkoConstructor(brick_size=50.0)
        bricks = constructor.on_tick(tick)  # returns list of newly closed bricks
    """

    def __init__(
        self,
        brick_size: float,
        symbol: str,
        venue: Venue,
        max_bricks: int = 500,
    ) -> None:
        self._brick_size = brick_size
        self._symbol = symbol
        self._venue = venue
        self._bricks: deque[RenkoBrick] = deque(maxlen=max_bricks)
        self._current_brick: RenkoBrick | None = None
        self._brick_count = 0
        self._last_direction: BrickDirection = BrickDirection.NONE
        self._stats = {"bricks_formed": 0, "ticks_processed": 0}

    def on_tick(self, tick: Tick) -> list[RenkoBrick]:
        """
        Process a tick. Returns list of bricks that were closed by this tick
        (can be 0, 1, or multiple if price jumped significantly).
        """
        self._stats["ticks_processed"] += 1
        closed_bricks: list[RenkoBrick] = []

        if self._current_brick is None:
            # Start first brick
            self._current_brick = RenkoBrick(
                ts_open=tick.ts,
                symbol=self._symbol,
                venue=self._venue,
                brick_size=self._brick_size,
                direction=BrickDirection.NONE,
                open_price=tick.price,
                close_price=tick.price,
                high_price=tick.price,
                low_price=tick.price,
                volume=tick.size or 0,
                n_ticks=1,
                brick_number=self._brick_count,
            )
            return closed_bricks

        # Update current brick
        brick = self._current_brick
        brick.high_price = max(brick.high_price, tick.price)
        brick.low_price = min(brick.low_price, tick.price)
        brick.close_price = tick.price
        brick.volume += tick.size or 0
        brick.n_ticks += 1

        # Check if brick should close
        # Up brick: price moved up by brick_size from open
        # Down brick: price moved down by brick_size from open
        up_threshold = brick.open_price + self._brick_size
        down_threshold = brick.open_price - self._brick_size

        if tick.price >= up_threshold:
            # Close as up brick
            brick.direction = BrickDirection.UP
            brick.closed = True
            brick.ts_close = tick.ts
            brick.close_price = up_threshold  # snap to brick boundary
            self._bricks.append(brick)
            self._brick_count += 1
            self._stats["bricks_formed"] += 1
            closed_bricks.append(brick)
            self._last_direction = BrickDirection.UP

            # Check if price jumped multiple bricks (rare but possible)
            remaining = tick.price - up_threshold
            while remaining >= self._brick_size:
                next_open = up_threshold
                next_close = next_open + self._brick_size
                next_brick = RenkoBrick(
                    ts_open=tick.ts,
                    ts_close=tick.ts,
                    symbol=self._symbol,
                    venue=self._venue,
                    brick_size=self._brick_size,
                    direction=BrickDirection.UP,
                    open_price=next_open,
                    close_price=next_close,
                    high_price=next_close,
                    low_price=next_open,
                    volume=0,
                    n_ticks=0,
                    closed=True,
                    brick_number=self._brick_count,
                )
                self._bricks.append(next_brick)
                self._brick_count += 1
                self._stats["bricks_formed"] += 1
                closed_bricks.append(next_brick)
                up_threshold = next_close
                remaining -= self._brick_size

            # Start new brick from the last close
            self._current_brick = RenkoBrick(
                ts_open=tick.ts,
                symbol=self._symbol,
                venue=self._venue,
                brick_size=self._brick_size,
                direction=BrickDirection.NONE,
                open_price=up_threshold,
                close_price=tick.price,
                high_price=max(up_threshold, tick.price),
                low_price=min(up_threshold, tick.price),
                volume=0,
                n_ticks=1,
                brick_number=self._brick_count,
            )

        elif tick.price <= down_threshold:
            # Close as down brick
            brick.direction = BrickDirection.DOWN
            brick.closed = True
            brick.ts_close = tick.ts
            brick.close_price = down_threshold  # snap to brick boundary
            self._bricks.append(brick)
            self._brick_count += 1
            self._stats["bricks_formed"] += 1
            closed_bricks.append(brick)
            self._last_direction = BrickDirection.DOWN

            # Check if price jumped multiple bricks
            remaining = down_threshold - tick.price
            while remaining >= self._brick_size:
                next_open = down_threshold
                next_close = next_open - self._brick_size
                next_brick = RenkoBrick(
                    ts_open=tick.ts,
                    ts_close=tick.ts,
                    symbol=self._symbol,
                    venue=self._venue,
                    brick_size=self._brick_size,
                    direction=BrickDirection.DOWN,
                    open_price=next_open,
                    close_price=next_close,
                    high_price=next_open,
                    low_price=next_close,
                    volume=0,
                    n_ticks=0,
                    closed=True,
                    brick_number=self._brick_count,
                )
                self._bricks.append(next_brick)
                self._brick_count += 1
                self._stats["bricks_formed"] += 1
                closed_bricks.append(next_brick)
                down_threshold = next_close
                remaining -= self._brick_size

            # Start new brick from the last close
            self._current_brick = RenkoBrick(
                ts_open=tick.ts,
                symbol=self._symbol,
                venue=self._venue,
                brick_size=self._brick_size,
                direction=BrickDirection.NONE,
                open_price=down_threshold,
                close_price=tick.price,
                high_price=max(down_threshold, tick.price),
                low_price=min(down_threshold, tick.price),
                volume=0,
                n_ticks=1,
                brick_number=self._brick_count,
            )

        return closed_bricks

    def get_bricks(self, n: int | None = None) -> list[RenkoBrick]:
        """Get closed bricks (most recent N, or all)."""
        bricks = list(self._bricks)
        return bricks[-n:] if n else bricks

    def get_current_brick(self) -> RenkoBrick | None:
        """Get the currently forming (unclosed) brick."""
        return self._current_brick

    def get_last_price(self) -> float | None:
        """Get the last tick price seen."""
        if self._current_brick:
            return self._current_brick.close_price
        return None

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()

    def update_brick_size(self, new_brick_size: float) -> None:
        """Update brick size (e.g., when NT sends a new sweep with different brick_size)."""
        if new_brick_size <= 0:
            return
        if abs(new_brick_size - self._brick_size) / self._brick_size > 0.1:
            log.info(
                "brick_size_updated",
                symbol=self._symbol,
                old=self._brick_size,
                new=new_brick_size,
                change_pct=abs(new_brick_size - self._brick_size) / self._brick_size * 100,
            )
        self._brick_size = new_brick_size


class BrickPatternAnalyzer:
    """
    Classifies the last N bricks into patterns for entry timing decisions.

    Patterns:
    - breakout_up/down: consecutive same-direction bricks
    - pullback_to_support/resistance: reversal after trend
    - double_top/bottom: two peaks/troughs at similar levels
    - reversal_up/down: direction change
    - consolidation: alternating small bricks
    - trend_up/down: sustained directional movement
    """

    def __init__(self, lookback: int = 10) -> None:
        self._lookback = lookback

    def classify(self, bricks: list[RenkoBrick]) -> BrickPattern:
        """Classify the pattern from recent bricks."""
        if len(bricks) < 2:
            return BrickPattern.UNKNOWN

        recent = bricks[-self._lookback :] if len(bricks) > self._lookback else bricks
        directions = [b.direction for b in recent if b.closed]

        if len(directions) < 2:
            return BrickPattern.UNKNOWN

        # Count consecutive directions
        last_dir = directions[-1]
        consecutive = 1
        for d in reversed(directions[:-1]):
            if d == last_dir:
                consecutive += 1
            else:
                break

        # Breakout: 3+ consecutive same direction
        if consecutive >= 3:
            if last_dir == BrickDirection.UP:
                return BrickPattern.BREAKOUT_UP
            else:
                return BrickPattern.BREAKOUT_DOWN

        # Trend: 2 consecutive + majority in same direction
        up_count = sum(1 for d in directions if d == BrickDirection.UP)
        down_count = sum(1 for d in directions if d == BrickDirection.DOWN)
        if consecutive >= 2:
            if last_dir == BrickDirection.UP and up_count > down_count:
                return BrickPattern.TREND_UP
            elif last_dir == BrickDirection.DOWN and down_count > up_count:
                return BrickPattern.TREND_DOWN

        # Reversal: direction changed in last brick
        if len(directions) >= 2 and directions[-1] != directions[-2]:
            if directions[-1] == BrickDirection.UP:
                return BrickPattern.REVERSAL_UP
            else:
                return BrickPattern.REVERSAL_DOWN

        # Double top/bottom: check price levels
        if len(recent) >= 4:
            highs = [b.high_price for b in recent[-4:]]
            lows = [b.low_price for b in recent[-4:]]
            # Double top: two similar highs with a dip between
            if (
                abs(highs[0] - highs[2]) / max(highs[0], highs[2]) < 0.01
                and highs[1] < highs[0] * 0.99
            ):
                return BrickPattern.DOUBLE_TOP
            # Double bottom: two similar lows with a bump between
            if (
                abs(lows[0] - lows[2]) / max(lows[0], lows[2]) < 0.01
                and lows[1] > lows[0] * 1.01
            ):
                return BrickPattern.DOUBLE_BOTTOM

        # Pullback: trend then reversal
        if len(directions) >= 3:
            first_two = directions[:2]
            last_one = directions[-1]
            if all(d == BrickDirection.UP for d in first_two) and last_one == BrickDirection.DOWN:
                return BrickPattern.PULLBACK_TO_SUPPORT
            if all(d == BrickDirection.DOWN for d in first_two) and last_one == BrickDirection.UP:
                return BrickPattern.PULLBACK_TO_RESISTANCE

        # Consolidation: alternating directions
        if len(directions) >= 4:
            alternating = all(
                directions[i] != directions[i + 1] for i in range(len(directions) - 1)
            )
            if alternating:
                return BrickPattern.CONSOLIDATION

        return BrickPattern.UNKNOWN

    def get_pattern_summary(self, bricks: list[RenkoBrick]) -> dict:
        """Get detailed pattern analysis for logging/debugging."""
        pattern = self.classify(bricks)
        recent = bricks[-self._lookback :] if len(bricks) > self._lookback else bricks
        directions = [b.direction.value for b in recent if b.closed]

        return {
            "pattern": pattern.value,
            "recent_directions": directions,
            "n_bricks_analyzed": len(directions),
            "last_brick_direction": directions[-1] if directions else None,
            "last_brick_close": recent[-1].close_price if recent else None,
        }
