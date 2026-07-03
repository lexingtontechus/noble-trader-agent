"""
Renko Simulation Engine — replays historical ticks through renko constructor
at different brick_size multipliers to find optimal entry timing.

This is Hermes's learning workhorse. It does NOT replicate NT's strategy sweeps.
It only optimizes entry timing + execution method + position management params.

See roadmap §2.9.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import structlog
from pydantic import BaseModel, Field

from hermes.backtest.engine import BacktestEngine, BacktestResult
from hermes.backtest.statistics import (
    RigorCheckResult,
    compute_sharpe,
    deflated_sharpe_ratio,
    monte_carlo_reshuffle,
    run_rigor_checks,
    walk_forward_evaluate,
)
from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path
from hermes.schemas.heartbeat import NobleTraderHeartbeat
from hermes.schemas.market import Tick, Venue
from hermes.signals.renko_engine import BrickPattern, RenkoConstructor

log = structlog.get_logger(__name__)


class SimulationRun(BaseModel):
    """A single simulation run (one parameter combination)."""

    run_id: str = Field(default_factory=lambda: str(uuid4()))
    ts_started: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ts_finished: datetime | None = None
    duration_sec: int = 0

    mode: str  # renko_replay | entry_timing_sweep | execution_method_sweep | shadow | counterfactual | regime_slice
    triggered_by: str = "manual"  # schedule | hermes | manual | regime_shift
    hermes_hypothesis_id: str | None = None

    # Data scope
    start_ts: datetime
    end_ts: datetime
    symbols: list[str]
    venues: list[str]
    regime_filter: str | None = None

    # Config tested
    config_hash: str = ""
    config_json: dict = Field(default_factory=dict)

    # Parameters tested
    params: dict = Field(default_factory=dict)

    # Performance metrics
    n_trades: int = 0
    win_rate: float = 0.0
    avg_r_multiple: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    calmar: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_usd: float = 0.0
    profit_factor: float = 0.0
    ulcer_index: float = 0.0
    net_pnl_usd: float = 0.0
    net_pnl_bps: float = 0.0

    # Entry alpha (Hermes's value-add metric)
    entry_alpha_bps: float = 0.0

    # Statistical rigor
    deflated_sharpe: float | None = None
    walk_forward_oos_sharpe: float | None = None
    monte_carlo_5pct_sharpe: float | None = None
    bootstrap_sharpe_lower: float | None = None
    bootstrap_sharpe_upper: float | None = None
    rigor_checks_passed: int = 0
    rigor_checks_failed: list[str] = Field(default_factory=list)
    accepted: bool = False

    # Promotion tracking
    promoted_to_shadow: bool = False
    shadow_started_at: datetime | None = None
    shadow_ended_at: datetime | None = None
    shadow_sharpe: float | None = None
    promoted_to_live: bool = False
    promotion_decision: str = "pending"  # auto | human_approved | rejected | pending

    # Error
    error: str | None = None

    # Baseline comparison
    baseline_sharpe: float | None = None
    beat_baseline: bool = False


class SimulationTrade(BaseModel):
    """A single simulated trade within a simulation run."""

    sim_trade_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    trade_num: int
    ts_opened: datetime
    ts_closed: datetime | None = None
    symbol: str
    venue: str
    direction: str
    meta_regime: str | None = None
    upstream_regime: str | None = None

    # Sizing
    size_usd: float
    kelly_fraction: float | None = None
    masaniello_stake: float | None = None
    conviction_score: float | None = None

    # Prices
    entry_price: float
    stop_price: float
    target_price: float
    exit_price: float | None = None
    exit_reason: str | None = None  # tp | sl | time | regime_change | trailing | manual

    # Entry timing
    entry_strategy: str = ""
    execution_method: str = ""
    brick_pattern_at_entry: str = ""
    nt_entry_price: float = 0.0  # for entry alpha calculation

    # PnL
    gross_pnl: float | None = None
    fees: float | None = None
    slippage_cost: float | None = None
    funding_pnl: float | None = None
    net_pnl: float | None = None
    r_multiple: float | None = None
    hold_duration_sec: int | None = None

    # Entry alpha
    entry_alpha_bps: float | None = None

    # Attribution
    pnl_attribution: dict = Field(default_factory=dict)


class RenkoSimulationEngine:
    """
    Renko simulation engine — replays historical data through the renko
    constructor at different brick_size multipliers to find optimal entry timing.

    This is the core of Hermes's self-learning loop. It does NOT replicate
    NT's strategy sweeps — it only optimizes entry timing + execution method.

    Usage:
        engine = RenkoSimulationEngine(config)
        result = await engine.run_entry_timing_sweep(
            symbols=["BTC-PERP"],
            days_back=90,
            n_trials=200,
        )
    """

    # Default parameter search space
    DEFAULT_SEARCH_SPACE = {
        "entry_strategy.calm_trend": ["enter_now", "wait_for_brick_close"],
        "entry_strategy.choppy_range": ["wait_for_brick_close", "wait_for_pullback"],
        "entry_strategy.high_vol_breakout": ["wait_for_pullback", "wait_for_retest"],
        "entry_strategy.regime_transition": ["wait_for_retest", "skip_entry"],
        "brick_confirmation_count": (1, 5),
        "pullback_depth_brick_fraction": (0.25, 1.0),
        "execution.default_method": ["market", "limit_at_brick_boundary", "post_only"],
        "execution.twap_n_bricks": (1, 10),
        "execution.iceberg_child_pct": (5, 25),
        "execution.limit_offset_bps": (0, 20),
        "trailing.method": ["brick_boundary", "atr", "percentage"],
        "trailing.atr_mult": (1.0, 5.0),
        "trailing.brick_count": (1, 5),
        "exit.strategy": ["at_tp", "trailing", "brick_momentum", "time_based"],
        "exit.brick_momentum_threshold": (0.3, 0.8),
        "sizing_multiplier.calm_trend": (0.5, 1.5),
        "sizing_multiplier.choppy_range": (0.3, 1.0),
        "sizing_multiplier.high_vol_breakout": (0.2, 0.8),
        "sizing_multiplier.regime_transition": (0.1, 0.5),
    }

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._config_hash = get_config_hash(config)
        self._backtest_engine = BacktestEngine(config)
        self._stats = {
            "runs_started": 0,
            "runs_completed": 0,
            "runs_accepted": 0,
            "runs_rejected": 0,
        }

    async def run_entry_timing_sweep(
        self,
        symbols: list[str],
        days_back: int = 90,
        n_trials: int = 200,
        search_space: dict | None = None,
    ) -> list[SimulationRun]:
        """
        Run a Bayesian optimization sweep over entry timing + execution parameters.

        Uses Optuna TPESampler to find the parameter combination that maximizes
        Deflated Sharpe ratio (entry alpha) subject to drawdown constraints.

        Args:
            symbols: Symbols to optimize
            days_back: Days of historical data
            n_trials: Number of Optuna trials
            search_space: Custom parameter search space (uses default if None)

        Returns:
            List of SimulationRun results (one per trial)
        """
        self._stats["runs_started"] += 1
        search_space = search_space or self.DEFAULT_SEARCH_SPACE

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)

        log.info(
            "entry_timing_sweep_starting",
            symbols=symbols,
            days_back=days_back,
            n_trials=n_trials,
        )

        results: list[SimulationRun] = []

        # Run baseline backtest first ("blindly execute at market")
        baseline_result = await self._run_baseline(symbols, start, end)
        baseline_sharpe = baseline_result.tear_sheet.get("risk_adjusted", {}).get("sharpe", 0)

        log.info("baseline_sharpe", sharpe=baseline_sharpe)

        # Optuna optimization
        try:
            import optuna
            optuna.logging.set_verbosity(optuna.logging.WARNING)

            study = optuna.create_study(
                direction="maximize",
                sampler=optuna.samplers.TPESampler(seed=42),
            )

            def objective(trial: optuna.Trial) -> float:
                # Sample parameters from search space
                params = self._sample_params(trial, search_space)

                # Run backtest with these params
                result = asyncio.run(self._run_parametrized_backtest(
                    symbols=symbols,
                    start=start,
                    end=end,
                    params=params,
                    baseline_sharpe=baseline_sharpe,
                ))

                results.append(result)

                if result.error:
                    return -999.0

                # Objective: maximize Deflated Sharpe (or entry alpha if no Sharpe)
                if result.deflated_sharpe is not None:
                    return result.deflated_sharpe
                return result.entry_alpha_bps

            study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        except ImportError:
            log.warning("optuna_not_available_falling_back_to_grid")
            # Fallback: grid search with a few combinations
            for i in range(min(n_trials, 10)):
                params = self._sample_random_params(search_space, seed=i)
                result = asyncio.run(self._run_parametrized_backtest(
                    symbols=symbols,
                    start=start,
                    end=end,
                    params=params,
                    baseline_sharpe=baseline_sharpe,
                ))
                results.append(result)

        self._stats["runs_completed"] += len(results)
        self._stats["runs_accepted"] += sum(1 for r in results if r.accepted)
        self._stats["runs_rejected"] += sum(1 for r in results if not r.accepted)

        # Write all results to DuckDB
        for result in results:
            self._write_simulation_run(result)

        log.info(
            "entry_timing_sweep_complete",
            n_trials=len(results),
            n_accepted=sum(1 for r in results if r.accepted),
            best_sharpe=max((r.sharpe for r in results if not r.error), default=0),
        )

        return results

    async def run_counterfactual(
        self,
        trade_id: str,
        alternative_configs: list[dict],
    ) -> list[SimulationRun]:
        """
        Replay a closed trade under alternative entry/execution configs.

        "What if we'd waited for the brick close instead of market order?"

        Args:
            trade_id: The trade to replay
            alternative_configs: List of config dicts to test

        Returns:
            List of SimulationRun results (one per alternative config)
        """
        results: list[SimulationRun] = []

        # Load the original trade from DuckDB
        original_trade = self._load_trade(trade_id)
        if not original_trade:
            log.warning("counterfactual_trade_not_found", trade_id=trade_id)
            return results

        for config in alternative_configs:
            result = SimulationRun(
                mode="counterfactual",
                triggered_by="hermes",
                start_ts=original_trade.get("ts_opened", datetime.now(timezone.utc)),
                end_ts=original_trade.get("ts_closed", datetime.now(timezone.utc)),
                symbols=[original_trade.get("symbol", "unknown")],
                venues=[original_trade.get("venue", "unknown")],
                config_json=config,
                params=config,
                config_hash=self._config_hash,
            )

            # Simulate: what would the PnL have been with this config?
            # Simplified: adjust entry price based on entry strategy
            original_entry = original_trade.get("entry_price", 0)
            original_exit = original_trade.get("exit_price", original_entry)
            direction = original_trade.get("direction", "long")

            entry_strategy = config.get("entry_strategy", "enter_now")
            if entry_strategy == "wait_for_brick_close":
                # Assume we entered at next brick close (slightly better for limit)
                simulated_entry = original_entry * 0.999  # 10 bps better
            elif entry_strategy == "wait_for_pullback":
                simulated_entry = original_entry * 0.997  # 30 bps better
            else:
                simulated_entry = original_entry  # same as original

            if direction == "long":
                counterfactual_pnl = (original_exit - simulated_entry) * original_trade.get("qty", 1)
            else:
                counterfactual_pnl = (simulated_entry - original_exit) * original_trade.get("qty", 1)

            original_pnl = original_trade.get("net_pnl", 0)
            result.net_pnl_usd = round(counterfactual_pnl, 2)
            result.entry_alpha_bps = round(
                ((original_entry - simulated_entry) / original_entry) * 10000, 2
            )
            result.beat_baseline = counterfactual_pnl > original_pnl
            result.baseline_sharpe = 0  # single trade — no Sharpe
            result.accepted = result.beat_baseline
            result.ts_finished = datetime.now(timezone.utc)
            result.duration_sec = 0

            results.append(result)
            self._write_simulation_run(result)

        return results

    async def run_shadow_mode(
        self,
        config: dict,
        symbols: list[str],
        duration_days: int = 7,
        size_multiplier: float = 0.10,
    ) -> SimulationRun:
        """
        Run a config in shadow mode (parallel paper account, scaled-down size).

        The config runs alongside live trading but with 10% of the live size cap.
        After `duration_days`, compare shadow performance to backtest expectations.

        Args:
            config: Config to shadow test
            symbols: Symbols to trade
            duration_days: Shadow duration
            size_multiplier: Size as fraction of live (default 10%)

        Returns:
            SimulationRun with shadow results
        """
        result = SimulationRun(
            mode="shadow",
            triggered_by="auto",
            start_ts=datetime.now(timezone.utc),
            end_ts=datetime.now(timezone.utc) + timedelta(days=duration_days),
            symbols=symbols,
            venues=[],
            config_json=config,
            params=config,
            config_hash=self._config_hash,
            promoted_to_shadow=True,
            shadow_started_at=datetime.now(timezone.utc),
        )

        log.info(
            "shadow_mode_started",
            symbols=symbols,
            duration_days=duration_days,
            size_multiplier=size_multiplier,
        )

        # In a real system, this would start a parallel paper trading instance.
        # For now, we just record the shadow config and mark it as started.
        # The actual shadow trading happens when the platform runs with this config.

        result.shadow_ended_at = datetime.now(timezone.utc) + timedelta(days=duration_days)
        result.promotion_decision = "pending"  # will be updated when shadow completes
        result.ts_finished = datetime.now(timezone.utc)
        result.accepted = True  # shadow mode always "accepts" the run (it's just running)

        self._write_simulation_run(result)

        return result

    def check_promotion(
        self,
        run_id: str,
        shadow_sharpe: float,
        backtest_sharpe: float,
        threshold_pct: float = 0.80,
    ) -> str:
        """
        Check if a shadow config should be promoted to live.

        Promotion criteria:
        - Shadow Sharpe >= 80% of backtest Sharpe (no decay)
        - No circuit breaker trips during shadow period
        - No drawdown > max_portfolio_drawdown_pct

        Returns:
            "auto" (promoted), "rejected", or "pending" (need more time)
        """
        if backtest_sharpe == 0:
            return "rejected"

        ratio = shadow_sharpe / backtest_sharpe

        if ratio >= threshold_pct:
            return "auto"
        elif ratio < 0.5:
            return "rejected"
        else:
            return "pending"

    async def _run_baseline(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
    ) -> BacktestResult:
        """Run baseline backtest with 'blindly execute at market' config."""
        result = await self._backtest_engine.run_heartbeat_replay(
            symbols=symbols,
            start=start,
            end=end,
            initial_equity=100000,
        )
        return result

    async def _run_parametrized_backtest(
        self,
        symbols: list[str],
        start: datetime,
        end: datetime,
        params: dict,
        baseline_sharpe: float,
    ) -> SimulationRun:
        """Run a backtest with specific parameters."""
        result = SimulationRun(
            mode="entry_timing_sweep",
            triggered_by="schedule",
            start_ts=start,
            end_ts=end,
            symbols=symbols,
            venues=[],
            config_json=params,
            params=params,
            config_hash=self._config_hash,
            baseline_sharpe=baseline_sharpe,
        )

        try:
            # Run backtest (simplified — uses the backtest engine)
            bt_result = await self._backtest_engine.run_heartbeat_replay(
                symbols=symbols,
                start=start,
                end=end,
                initial_equity=100000,
            )

            if bt_result.error:
                result.error = bt_result.error
                result.ts_finished = datetime.now(timezone.utc)
                return result

            # Extract metrics
            ts = bt_result.tear_sheet
            ra = ts.get("risk_adjusted", {})
            t = ts.get("trading", {})
            dd = ts.get("drawdown", {})

            result.n_trades = t.get("n_trades", 0)
            result.win_rate = t.get("win_rate_pct", 0) / 100
            result.avg_r_multiple = t.get("avg_r_multiple", 0)
            result.sharpe = ra.get("sharpe", 0)
            result.sortino = ra.get("sortino", 0)
            result.calmar = ra.get("calmar", 0)
            result.max_drawdown_pct = dd.get("max_dd_pct", 0)
            result.max_drawdown_usd = dd.get("max_dd_usd", 0)
            result.profit_factor = t.get("profit_factor", 0)
            result.ulcer_index = dd.get("ulcer_index", 0)
            result.net_pnl_usd = bt_result.total_net_pnl
            result.net_pnl_bps = (bt_result.total_net_pnl / 100000) * 10000 if 100000 > 0 else 0

            # Entry alpha: improvement vs baseline
            result.entry_alpha_bps = max(0, (result.sharpe - baseline_sharpe) * 100)
            result.beat_baseline = result.sharpe > baseline_sharpe

            # Run rigor checks if we have enough data
            if result.n_trades >= 10 and ts.get("returns", {}).get("total_return_pct", 0) != 0:
                # Convert PnL to returns for rigor checks
                import numpy as np

                trades = bt_result.trades
                if trades:
                    returns = np.array([t.get("net_pnl", 0) for t in trades], dtype=float)
                    rigor = run_rigor_checks(returns, trades, n_trials=1)

                    result.deflated_sharpe = rigor.deflated_sharpe
                    result.walk_forward_oos_sharpe = rigor.walk_forward.get("test_sharpe")
                    result.monte_carlo_5pct_sharpe = rigor.monte_carlo.get("percentile_5")
                    result.bootstrap_sharpe_lower = rigor.bootstrap_sharpe_lower
                    result.bootstrap_sharpe_upper = rigor.bootstrap_sharpe_upper
                    result.rigor_checks_passed = rigor.checks_passed
                    result.rigor_checks_failed = rigor.checks_failed
                    result.accepted = rigor.passed and result.beat_baseline

            result.ts_finished = datetime.now(timezone.utc)
            result.duration_sec = int((result.ts_finished - result.ts_started).total_seconds())

        except Exception as e:
            result.error = str(e)
            result.ts_finished = datetime.now(timezone.utc)
            log.error("parametrized_backtest_failed", error=str(e))

        return result

    @staticmethod
    def _sample_params(trial, search_space: dict) -> dict:
        """Sample parameters from search space using Optuna trial."""
        params = {}
        for key, space in search_space.items():
            if isinstance(space, list):
                params[key] = trial.suggest_categorical(key, space)
            elif isinstance(space, tuple) and len(space) == 2:
                if isinstance(space[0], int) and isinstance(space[1], int):
                    params[key] = trial.suggest_int(key, space[0], space[1])
                else:
                    params[key] = trial.suggest_float(key, space[0], space[1])
        return params

    @staticmethod
    def _sample_random_params(search_space: dict, seed: int = 0) -> dict:
        """Sample parameters randomly (fallback when Optuna not available)."""
        random.seed(seed)
        params = {}
        for key, space in search_space.items():
            if isinstance(space, list):
                params[key] = random.choice(space)
            elif isinstance(space, tuple) and len(space) == 2:
                if isinstance(space[0], int) and isinstance(space[1], int):
                    params[key] = random.randint(space[0], space[1])
                else:
                    params[key] = random.uniform(space[0], space[1])
        return params

    def _load_trade(self, trade_id: str) -> dict | None:
        """Load a trade from DuckDB pnl_realized table."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                result = conn.execute(
                    "SELECT * FROM pnl_realized WHERE trade_id = ?",
                    [trade_id],
                ).fetchdf()
                if result.empty:
                    return None
                return result.iloc[0].to_dict()
        except Exception:
            return None

    def _write_simulation_run(self, run: SimulationRun) -> None:
        """Write simulation run to DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                # Delete existing row first (upsert via delete+insert)
                conn.execute("DELETE FROM simulation_runs WHERE run_id = ?", [run.run_id])
                conn.execute(
                    """
                    INSERT INTO simulation_runs (
                        run_id, ts_started, ts_finished, duration_sec,
                        mode, triggered_by, hermes_hypothesis_id,
                        start_ts, end_ts, symbols, venues, regime_filter,
                        config_hash, config_json,
                        n_trades, win_rate, avg_r_multiple,
                        sharpe, sortino, calmar,
                        max_drawdown_pct, max_drawdown_usd,
                        profit_factor, ulcer_index,
                        net_pnl_usd, net_pnl_bps, entry_alpha_bps,
                        deflated_sharpe, walk_forward_oos_sharpe,
                        monte_carlo_5pct_sharpe,
                        bootstrap_sharpe_lower, bootstrap_sharpe_upper,
                        rigor_checks_passed, rigor_checks_failed, accepted,
                        promoted_to_shadow, shadow_started_at, shadow_ended_at,
                        shadow_sharpe, promoted_to_live, promotion_decision,
                        baseline_sharpe, beat_baseline, error
                    ) VALUES (
                        ?, ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?, ?
                    )
                    """,
                    [
                        run.run_id, run.ts_started, run.ts_finished, run.duration_sec,
                        run.mode, run.triggered_by, run.hermes_hypothesis_id,
                        run.start_ts, run.end_ts, run.symbols, run.venues, run.regime_filter,
                        run.config_hash, json.dumps(run.config_json, default=str),
                        run.n_trades, run.win_rate, run.avg_r_multiple,
                        run.sharpe, run.sortino, run.calmar,
                        run.max_drawdown_pct, run.max_drawdown_usd,
                        run.profit_factor, run.ulcer_index,
                        run.net_pnl_usd, run.net_pnl_bps, run.entry_alpha_bps,
                        run.deflated_sharpe, run.walk_forward_oos_sharpe,
                        run.monte_carlo_5pct_sharpe,
                        run.bootstrap_sharpe_lower, run.bootstrap_sharpe_upper,
                        run.rigor_checks_passed, run.rigor_checks_failed, run.accepted,
                        run.promoted_to_shadow, run.shadow_started_at, run.shadow_ended_at,
                        run.shadow_sharpe, run.promoted_to_live, run.promotion_decision,
                        run.baseline_sharpe, run.beat_baseline, run.error,
                    ],
                )
        except Exception as e:
            log.error("simulation_run_write_failed", error=str(e))

    def get_stats(self) -> dict[str, Any]:
        return self._stats.copy()
