"""
Phase 4 tests — portfolio state, VaR, circuit breakers, risk gate,
autonomy gate, snapshot writer.

Run with:
    pytest tests/test_phase4.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest


# === Portfolio State Service ===


def test_portfolio_state_initial_values():
    """PortfolioStateService initializes with correct values."""
    from hermes.portfolio.state import PortfolioStateService

    state = PortfolioStateService(initial_equity=100000, initial_cash_usd=50000, initial_cash_usdc=50000)
    metrics = state.get_metrics()

    assert metrics.equity_total == 100000
    assert metrics.cash_usd == 50000
    assert metrics.cash_usdc == 50000
    assert metrics.n_open_positions == 0
    assert metrics.gross_exposure_usd == 0
    assert metrics.drawdown_pct == 0


def test_portfolio_state_add_position():
    """Adding a position updates exposure and cash."""
    from hermes.portfolio.state import PortfolioStateService
    from hermes.schemas.market import Position, Venue

    state = PortfolioStateService(initial_equity=100000, initial_cash_usd=100000, initial_cash_usdc=0)
    pos = Position(
        position_id="test-1",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=60000,
        stop_price=59000,
        target_price=62000,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000,
    )
    state.add_position(pos)

    metrics = state.get_metrics()
    assert metrics.n_open_positions == 1
    # Cash reduced by notional (60000 * 1.0)
    assert metrics.cash_usdc == -60000  # we started with 0, now -60000


def test_portfolio_state_close_position():
    """Closing a position realizes PnL."""
    from hermes.portfolio.state import PortfolioStateService
    from hermes.schemas.market import Position, Venue

    state = PortfolioStateService(initial_equity=100000, initial_cash_usd=0, initial_cash_usdc=100000)
    pos = Position(
        position_id="test-2",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=60000,
        stop_price=59000,
        target_price=62000,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000,
    )
    state.add_position(pos)

    # Close at 61000 → +1000 profit
    result = state.remove_position("test-2", exit_price=61000, exit_reason="target")
    assert result["realized_pnl"] == 1000
    assert result["r_multiple"] == 1.0

    metrics = state.get_metrics()
    assert metrics.realized_pnl == 1000
    assert metrics.n_open_positions == 0


def test_portfolio_state_update_price():
    """Updating price updates unrealized PnL."""
    from hermes.portfolio.state import PortfolioStateService
    from hermes.schemas.market import Position, Venue

    state = PortfolioStateService(initial_equity=100000, initial_cash_usdc=100000)
    pos = Position(
        position_id="test-3",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=60000,
        stop_price=59000,
        target_price=62000,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000,
    )
    state.add_position(pos)

    state.update_price("BTC", 61500)
    metrics = state.get_metrics()
    assert metrics.unrealized_pnl == 1500


def test_portfolio_state_short_position():
    """Short positions compute PnL correctly."""
    from hermes.portfolio.state import PortfolioStateService
    from hermes.schemas.market import Position, Venue

    state = PortfolioStateService(initial_equity=100000, initial_cash_usdc=100000)
    pos = Position(
        position_id="test-short",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="short",
        qty=1.0,
        entry_price=60000,
        stop_price=61000,
        target_price=58000,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000,
    )
    state.add_position(pos)

    # Price drops to 59000 → +1000 profit for short
    state.update_price("BTC", 59000)
    metrics = state.get_metrics()
    assert metrics.unrealized_pnl == 1000


def test_portfolio_state_drawdown_tracking():
    """PortfolioStateService tracks drawdown from peak."""
    from hermes.portfolio.state import PortfolioStateService
    from hermes.schemas.market import Position, Venue

    state = PortfolioStateService(initial_equity=100000, initial_cash_usdc=100000)
    pos = Position(
        position_id="test-dd",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=60000,
        stop_price=59000,
        target_price=62000,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000,
    )
    state.add_position(pos)

    # Price goes up → new peak
    state.update_price("BTC", 65000)
    metrics = state.get_metrics()
    assert metrics.peak_equity >= 100000
    assert metrics.drawdown_pct == 0

    # Price drops → drawdown
    state.update_price("BTC", 58000)
    metrics = state.get_metrics()
    assert metrics.drawdown_pct > 0
    assert metrics.drawdown_usd > 0


# === VaR Calculator ===


def test_var_calculator_historical():
    """VaRCalculator computes historical VaR."""
    from hermes.portfolio.var_calculator import VaRCalculator
    import numpy as np

    calc = VaRCalculator()
    # Generate 100 normal returns with mean 0, std 0.01
    np.random.seed(42)
    returns = np.random.normal(0, 0.01, 100).tolist()
    calc.add_returns(returns)

    var, cvar = calc.compute_historical(confidence=0.99)
    # VaR should be negative (loss)
    assert var < 0
    # CVaR should be worse (more negative) than VaR
    assert cvar <= var


def test_var_calculator_position_value():
    """VaR calculator returns USD amounts when position_value provided."""
    from hermes.portfolio.var_calculator import VaRCalculator
    import numpy as np

    calc = VaRCalculator()
    np.random.seed(42)
    returns = np.random.normal(0, 0.01, 100).tolist()
    calc.add_returns(returns)

    var, cvar = calc.compute_historical(confidence=0.99, position_value=100000)
    assert var < 0
    assert abs(var) < 100000  # shouldn't exceed position value


def test_var_calculator_insufficient_data():
    """VaR returns 0 with insufficient data."""
    from hermes.portfolio.var_calculator import VaRCalculator

    calc = VaRCalculator()
    calc.add_return(0.01)
    var, cvar = calc.compute_historical(confidence=0.99)
    assert var == 0
    assert cvar == 0


# === Circuit Breakers ===


def test_volatility_cb_no_trip_on_normal_vol():
    """Volatility CB doesn't trip when ATR ratio is normal."""
    from hermes.portfolio.circuit_breakers import BreakerLevel, VolatilityCircuitBreaker

    cb = VolatilityCircuitBreaker(vol_mult_threshold=2.5)
    level, event = cb.check(
        symbol="BTC",
        atr_baseline=100,
        atr_current=120,  # 1.2x — normal
        expected_edge_bps=10,
    )
    assert level == BreakerLevel.NONE
    assert event is None


def test_volatility_cb_trips_on_high_vol():
    """Volatility CB trips when ATR ratio exceeds threshold."""
    from hermes.portfolio.circuit_breakers import BreakerLevel, VolatilityCircuitBreaker

    cb = VolatilityCircuitBreaker(vol_mult_threshold=2.5)
    level, event = cb.check(
        symbol="BTC",
        atr_baseline=100,
        atr_current=300,  # 3.0x — exceeds 2.5 threshold
        expected_edge_bps=10,
    )
    assert level >= BreakerLevel.REDUCE_50
    assert event is not None


def test_volatility_cb_liquidates_on_risk_off():
    """Volatility CB liquidates on risk_off regime."""
    from hermes.portfolio.circuit_breakers import BreakerLevel, VolatilityCircuitBreaker

    cb = VolatilityCircuitBreaker()
    level, event = cb.check(
        symbol="BTC",
        atr_baseline=100,
        atr_current=120,
        meta_regime="risk_off",
    )
    assert level == BreakerLevel.LIQUIDATE


def test_risk_cb_trips_on_portfolio_dd():
    """Risk CB trips when portfolio DD exceeds limit."""
    from hermes.portfolio.circuit_breakers import RiskCircuitBreaker

    cb = RiskCircuitBreaker(max_portfolio_drawdown_pct=0.15)
    events = cb.check_portfolio(
        drawdown_pct=0.20,  # exceeds 15%
        daily_pnl_pct=0.0,
        var_1d_99=None,
        equity=100000,
        margin_used_pct=0.3,
    )
    assert len(events) >= 1
    assert cb.is_tripped()


def test_risk_cb_trips_on_daily_loss():
    """Risk CB trips on daily loss limit."""
    from hermes.portfolio.circuit_breakers import RiskCircuitBreaker

    cb = RiskCircuitBreaker(daily_loss_limit_pct=0.03)
    events = cb.check_portfolio(
        drawdown_pct=0.0,
        daily_pnl_pct=-0.05,  # 5% loss > 3% limit
        var_1d_99=None,
        equity=100000,
        margin_used_pct=0.3,
    )
    assert len(events) >= 1
    assert any(e.payload.get("check") == "daily_loss" for e in events)


def test_risk_cb_no_trip_on_normal_conditions():
    """Risk CB doesn't trip on normal conditions."""
    from hermes.portfolio.circuit_breakers import RiskCircuitBreaker

    cb = RiskCircuitBreaker()
    events = cb.check_portfolio(
        drawdown_pct=0.05,
        daily_pnl_pct=0.01,
        var_1d_99=-500,
        equity=100000,
        margin_used_pct=0.3,
    )
    assert len(events) == 0
    assert not cb.is_tripped()


def test_kill_switch_activate_deactivate():
    """KillSwitch activates and deactivates."""
    from hermes.portfolio.circuit_breakers import KillSwitch

    ks = KillSwitch()
    assert not ks.is_active

    ks.activate("manual_test", flatten=True)
    assert ks.is_active
    assert ks.reason == "manual_test"
    assert ks.flatten_requested

    ks.deactivate()
    assert not ks.is_active


# === Autonomy Gate ===


def test_autonomy_gate_tier0_read_only():
    """AutonomyGate classifies read-only actions as tier 0."""
    from hermes.portfolio.autonomy_gate import AutonomyGate

    gate = AutonomyGate()
    decision = gate.classify(
        action_type="query",
        notional_usd=0,
        equity=100000,
    )
    assert decision.tier == 0
    assert decision.approved
    assert not decision.requires_human_approval


def test_autonomy_gate_tier1_small_trade():
    """AutonomyGate approves small trades within tier 1 cap."""
    from hermes.portfolio.autonomy_gate import AutonomyGate

    gate = AutonomyGate(tier1_max_notional=5000, tier1_max_position_pct=0.05)
    decision = gate.classify(
        action_type="enter_trade",
        notional_usd=3000,
        equity=100000,
        is_crypto=True,
    )
    assert decision.tier == 1
    assert decision.approved
    assert not decision.requires_human_approval


def test_autonomy_gate_tier3_large_trade():
    """AutonomyGate requires human approval for large trades."""
    from hermes.portfolio.autonomy_gate import AutonomyGate

    gate = AutonomyGate(tier3_max_notional=25000)
    decision = gate.classify(
        action_type="enter_trade",
        notional_usd=30000,  # > 25000
        equity=100000,
        is_crypto=True,
    )
    assert decision.tier == 3
    assert not decision.approved
    assert decision.requires_human_approval


def test_autonomy_gate_tier4_structural_change():
    """AutonomyGate blocks structural changes."""
    from hermes.portfolio.autonomy_gate import AutonomyGate

    gate = AutonomyGate()
    decision = gate.classify(
        action_type="structural_change",
        notional_usd=0,
        equity=100000,
    )
    assert decision.tier == 4
    assert not decision.approved
    assert decision.requires_human_approval


# === Risk Gate ===


def test_risk_gate_approves_normal_signal():
    """RiskGate approves a normal signal."""
    from hermes.portfolio.circuit_breakers import KillSwitch, RiskCircuitBreaker, VolatilityCircuitBreaker
    from hermes.portfolio.risk_gate import RiskGate
    from hermes.portfolio.state import PortfolioStateService
    from hermes.portfolio.var_calculator import VaRCalculator
    from hermes.signals.synthesizer import BlendedSignal

    state = PortfolioStateService(initial_equity=100000, initial_cash_usd=100000)
    vol_cb = VolatilityCircuitBreaker()
    risk_cb = RiskCircuitBreaker()
    ks = KillSwitch()
    var_calc = VaRCalculator()

    gate = RiskGate(
        portfolio_state=state,
        vol_breaker=vol_cb,
        risk_breaker=risk_cb,
        kill_switch=ks,
        var_calculator=var_calc,
        max_gross_exposure_pct=1.5,
        risk_amount_cap=5000,
        reward_risk_min=1.0,
    )

    signal = BlendedSignal(
        signal_id="test-sig-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=60000,
        nt_stop_price=59000,
        nt_target_price=62000,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="calm_trend",
        meta_regime_confidence=0.7,
        sizing_multiplier=1.0,
        entry_strategy="enter_now",
        execution_method="market",
        final_size_usd=2000,
        final_size_pct=0.02,
        risk_amount_usd=200,
        brick_pattern="trend_up",
        expected_entry_alpha_bps=-2.0,
        config_hash="test",
    )

    decision = gate.evaluate(signal)
    assert decision.approved
    assert decision.approved_size_usd > 0


def test_risk_gate_blocks_on_kill_switch():
    """RiskGate blocks when kill switch is active."""
    from hermes.portfolio.circuit_breakers import KillSwitch, RiskCircuitBreaker, VolatilityCircuitBreaker
    from hermes.portfolio.risk_gate import RiskGate
    from hermes.portfolio.state import PortfolioStateService
    from hermes.portfolio.var_calculator import VaRCalculator
    from hermes.signals.synthesizer import BlendedSignal

    state = PortfolioStateService(initial_equity=100000)
    vol_cb = VolatilityCircuitBreaker()
    risk_cb = RiskCircuitBreaker()
    ks = KillSwitch()
    ks.activate("test")
    var_calc = VaRCalculator()

    gate = RiskGate(
        portfolio_state=state,
        vol_breaker=vol_cb,
        risk_breaker=risk_cb,
        kill_switch=ks,
        var_calculator=var_calc,
    )

    signal = BlendedSignal(
        signal_id="test-sig-2",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=60000,
        nt_stop_price=59000,
        nt_target_price=62000,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="calm_trend",
        meta_regime_confidence=0.7,
        sizing_multiplier=1.0,
        entry_strategy="enter_now",
        execution_method="market",
        final_size_usd=2000,
        final_size_pct=0.02,
        risk_amount_usd=200,
        brick_pattern="trend_up",
        expected_entry_alpha_bps=0,
        config_hash="test",
    )

    decision = gate.evaluate(signal)
    assert not decision.approved
    assert "kill_switch_active" in decision.limits_hit


def test_risk_gate_blocks_on_low_reward_risk():
    """RiskGate blocks when reward:risk is too low."""
    from hermes.portfolio.circuit_breakers import KillSwitch, RiskCircuitBreaker, VolatilityCircuitBreaker
    from hermes.portfolio.risk_gate import RiskGate
    from hermes.portfolio.state import PortfolioStateService
    from hermes.portfolio.var_calculator import VaRCalculator
    from hermes.signals.synthesizer import BlendedSignal

    state = PortfolioStateService(initial_equity=100000, initial_cash_usd=100000)
    gate = RiskGate(
        portfolio_state=state,
        vol_breaker=VolatilityCircuitBreaker(),
        risk_breaker=RiskCircuitBreaker(),
        kill_switch=KillSwitch(),
        var_calculator=VaRCalculator(),
        reward_risk_min=2.0,  # high bar
    )

    # Signal with R:R = 1.0 (entry=60000, stop=59000, target=61000 → 1000/1000 = 1.0)
    signal = BlendedSignal(
        signal_id="test-sig-3",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=60000,
        nt_stop_price=59000,
        nt_target_price=61000,  # only 1000 profit vs 1000 risk → R:R = 1.0
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="calm_trend",
        meta_regime_confidence=0.7,
        sizing_multiplier=1.0,
        entry_strategy="enter_now",
        execution_method="market",
        final_size_usd=2000,
        final_size_pct=0.02,
        risk_amount_usd=200,
        brick_pattern="trend_up",
        expected_entry_alpha_bps=0,
        config_hash="test",
    )

    decision = gate.evaluate(signal)
    assert not decision.approved
    assert any("reward_risk" in l for l in decision.limits_hit)


# === Snapshot Writer ===


@pytest.mark.asyncio
async def test_snapshot_writer_writes(tmp_path):
    """SnapshotWriter writes account snapshots to DuckDB."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.portfolio.snapshot_writer import SnapshotWriter
    from hermes.portfolio.state import PortfolioStateService

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = (
        Path(__file__).resolve().parent.parent
        / "src" / "hermes" / "db" / "schema.sql"
    )
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "hermes" / "db" / "migrations"
    )

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    state = PortfolioStateService(initial_equity=100000, config_hash="test")
    writer = SnapshotWriter(config, state, snapshot_interval_sec=60)
    writer._db_path = db_path  # override

    await writer.write_snapshot("test")

    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM account_snapshots").fetchone()[0]
        assert count == 1


# === Portfolio Risk Engine (integration) ===


@pytest.mark.asyncio
async def test_portfolio_risk_engine_evaluate(tmp_path):
    """PortfolioRiskEngine evaluates a blended signal end-to-end."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.portfolio.orchestrator import PortfolioRiskEngine
    from hermes.signals.synthesizer import BlendedSignal

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = (
        Path(__file__).resolve().parent.parent
        / "src" / "hermes" / "db" / "schema.sql"
    )
    migrations_dir = (
        Path(__file__).resolve().parent.parent
        / "src" / "hermes" / "db" / "migrations"
    )

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    engine = PortfolioRiskEngine(config, initial_equity=100000)
    engine._db_path = db_path  # override
    # Override sub-component db paths
    engine._snapshot_writer._db_path = db_path

    await engine.start()

    signal = BlendedSignal(
        signal_id="test-engine-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=60000,
        nt_stop_price=59000,
        nt_target_price=62000,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="calm_trend",
        meta_regime_confidence=0.7,
        sizing_multiplier=1.0,
        entry_strategy="enter_now",
        execution_method="market",
        final_size_usd=2000,
        final_size_pct=0.02,
        risk_amount_usd=200,
        brick_pattern="trend_up",
        expected_entry_alpha_bps=-2.0,
        config_hash="test",
    )

    decision = await engine.evaluate_signal(signal)

    # Should be approved (within caps, good R:R, no breakers tripped)
    assert decision.signal_id == "test-engine-1"

    # Verify risk decision was written to DuckDB
    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM risk_decisions").fetchone()[0]
        assert count == 1

    await engine.stop()


# === CLI ===


def test_cli_risk_help():
    """`platform risk --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["risk", "--help"])
    assert result.exit_code == 0
    assert "--equity" in result.output


# === Dashboard ===


def test_dashboard_portfolio_page():
    """GET /portfolio returns 200."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/portfolio")
    assert response.status_code == 200
    assert "Portfolio Metrics" in response.text


def test_dashboard_api_portfolio():
    """GET /api/portfolio returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/portfolio")
    assert response.status_code == 200
    data = response.json()
    assert "metrics" in data


def test_dashboard_api_risk_decisions():
    """GET /api/risk/decisions returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/risk/decisions")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "decisions" in data
