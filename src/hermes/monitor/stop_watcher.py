"""
Stop-Loss / Take-Profit / Trailing Stop Watcher.

Monitors open positions against live prices and emits events when:
- Stop-loss price is hit (stop_hit)
- Take-profit price is hit (target_hit)
- Trailing stop moves (trail_update)
- PnL tail risk warning (pnl_warning)

Target: sub-50ms latency from tick arrival to event emission.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

import structlog

from hermes.schemas.market import Position, PriceMonitorEvent, Tick, Venue

log = structlog.get_logger(__name__)


class StopWatcher:
    """
    Watches open positions for stop/target hits and manages trailing stops.

    Usage:
        watcher = StopWatcher()
        watcher.add_position(position)
        event = watcher.on_tick(tick)  # returns list[PriceMonitorEvent]
    """

    def __init__(
        self,
        pnl_warning_threshold_r: float = -1.0,
        trailing_atr_mult: float = 3.0,
        trailing_brick_count: int = 1,
    ) -> None:
        self._positions: dict[str, Position] = {}  # position_id → Position
        self._symbol_positions: dict[str, list[str]] = {}  # symbol → [position_ids]
        self._pnl_warning_threshold_r = pnl_warning_threshold_r
        self._trailing_atr_mult = trailing_atr_mult
        self._trailing_brick_count = trailing_brick_count
        self._stats = {
            "stops_hit": 0,
            "targets_hit": 0,
            "trails_updated": 0,
            "pnl_warnings": 0,
        }

    def add_position(self, position: Position) -> None:
        """Register a position for monitoring."""
        self._positions[position.position_id] = position
        if position.symbol not in self._symbol_positions:
            self._symbol_positions[position.symbol] = []
        self._symbol_positions[position.symbol].append(position.position_id)
        log.info(
            "position_added_to_watcher",
            position_id=position.position_id,
            symbol=position.symbol,
            direction=position.direction,
            entry=position.entry_price,
            stop=position.stop_price,
            target=position.target_price,
        )

    def remove_position(self, position_id: str) -> None:
        """Remove a position from monitoring."""
        position = self._positions.pop(position_id, None)
        if position and position.symbol in self._symbol_positions:
            self._symbol_positions[position.symbol] = [
                pid for pid in self._symbol_positions[position.symbol] if pid != position_id
            ]

    def on_tick(
        self,
        tick: Tick,
        current_atr: float | None = None,
    ) -> list[PriceMonitorEvent]:
        """
        Process a tick. Returns list of events (stop_hit, target_hit, trail_update, pnl_warning).
        """
        events: list[PriceMonitorEvent] = []
        position_ids = self._symbol_positions.get(tick.symbol, [])

        for pid in position_ids[:]:  # copy since we may modify
            position = self._positions.get(pid)
            if position is None:
                continue

            # Check stop hit
            if self._is_stop_hit(position, tick.price):
                event = self._create_event(
                    tick=tick,
                    position=position,
                    event_type="stop_hit",
                    severity="critical",
                    payload={
                        "stop_price": position.stop_price,
                        "fill_price_estimate": tick.price,
                        "slippage_estimate": abs(tick.price - position.stop_price),
                        "position_qty": position.qty,
                        "direction": position.direction,
                    },
                )
                events.append(event)
                self._stats["stops_hit"] += 1
                # Position is closed — remove from monitoring
                self.remove_position(pid)
                continue

            # Check target hit
            if self._is_target_hit(position, tick.price):
                event = self._create_event(
                    tick=tick,
                    position=position,
                    event_type="target_hit",
                    severity="info",
                    payload={
                        "target_price": position.target_price,
                        "fill_price_estimate": tick.price,
                        "slippage_estimate": abs(tick.price - position.target_price),
                        "position_qty": position.qty,
                        "direction": position.direction,
                    },
                )
                events.append(event)
                self._stats["targets_hit"] += 1
                self.remove_position(pid)
                continue

            # Update trailing stop.
            # Only the 'atr' / 'brick_boundary' methods need ATR. The 'percentage'
            # method does NOT use ATR, so gating it on `current_atr is not None`
            # made percentage trailing stops never fire. Require ATR only for the
            # methods that actually consume it.
            needs_atr = position.trailing_method in ("atr", "brick_boundary")
            if position.trailing_method and (not needs_atr or current_atr is not None):
                new_trail = self._compute_trailing_stop(
                    position, tick.price, current_atr
                )
                if new_trail is not None and (
                    position.trailing_stop is None
                    or self._is_trail_better(position, new_trail)
                ):
                    old_stop = position.trailing_stop or position.stop_price
                    position.trailing_stop = new_trail

                    event = self._create_event(
                        tick=tick,
                        position=position,
                        event_type="trail_update",
                        severity="info",
                        payload={
                            "old_stop": old_stop,
                            "new_stop": new_trail,
                            "trail_method": position.trailing_method,
                            "atr_at_update": current_atr,
                        },
                    )
                    events.append(event)
                    self._stats["trails_updated"] += 1

            # PnL warning
            pnl_r = self._compute_r_multiple(position, tick.price)
            if pnl_r <= self._pnl_warning_threshold_r:
                event = self._create_event(
                    tick=tick,
                    position=position,
                    event_type="pnl_warning",
                    severity="warning",
                    payload={
                        "pnl_now_usd": self._compute_pnl_usd(position, tick.price),
                        "r_multiple_now": pnl_r,
                        "threshold_r": self._pnl_warning_threshold_r,
                        "stop_price": position.trailing_stop or position.stop_price,
                    },
                )
                events.append(event)
                self._stats["pnl_warnings"] += 1

        return events

    def _is_stop_hit(self, position: Position, price: float) -> bool:
        """Check if stop-loss is hit."""
        effective_stop = position.trailing_stop or position.stop_price
        if position.direction == "long":
            return price <= effective_stop
        else:  # short
            return price >= effective_stop

    def _is_target_hit(self, position: Position, price: float) -> bool:
        """Check if take-profit is hit."""
        if position.direction == "long":
            return price >= position.target_price
        else:  # short
            return price <= position.target_price

    def _compute_trailing_stop(
        self,
        position: Position,
        current_price: float,
        current_atr: float,
    ) -> float | None:
        """Compute new trailing stop based on method."""
        if position.trailing_method == "atr":
            if position.direction == "long":
                return current_price - (self._trailing_atr_mult * current_atr)
            else:
                return current_price + (self._trailing_atr_mult * current_atr)

        elif position.trailing_method == "percentage":
            pct = 0.02  # 2% trailing
            if position.direction == "long":
                return current_price * (1 - pct)
            else:
                return current_price * (1 + pct)

        elif position.trailing_method == "brick_boundary":
            # Simplified: use ATR as proxy for brick size
            brick_size = current_atr
            if position.direction == "long":
                return current_price - (self._trailing_brick_count * brick_size)
            else:
                return current_price + (self._trailing_brick_count * brick_size)

        return None

    def _is_trail_better(self, position: Position, new_stop: float) -> bool:
        """Check if new trailing stop is better (closer to current price in profit direction)."""
        if position.direction == "long":
            return new_stop > (position.trailing_stop or position.stop_price)
        else:
            return new_stop < (position.trailing_stop or position.stop_price)

    @staticmethod
    def _compute_r_multiple(position: Position, current_price: float) -> float:
        """Compute R-multiple at current price."""
        if position.direction == "long":
            pnl = (current_price - position.entry_price) * position.qty
        else:
            pnl = (position.entry_price - current_price) * position.qty
        return pnl / position.risk_amount if position.risk_amount > 0 else 0.0

    @staticmethod
    def _compute_pnl_usd(position: Position, current_price: float) -> float:
        """Compute unrealized PnL in USD."""
        if position.direction == "long":
            return (current_price - position.entry_price) * position.qty
        else:
            return (position.entry_price - current_price) * position.qty

    @staticmethod
    def _create_event(
        tick: Tick,
        position: Position,
        event_type: str,
        severity: str,
        payload: dict,
    ) -> PriceMonitorEvent:
        return PriceMonitorEvent(
            event_id=str(uuid4()),
            ts=tick.ts,
            symbol=tick.symbol,
            venue=tick.venue,
            event_type=event_type,  # type: ignore
            severity=severity,  # type: ignore
            last_price=tick.price,
            payload=payload,
            position_id=position.position_id,
        )

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()

    def get_positions(self) -> list[Position]:
        return list(self._positions.values())
