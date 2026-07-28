"""Tests for hermes.execution.brokers.metaapi_broker.

The MetaApi SDK is mocked entirely (no network). We patch
`metaapi_cloud_sdk.MetaApi` so `MetaApiBroker.connect()` builds a fake account
+ RPC connection, and we assert the correct RPC methods are invoked with the
expected args (symbol normalized, volume=lots, clientId=order_id).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hermes.execution.brokers.metaapi_broker import MetaApiBroker, _parse_bool
from hermes.execution.orders import (
    Fill,
    Order,
    OrderEvent,
    OrderSide,
    OrderStatus,
    OrderType,
)


def _make_order(symbol="BTCUSD", side=OrderSide.BUY, otype=OrderType.MARKET, qty=0.1):
    return Order(
        trade_id="t1",
        symbol=symbol,
        venue="mt4_mt5",
        side=side,
        order_type=otype,
        qty_requested=qty,
        price_limit=1.0 if otype in (OrderType.LIMIT, OrderType.STOP) else None,
    )


def _install_mock_metaapi(monkeypatch, *, order_result=None, order_state="ORDER_STATE_FILLED",
                           symbol_price=None):
    """Install a fake metaapi_cloud_sdk.MetaApi and return the fake connection."""
    conn = MagicMock()
    conn.connect = AsyncMock()
    conn.wait_synchronized = AsyncMock()
    conn.get_order = AsyncMock(
        return_value={"id": "v-1", "state": order_state, "currentPrice": 50000.0}
    )
    conn.get_symbol_price = AsyncMock(return_value=symbol_price or {
        "bid": 49990.0, "ask": 50010.0, "last": 50000.0,
    })
    conn.get_symbol_specification = AsyncMock(return_value={
        "contractSize": 100000, "lotStep": 0.01, "minVolume": 0.01, "maxVolume": 100,
    })
    conn.calculate_margin = AsyncMock(return_value={"margin": 10.0})
    conn.modify_position = AsyncMock()
    # RPC submit methods capture their args
    for m in (
        "create_market_buy_order",
        "create_market_sell_order",
        "create_limit_buy_order",
        "create_limit_sell_order",
        "create_stop_buy_order",
        "create_stop_sell_order",
        "cancel_order",
        "close_position",
    ):
        setattr(conn, m, AsyncMock(return_value=order_result or {"orderId": "v-1", "stringCode": "OK", "numericCode": 10009}))

    account = MagicMock()
    account.state = "DEPLOYED"
    account.wait_connected = AsyncMock()
    account.deploy = AsyncMock()
    account.undeploy = AsyncMock()
    account.get_rpc_connection = MagicMock(return_value=conn)

    api = MagicMock()
    api.metatrader_account_api.get_account = AsyncMock(return_value=account)
    api.format_error = MagicMock(side_effect=lambda e: str(e))

    import sys

    fake_mod = MagicMock()
    fake_mod.MetaApi = MagicMock(return_value=api)
    monkeypatch.setitem(sys.modules, "metaapi_cloud_sdk", fake_mod)
    return conn


@pytest.fixture
def broker(monkeypatch):
    monkeypatch.setenv("METAAPI_TOKEN", "tok")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acc-1")
    monkeypatch.setenv("METAAPI_DEMO", "true")
    _install_mock_metaapi(monkeypatch)
    b = MetaApiBroker()
    return b


@pytest.mark.asyncio
async def test_connect_deploys_only_if_needed(monkeypatch):
    monkeypatch.setenv("METAAPI_TOKEN", "tok")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "acc-1")
    conn = _install_mock_metaapi(monkeypatch)
    b = MetaApiBroker()
    await b.connect()
    assert b._connected
    # account already DEPLOYED -> deploy() NOT called
    account = b._account
    account.deploy.assert_not_called()
    conn.connect.assert_awaited()
    conn.wait_synchronized.assert_awaited()


@pytest.mark.asyncio
async def test_market_buy_submits_with_client_id(broker):
    await broker.connect()
    # 1000 units EUR (≈ $1000 notional at ~1.1750) → /100000 = 0.01 lots
    order = _make_order(side=OrderSide.BUY, otype=OrderType.MARKET, qty=1000)
    await broker.submit_order(order, current_price=1.1750)
    broker._conn.create_market_buy_order.assert_awaited_once()
    args, kwargs = broker._conn.create_market_buy_order.call_args
    assert args[0] == "BTCUSD"          # normalized symbol
    assert args[1] == 0.01               # volume in LOTS (units→lots conversion)
    assert args[2]["clientId"] == order.order_id
    assert order.venue_order_id == "v-1"
    assert order.status == OrderStatus.FILLED  # polled fill
    # Behavior 1: BUY fills at broker ASK (50010), not last/currentPrice.
    assert order.avg_fill_price == 50010.0


@pytest.mark.asyncio
async def test_market_buy_records_slippage(broker):
    """Behavior 1: slippage vs arrival recorded on the Fill (BUY at ask → +10 bps)."""
    await broker.connect()
    fills: list = []
    broker.set_callbacks(fill_callback=lambda f: fills.append(f))
    order = _make_order(side=OrderSide.BUY, otype=OrderType.MARKET, qty=1000)
    await broker.submit_order(order, current_price=50000.0)
    assert len(fills) == 1
    assert fills[0].price == 50010.0
    assert fills[0].slippage_bps == pytest.approx(2.0)  # 10000*(50010-50000)/50000
    assert fills[0].qty == 1000  # reported in units (matches order.qty_requested)


@pytest.mark.asyncio
async def test_market_sell_fills_at_bid(broker):
    await broker.connect()
    fills: list = []
    broker.set_callbacks(fill_callback=lambda f: fills.append(f))
    order = _make_order(side=OrderSide.SELL, otype=OrderType.MARKET, qty=1000)
    await broker.submit_order(order, current_price=50000.0)
    broker._conn.create_market_sell_order.assert_awaited_once()
    # Behavior 1: SELL fills at broker BID (49990) → +2.0 bps (unfavorable: sold below arrival).
    assert order.avg_fill_price == 49990.0
    assert len(fills) == 1
    assert fills[0].price == 49990.0
    assert fills[0].slippage_bps == pytest.approx(2.0)  # 10000*(50000-49990)/50000
    assert fills[0].qty == 1000  # units


@pytest.mark.asyncio
async def test_broker_rejection_via_numeric_code(broker):
    """Behavior 5: result numericCode outside success set → REJECTED."""
    await broker.connect()
    # 10004 = TRADE_RETCODE_REJECTED
    broker._conn.create_market_buy_order = AsyncMock(return_value={
        "numericCode": 10004, "stringCode": "TRADE_RETCODE_REJECTED",
        "orderId": None, "positionId": None,
    })
    order = _make_order(side=OrderSide.BUY, otype=OrderType.MARKET, qty=0.07)
    await broker.submit_order(order, current_price=50000.0)
    assert order.status == OrderStatus.REJECTED
    assert order.venue_order_id != "v-1"  # no accepted venue order id
    # does NOT poll for a fill (stays rejected, not filled)
    assert order.status != OrderStatus.FILLED


@pytest.mark.asyncio
async def test_limit_rejection_via_numeric_code_stays_rejected(broker):
    """Behavior 5 also covers resting orders that are rejected at submit."""
    await broker.connect()
    broker._conn.create_limit_buy_order = AsyncMock(return_value={
        "numericCode": 10017, "stringCode": "TRADE_RETCODE_INVALID",  # not in success set
        "orderId": None,
    })
    order = _make_order(side=OrderSide.BUY, otype=OrderType.LIMIT, qty=0.2)
    order.price_limit = 1.05
    await broker.submit_order(order, current_price=1.06)
    # Would have been SUBMITTED without the numericCode check; must be REJECTED.
    assert order.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_limit_sell_submits_limit_order(broker):
    await broker.connect()
    order = _make_order(symbol="EURUSD", side=OrderSide.SELL, otype=OrderType.LIMIT, qty=25000)
    order.price_limit = 1.05
    await broker.submit_order(order, current_price=1.06)
    broker._conn.create_limit_sell_order.assert_awaited_once()
    args, kwargs = broker._conn.create_limit_sell_order.call_args
    assert args[0] == "EURUSD"
    assert args[1] == 0.25               # 25000 units / 100000 = 0.25 lots
    assert args[2]["openPrice"] == 1.05
    assert args[2]["clientId"] == order.order_id
    # Resting order -> SUBMITTED (not filled by poll)
    assert order.status == OrderStatus.SUBMITTED


@pytest.mark.asyncio
async def test_perp_symbol_normalized(broker):
    await broker.connect()
    order = _make_order(symbol="BTC-PERP", side=OrderSide.BUY, otype=OrderType.MARKET, qty=0.01)
    await broker.submit_order(order, current_price=50000.0)
    args, _ = broker._conn.create_market_buy_order.call_args
    assert args[0] == "BTCUSD"  # map applied


@pytest.mark.asyncio
async def test_close_position(broker):
    await broker.connect()
    await broker.close_position("pos-9", reason="CLOSE_TAKE_PROFIT")
    broker._conn.close_position.assert_awaited_once_with(
        "pos-9", {"comment": "hermes-close:CLOSE_TAKE_PROFIT"[:31]}
    )


@pytest.mark.asyncio
async def test_submit_handles_error_gracefully(broker):
    await broker.connect()
    broker._conn.create_market_buy_order = AsyncMock(side_effect=RuntimeError("boom"))
    order = _make_order()
    # must not raise
    await broker.submit_order(order, current_price=50000.0)
    assert order.status == OrderStatus.REJECTED


@pytest.mark.asyncio
async def test_missing_env_raises_on_connect(monkeypatch):
    monkeypatch.delenv("METAAPI_TOKEN", raising=False)
    monkeypatch.delenv("METAAPI_ACCOUNT_ID", raising=False)
    b = MetaApiBroker()
    with pytest.raises(RuntimeError):
        await b.connect()


def test_parse_bool():
    assert _parse_bool("true") is True
    assert _parse_bool("false") is False
    assert _parse_bool(None, default=True) is True
    assert _parse_bool(None, default=False) is False


@pytest.mark.asyncio
async def test_units_to_lots_conversion(broker):
    """qty_requested is UNITS; MetaApi volume must be LOTS (contract/100k)."""
    await broker.connect()
    # 1000 units EUR / 100000 contract = 0.01 lots
    assert await broker._units_to_lots(1000, 1.1750, "EURUSD") == 0.01
    # 25000 units / 100000 = 0.25 lots
    assert await broker._units_to_lots(25000, 1.0, "EURUSD") == 0.25
    # 851.06 units (≈ $1000 / 1.1750) / 100000 = 0.00851 → round to 0.01
    assert await broker._units_to_lots(851.06, 1.1750, "EURUSD") == 0.01


@pytest.mark.asyncio
async def test_calculate_margin_called_on_submit(broker):
    """Contract item: margin is estimated before a trade is placed."""
    await broker.connect()
    order = _make_order(side=OrderSide.BUY, otype=OrderType.MARKET, qty=1000)
    await broker.submit_order(order, current_price=1.1750)
    broker._conn.calculate_margin.assert_awaited_once()
    kwargs = broker._conn.calculate_margin.call_args[0][0]
    assert kwargs["symbol"] == "BTCUSD"
    assert kwargs["type"] == "ORDER_TYPE_BUY"
    assert kwargs["volume"] == 0.01  # lots


@pytest.mark.asyncio
async def test_sl_tp_set_on_fill(broker):
    """After a market fill, SL/TP on the order are pushed to modify_position."""
    await broker.connect()
    order = _make_order(side=OrderSide.BUY, otype=OrderType.MARKET, qty=1000)
    order.stop_loss = 49000.0
    order.take_profit = 52000.0
    # Simulate a position opened with our clientId so resolve finds it.
    broker._conn.get_positions = AsyncMock(return_value=[
        {"id": "POS-1", "clientId": order.order_id, "symbol": "BTCUSD"},
    ])
    await broker.submit_order(order, current_price=50000.0)
    broker._conn.modify_position.assert_awaited_once_with(
        "POS-1", {"stopLoss": 49000.0, "takeProfit": 52000.0}
    )
