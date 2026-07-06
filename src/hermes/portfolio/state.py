"""
Portfolio State Service — tracks positions, cash, exposure, equity.

The single source of truth for portfolio state. All other components
(risk gate, snapshot writer, dashboard) read from this service.

See roadmap §2.3 for design.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.schemas.market import Position, Venue

log = structlog.get_logger(__name__)


class PortfolioPosition(BaseModel):
    """A tracked position in the portfolio."""

    position_id: str
    symbol: str
    venue: str
    direction: str  # long | short
    qty: float
    entry_price: float
    current_price: float
    stop_price: float
    target_price: float
    opened_at: datetime
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    risk_amount: float  # $ at risk at entry
    trailing_stop: float | None = None
    strategy_id: str = ""
    signal_id: str = ""  # BlendedSignal that triggered this position


class PortfolioMetrics(BaseModel):
    """Snapshot of portfolio metrics."""

    equity_total: float
    cash_usd: float
    cash_usdc: float
    margin_used: float
    margin_available: float
    leverage_gross: float
    leverage_net: float

    realized_pnl: float
    unrealized_pnl: float
    funding_pnl: float
    fees_paid: float

    gross_exposure_usd: float
    net_exposure_usd: float
    long_exposure_usd: float
    short_exposure_usd: float
    n_open_positions: int
    n_venues: int

    peak_equity: float
    drawdown_pct: float
    drawdown_usd: float
    time_in_dd_sec: int

    var_1d_99: float | None = None
    cvar_1d_99: float | None = None
    beta_to_spy: float | None = None

    config_hash: str = ""
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PortfolioStateService:
    """
    Single source of truth for portfolio state.

    Tracks:
    - Open positions (by position_id and by symbol)
    - Cash (USD for Alpaca, USDC for Hyperliquid)
    - Realized + unrealized PnL
    - Exposure (gross, net, long, short)
    - Drawdown (peak equity, current DD, time in DD)

    Usage:
        state = PortfolioStateService(initial_equity=100000)
        state.add_position(position)
        state.update_price("BTC", 65000)
        metrics = state.get_metrics()
    """

    def __init__(
        self,
        initial_equity: float = 100000,
        initial_cash_usd: float = 50000,
        initial_cash_usdc: float = 50000,
        config_hash: str = "",
    ) -> None:
        self._positions: dict[str, PortfolioPosition] = {}  # position_id → position
        self._symbol_positions: dict[str, list[str]] = defaultdict(list)  # symbol → [position_ids]

        self._cash_usd = initial_cash_usd
        self._cash_usdc = initial_cash_usdc
        self._initial_equity = initial_equity

        self._realized_pnl = 0.0
        self._funding_pnl = 0.0
        self._fees_paid = 0.0

        self._peak_equity = initial_equity
        self._dd_start_time: datetime | None = None
        self._time_in_dd_sec = 0

        self._config_hash = config_hash

        self._stats = {
            "positions_opened": 0,
            "positions_closed": 0,
            "snapshots_taken": 0,
        }

    def add_position(
        self,
        position: Position,
        signal_id: str = "",
        strategy_id: str = "",
    ) -> str:
        """Register a new position."""
        position_id = position.position_id
        p = PortfolioPosition(
            position_id=position_id,
            symbol=position.symbol,
            venue=position.venue.value,
            direction=position.direction,
            qty=position.qty,
            entry_price=position.entry_price,
            current_price=position.entry_price,
            stop_price=position.stop_price,
            target_price=position.target_price,
            opened_at=position.opened_at,
            risk_amount=position.risk_amount,
            signal_id=signal_id,
            strategy_id=strategy_id,
        )
        self._positions[position_id] = p
        self._symbol_positions[position.symbol].append(position_id)
        self._stats["positions_opened"] += 1

        # Deduct cash for the position
        notional = position.qty * position.entry_price
        if position.venue == Venue.ALPACA:
            self._cash_usd -= notional
        else:
            self._cash_usdc -= notional

        log.info(
            "position_opened",
            position_id=position_id,
            symbol=position.symbol,
            direction=position.direction,
            qty=position.qty,
            entry=position.entry_price,
            notional=notional,
        )
        return position_id

    def remove_position(self, position_id: str, exit_price: float, exit_reason: str = "") -> dict[str, Any]:
        """Close a position and realize PnL."""
        pos = self._positions.pop(position_id, None)
        if pos is None:
            log.warning("position_not_found", position_id=position_id)
            return {}

        # Calculate realized PnL
        if pos.direction == "long":
            realized = (exit_price - pos.entry_price) * pos.qty
        else:
            realized = (pos.entry_price - exit_price) * pos.qty

        self._realized_pnl += realized

        # Return cash
        notional = pos.qty * exit_price
        if pos.venue == "alpaca":
            self._cash_usd += notional
        else:
            self._cash_usdc += notional

        # Remove from symbol index
        if pos.symbol in self._symbol_positions:
            self._symbol_positions[pos.symbol] = [
                pid for pid in self._symbol_positions[pos.symbol] if pid != position_id
            ]

        self._stats["positions_closed"] += 1

        log.info(
            "position_closed",
            position_id=position_id,
            symbol=pos.symbol,
            exit_price=exit_price,
            realized_pnl=realized,
            exit_reason=exit_reason,
        )

        return {
            "position_id": position_id,
            "symbol": pos.symbol,
            "direction": pos.direction,
            "entry_price": pos.entry_price,
            "exit_price": exit_price,
            "qty": pos.qty,
            "realized_pnl": realized,
            "r_multiple": realized / pos.risk_amount if pos.risk_amount > 0 else 0.0,
            "hold_duration_sec": (datetime.now(timezone.utc) - pos.opened_at).total_seconds(),
            "exit_reason": exit_reason,
        }

    def update_price(self, symbol: str, price: float) -> None:
        """Update current price for all positions of a symbol."""
        for pid in self._symbol_positions.get(symbol, []):
            pos = self._positions.get(pid)
            if pos:
                pos.current_price = price
                if pos.direction == "long":
                    pos.unrealized_pnl = (price - pos.entry_price) * pos.qty
                else:
                    pos.unrealized_pnl = (pos.entry_price - price) * pos.qty

    def get_position(self, position_id: str) -> PortfolioPosition | None:
        return self._positions.get(position_id)

    def get_positions_by_symbol(self, symbol: str) -> list[PortfolioPosition]:
        return [self._positions[pid] for pid in self._symbol_positions.get(symbol, []) if pid in self._positions]

    def get_all_positions(self) -> list[PortfolioPosition]:
        return list(self._positions.values())

    def get_metrics(self, var_1d_99: float | None = None, cvar_1d_99: float | None = None) -> PortfolioMetrics:
        """Compute current portfolio metrics."""
        positions = list(self._positions.values())

        # Exposure
        long_exposure = sum(p.qty * p.current_price for p in positions if p.direction == "long")
        short_exposure = sum(p.qty * p.current_price for p in positions if p.direction == "short")
        gross_exposure = long_exposure + short_exposure
        net_exposure = long_exposure - short_exposure

        # Unrealized PnL
        unrealized = sum(p.unrealized_pnl for p in positions)

        # Equity
        equity = self._cash_usd + self._cash_usdc + gross_exposure + unrealized

        # Leverage - protect against division by zero
        if equity <= 0:
            log.warning(
                "invalid_equity_for_leverage_calculation",
                equity=equity,
                gross_exposure=gross_exposure,
                net_exposure=net_exposure,
                note="Equity is zero or negative - using safe defaults"
            )
            leverage_gross = 0.0
            leverage_net = 0.0
        else:
            leverage_gross = gross_exposure / equity
            leverage_net = net_exposure / equity

        # Drawdown
        if equity > self._peak_equity:
            self._peak_equity = equity
            self._dd_start_time = None
        
        # Protect against division by zero in drawdown calculation
        if self._peak_equity <= 0:
            log.warning(
                "invalid_peak_equity_for_drawdown_calculation",
                peak_equity=self._peak_equity,
                equity=equity,
                note="Peak equity is zero or negative - using safe defaults"
            )
            drawdown_usd = 0.0
            drawdown_pct = 0.0
        else:
            drawdown_usd = self._peak_equity - equity
            drawdown_pct = drawdown_usd / self._peak_equity

        if drawdown_pct > 0:
            if self._dd_start_time is None:
                self._dd_start_time = datetime.now(timezone.utc)
            self._time_in_dd_sec = int((datetime.now(timezone.utc) - self._dd_start_time).total_seconds())
        else:
            self._dd_start_time = None
            self._time_in_dd_sec = 0

        # Venues
        venues = set(p.venue for p in positions)

        # Margin (simplified: margin = gross_exposure / leverage)
        margin_used = gross_exposure / 4.0 if leverage_gross > 1 else gross_exposure  # assume 4x max
        margin_available = equity - margin_used

        return PortfolioMetrics(
            equity_total=round(equity, 2),
            cash_usd=round(self._cash_usd, 2),
            cash_usdc=round(self._cash_usdc, 2),
            margin_used=round(margin_used, 2),
            margin_available=round(margin_available, 2),
            leverage_gross=round(leverage_gross, 4),
            leverage_net=round(leverage_net, 4),
            realized_pnl=round(self._realized_pnl, 2),
            unrealized_pnl=round(unrealized, 2),
            funding_pnl=round(self._funding_pnl, 2),
            fees_paid=round(self._fees_paid, 2),
            gross_exposure_usd=round(gross_exposure, 2),
            net_exposure_usd=round(net_exposure, 2),
            long_exposure_usd=round(long_exposure, 2),
            short_exposure_usd=round(short_exposure, 2),
            n_open_positions=len(positions),
            n_venues=len(venues),
            peak_equity=round(self._peak_equity, 2),
            drawdown_pct=round(drawdown_pct, 6),
            drawdown_usd=round(drawdown_usd, 2),
            time_in_dd_sec=self._time_in_dd_sec,
            var_1d_99=var_1d_99,
            cvar_1d_99=cvar_1d_99,
            config_hash=self._config_hash,
        )

    def add_funding_pnl(self, amount: float) -> None:
        """Accrue funding PnL (positive = received, negative = paid)."""
        self._funding_pnl += amount

    def add_fees(self, amount: float) -> None:
        """Track fees paid."""
        self._fees_paid += amount

    def get_stats(self) -> dict[str, Any]:
        return self._stats.copy()
