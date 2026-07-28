"""Offline regression tests for MetaApiBroker (no live account / network).

Locks in the Plan-B bug fixes:
  - submit_order passes `options` as a KEYWORD (not positionally), so it
    lands in MetaApi's `options` param instead of `stop_loss`.
  - clientId is OMITTED (this broker rejects every clientId format).
  - qty_requested (units) is converted to lots via contractSize.
"""
import asyncio
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hermes.execution.brokers.metaapi_broker import MetaApiBroker
from hermes.execution.orders import Order, OrderSide, OrderType


def _install_fake_sdk(fake_api: MagicMock) -> None:
    """Inject a fake metaapi_cloud_sdk so the broker's lazy `from metaapi_cloud_sdk import MetaApi` works without the real SDK installed."""
    mod = types.ModuleType("metaapi_cloud_sdk")
    mod.MetaApi = fake_api
    sys.modules["metaapi_cloud_sdk"] = mod


def _make_broker_and_fakes():
    fake_api = MagicMock()
    _install_fake_sdk(fake_api)

    fake_account = MagicMock()
    fake_account.state = "DEPLOYED"  # already deployed -> no deploy() call
    fake_conn = AsyncMock()
    fake_conn.get_symbol_price = AsyncMock(return_value={"ask": 100.0, "bid": 99.0})
    fake_conn.create_market_buy_order = AsyncMock(
        return_value={"orderId": "VENUE-1", "numericCode": 10008}
    )
    fake_conn.create_market_sell_order = AsyncMock(
        return_value={"orderId": "VENUE-2", "numericCode": 10008}
    )
    fake_conn.get_order = AsyncMock(return_value=None)  # -> no fill polled
    fake_conn.wait_synchronized = AsyncMock()
    fake_account.get_rpc_connection = MagicMock(return_value=fake_conn)
    fake_account.wait_connected = AsyncMock()
    fake_api.return_value.metatrader_account_api.get_account = AsyncMock(
        return_value=fake_account
    )
    return fake_api, fake_account, fake_conn


async def _build_and_submit():
    b = MetaApiBroker(token="t", account_id="a", demo=True)
    await b.connect()
    # Force a known symbol spec so lot conversion is deterministic.
    b.get_symbol_specification = AsyncMock(
        return_value={
            "contractSize": 100.0,
            "lotStep": 0.01,
            "minVolume": 0.0,
            "maxVolume": 1e9,
        }
    )
    order = Order(
        symbol="XAUUSD",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        qty_requested=10.0,  # 10 units / 100 contractSize = 0.1 lots
        signal_id="sig-1",
        trade_id="trd-1",
        venue="metaapi",
    )
    await b.submit_order(order, current_price=100.0)
    return b, order


def test_submit_passes_options_kwarg_and_omits_clientid():
    fake_api, fake_account, fake_conn = _make_broker_and_fakes()
    with patch("metaapi_cloud_sdk.MetaApi", fake_api):
        b, order = asyncio.run(_build_and_submit())

    # 1) RPC method invoked with options as a KEYWORD (the core bug fix).
    fake_conn.create_market_buy_order.assert_called_once()
    args, kwargs = fake_conn.create_market_buy_order.call_args
    assert args[0] == "XAUUSD"
    assert args[1] == 0.1  # units(10) / contractSize(100) = 0.1 lots
    assert "options" in kwargs
    # 2) clientId omitted (broker requires it absent).
    assert kwargs["options"] == {}
    # 3) order transitioned to a live state (not rejected).
    assert order.status.value in ("submitted", "partial", "filled")


def test_units_to_lots_conversion():
    b = MetaApiBroker(token="t", account_id="a", demo=True)
    b.get_symbol_specification = AsyncMock(
        return_value={"contractSize": 100.0, "lotStep": 0.01, "minVolume": 0.0, "maxVolume": 1e9}
    )
    lots = asyncio.run(b._units_to_lots(10.0, 100.0, "XAUUSD"))
    assert lots == 0.1
