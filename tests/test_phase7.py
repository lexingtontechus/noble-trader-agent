"""
Phase 7 tests — backtest engine, walk-forward, Monte Carlo, Deflated Sharpe, rigor checks.

Run with:
    pytest tests/test_phase7.py -v
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest


# === Walk-Forward ===


def test_walk_forward_split_generates_folds():
    """walk_forward_split generates correct train/test splits."""
    from hermes.backtest.statistics import walk_forward_split

    splits = walk_forward_split(n_points=100, n_folds=5, train_ratio=0.7, gap=5)

    assert len(splits) == 5
    for train_idx, test_idx in splits:
        assert len(train_idx) > 0
        assert len(test_idx) > 0
        # Test indices should start after train + gap
        assert test_idx.start >= train_idx.stop + 5


def test_walk_forward_evaluate_passes_with_good_strategy():
    """walk_forward_evaluate passes for a consistently profitable strategy."""
    from hermes.backtest.statistics import walk_forward_evaluate

    # Generate 200 returns with consistent positive edge
    np.random.seed(42)
    returns = np.random.normal(0.001, 0.01, 200)  # positive mean

    result = walk_forward_evaluate(returns, n_folds=5, decay_threshold=0.5)

    assert result.n_folds == 5
    assert result.train_sharpe > 0
    assert result.test_sharpe > 0


def test_walk_forward_evaluate_fails_with_insufficient_data():
    """walk_forward_evaluate returns failure with insufficient data."""
    from hermes.backtest.statistics import walk_forward_evaluate

    result = walk_forward_evaluate(np.array([0.01, 0.02]), n_folds=5)

    assert result.n_folds == 0
    assert not result.passed


# === Monte Carlo ===


def test_monte_carlo_reshuffle_computes_percentiles():
    """monte_carlo_reshuffle computes percentile distribution."""
    from hermes.backtest.statistics import monte_carlo_reshuffle

    np.random.seed(42)
    # Mix of positive and negative returns so reshuffling produces variance
    returns = np.array([0.01, -0.005, 0.008, -0.003, 0.012, -0.007, 0.005, -0.002, 0.009, -0.004] * 20)

    result = monte_carlo_reshuffle(returns, n_iterations=500)

    assert result.n_iterations == 500
    # With mixed returns, reshuffled Sharpes should have variance
    assert result.percentile_5 < result.percentile_95
    assert 0 <= result.p_value <= 1


def test_monte_carlo_passes_with_positive_edge():
    """Monte Carlo 5th percentile > 0 for strong positive edge."""
    from hermes.backtest.statistics import monte_carlo_reshuffle

    np.random.seed(42)
    returns = np.random.normal(0.002, 0.005, 200)  # strong positive, low vol

    result = monte_carlo_reshuffle(returns, n_iterations=500)

    # With strong positive edge, even reshuffled should have positive Sharpe
    assert result.passed
    assert result.percentile_5 > 0


def test_monte_carlo_insufficient_data():
    """Monte Carlo returns failure with insufficient data."""
    from hermes.backtest.statistics import monte_carlo_reshuffle

    result = monte_carlo_reshuffle(np.array([0.01]), n_iterations=100)

    assert result.n_iterations == 0
    assert not result.passed


# === Deflated Sharpe ===


def test_deflated_sharpe_positive_for_good_strategy():
    """Deflated Sharpe is positive for a good strategy."""
    from hermes.backtest.statistics import deflated_sharpe_ratio

    dsr = deflated_sharpe_ratio(
        sharpe=2.0,
        n_returns=252,
        n_trials=1,
        skewness=0.0,
        kurtosis=0.0,
    )

    assert dsr > 0


def test_deflated_sharpe_decreases_with_more_trials():
    """Deflated Sharpe decreases with more trials (multiple testing penalty)."""
    from hermes.backtest.statistics import deflated_sharpe_ratio

    dsr_1 = deflated_sharpe_ratio(sharpe=2.0, n_returns=252, n_trials=1)
    dsr_100 = deflated_sharpe_ratio(sharpe=2.0, n_returns=252, n_trials=100)

    assert dsr_100 < dsr_1  # multiple testing penalty


def test_deflated_sharpe_zero_for_zero_sharpe():
    """Deflated Sharpe returns 0 for zero Sharpe."""
    from hermes.backtest.statistics import deflated_sharpe_ratio

    dsr = deflated_sharpe_ratio(sharpe=0.0, n_returns=252, n_trials=1)

    assert dsr == 0.0


# === Rigor Checks ===


def test_rigor_checks_pass_with_good_strategy():
    """run_rigor_checks passes for a strong, consistent strategy."""
    from hermes.backtest.statistics import run_rigor_checks

    np.random.seed(42)
    returns = np.random.normal(0.002, 0.005, 200)

    trades = [
        {"net_pnl": 500, "regime_at_close": "calm_trend"},
        {"net_pnl": 300, "regime_at_close": "calm_trend"},
        {"net_pnl": 200, "regime_at_close": "choppy_range"},
        {"net_pnl": 100, "regime_at_close": "high_vol_breakout"},
        {"net_pnl": 50, "regime_at_close": "regime_transition"},
    ]

    result = run_rigor_checks(returns, trades, n_trials=1)

    assert result.n_trades == 5
    assert result.checks_passed > 0
    # At least some checks should pass with a good strategy
    assert len(result.checks_failed) < 6


def test_rigor_checks_fail_with_no_data():
    """run_rigor_checks fails with no data."""
    from hermes.backtest.statistics import run_rigor_checks

    result = run_rigor_checks(np.array([]), [], n_trials=1)

    assert not result.passed
    assert "insufficient_data" in result.checks_failed


# === Backtest Engine ===


@pytest.mark.asyncio
async def test_backtest_engine_no_heartbeats(tmp_path):
    """BacktestEngine returns error when no heartbeats found."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.backtest.engine import BacktestEngine

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    import hermes.db.migrate as migrate_mod
    original = migrate_mod.get_duckdb_path
    migrate_mod.get_duckdb_path = lambda c: db_path

    engine = BacktestEngine(config)
    result = await engine.run_heartbeat_replay(
        symbols=["BTC-PERP"],
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        initial_equity=100000,
    )

    assert result.error == "no_heartbeats_found"
    assert result.n_heartbeats == 0

    migrate_mod.get_duckdb_path = original


# === CLI ===


def test_cli_backtest_help():
    """`platform backtest --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["backtest", "--help"])
    assert result.exit_code == 0
    assert "--symbols" in result.output
    assert "--days-back" in result.output
    assert "--equity" in result.output


def test_cli_rigor_help():
    """`platform rigor --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["rigor", "--help"])
    assert result.exit_code == 0
    assert "--symbols" in result.output


# === Dashboard ===


def test_dashboard_backtest_page():
    """GET /backtest returns 200."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/backtest")
    assert response.status_code == 200
    assert "Backtest" in response.text


def test_dashboard_api_backtest_runs():
    """GET /api/backtest/runs returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/backtest/runs")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "runs" in data
