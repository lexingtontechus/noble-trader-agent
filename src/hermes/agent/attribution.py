"""
Performance Attribution — attributes PnL to specific decision tree branches.

Tracks which AgentAction each trade took at entry and exit, then analyzes:
- Win rate by decision branch
- Avg R-multiple by decision branch
- Expectancy by decision branch
- Decision quality by regime (branch × regime matrix)
- Feedback for threshold tuning

This closes the biggest gap: "No attribution of PnL to specific decision branches"

See user requirements: Performance Attribution analysis.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.agent.decision_tree import AgentAction
from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


class TradeDecisionRecord(BaseModel):
    """Records which decision branch a trade took at entry and exit."""

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    trade_id: str
    symbol: str
    venue: str
    ts_opened: datetime
    ts_closed: datetime | None = None

    # Entry decision
    entry_action: AgentAction
    entry_strategy: str = ""  # enter_now, wait_for_brick_close, etc.
    execution_method: str = ""
    meta_regime_at_entry: str = ""
    brick_pattern_at_entry: str = ""
    conviction_score: float = 0.0
    sizing_multiplier: float = 1.0

    # Exit decision
    exit_action: AgentAction | None = None
    exit_reason: str = ""
    meta_regime_at_exit: str = ""

    # Outcome
    net_pnl: float = 0.0
    r_multiple: float = 0.0
    hold_duration_sec: int = 0
    max_favorable_exc: float | None = None  # MFE
    max_adverse_exc: float | None = None    # MAE

    # Entry alpha (Hermes's value-add)
    entry_alpha_bps: float = 0.0

    # Hypothesis links
    hypothesis_ids: list[str] = Field(default_factory=list)


class BranchStats(BaseModel):
    """Statistics for a single decision branch."""

    branch: str  # AgentAction value
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    win_rate: float = 0.0
    avg_r_multiple: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    expectancy: float = 0.0  # (win_rate * avg_win) - (loss_rate * avg_loss)
    avg_win: float = 0.0
    avg_loss: float = 0.0
    profit_factor: float = 0.0
    avg_hold_duration_sec: int = 0
    avg_entry_alpha_bps: float = 0.0


class RegimeBranchMatrix(BaseModel):
    """Decision quality matrix: branch × regime."""

    matrix: dict[str, dict[str, BranchStats]] = Field(default_factory=dict)
    # matrix[branch][regime] = BranchStats


class ABTestResult(BaseModel):
    """Result of an A/B test between two configurations."""

    test_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    config_a_name: str
    config_b_name: str
    config_a_sharpe: float
    config_b_sharpe: float
    config_a_returns: list[float] = Field(default_factory=list)
    config_b_returns: list[float] = Field(default_factory=list)

    # Statistical tests
    diebold_mariano_stat: float | None = None
    diebold_mariano_pvalue: float | None = None
    paired_t_stat: float | None = None
    paired_t_pvalue: float | None = None

    # Verdict
    winner: str = ""  # config_a_name, config_b_name, or "inconclusive"
    confidence: float = 0.0  # 0-1
    significant: bool = False  # p < 0.05


class DecisionBranchTracker:
    """
    Tracks which decision branch each trade took and attributes PnL.

    Usage:
        tracker = DecisionBranchTracker(config)

        # On trade entry:
        tracker.record_entry(trade_id, symbol, venue, entry_action=AgentAction.ENTER_NEW, ...)

        # On trade exit:
        tracker.record_exit(trade_id, exit_action=AgentAction.CLOSE_EARLY_PROFIT, net_pnl=500, ...)

        # Analysis:
        stats = tracker.analyze_branch_performance()
        matrix = tracker.analyze_regime_branch_matrix()
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._records: dict[str, TradeDecisionRecord] = {}  # trade_id → record
        self._stats = {
            "entries_recorded": 0,
            "exits_recorded": 0,
            "analyses_run": 0,
        }

    def record_entry(
        self,
        trade_id: str,
        symbol: str,
        venue: str,
        entry_action: AgentAction,
        entry_strategy: str = "",
        execution_method: str = "",
        meta_regime: str = "",
        brick_pattern: str = "",
        conviction_score: float = 0.0,
        sizing_multiplier: float = 1.0,
        ts_opened: datetime | None = None,
        hypothesis_ids: list[str] | None = None,
    ) -> TradeDecisionRecord:
        """Record the entry decision for a trade."""
        record = TradeDecisionRecord(
            trade_id=trade_id,
            symbol=symbol,
            venue=venue,
            ts_opened=ts_opened or datetime.now(timezone.utc),
            entry_action=entry_action,
            entry_strategy=entry_strategy,
            execution_method=execution_method,
            meta_regime_at_entry=meta_regime,
            brick_pattern_at_entry=brick_pattern,
            conviction_score=conviction_score,
            sizing_multiplier=sizing_multiplier,
            hypothesis_ids=hypothesis_ids or [],
        )
        self._records[trade_id] = record
        self._stats["entries_recorded"] += 1
        return record

    def record_exit(
        self,
        trade_id: str,
        exit_action: AgentAction,
        exit_reason: str = "",
        net_pnl: float = 0.0,
        r_multiple: float = 0.0,
        hold_duration_sec: int = 0,
        meta_regime_at_exit: str = "",
        max_favorable_exc: float | None = None,
        max_adverse_exc: float | None = None,
        entry_alpha_bps: float = 0.0,
    ) -> TradeDecisionRecord | None:
        """Record the exit decision for a trade."""
        record = self._records.get(trade_id)
        if record is None:
            log.warning("exit_recorded_without_entry", trade_id=trade_id)
            return None

        record.exit_action = exit_action
        record.exit_reason = exit_reason
        record.net_pnl = net_pnl
        record.r_multiple = r_multiple
        record.hold_duration_sec = hold_duration_sec
        record.meta_regime_at_exit = meta_regime_at_exit
        record.max_favorable_exc = max_favorable_exc
        record.max_adverse_exc = max_adverse_exc
        record.entry_alpha_bps = entry_alpha_bps
        record.ts_closed = datetime.now(timezone.utc)

        self._stats["exits_recorded"] += 1

        # Write to DuckDB
        self._write_to_duckdb(record)

        return record

    def analyze_branch_performance(self) -> dict[str, BranchStats]:
        """
        Analyze PnL attribution by exit decision branch.

        Returns dict mapping exit_action → BranchStats.
        Example: {
            "close_stop_loss": BranchStats(n_trades=10, win_rate=0.0, avg_r=-1.0, ...),
            "close_early_profit": BranchStats(n_trades=5, win_rate=1.0, avg_r=0.8, ...),
            "close_take_profit": BranchStats(n_trades=8, win_rate=1.0, avg_r=0.5, ...),
            "trail_stop": BranchStats(n_trades=3, win_rate=0.67, avg_r=0.3, ...),
        }
        """
        closed = [r for r in self._records.values() if r.exit_action is not None]

        by_branch: dict[str, list[TradeDecisionRecord]] = defaultdict(list)
        for record in closed:
            by_branch[record.exit_action.value].append(record)

        result: dict[str, BranchStats] = {}
        for branch, records in by_branch.items():
            result[branch] = self._compute_stats(branch, records)

        self._stats["analyses_run"] += 1
        return result

    def analyze_entry_branch_performance(self) -> dict[str, BranchStats]:
        """
        Analyze PnL attribution by entry decision branch.

        Returns dict mapping entry_action → BranchStats.
        """
        closed = [r for r in self._records.values() if r.exit_action is not None]

        by_branch: dict[str, list[TradeDecisionRecord]] = defaultdict(list)
        for record in closed:
            by_branch[record.entry_action.value].append(record)

        result: dict[str, BranchStats] = {}
        for branch, records in by_branch.items():
            result[branch] = self._compute_stats(branch, records)

        return result

    def analyze_regime_branch_matrix(self) -> RegimeBranchMatrix:
        """
        Analyze decision quality by regime: branch × regime matrix.

        Returns RegimeBranchMatrix where matrix[exit_branch][regime] = BranchStats.

        This answers: "In calm_trend regime, how did CLOSE_EARLY_PROFIT perform?"
        """
        closed = [r for r in self._records.values() if r.exit_action is not None]

        matrix: dict[str, dict[str, list[TradeDecisionRecord]]] = defaultdict(lambda: defaultdict(list))
        for record in closed:
            branch = record.exit_action.value
            regime = record.meta_regime_at_exit or "unknown"
            matrix[branch][regime].append(record)

        result = RegimeBranchMatrix()
        for branch, regimes in matrix.items():
            result.matrix[branch] = {}
            for regime, records in regimes.items():
                result.matrix[branch][regime] = self._compute_stats(branch, records)

        return result

    def analyze_hypothesis_performance(self) -> dict[str, BranchStats]:
        """
        Attribute PnL to hypotheses that informed each trade.

        Returns dict mapping hypothesis_id → BranchStats.
        """
        closed = [r for r in self._records.values() if r.exit_action is not None and r.hypothesis_ids]

        by_hypothesis: dict[str, list[TradeDecisionRecord]] = defaultdict(list)
        for record in closed:
            for hyp_id in record.hypothesis_ids:
                by_hypothesis[hyp_id].append(record)

        result: dict[str, BranchStats] = {}
        for hyp_id, records in by_hypothesis.items():
            result[hyp_id] = self._compute_stats(f"hypothesis:{hyp_id[:8]}", records)

        return result

    def get_threshold_feedback(self) -> dict[str, Any]:
        """
        Generate feedback for decision tree threshold tuning.

        Analyzes which thresholds are working and which need adjustment.

        Returns dict with recommendations:
        - "stop_loss_pct": current threshold, suggested change, evidence
        - "take_profit_pct": ...
        - "early_profit_pct": ...
        - "fading_brick_count": ...
        - "strong_conviction_threshold": ...
        """
        branch_stats = self.analyze_branch_performance()
        feedback: dict[str, Any] = {}

        # Stop-loss analysis
        sl_stats = branch_stats.get("close_stop_loss")
        if sl_stats and sl_stats.n_trades >= 5:
            # If SL trades have avg R much worse than -1.0, SL is too loose
            # If SL trades have avg R much better than -1.0, SL is too tight
            avg_r = sl_stats.avg_r_multiple
            if avg_r < -1.2:
                feedback["stop_loss_pct"] = {
                    "current": -0.01,
                    "issue": "SL too loose — avg R = {:.2f} (worse than -1.0R)".format(avg_r),
                    "suggestion": "tighten SL from -1% to -0.8%",
                    "evidence": f"{sl_stats.n_trades} trades, avg R = {avg_r:.2f}",
                }
            elif avg_r > -0.8:
                feedback["stop_loss_pct"] = {
                    "current": -0.01,
                    "issue": "SL too tight — avg R = {:.2f} (better than -1.0R, cutting winners early)".format(avg_r),
                    "suggestion": "loosen SL from -1% to -1.2%",
                    "evidence": f"{sl_stats.n_trades} trades, avg R = {avg_r:.2f}",
                }

        # Take-profit analysis (native TP — only fires when no signal)
        tp_stats = branch_stats.get("close_take_profit")
        if tp_stats and tp_stats.n_trades >= 5:
            # If TP trades have low avg R, TP is too tight
            if tp_stats.avg_r_multiple < 0.3:
                feedback["take_profit_pct"] = {
                    "current": 0.025,
                    "issue": "Native TP too tight — avg R = {:.2f}".format(tp_stats.avg_r_multiple),
                    "suggestion": "raise TP from 2.5% to 3.0%",
                    "evidence": f"{tp_stats.n_trades} trades, avg R = {tp_stats.avg_r_multiple:.2f}",
                }

        # Early profit analysis
        ep_stats = branch_stats.get("close_early_profit")
        if ep_stats and ep_stats.n_trades >= 5:
            # If early profit trades have high avg R, threshold is good
            # If low avg R, we're exiting too early
            if ep_stats.avg_r_multiple < 0.5:
                feedback["early_profit_pct"] = {
                    "current": 0.045,
                    "issue": "Early profit threshold too low — exiting before full profit",
                    "suggestion": "raise from 4.5% to 5.5%",
                    "evidence": f"{ep_stats.n_trades} trades, avg R = {ep_stats.avg_r_multiple:.2f}",
                }

        # Trail stop analysis
        trail_stats = branch_stats.get("trail_stop")
        if trail_stats and trail_stats.n_trades >= 5:
            # If trail trades have negative avg R, trailing is too tight
            if trail_stats.avg_r_multiple < 0:
                feedback["fading_brick_count"] = {
                    "current": 2,
                    "issue": "Trail trigger too sensitive — trailing on noise",
                    "suggestion": "increase from 2 to 3 adverse bricks",
                    "evidence": f"{trail_stats.n_trades} trail trades, avg R = {trail_stats.avg_r_multiple:.2f}",
                }

        # Flip analysis
        flip_stats = branch_stats.get("close_flip")
        if flip_stats and flip_stats.n_trades >= 3:
            # If flip trades have negative avg R, flipping is not working
            if flip_stats.avg_r_multiple < 0:
                feedback["strong_conviction_threshold"] = {
                    "current": 0.7,
                    "issue": "Flip threshold too low — flipping on weak signals",
                    "suggestion": "raise from 0.7 to 0.8",
                    "evidence": f"{flip_stats.n_trades} flip trades, avg R = {flip_stats.avg_r_multiple:.2f}",
                }

        return feedback

    def get_decision_quality_report(self) -> dict[str, Any]:
        """
        Generate a comprehensive decision quality report.

        Includes:
        - Branch performance (exit actions)
        - Entry branch performance
        - Regime × branch matrix
        - Hypothesis performance
        - Threshold feedback
        - Best/worst performing branches
        """
        exit_stats = self.analyze_branch_performance()
        entry_stats = self.analyze_entry_branch_performance()
        regime_matrix = self.analyze_regime_branch_matrix()
        hypothesis_stats = self.analyze_hypothesis_performance()
        threshold_feedback = self.get_threshold_feedback()

        # Best and worst branches
        sorted_by_expectancy = sorted(
            exit_stats.items(),
            key=lambda x: x[1].expectancy,
            reverse=True,
        )

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "total_trades_analyzed": sum(s.n_trades for s in exit_stats.values()),
            "exit_branch_performance": {
                branch: stats.model_dump() for branch, stats in exit_stats.items()
            },
            "entry_branch_performance": {
                branch: stats.model_dump() for branch, stats in entry_stats.items()
            },
            "regime_branch_matrix": {
                branch: {
                    regime: stats.model_dump()
                    for regime, stats in regimes.items()
                }
                for branch, regimes in regime_matrix.matrix.items()
            },
            "hypothesis_performance": {
                hyp_id: stats.model_dump() for hyp_id, stats in hypothesis_stats.items()
            },
            "threshold_feedback": threshold_feedback,
            "best_branch": sorted_by_expectancy[0][0] if sorted_by_expectancy else None,
            "worst_branch": sorted_by_expectancy[-1][0] if sorted_by_expectancy else None,
        }

    @staticmethod
    def _compute_stats(branch: str, records: list[TradeDecisionRecord]) -> BranchStats:
        """Compute BranchStats from a list of TradeDecisionRecords."""
        n = len(records)
        if n == 0:
            return BranchStats(branch=branch)

        pnls = [r.net_pnl for r in records]
        rs = [r.r_multiple for r in records]
        alphas = [r.entry_alpha_bps for r in records]
        holds = [r.hold_duration_sec for r in records]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = n_wins / n if n > 0 else 0
        avg_win = sum(wins) / n_wins if wins else 0
        avg_loss = sum(losses) / n_losses if losses else 0
        loss_rate = 1 - win_rate
        expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))
        total_pnl = sum(pnls)
        avg_pnl = total_pnl / n if n > 0 else 0
        avg_r = sum(rs) / n if n > 0 else 0
        profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else 999.0
        avg_hold = sum(holds) / n if n > 0 else 0
        avg_alpha = sum(alphas) / n if n > 0 else 0

        return BranchStats(
            branch=branch,
            n_trades=n,
            n_wins=n_wins,
            n_losses=n_losses,
            win_rate=round(win_rate, 4),
            avg_r_multiple=round(avg_r, 4),
            total_pnl=round(total_pnl, 2),
            avg_pnl=round(avg_pnl, 2),
            expectancy=round(expectancy, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            profit_factor=round(profit_factor, 2),
            avg_hold_duration_sec=int(avg_hold),
            avg_entry_alpha_bps=round(avg_alpha, 2),
        )

    def _write_to_duckdb(self, record: TradeDecisionRecord) -> None:
        """Write decision record to DuckDB audit_log."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO audit_log (
                        audit_id, ts, actor, action, target, payload, result, error
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        str(uuid4()),
                        record.ts_closed or datetime.now(timezone.utc),
                        "decision_tree",
                        f"branch_attribution",
                        record.trade_id,
                        json.dumps({
                            "trade_id": record.trade_id,
                            "entry_action": record.entry_action.value,
                            "exit_action": record.exit_action.value if record.exit_action else None,
                            "entry_strategy": record.entry_strategy,
                            "exit_reason": record.exit_reason,
                            "meta_regime_at_entry": record.meta_regime_at_entry,
                            "meta_regime_at_exit": record.meta_regime_at_exit,
                            "net_pnl": record.net_pnl,
                            "r_multiple": record.r_multiple,
                            "hold_duration_sec": record.hold_duration_sec,
                            "entry_alpha_bps": record.entry_alpha_bps,
                            "hypothesis_ids": record.hypothesis_ids,
                        }, default=str),
                        "success",
                        None,
                    ],
                )
        except Exception as e:
            log.warning("branch_attribution_write_failed", error=str(e))

    def get_all_records(self) -> list[TradeDecisionRecord]:
        """Get all decision records."""
        return list(self._records.values())

    def get_stats(self) -> dict[str, Any]:
        return self._stats.copy()


class ABTestFramework:
    """
    A/B testing framework for parallel hypothesis testing.

    Runs two configs in parallel and compares performance using:
    - Diebold-Mariano test (for predictive accuracy)
    - Paired t-test (for return differences)
    - Sharpe ratio comparison

    Usage:
        framework = ABTestFramework()
        result = framework.compare(
            config_a_name="current",
            config_a_returns=[0.01, -0.005, 0.008, ...],
            config_b_name="hypothesis_1",
            config_b_returns=[0.012, -0.003, 0.010, ...],
        )
        if result.significant:
            print(f"Winner: {result.winner} (p={result.paired_t_pvalue:.4f})")
    """

    @staticmethod
    def compare(
        config_a_name: str,
        config_a_returns: list[float],
        config_b_name: str,
        config_b_returns: list[float],
        significance_level: float = 0.05,
    ) -> ABTestResult:
        """
        Compare two configurations using statistical tests.

        Args:
            config_a_name: Name of config A (incumbent)
            config_a_returns: Daily returns from config A
            config_b_name: Name of config B (challenger)
            config_b_returns: Daily returns from config B
            significance_level: p-value threshold (default 0.05)

        Returns:
            ABTestResult with statistical tests + winner
        """
        import numpy as np

        result = ABTestResult(
            config_a_name=config_a_name,
            config_b_name=config_b_name,
            config_a_returns=config_a_returns,
            config_b_returns=config_b_returns,
            config_a_sharpe=ABTestFramework._sharpe(config_a_returns),
            config_b_sharpe=ABTestFramework._sharpe(config_b_returns),
        )

        n = min(len(config_a_returns), len(config_b_returns))
        if n < 10:
            result.winner = "inconclusive"
            result.confidence = 0.0
            return result

        returns_a = np.array(config_a_returns[:n])
        returns_b = np.array(config_b_returns[:n])

        # Paired t-test (are the mean returns different?)
        diff = returns_b - returns_a
        mean_diff = float(np.mean(diff))
        std_diff = float(np.std(diff, ddof=1))

        if std_diff > 0:
            t_stat = mean_diff / (std_diff / np.sqrt(n))
        else:
            t_stat = 0.0

        # Two-tailed p-value from t-distribution
        try:
            from scipy import stats
            p_value = 2 * (1 - stats.t.cdf(abs(t_stat), df=n - 1))
        except ImportError:
            # Fallback: normal approximation
            from math import erf, sqrt
            p_value = 2 * (1 - 0.5 * (1 + erf(abs(t_stat) / sqrt(2))))

        result.paired_t_stat = round(t_stat, 4)
        result.paired_t_pvalue = round(p_value, 6)

        # Diebold-Mariano test (for forecast accuracy)
        # DM = mean(loss_diff) / sqrt(var(loss_diff) / n)
        # Loss = -return (we want to maximize returns)
        loss_a = -returns_a
        loss_b = -returns_b
        loss_diff = loss_b - loss_a
        mean_loss_diff = float(np.mean(loss_diff))
        var_loss_diff = float(np.var(loss_diff, ddof=1))

        if var_loss_diff > 0:
            dm_stat = mean_loss_diff / np.sqrt(var_loss_diff / n)
        else:
            dm_stat = 0.0

        try:
            from scipy import stats
            dm_pvalue = 2 * (1 - stats.norm.cdf(abs(dm_stat)))
        except ImportError:
            from math import erf, sqrt
            dm_pvalue = 2 * (1 - 0.5 * (1 + erf(abs(dm_stat) / sqrt(2))))

        result.diebold_mariano_stat = round(dm_stat, 4)
        result.diebold_mariano_pvalue = round(dm_pvalue, 6)

        # Determine winner
        result.significant = p_value < significance_level

        if result.significant:
            if result.config_b_sharpe > result.config_a_sharpe:
                result.winner = config_b_name
            else:
                result.winner = config_a_name
            result.confidence = 1.0 - p_value
        else:
            result.winner = "inconclusive"
            result.confidence = 0.0

        return result

    @staticmethod
    def _sharpe(returns: list[float]) -> float:
        """Compute annualized Sharpe ratio."""
        import numpy as np
        import math

        if len(returns) < 2:
            return 0.0

        arr = np.array(returns)
        mean = float(np.mean(arr))
        std = float(np.std(arr, ddof=1))

        if std < 1e-10:  # effectively zero (constant returns)
            return 0.0

        return round((mean / std) * math.sqrt(252), 3)


class SignalWindowOptimizer:
    """
    Optimizes signal_expiry_minutes — how long a signal stays valid.

    The signal window determines how long after a Noble Trader heartbeat
    Hermes will still act on the signal. Too short → miss opportunities.
    Too long → act on stale signals.

    Usage:
        optimizer = SignalWindowOptimizer()
        result = optimizer.optimize_window(
            signals=[...],
            price_data={symbol: [(ts, price), ...]},
            windows=[10, 15, 20, 30, 45, 60],
        )
    """

    @staticmethod
    def optimize_window(
        signals: list[dict],
        price_data: dict[str, list[tuple[datetime, float]]],
        windows: list[int] | None = None,
    ) -> dict[str, Any]:
        """
        Find the optimal signal expiry window.

        For each window length, simulates: "if we entered at the best price
        within N minutes of the signal, what would the PnL be?"

        Args:
            signals: List of signal dicts with 'ts', 'symbol', 'direction', 'entry_price'
            price_data: {symbol: [(ts, price), ...]} sorted by ts ascending
            windows: List of window lengths in minutes to test

        Returns:
            {window_minutes: {n_signals, n_filled, avg_entry_alpha_bps, total_pnl}, ...}
            + 'best_window' and 'rationale'
        """
        if windows is None:
            windows = [5, 10, 15, 20, 30, 45, 60, 90]

        results: dict[int, dict[str, Any]] = {}

        for window_min in windows:
            window_sec = window_min * 60
            n_filled = 0
            n_total = 0
            entry_alphas: list[float] = []
            pnls: list[float] = []

            for signal in signals:
                symbol = signal.get("symbol", "")
                direction = signal.get("direction", "buy")
                signal_ts = signal.get("ts")
                nt_entry = signal.get("entry_price", 0)

                if not signal_ts or symbol not in price_data:
                    continue

                n_total += 1

                # Find best price within window
                window_end = signal_ts + timedelta(seconds=window_sec)
                prices_in_window = [
                    (ts, price) for ts, price in price_data[symbol]
                    if signal_ts <= ts <= window_end
                ]

                if not prices_in_window:
                    continue

                # Best entry: for buy, lowest price; for sell, highest price
                if direction == "buy":
                    best_ts, best_price = min(prices_in_window, key=lambda x: x[1])
                else:
                    best_ts, best_price = max(prices_in_window, key=lambda x: x[1])

                n_filled += 1

                # Entry alpha: how much better than NT's suggested entry
                if nt_entry > 0:
                    if direction == "buy":
                        alpha = (nt_entry - best_price) / nt_entry * 10000
                    else:
                        alpha = (best_price - nt_entry) / nt_entry * 10000
                    entry_alphas.append(alpha)

                # Simplified PnL: use signal's stop/target
                stop = signal.get("stop_loss", 0)
                target = signal.get("take_profit", 0)

                if direction == "buy" and target > 0 and stop > 0:
                    # Did price hit target or stop first after entry?
                    post_entry_prices = [
                        (ts, price) for ts, price in price_data[symbol]
                        if ts > best_ts
                    ]
                    for ts, price in post_entry_prices:
                        if price >= target:
                            pnls.append((target - best_price) / best_price)
                            break
                        elif price <= stop:
                            pnls.append((stop - best_price) / best_price)
                            break
                elif direction == "sell" and target > 0 and stop > 0:
                    post_entry_prices = [
                        (ts, price) for ts, price in price_data[symbol]
                        if ts > best_ts
                    ]
                    for ts, price in post_entry_prices:
                        if price <= target:
                            pnls.append((best_price - target) / best_price)
                            break
                        elif price >= stop:
                            pnls.append((best_price - stop) / best_price)
                            break

            avg_alpha = sum(entry_alphas) / len(entry_alphas) if entry_alphas else 0
            total_pnl = sum(pnls) if pnls else 0
            fill_rate = n_filled / n_total if n_total > 0 else 0

            results[window_min] = {
                "n_signals": n_total,
                "n_filled": n_filled,
                "fill_rate": round(fill_rate, 4),
                "avg_entry_alpha_bps": round(avg_alpha, 2),
                "total_pnl_pct": round(total_pnl * 100, 2),
                "avg_pnl_pct": round(total_pnl / len(pnls) * 100, 2) if pnls else 0,
            }

        # Find best window (maximize total PnL with fill_rate > 50%)
        valid = {w: r for w, r in results.items() if r["fill_rate"] > 0.5}
        if valid:
            best_window = max(valid, key=lambda w: valid[w]["total_pnl_pct"])
        else:
            best_window = max(results, key=lambda w: results[w]["total_pnl_pct"]) if results else 30

        return {
            "by_window": results,
            "best_window": best_window,
            "rationale": f"Window {best_window}min maximizes total PnL ({results[best_window]['total_pnl_pct']}%) with {results[best_window]['fill_rate']:.0%} fill rate",
        }
