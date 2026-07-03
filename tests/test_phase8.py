"""
Phase 8 tests — renko simulation engine, optimizer, shadow mode, counterfactual.

Run with:
    pytest tests/test_phase8.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# === Renko Simulation Engine ===


def test_simulation_run_model():
    """SimulationRun model creates correctly."""
    from hermes.backtest.optimizer import SimulationRun

    run = SimulationRun(
        mode="entry_timing_sweep",
        start_ts=datetime.now(timezone.utc),
        end_ts=datetime.now(timezone.utc) + timedelta(days=90),
        symbols=["BTC-PERP"],
        venues=["hyperliquid"],
    )
    assert run.mode == "entry_timing_sweep"
    assert run.accepted is False
    assert run.promotion_decision == "pending"


def test_simulation_trade_model():
    """SimulationTrade model creates correctly."""
    from hermes.backtest.optimizer import SimulationTrade

    trade = SimulationTrade(
        run_id="test-run-1",
        trade_num=1,
        ts_opened=datetime.now(timezone.utc),
        symbol="BTC",
        venue="hyperliquid",
        direction="long",
        size_usd=2000,
        entry_price=64000,
        stop_price=63000,
        target_price=66000,
    )
    assert trade.direction == "long"
    assert trade.entry_alpha_bps is None


def test_renko_simulation_engine_initializes():
    """RenkoSimulationEngine initializes without error."""
    from hermes.core.config import load_config
    from hermes.backtest.optimizer import RenkoSimulationEngine

    config = load_config()
    engine = RenkoSimulationEngine(config)

    assert engine is not None
    assert engine._stats["runs_started"] == 0


def test_optimizer_default_search_space():
    """Default search space has expected parameters."""
    from hermes.backtest.optimizer import RenkoSimulationEngine

    space = RenkoSimulationEngine.DEFAULT_SEARCH_SPACE
    assert "entry_strategy.calm_trend" in space
    assert "execution.default_method" in space
    assert "trailing.method" in space
    assert "exit.strategy" in space
    assert "sizing_multiplier.calm_trend" in space


def test_optimizer_sample_random_params():
    """_sample_random_params produces valid params."""
    from hermes.backtest.optimizer import RenkoSimulationEngine

    space = {
        "entry_strategy.calm_trend": ["enter_now", "wait_for_brick_close"],
        "brick_confirmation_count": (1, 5),
        "trailing.atr_mult": (1.0, 5.0),
    }

    params = RenkoSimulationEngine._sample_random_params(space, seed=42)
    assert params["entry_strategy.calm_trend"] in ["enter_now", "wait_for_brick_close"]
    assert 1 <= params["brick_confirmation_count"] <= 5
    assert 1.0 <= params["trailing.atr_mult"] <= 5.0


@pytest.mark.asyncio
async def test_shadow_mode_creates_run(tmp_path):
    """Shadow mode creates a SimulationRun."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.backtest.optimizer import RenkoSimulationEngine

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

    engine = RenkoSimulationEngine(config)
    engine._db_path = db_path  # override after init
    result = await engine.run_shadow_mode(
        config={"test": True},
        symbols=["BTC-PERP"],
        duration_days=3,
    )

    assert result.mode == "shadow"
    assert result.promoted_to_shadow is True
    assert result.accepted is True

    # Verify written to DuckDB
    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM simulation_runs").fetchone()[0]
        assert count == 1

    migrate_mod.get_duckdb_path = original


@pytest.mark.asyncio
async def test_counterfactual_with_no_trade(tmp_path):
    """Counterfactual returns empty list when trade not found."""
    from hermes.core.config import load_config
    from hermes.backtest.optimizer import RenkoSimulationEngine

    config = load_config()
    import hermes.db.migrate as migrate_mod
    db_path = tmp_path / "test.duckdb"

    import duckdb
    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    original = migrate_mod.get_duckdb_path
    migrate_mod.get_duckdb_path = lambda c: db_path

    engine = RenkoSimulationEngine(config)
    results = await engine.run_counterfactual(
        trade_id="nonexistent",
        alternative_configs=[{"entry_strategy": "wait_for_brick_close"}],
    )

    assert len(results) == 0

    migrate_mod.get_duckdb_path = original


def test_check_promotion_auto():
    """check_promotion returns 'auto' when shadow >= 80% of backtest."""
    from hermes.core.config import load_config
    from hermes.backtest.optimizer import RenkoSimulationEngine

    config = load_config()
    engine = RenkoSimulationEngine(config)

    # Shadow 1.6, backtest 2.0 → ratio = 0.8 → auto
    decision = engine.check_promotion("test", shadow_sharpe=1.6, backtest_sharpe=2.0)
    assert decision == "auto"


def test_check_promotion_rejected():
    """check_promotion returns 'rejected' when shadow < 50% of backtest."""
    from hermes.core.config import load_config
    from hermes.backtest.optimizer import RenkoSimulationEngine

    config = load_config()
    engine = RenkoSimulationEngine(config)

    decision = engine.check_promotion("test", shadow_sharpe=0.5, backtest_sharpe=2.0)
    assert decision == "rejected"


def test_check_promotion_pending():
    """check_promotion returns 'pending' when shadow between 50% and 80%."""
    from hermes.core.config import load_config
    from hermes.backtest.optimizer import RenkoSimulationEngine

    config = load_config()
    engine = RenkoSimulationEngine(config)

    decision = engine.check_promotion("test", shadow_sharpe=1.3, backtest_sharpe=2.0)
    assert decision == "pending"


# === CLI ===


def test_cli_optimize_help():
    """`platform optimize --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["optimize", "--help"])
    assert result.exit_code == 0
    assert "--symbols" in result.output
    assert "--n-trials" in result.output


def test_cli_shadow_help():
    """`platform shadow --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["shadow", "--help"])
    assert result.exit_code == 0
    assert "--duration-days" in result.output


def test_cli_counterfactual_help():
    """`platform counterfactual --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["counterfactual", "--help"])
    assert result.exit_code == 0
    assert "--trade-id" in result.output


# === Dashboard ===


def test_dashboard_optimize_page():
    """GET /optimize returns 200."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/optimize")
    assert response.status_code == 200
    assert "Simulation" in response.text


def test_dashboard_api_simulations():
    """GET /api/simulations returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/simulations")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "runs" in data
