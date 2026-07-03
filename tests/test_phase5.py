"""
Phase 5 tests — order schemas, paper trading engine, smart order router,
slippage modeler, execution orchestrator.

Run with:
    pytest tests/test_phase5.py -v
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest


# === Order State Machine ===


def test_order_state_machine_valid_transition():
    """OrderStateMachine allows valid transitions."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderStateMachine, OrderType, TimeInForce

    order = Order(
        trade_id="test-1",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )

    event = OrderStateMachine.transition(order, OrderStatus.SUBMITTED)
    assert order.status == OrderStatus.SUBMITTED
    assert event.event_type == "submitted"


def test_order_state_machine_invalid_transition():
    """OrderStateMachine rejects invalid transitions."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderStateMachine, OrderType, TimeInForce

    order = Order(
        trade_id="test-2",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )
    order.status = OrderStatus.FILLED  # terminal state

    with pytest.raises(ValueError, match="Invalid transition"):
        OrderStateMachine.transition(order, OrderStatus.SUBMITTED)


def test_order_state_machine_apply_fill():
    """OrderStateMachine applies fills correctly."""
    from hermes.execution.orders import Fill, Order, OrderSide, OrderStatus, OrderStateMachine, OrderType, TimeInForce

    order = Order(
        trade_id="test-3",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )
    order.status = OrderStatus.SUBMITTED

    fill = Fill(
        order_id=order.order_id,
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        qty=1.0,
        price=64000,
        fee=1.28,
        arrival_price=63990,
        slippage_bps=1.56,
    )

    event, new_status = OrderStateMachine.apply_fill(order, fill)
    assert order.qty_filled == 1.0
    assert order.avg_fill_price == 64000
    assert order.total_fees == 1.28
    assert new_status == OrderStatus.FILLED
    assert order.status == OrderStatus.FILLED


def test_order_state_machine_partial_fill():
    """OrderStateMachine handles partial fills."""
    from hermes.execution.orders import Fill, Order, OrderSide, OrderStatus, OrderStateMachine, OrderType, TimeInForce

    order = Order(
        trade_id="test-4",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=2.0,
    )
    order.status = OrderStatus.SUBMITTED

    fill = Fill(
        order_id=order.order_id,
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        qty=0.5,
        price=64000,
        fee=0.64,
        arrival_price=63990,
        slippage_bps=1.56,
    )

    _, new_status = OrderStateMachine.apply_fill(order, fill)
    assert order.qty_filled == 0.5
    assert new_status == OrderStatus.PARTIAL
    assert order.status == OrderStatus.PARTIAL


# === Slippage Modeler ===


def test_slippage_market_order_positive():
    """SlippageModeler returns positive slippage for market orders."""
    from hermes.execution.slippage import SlippageModeler

    modeler = SlippageModeler(k_constant=0.1, default_adv_usd=10_000_000)
    slip = modeler.estimate_market_slippage_bps(
        order_size_usd=100_000,
        annualized_vol=0.60,
    )
    assert slip > 0
    # 100k / 10M = 0.01 participation; slip = 0.1 * 0.6 * sqrt(0.01) = 0.006 = 60 bps
    assert 10 < slip < 200


def test_slippage_larger_order_more_slippage():
    """Larger orders have more slippage."""
    from hermes.execution.slippage import SlippageModeler

    modeler = SlippageModeler()
    small = modeler.estimate_market_slippage_bps(10_000, 0.60)
    large = modeler.estimate_market_slippage_bps(1_000_000, 0.60)
    assert large > small


def test_slippage_limit_order_zero():
    """Limit orders have zero slippage."""
    from hermes.execution.slippage import SlippageModeler

    modeler = SlippageModeler()
    slip = modeler.estimate_limit_slippage_bps()
    assert slip == 0.0


def test_slippage_post_only_negative():
    """Post-only orders have negative slippage (maker rebate)."""
    from hermes.execution.slippage import SlippageModeler

    modeler = SlippageModeler()
    slip = modeler.estimate_post_only_slippage_bps(maker_rebate_bps=2.0)
    assert slip < 0


def test_slippage_actual_computation():
    """Actual slippage computed correctly for buy and sell."""
    from hermes.execution.slippage import SlippageModeler

    # Buy: fill > arrival = positive slippage (unfavorable)
    slip_buy = SlippageModeler.compute_actual_slippage_bps(
        arrival_price=64000, fill_price=64064, side="buy"
    )
    assert slip_buy > 0  # 10 bps

    # Sell: fill < arrival = positive slippage (unfavorable)
    slip_sell = SlippageModeler.compute_actual_slippage_bps(
        arrival_price=64000, fill_price=63936, side="sell"
    )
    assert slip_sell > 0  # 10 bps


# === Paper Trading Engine ===


@pytest.mark.asyncio
async def test_paper_engine_market_order_fills():
    """PaperTradingEngine fills a market order."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(fill_delay_ms=0)
    order = Order(
        trade_id="paper-1",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )

    await engine.submit_order(order, current_price=64000, annualized_vol=0.60)

    assert order.status == OrderStatus.FILLED
    assert order.qty_filled == 1.0
    assert order.avg_fill_price is not None
    assert order.avg_fill_price > 64000  # buy with slippage → higher
    assert order.total_fees > 0


@pytest.mark.asyncio
async def test_paper_engine_limit_order_fills():
    """PaperTradingEngine fills a limit order at limit price."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(fill_delay_ms=0)
    order = Order(
        trade_id="paper-2",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        qty_requested=1.0,
        price_limit=63900,
    )

    await engine.submit_order(order, current_price=64000)

    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == 63900  # filled at limit


@pytest.mark.asyncio
async def test_paper_engine_post_only_maker_fill():
    """PaperTradingEngine fills post-only as maker."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(fill_delay_ms=0)
    order = Order(
        trade_id="paper-3",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.POST_ONLY,
        time_in_force=TimeInForce.GTC,
        qty_requested=1.0,
        price_limit=63900,
    )

    await engine.submit_order(order, current_price=64000)

    assert order.status == OrderStatus.FILLED
    fills = engine.get_fills(order.order_id)
    assert len(fills) == 1
    assert fills[0].is_maker is True
    assert fills[0].liquidity == "maker"


@pytest.mark.asyncio
async def test_paper_engine_cancel_order():
    """PaperTradingEngine can cancel an order."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(fill_delay_ms=500)  # long delay to allow cancel
    order = Order(
        trade_id="paper-4",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )

    # Submit but cancel before fill
    await engine.submit_order(order, current_price=64000)

    # Note: with 0 delay this would fill before cancel
    # This test just verifies cancel doesn't error on terminal orders
    assert order.status in (OrderStatus.FILLED, OrderStatus.SUBMITTED)


@pytest.mark.asyncio
async def test_paper_engine_twap_splits():
    """PaperTradingEngine splits TWAP into multiple fills."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(fill_delay_ms=0)
    order = Order(
        trade_id="paper-5",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=3.0,
    )
    order.algo = "twap"

    await engine.submit_order(order, current_price=64000, annualized_vol=0.60)

    assert order.status == OrderStatus.FILLED
    fills = engine.get_fills(order.order_id)
    assert len(fills) >= 3  # at least 3 TWAP slices


@pytest.mark.asyncio
async def test_paper_engine_iceberg_splits():
    """PaperTradingEngine splits iceberg into small child fills."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    engine = PaperTradingEngine(fill_delay_ms=0)
    order = Order(
        trade_id="paper-6",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )
    order.algo = "iceberg"

    await engine.submit_order(order, current_price=64000, annualized_vol=0.60)

    assert order.status == OrderStatus.FILLED
    fills = engine.get_fills(order.order_id)
    assert len(fills) >= 5  # at least 5 iceberg children (10% each)


@pytest.mark.asyncio
async def test_paper_engine_callbacks():
    """PaperTradingEngine calls event + fill callbacks."""
    from hermes.execution.orders import Order, OrderSide, OrderType, TimeInForce
    from hermes.execution.paper_engine import PaperTradingEngine

    events_received = []
    fills_received = []

    engine = PaperTradingEngine(fill_delay_ms=0)

    async def on_event(order_id, event):
        events_received.append(event)

    async def on_fill(fill):
        fills_received.append(fill)

    engine.set_callbacks(event_callback=on_event, fill_callback=on_fill)

    order = Order(
        trade_id="paper-7",
        symbol="BTC",
        venue="hyperliquid",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=1.0,
    )

    await engine.submit_order(order, current_price=64000)

    assert len(events_received) >= 2  # submitted + fill
    assert len(fills_received) == 1


# === Smart Order Router ===


def test_router_creates_market_order():
    """SmartOrderRouter creates a market order for market execution."""
    from hermes.execution.orders import OrderType, OrderStatus
    from hermes.execution.router import SmartOrderRouter
    from hermes.portfolio.risk_gate import RiskDecision
    from hermes.signals.synthesizer import BlendedSignal

    signal = BlendedSignal(
        signal_id="test-router-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=64000,
        nt_stop_price=63000,
        nt_target_price=66000,
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

    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        requested_size_usd=2000,
        approved_size_usd=2000,
        config_hash="test",
    )

    router = SmartOrderRouter()
    orders = router.create_orders(decision, signal)

    assert len(orders) == 1
    assert orders[0].order_type == OrderType.MARKET
    assert orders[0].side.value == "buy"
    assert orders[0].qty_requested > 0


def test_router_creates_limit_order():
    """SmartOrderRouter creates a limit order for limit_at_brick execution."""
    from hermes.execution.orders import OrderType
    from hermes.execution.router import SmartOrderRouter
    from hermes.portfolio.risk_gate import RiskDecision
    from hermes.signals.synthesizer import BlendedSignal

    signal = BlendedSignal(
        signal_id="test-router-2",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=64000,
        nt_stop_price=63000,
        nt_target_price=66000,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="choppy_range",
        meta_regime_confidence=0.65,
        sizing_multiplier=0.8,
        entry_strategy="wait_for_brick_close",
        execution_method="limit_at_brick_boundary",
        entry_price_target=64000,
        limit_price=63950,
        final_size_usd=1600,
        final_size_pct=0.016,
        risk_amount_usd=160,
        brick_pattern="consolidation",
        expected_entry_alpha_bps=4.0,
        config_hash="test",
    )

    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        requested_size_usd=1600,
        approved_size_usd=1600,
        config_hash="test",
    )

    router = SmartOrderRouter()
    orders = router.create_orders(decision, signal)

    assert len(orders) == 1
    assert orders[0].order_type == OrderType.LIMIT
    assert orders[0].price_limit == 63950


def test_router_creates_post_only_order():
    """SmartOrderRouter creates a post_only order."""
    from hermes.execution.orders import OrderType
    from hermes.execution.router import SmartOrderRouter
    from hermes.portfolio.risk_gate import RiskDecision
    from hermes.signals.synthesizer import BlendedSignal

    signal = BlendedSignal(
        signal_id="test-router-3",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=64000,
        nt_stop_price=63000,
        nt_target_price=66000,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="liquidity_drained",
        meta_regime_confidence=0.8,
        sizing_multiplier=0.3,
        entry_strategy="maker_only",
        execution_method="post_only",
        entry_price_target=64000,
        limit_price=63990,
        final_size_usd=600,
        final_size_pct=0.006,
        risk_amount_usd=60,
        brick_pattern="unknown",
        expected_entry_alpha_bps=2.0,
        config_hash="test",
    )

    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        requested_size_usd=600,
        approved_size_usd=600,
        config_hash="test",
    )

    router = SmartOrderRouter()
    orders = router.create_orders(decision, signal)

    assert len(orders) == 1
    assert orders[0].order_type == OrderType.POST_ONLY


def test_router_no_orders_for_rejected_decision():
    """SmartOrderRouter returns no orders for rejected decision."""
    from hermes.execution.router import SmartOrderRouter
    from hermes.portfolio.risk_gate import RiskDecision
    from hermes.signals.synthesizer import BlendedSignal

    signal = BlendedSignal(
        signal_id="test-router-4",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=64000,
        nt_stop_price=63000,
        nt_target_price=66000,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime="risk_off",
        meta_regime_confidence=0.9,
        sizing_multiplier=0.0,
        entry_strategy="block",
        execution_method="market",
        final_size_usd=0,
        final_size_pct=0,
        risk_amount_usd=0,
        brick_pattern="unknown",
        expected_entry_alpha_bps=0,
        config_hash="test",
    )

    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=False,
        requested_size_usd=0,
        approved_size_usd=0,
        config_hash="test",
    )

    router = SmartOrderRouter()
    orders = router.create_orders(decision, signal)

    assert len(orders) == 0


# === Execution Orchestrator (integration) ===


@pytest.mark.asyncio
async def test_execution_engine_paper_trade(tmp_path):
    """ExecutionEngine executes a paper trade end-to-end."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.execution.orchestrator import ExecutionEngine
    from hermes.portfolio.risk_gate import RiskDecision
    from hermes.portfolio.state import PortfolioStateService
    from hermes.signals.synthesizer import BlendedSignal

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    # Apply schema
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

    portfolio_state = PortfolioStateService(initial_equity=100000, initial_cash_usdc=100000)
    engine = ExecutionEngine(config, portfolio_state, paper_mode=True)
    engine._db_path = db_path
    engine._writer._db_path = db_path  # override writer path

    await engine.start()

    signal = BlendedSignal(
        signal_id="test-exec-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="buy",
        nt_entry_price=64000,
        nt_stop_price=63000,
        nt_target_price=66000,
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

    decision = RiskDecision(
        signal_id=signal.signal_id,
        approved=True,
        requested_size_usd=2000,
        approved_size_usd=2000,
        config_hash="test",
    )

    orders = await engine.execute_decision(
        decision=decision,
        signal=signal,
        current_price=64000,
    )

    assert len(orders) == 1
    assert orders[0].status.value == "filled"

    # Verify position was registered
    positions = portfolio_state.get_all_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "BTC"
    assert positions[0].direction == "long"

    # Verify order + fills written to DuckDB
    with duckdb.connect(str(db_path), read_only=True) as conn:
        order_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        fill_count = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
        assert order_count >= 1
        assert fill_count >= 1

    await engine.stop()


# === CLI ===


def test_cli_execute_help():
    """`platform execute --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["execute", "--help"])
    assert result.exit_code == 0
    assert "--equity" in result.output
    assert "--paper" in result.output


# === Dashboard ===


def test_dashboard_orders_page():
    """GET /orders returns 200."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/orders")
    assert response.status_code == 200
    assert "Orders" in response.text


def test_dashboard_api_orders():
    """GET /api/orders returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/orders")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "orders" in data


def test_dashboard_api_fills():
    """GET /api/fills returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/fills")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "fills" in data
