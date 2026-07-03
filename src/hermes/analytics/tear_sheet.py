"""
Tear Sheet Generator — quantstats-style performance metrics.

Computes 30+ performance metrics from equity curve + trade history:
- Returns: total, annual, monthly, daily
- Risk: Sharpe, Sortino, Calmar, Omega, VaR, CVaR
- Drawdown: max DD, recovery, ulcer index
- Trading: win rate, profit factor, avg R, expectancy
- Distribution: skew, kurtosis, best/worst day

See roadmap §2.5.
"""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import structlog

from hermes.analytics.pnl_service import PnLService

log = structlog.get_logger(__name__)


class TearSheet:
    """Performance metrics tear sheet."""

    def __init__(self, pnl_service: PnLService) -> None:
        self._pnl = pnl_service

    def generate(
        self,
        equity_curve: list[tuple[datetime, float]] | None = None,
        realized_trades: list[dict] | None = None,
    ) -> dict[str, Any]:
        """
        Generate full tear sheet.

        Args:
            equity_curve: List of (timestamp, equity) tuples
            realized_trades: List of realized PnL dicts from DuckDB

        Returns:
            Dict with all metrics grouped by category
        """
        if equity_curve is None:
            equity_curve = self._load_equity_curve()
        if realized_trades is None:
            realized_trades = self._load_realized_trades()

        if len(equity_curve) < 2:
            return {"error": "insufficient_data", "n_points": len(equity_curve)}

        # Extract returns
        equities = [eq for _, eq in equity_curve]
        returns = np.diff(equities) / equities[:-1]
        returns = returns[np.isfinite(returns)]

        metrics: dict[str, Any] = {}

        # === Returns ===
        metrics["returns"] = self._compute_returns(equities, returns, equity_curve)

        # === Risk-adjusted ===
        metrics["risk_adjusted"] = self._compute_risk_adjusted(returns, equities)

        # === Drawdown ===
        metrics["drawdown"] = self._compute_drawdown(equities, equity_curve)

        # === Trading stats ===
        if realized_trades:
            metrics["trading"] = self._compute_trading_stats(realized_trades)
        else:
            metrics["trading"] = {"n_trades": 0}

        # === Distribution ===
        metrics["distribution"] = self._compute_distribution(returns)

        # === Summary ===
        metrics["summary"] = {
            "n_data_points": len(equity_curve),
            "n_trades": len(realized_trades),
            "period_start": equity_curve[0][0].isoformat() if equity_curve else None,
            "period_end": equity_curve[-1][0].isoformat() if equity_curve else None,
            "initial_equity": equities[0] if equities else 0,
            "final_equity": equities[-1] if equities else 0,
        }

        return metrics

    @staticmethod
    def _compute_returns(
        equities: list[float],
        returns: np.ndarray,
        equity_curve: list[tuple[datetime, float]],
    ) -> dict[str, float]:
        """Compute return metrics."""
        total_return = (equities[-1] / equities[0] - 1) if equities[0] > 0 else 0

        # Period in years
        if len(equity_curve) >= 2:
            period_sec = (equity_curve[-1][0] - equity_curve[0][0]).total_seconds()
            period_years = period_sec / (365.25 * 24 * 3600)
        else:
            period_years = 1

        annual_return = (1 + total_return) ** (1 / period_years) - 1 if period_years > 0 else 0

        # Daily returns
        daily_returns = returns if len(returns) > 0 else np.array([0])

        return {
            "total_return_pct": round(total_return * 100, 2),
            "annual_return_pct": round(annual_return * 100, 2),
            "avg_daily_return_bps": round(float(np.mean(daily_returns)) * 10000, 2),
            "best_day_bps": round(float(np.max(daily_returns)) * 10000, 2) if len(daily_returns) > 0 else 0,
            "worst_day_bps": round(float(np.min(daily_returns)) * 10000, 2) if len(daily_returns) > 0 else 0,
            "positive_days_pct": round(float(np.mean(daily_returns > 0)) * 100, 1) if len(daily_returns) > 0 else 0,
        }

    @staticmethod
    def _compute_risk_adjusted(returns: np.ndarray, equities: list[float]) -> dict[str, float]:
        """Compute risk-adjusted metrics."""
        if len(returns) < 2:
            return {"sharpe": 0, "sortino": 0, "calmar": 0}

        # Annualization factor (assume daily data → 252 trading days)
        ann_factor = math.sqrt(252)

        mean_return = float(np.mean(returns))
        std_return = float(np.std(returns, ddof=1))

        # Sharpe ratio (annualized, risk-free = 0)
        sharpe = (mean_return / std_return * ann_factor) if std_return > 0 else 0

        # Sortino ratio (only downside deviation)
        downside = returns[returns < 0]
        downside_std = float(np.std(downside, ddof=1)) if len(downside) > 1 else 0
        sortino = (mean_return / downside_std * ann_factor) if downside_std > 0 else 0

        # Max drawdown
        peak = equities[0]
        max_dd = 0
        for eq in equities:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd

        # Calmar ratio (annual return / max DD)
        total_return = (equities[-1] / equities[0] - 1) if equities[0] > 0 else 0
        calmar = (total_return / max_dd) if max_dd > 0 else 0

        # Omega ratio
        threshold = 0
        gains = returns[returns > threshold]
        losses = returns[returns < threshold]
        omega = (float(np.sum(gains)) / abs(float(np.sum(losses)))) if len(losses) > 0 and np.sum(losses) != 0 else float("inf")

        # VaR / CVaR (daily)
        var_95 = float(np.percentile(returns, 5))
        cvar_95 = float(np.mean(returns[returns <= var_95])) if len(returns[returns <= var_95]) > 0 else var_95

        return {
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "calmar": round(calmar, 3),
            "omega": round(omega, 3) if omega != float("inf") else 999,
            "var_95_daily_pct": round(var_95 * 100, 2),
            "cvar_95_daily_pct": round(cvar_95 * 100, 2),
            "volatility_annualized_pct": round(std_return * ann_factor * 100, 2),
        }

    @staticmethod
    def _compute_drawdown(
        equities: list[float],
        equity_curve: list[tuple[datetime, float]],
    ) -> dict[str, Any]:
        """Compute drawdown metrics."""
        peak = equities[0]
        max_dd_pct = 0
        max_dd_usd = 0
        current_dd_pct = 0
        dd_start: datetime | None = None
        max_dd_duration_sec = 0
        underwater_periods = 0

        for i, eq in enumerate(equities):
            if eq > peak:
                peak = eq
                if dd_start is not None:
                    duration = (equity_curve[i][0] - dd_start).total_seconds()
                    if duration > max_dd_duration_sec:
                        max_dd_duration_sec = duration
                    dd_start = None
            else:
                if dd_start is None:
                    dd_start = equity_curve[i][0]
                underwater_periods += 1

            dd = (peak - eq) / peak if peak > 0 else 0
            if dd > max_dd_pct:
                max_dd_pct = dd
                max_dd_usd = peak - eq

        current_dd = (peak - equities[-1]) / peak if peak > 0 else 0

        # Ulcer index: sqrt(mean(DD%^2))
        dd_series = []
        peak = equities[0]
        for eq in equities:
            if eq > peak:
                peak = eq
            dd_series.append(((peak - eq) / peak * 100) ** 2)
        ulcer = math.sqrt(np.mean(dd_series)) if dd_series else 0

        return {
            "max_dd_pct": round(max_dd_pct * 100, 2),
            "max_dd_usd": round(max_dd_usd, 2),
            "current_dd_pct": round(current_dd * 100, 2),
            "max_dd_duration_hours": round(max_dd_duration_sec / 3600, 1),
            "underwater_pct": round(underwater_periods / len(equities) * 100, 1) if equities else 0,
            "ulcer_index": round(ulcer, 2),
        }

    @staticmethod
    def _compute_trading_stats(trades: list[dict]) -> dict[str, Any]:
        """Compute trading statistics from realized PnL."""
        n_trades = len(trades)
        if n_trades == 0:
            return {"n_trades": 0}

        net_pnls = [t.get("net_pnl", 0) for t in trades]
        r_multiples = [t.get("r_multiple", 0) for t in trades]

        wins = [p for p in net_pnls if p > 0]
        losses = [p for p in net_pnls if p < 0]
        win_rate = len(wins) / n_trades if n_trades > 0 else 0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * abs(avg_loss))

        avg_r = sum(r_multiples) / n_trades if n_trades > 0 else 0
        hold_durations = [t.get("hold_duration_sec", 0) for t in trades]
        avg_hold_sec = sum(hold_durations) / n_trades if n_trades > 0 else 0

        # By regime
        by_regime: dict[str, list[float]] = defaultdict(list)
        for t in trades:
            regime = t.get("regime_at_close", "unknown")
            by_regime[regime].append(t.get("net_pnl", 0))

        regime_stats = {}
        for regime, pnls in by_regime.items():
            regime_wins = [p for p in pnls if p > 0]
            regime_stats[regime] = {
                "n_trades": len(pnls),
                "win_rate": round(len(regime_wins) / len(pnls) * 100, 1) if pnls else 0,
                "total_pnl": round(sum(pnls), 2),
                "avg_pnl": round(sum(pnls) / len(pnls), 2) if pnls else 0,
            }

        return {
            "n_trades": n_trades,
            "win_rate_pct": round(win_rate * 100, 1),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999,
            "avg_win_usd": round(avg_win, 2),
            "avg_loss_usd": round(avg_loss, 2),
            "expectancy_usd": round(expectancy, 2),
            "avg_r_multiple": round(avg_r, 4),
            "avg_hold_duration_min": round(avg_hold_sec / 60, 1),
            "total_net_pnl": round(sum(net_pnls), 2),
            "by_regime": regime_stats,
        }

    @staticmethod
    def _compute_distribution(returns: np.ndarray) -> dict[str, float]:
        """Compute return distribution metrics."""
        if len(returns) < 2:
            return {"skew": 0, "kurtosis": 0}

        mean = float(np.mean(returns))
        std = float(np.std(returns, ddof=1))

        # Skewness
        if std > 0:
            skew = float(np.mean(((returns - mean) / std) ** 3))
        else:
            skew = 0

        # Excess kurtosis
        if std > 0:
            kurtosis = float(np.mean(((returns - mean) / std) ** 4)) - 3
        else:
            kurtosis = 0

        return {
            "skewness": round(skew, 3),
            "kurtosis_excess": round(kurtosis, 3),
            "mean_daily_bps": round(mean * 10000, 2),
            "std_daily_bps": round(std * 10000, 2),
        }

    def _load_equity_curve(self) -> list[tuple[datetime, float]]:
        """Load equity curve from DuckDB account_snapshots."""
        import duckdb

        try:
            with duckdb.connect(str(self._pnl._db_path), read_only=True) as conn:
                result = conn.execute(
                    """
                    SELECT ts, equity_total FROM account_snapshots
                    ORDER BY ts ASC
                    """
                ).fetchall()
                return [(row[0], float(row[1])) for row in result]
        except Exception:
            return []

    def _load_realized_trades(self) -> list[dict]:
        """Load realized trades from DuckDB pnl_realized."""
        import duckdb

        try:
            with duckdb.connect(str(self._pnl._db_path), read_only=True) as conn:
                result = conn.execute(
                    """
                    SELECT trade_id, symbol, venue, regime_at_close,
                           net_pnl, r_multiple, hold_duration_sec,
                           gross_pnl, fees_total, funding_pnl, slippage_cost
                    FROM pnl_realized
                    ORDER BY ts ASC
                    """
                ).fetchdf()
                if result.empty:
                    return []
                return result.to_dict("records")
        except Exception:
            return []
