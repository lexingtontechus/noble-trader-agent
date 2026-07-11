"""
PnL Service — realized + unrealized PnL tracking + attribution.

Decomposes PnL into components:
- Directional (from price move)
- Timing (from entry timing)
- Sizing (from position sizing)
- Regime (regime-attributed)
- Funding (for perps)
- Fees
- Slippage

See roadmap §6.2.5.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.execution.orders import Fill, Order
from hermes.portfolio.state import PortfolioStateService

log = structlog.get_logger(__name__)


class RealizedPnL(BaseModel):
    """Realized PnL for a closed trade."""

    pnl_id: str = Field(default_factory=lambda: str(uuid4()))
    trade_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    venue: str
    strategy_id: str = ""
    regime_at_close: str | None = None

    # Components (in USD)
    gross_pnl: float  # (exit - entry) * qty, signed
    fees_total: float  # entry + exit fees
    funding_pnl: float  # for perps, accrues over hold
    slippage_cost: float  # vs arrival price, both legs
    net_pnl: float  # gross - fees - slippage + funding
    net_pnl_bps: float  # net_pnl / notional * 10000

    # R-multiple
    risk_amount: float  # $ at risk (stop distance * qty)
    r_multiple: float  # net_pnl / risk_amount

    # Holding
    hold_duration_sec: int
    n_fills: int

    # Attribution
    direction_pnl: float | None = None
    timing_pnl: float | None = None
    sizing_pnl: float | None = None
    regime_pnl: float | None = None

    config_hash: str = ""


class UnrealizedPnL(BaseModel):
    """Unrealized PnL snapshot for an open position."""

    snapshot_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    venue: str
    strategy_id: str = ""
    position_id: str

    position_qty: float
    avg_entry_price: float
    mark_price: float
    unrealized_gross: float
    unrealized_funding: float
    unrealized_fees_est: float
    unrealized_net: float
    position_notional: float
    position_risk: float  # current $ at risk


class PnLAttribution:
    """
    Decomposes PnL into directional, timing, sizing, regime components.

    - Directional: PnL from the price move (exit - entry)
    - Timing: PnL from Hermes's entry timing (NT_entry vs actual_entry)
    - Sizing: PnL from position sizing (larger size = more PnL)
    - Regime: PnL attributed to the regime at close
    """

    @staticmethod
    def compute(
        entry_price: float,
        exit_price: float,
        nt_entry_price: float,
        qty: float,
        risk_amount: float,
        regime_at_close: str | None,
        gross_pnl: float,
        fees: float,
        slippage: float,
        funding: float,
        direction: str = "long",
    ) -> dict[str, float]:
        """
        Compute PnL attribution.

        Returns dict with direction_pnl, timing_pnl, sizing_pnl, regime_pnl.
        """
        # Directional: PnL from pure price move
        direction_pnl = (exit_price - entry_price) * qty

        # Timing: PnL from entry timing alpha
        # If Hermes entered better than NT suggested, that's timing alpha
        entry_alpha = (nt_entry_price - entry_price) * qty  # positive = Hermes entered better (long)
        # For SHORTS a better entry is HIGHER than NT's suggested entry, so the sign
        # flips. Without this, short timing alpha is reported inverted (penalised when
        # Hermes actually entered better).
        timing_pnl = entry_alpha if direction == "long" else -entry_alpha

        # Sizing: PnL from position sizing (relative to a "baseline" size)
        # Baseline = risk_amount / stop_distance (standard risk-based sizing)
        # If Hermes sized differently, the delta PnL is sizing PnL
        # Simplified: assume baseline = qty, so sizing PnL = 0 (no deviation)
        sizing_pnl = 0.0  # TODO: compare actual size to "standard" size

        # Regime: PnL attributed to regime (simplified: map regime to multiplier)
        regime_multiplier = {
            "calm_trend": 1.0,
            "choppy_range": 0.5,
            "high_vol_breakout": 1.5,
            "regime_transition": 0.3,
            "risk_off": -1.0,
            "funding_stress": -0.5,
            "liquidity_drained": -0.3,
        }
        mult = regime_multiplier.get(regime_at_close or "", 1.0)
        regime_pnl = gross_pnl * (mult - 1.0)  # delta from baseline

        return {
            "direction_pnl": round(direction_pnl, 2),
            "timing_pnl": round(timing_pnl, 2),
            "sizing_pnl": round(sizing_pnl, 2),
            "regime_pnl": round(regime_pnl, 2),
        }


class DrawdownTracker:
    """
    Tracks drawdown statistics from equity curve.

    - Peak equity
    - Current drawdown %
    - Max drawdown %
    - Time in drawdown
    - Recovery half-life (time to recover half the DD)
    - Underwater curve (time spent below peak)
    """

    def __init__(self) -> None:
        self._peak_equity: float = 0.0
        self._max_dd_pct: float = 0.0
        self._max_dd_usd: float = 0.0
        self._dd_start: datetime | None = None
        self._total_time_in_dd_sec: int = 0
        self._equity_history: list[tuple[datetime, float]] = []
        self._underwater_periods: int = 0
        self._total_periods: int = 0

    def _compute_stats(self, equity: float, ts: datetime) -> dict[str, Any]:
        """Compute drawdown stats for a given equity WITHOUT mutating history."""
        current_dd_usd = self._peak_equity - equity
        current_dd_pct = current_dd_usd / self._peak_equity if self._peak_equity > 0 else 0
        return {
            "peak_equity": round(self._peak_equity, 2),
            "current_dd_pct": round(current_dd_pct, 6),
            "current_dd_usd": round(current_dd_usd, 2),
            "max_dd_pct": round(self._max_dd_pct, 6),
            "max_dd_usd": round(self._max_dd_usd, 2),
            "time_in_dd_sec": self._total_time_in_dd_sec + (
                int((ts - self._dd_start).total_seconds()) if self._dd_start else 0
            ),
            "underwater_pct": round(
                self._underwater_periods / self._total_periods if self._total_periods > 0 else 0, 4
            ),
        }

    def update(self, equity: float, ts: datetime | None = None) -> dict[str, Any]:
        """Update drawdown tracking with new equity value."""
        ts = ts or datetime.now(timezone.utc)
        self._equity_history.append((ts, equity))
        self._total_periods += 1

        if equity > self._peak_equity:
            self._peak_equity = equity
            if self._dd_start is not None:
                # Was in DD, now recovered
                dd_duration = (ts - self._dd_start).total_seconds()
                self._total_time_in_dd_sec += int(dd_duration)
                self._dd_start = None
        else:
            if self._dd_start is None:
                self._dd_start = ts
            self._underwater_periods += 1

        current_dd_usd = self._peak_equity - equity
        current_dd_pct = current_dd_usd / self._peak_equity if self._peak_equity > 0 else 0

        if current_dd_pct > self._max_dd_pct:
            self._max_dd_pct = current_dd_pct
            self._max_dd_usd = current_dd_usd

        return self._compute_stats(equity, ts)

    def get_stats(self) -> dict[str, Any]:
        """Get current drawdown statistics.

        NOTE: must NOT call update() -- that would append a DUPLICATE equity entry
        to _equity_history on every read, corrupting the equity curve / drawdown math.
        """
        if not self._equity_history:
            return {}
        latest_ts, latest_equity = self._equity_history[-1]
        return self._compute_stats(latest_equity, latest_ts)

    def get_equity_curve(self) -> list[tuple[str, float]]:
        """Get equity curve as list of (iso_ts, equity) tuples."""
        return [(ts.isoformat(), eq) for ts, eq in self._equity_history]


class PnLService:
    """
    PnL tracking service — realized + unrealized + attribution.

    Usage:
        service = PnLService(config, portfolio_state)
        await service.start()
        # On position close:
        realized = service.record_realized_pnl(trade_data)
        # Periodically:
        await service.snapshot_unrealized()
    """

    def __init__(
        self,
        config: HermesConfig,
        portfolio_state: PortfolioStateService,
    ) -> None:
        self._config = config
        self._state = portfolio_state
        self._db_path = get_duckdb_path(config)
        self._drawdown = DrawdownTracker()
        self._funding_accrual: dict[str, float] = defaultdict(float)  # symbol → accumulated funding
        self._stats = {
            "realized_recorded": 0,
            "unrealized_snapshots": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        log.info("pnl_service_started")

    async def stop(self) -> None:
        log.info("pnl_service_stopped", stats=self._stats)

    def record_realized_pnl(
        self,
        trade_id: str,
        symbol: str,
        venue: str,
        direction: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        fees: float,
        slippage: float,
        funding: float,
        risk_amount: float,
        hold_duration_sec: int,
        n_fills: int,
        nt_entry_price: float,
        regime_at_close: str | None = None,
        strategy_id: str = "",
        config_hash: str = "",
    ) -> RealizedPnL:
        """
        Record realized PnL for a closed trade.

        Computes:
        - Gross PnL (exit - entry) * qty, signed by direction
        - Net PnL (gross - fees - slippage + funding)
        - R-multiple (net_pnl / risk_amount)
        - Attribution (directional, timing, sizing, regime)
        """
        # Gross PnL
        if direction == "long":
            gross_pnl = (exit_price - entry_price) * qty
        else:
            gross_pnl = (entry_price - exit_price) * qty

        # Net PnL
        net_pnl = gross_pnl - fees - slippage + funding
        notional = entry_price * qty
        net_pnl_bps = (net_pnl / notional * 10000) if notional > 0 else 0

        # R-multiple
        r_multiple = net_pnl / risk_amount if risk_amount > 0 else 0

        # Attribution
        attribution = PnLAttribution.compute(
            entry_price=entry_price,
            exit_price=exit_price,
            nt_entry_price=nt_entry_price,
            qty=qty,
            risk_amount=risk_amount,
            regime_at_close=regime_at_close,
            gross_pnl=gross_pnl,
            fees=fees,
            slippage=slippage,
            funding=funding,
            direction=direction,
        )

        realized = RealizedPnL(
            trade_id=trade_id,
            symbol=symbol,
            venue=venue,
            strategy_id=strategy_id,
            regime_at_close=regime_at_close,
            gross_pnl=round(gross_pnl, 2),
            fees_total=round(fees, 2),
            funding_pnl=round(funding, 2),
            slippage_cost=round(slippage, 2),
            net_pnl=round(net_pnl, 2),
            net_pnl_bps=round(net_pnl_bps, 2),
            risk_amount=round(risk_amount, 2),
            r_multiple=round(r_multiple, 4),
            hold_duration_sec=hold_duration_sec,
            n_fills=n_fills,
            direction_pnl=attribution["direction_pnl"],
            timing_pnl=attribution["timing_pnl"],
            sizing_pnl=attribution["sizing_pnl"],
            regime_pnl=attribution["regime_pnl"],
            config_hash=config_hash,
        )

        # Write to DuckDB
        self._write_realized(realized)

        # Update drawdown tracker with new equity
        metrics = self._state.get_metrics()
        self._drawdown.update(metrics.equity_total)

        self._stats["realized_recorded"] += 1

        log.info(
            "realized_pnl_recorded",
            trade_id=trade_id,
            symbol=symbol,
            net_pnl=net_pnl,
            r_multiple=r_multiple,
            regime=regime_at_close,
        )

        return realized

    async def snapshot_unrealized(self) -> list[UnrealizedPnL]:
        """Snapshot unrealized PnL for all open positions."""
        positions = self._state.get_all_positions()
        snapshots: list[UnrealizedPnL] = []

        for pos in positions:
            notional = pos.qty * pos.current_price
            unrealized_gross = pos.unrealized_pnl
            unrealized_funding = self._funding_accrual.get(pos.symbol, 0)
            unrealized_fees_est = notional * 0.0002  # estimate 2 bps round-trip
            unrealized_net = unrealized_gross - unrealized_fees_est + unrealized_funding

            stop_distance = abs(pos.entry_price - pos.stop_price)
            position_risk = stop_distance * pos.qty

            snapshot = UnrealizedPnL(
                symbol=pos.symbol,
                venue=pos.venue,
                strategy_id=pos.strategy_id,
                position_id=pos.position_id,
                position_qty=pos.qty,
                avg_entry_price=pos.entry_price,
                mark_price=pos.current_price,
                unrealized_gross=round(unrealized_gross, 2),
                unrealized_funding=round(unrealized_funding, 2),
                unrealized_fees_est=round(unrealized_fees_est, 2),
                unrealized_net=round(unrealized_net, 2),
                position_notional=round(notional, 2),
                position_risk=round(position_risk, 2),
            )

            self._write_unrealized(snapshot)
            snapshots.append(snapshot)

        self._stats["unrealized_snapshots"] += len(snapshots)
        return snapshots

    def add_funding_pnl(self, symbol: str, amount: float) -> None:
        """Accrue funding PnL for a symbol (positive = received)."""
        self._funding_accrual[symbol] += amount
        self._state.add_funding_pnl(amount)

    def get_drawdown_stats(self) -> dict[str, Any]:
        return self._drawdown.get_stats()

    def get_equity_curve(self) -> list[tuple[str, float]]:
        return self._drawdown.get_equity_curve()

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["drawdown"] = self._drawdown.get_stats()
        return stats

    def _write_realized(self, pnl: RealizedPnL) -> None:
        """Write realized PnL to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO pnl_realized (
                        pnl_id, trade_id, ts, symbol, venue, strategy_id, regime_at_close,
                        gross_pnl, fees_total, funding_pnl, slippage_cost,
                        net_pnl, net_pnl_bps, risk_amount, r_multiple,
                        hold_duration_sec, n_fills,
                        direction_pnl, timing_pnl, sizing_pnl, regime_pnl,
                        config_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        pnl.pnl_id, pnl.trade_id, pnl.ts, pnl.symbol, pnl.venue,
                        pnl.strategy_id, pnl.regime_at_close,
                        pnl.gross_pnl, pnl.fees_total, pnl.funding_pnl, pnl.slippage_cost,
                        pnl.net_pnl, pnl.net_pnl_bps, pnl.risk_amount, pnl.r_multiple,
                        pnl.hold_duration_sec, pnl.n_fills,
                        pnl.direction_pnl, pnl.timing_pnl, pnl.sizing_pnl, pnl.regime_pnl,
                        pnl.config_hash,
                    ],
                )
        except Exception as e:
            self._stats["errors"] += 1
            log.error("realized_pnl_write_failed", error=str(e))

    def _write_unrealized(self, snapshot: UnrealizedPnL) -> None:
        """Write unrealized PnL snapshot to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO pnl_unrealized (
                        snapshot_id, ts, symbol, venue, strategy_id, position_id,
                        position_qty, avg_entry_price, mark_price,
                        unrealized_gross, unrealized_funding, unrealized_fees_est,
                        unrealized_net, position_notional, position_risk
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        snapshot.snapshot_id, snapshot.ts, snapshot.symbol, snapshot.venue,
                        snapshot.strategy_id, snapshot.position_id,
                        snapshot.position_qty, snapshot.avg_entry_price, snapshot.mark_price,
                        snapshot.unrealized_gross, snapshot.unrealized_funding,
                        snapshot.unrealized_fees_est, snapshot.unrealized_net,
                        snapshot.position_notional, snapshot.position_risk,
                    ],
                )
        except Exception as e:
            self._stats["errors"] += 1
            log.error("unrealized_pnl_write_failed", error=str(e))
