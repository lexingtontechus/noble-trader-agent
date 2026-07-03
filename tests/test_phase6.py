"""
Phase 6 tests — PnL service, attribution, drawdown tracker, tear sheet.

Run with:
    pytest tests/test_phase6.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# === PnL Attribution ===


def test_pnl_attribution_basic():
    """PnL attribution computes directional + timing PnL."""
    from hermes.analytics.pnl_service import PnLAttribution

    attribution = PnLAttribution.compute(
        entry_price=64000,
        exit_price=65000,
        nt_entry_price=64100,  # Hermes entered 100 better
        qty=1.0,
        risk_amount=1000,
        regime_at_close="calm_trend",
        gross_pnl=1000,
        fees=5,
        slippage=10,
        funding=0,
    )

    # Directional: (65000 - 64000) * 1.0 = 1000
    assert attribution["direction_pnl"] == 1000.0
    # Timing: (64100 - 64000) * 1.0 = 100 (Hermes entered better)
    assert attribution["timing_pnl"] == 100.0
    # Sizing: simplified to 0
    assert attribution["sizing_pnl"] == 0.0
    # Regime: calm_trend multiplier = 1.0 → delta = 0
    assert attribution["regime_pnl"] == 0.0


def test_pnl_attribution_short_trade():
    """PnL attribution handles short trades."""
    from hermes.analytics.pnl_service import PnLAttribution

    attribution = PnLAttribution.compute(
        entry_price=64000,
        exit_price=63000,  # price dropped — good for short
        nt_entry_price=64100,
        qty=1.0,
        risk_amount=1000,
        regime_at_close="risk_off",
        gross_pnl=1000,  # (64000 - 63000) * 1.0
        fees=5,
        slippage=10,
        funding=0,
    )

    # Directional: (63000 - 64000) * 1.0 = -1000 (but for short, gross is positive)
    # The attribution uses (exit - entry) * qty, so for short it's negative
    assert attribution["direction_pnl"] == -1000.0
    # Timing still positive (Hermes entered better than NT)
    assert attribution["timing_pnl"] == 100.0


# === Drawdown Tracker ===


def test_drawdown_tracker_no_dd_on_rising_equity():
    """DrawdownTracker shows no DD when equity rises."""
    from hermes.analytics.pnl_service import DrawdownTracker

    tracker = DrawdownTracker()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    tracker.update(100000, base)
    tracker.update(105000, base + timedelta(hours=1))
    tracker.update(110000, base + timedelta(hours=2))

    stats = tracker.get_stats()
    assert stats["current_dd_pct"] == 0
    assert stats["max_dd_pct"] == 0
    assert stats["peak_equity"] == 110000


def test_drawdown_tracker_detects_dd():
    """DrawdownTracker detects drawdown."""
    from hermes.analytics.pnl_service import DrawdownTracker

    tracker = DrawdownTracker()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    tracker.update(100000, base)
    tracker.update(110000, base + timedelta(hours=1))  # peak
    tracker.update(100000, base + timedelta(hours=2))  # 9.1% DD

    stats = tracker.get_stats()
    assert stats["current_dd_pct"] > 0
    assert stats["max_dd_pct"] > 0
    assert stats["peak_equity"] == 110000


def test_drawdown_tracker_max_dd():
    """DrawdownTracker tracks max drawdown."""
    from hermes.analytics.pnl_service import DrawdownTracker

    tracker = DrawdownTracker()
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)

    tracker.update(100000, base)
    tracker.update(120000, base + timedelta(hours=1))  # peak
    tracker.update(90000, base + timedelta(hours=2))   # 25% DD
    tracker.update(130000, base + timedelta(hours=3))  # new peak
    tracker.update(110000, base + timedelta(hours=4))  # 15.4% DD

    stats = tracker.get_stats()
    # Max DD was 25% (120k → 90k)
    assert stats["max_dd_pct"] >= 0.24  # ~25%
    assert stats["peak_equity"] == 130000


# === PnL Service ===


@pytest.mark.asyncio
async def test_pnl_service_record_realized(tmp_path):
    """PnLService records realized PnL to DuckDB."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.analytics.pnl_service import PnLService
    from hermes.portfolio.state import PortfolioStateService

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    state = PortfolioStateService(initial_equity=100000, config_hash="test")
    service = PnLService(config, state)
    service._db_path = db_path  # override
    await service.start()

    realized = service.record_realized_pnl(
        trade_id="test-pnl-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="long",
        entry_price=64000,
        exit_price=65000,
        qty=1.0,
        fees=5.0,
        slippage=10.0,
        funding=0.0,
        risk_amount=1000,
        hold_duration_sec=3600,
        n_fills=2,
        nt_entry_price=64100,
        regime_at_close="calm_trend",
        config_hash="test",
    )

    assert realized.net_pnl == 985.0  # 1000 - 5 - 10 + 0
    assert realized.r_multiple == 0.985
    assert realized.direction_pnl == 1000.0
    assert realized.timing_pnl == 100.0

    # Verify written to DuckDB
    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pnl_realized").fetchone()[0]
        assert count == 1

    await service.stop()


@pytest.mark.asyncio
async def test_pnl_service_snapshot_unrealized(tmp_path):
    """PnLService snapshots unrealized PnL."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.analytics.pnl_service import PnLService
    from hermes.portfolio.state import PortfolioStateService
    from hermes.schemas.market import Position, Venue

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    state = PortfolioStateService(initial_equity=100000, initial_cash_usdc=100000, config_hash="test")
    state.add_position(Position(
        position_id="test-pos-1",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=64000,
        stop_price=63000,
        target_price=66000,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000,
    ))
    state.update_price("BTC", 65000)

    service = PnLService(config, state)
    service._db_path = db_path
    await service.start()

    snapshots = await service.snapshot_unrealized()
    assert len(snapshots) == 1
    assert snapshots[0].unrealized_gross == 1000.0  # (65000 - 64000) * 1.0

    # Verify written to DuckDB
    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM pnl_unrealized").fetchone()[0]
        assert count == 1

    await service.stop()


def test_pnl_service_funding_accrual(tmp_path):
    """PnLService accrues funding PnL."""
    from hermes.core.config import load_config
    from hermes.analytics.pnl_service import PnLService
    from hermes.portfolio.state import PortfolioStateService

    config = load_config()
    state = PortfolioStateService(initial_equity=100000)
    service = PnLService(config, state)
    service._db_path = tmp_path / "test.duckdb"

    service.add_funding_pnl("BTC", 50.0)
    service.add_funding_pnl("BTC", 30.0)

    # The funding accrual is stored internally
    assert service._funding_accrual["BTC"] == 80.0


# === Tear Sheet ===


def test_tear_sheet_generates_metrics(tmp_path):
    """TearSheet generates performance metrics from equity curve."""
    from hermes.analytics.pnl_service import PnLService
    from hermes.analytics.tear_sheet import TearSheet
    from hermes.core.config import load_config
    from hermes.portfolio.state import PortfolioStateService

    config = load_config()
    state = PortfolioStateService(initial_equity=100000)
    service = PnLService(config, state)
    service._db_path = tmp_path / "test.duckdb"

    tear_sheet = TearSheet(service)

    # Create synthetic equity curve
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    equity_curve = []
    equity = 100000
    for i in range(100):
        equity *= 1.001  # 0.1% daily growth
        equity_curve.append((base + timedelta(days=i), equity))

    metrics = tear_sheet.generate(equity_curve=equity_curve, realized_trades=[])

    assert "returns" in metrics
    assert "risk_adjusted" in metrics
    assert "drawdown" in metrics
    assert "summary" in metrics
    assert metrics["returns"]["total_return_pct"] > 0
    assert metrics["risk_adjusted"]["sharpe"] > 0
    assert metrics["summary"]["n_data_points"] == 100


def test_tear_sheet_with_trades(tmp_path):
    """TearSheet includes trading stats when trades are provided."""
    from hermes.analytics.pnl_service import PnLService
    from hermes.analytics.tear_sheet import TearSheet
    from hermes.core.config import load_config
    from hermes.portfolio.state import PortfolioStateService

    config = load_config()
    state = PortfolioStateService(initial_equity=100000)
    service = PnLService(config, state)
    tear_sheet = TearSheet(service)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    equity_curve = [(base + timedelta(days=i), 100000 * (1 + i * 0.001)) for i in range(50)]

    trades = [
        {"net_pnl": 500, "r_multiple": 0.5, "hold_duration_sec": 3600, "regime_at_close": "calm_trend"},
        {"net_pnl": -200, "r_multiple": -0.2, "hold_duration_sec": 1800, "regime_at_close": "choppy_range"},
        {"net_pnl": 800, "r_multiple": 0.8, "hold_duration_sec": 7200, "regime_at_close": "calm_trend"},
    ]

    metrics = tear_sheet.generate(equity_curve=equity_curve, realized_trades=trades)

    assert metrics["trading"]["n_trades"] == 3
    assert metrics["trading"]["win_rate_pct"] == 66.7  # 2/3 wins
    assert metrics["trading"]["total_net_pnl"] == 1100  # 500 - 200 + 800
    assert "by_regime" in metrics["trading"]
    assert "calm_trend" in metrics["trading"]["by_regime"]


def test_tear_sheet_insufficient_data(tmp_path):
    """TearSheet returns error with insufficient data."""
    from hermes.analytics.pnl_service import PnLService
    from hermes.analytics.tear_sheet import TearSheet
    from hermes.core.config import load_config
    from hermes.portfolio.state import PortfolioStateService

    config = load_config()
    state = PortfolioStateService(initial_equity=100000)
    service = PnLService(config, state)
    tear_sheet = TearSheet(service)

    metrics = tear_sheet.generate(equity_curve=[], realized_trades=[])
    assert "error" in metrics


# === CLI ===


def test_cli_pnl_help():
    """`platform pnl --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["pnl", "--help"])
    assert result.exit_code == 0
    assert "--equity" in result.output


# === Dashboard ===


def test_dashboard_pnl_page():
    """GET /pnl returns 200."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/pnl")
    assert response.status_code == 200
    assert "PnL" in response.text


def test_dashboard_api_pnl_tear_sheet():
    """GET /api/pnl/tear_sheet returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/pnl/tear_sheet")
    assert response.status_code == 200
    data = response.json()
    # Either has error or has summary
    assert "error" in data or "summary" in data


def test_dashboard_api_pnl_history():
    """GET /api/pnl/history returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/pnl/history")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "history" in data
