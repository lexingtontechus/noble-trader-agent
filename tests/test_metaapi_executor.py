"""
Tests for the MetaApi venue adapter + live executor.

These tests use monkeypatched fakes for the MetaApi SDK (so they run without
``metaapi-cloud-sdk`` installed). They mirror ``test_phase5.py``'s paper-engine
tests so the contract parity is verifiable.

Coverage:
  - Symbol normalization (strips COINBASE: / MT5: qualifiers)
  - Adapter construction (placeholder detection → _configured=False)
  - connect() happy path (deploy + wait_connected + RPC connect + sync)
  - submit_order() market BUY: SDK call shape + fill emission + state transition
  - submit_order() market SELL: mirror
  - submit_order() LIMIT: pending state (no fill until broker confirms)
  - submit_order() broker rejection (numericCode 10004 = REJECT) → REJECTED
  - submit_order() SDK exception → REJECTED
  - cancel_order() for pending LIMIT order → calls cancel_order on SDK
  - cancel_order() for terminal order → False
  - get_position / get_account_information SDK passthrough
  - Venue.METAAPI enum registered
  - config/default.yaml has a metaapi venue block

Run:
    pytest tests/test_metaapi_executor.py -v
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ────────────────────────────────────────────────────────────────────────────
# Fakes for the MetaApi SDK
# ────────────────────────────────────────────────────────────────────────────


class FakeRpcConnection:
    """Fake RPC connection — records all calls + returns canned responses."""

    def __init__(self) -> None:
        self.connected = False
        self.closed = False
        self.calls: list[tuple[str, tuple, dict]] = []
        self._next_result: dict[str, Any] = {
            "numericCode": 10009,
            "stringCode": "TRADE_RETCODE_DONE",
            "orderId": "test-order-1",
            "positionId": "test-position-1",
        }
        self._symbol_price: dict[str, dict[str, float]] = {
            "EURUSD": {"bid": 1.0849, "ask": 1.0851, "last": 1.0850},
            "XAUUSD": {"bid": 2349.50, "ask": 2350.50, "last": 2350.0},
        }
        self._positions: list[dict[str, Any]] = []

    def set_next_result(self, result: dict[str, Any]) -> None:
        self._next_result = result

    def set_positions(self, positions: list[dict[str, Any]]) -> None:
        self._positions = positions

    async def connect(self) -> None:
        self.connected = True

    async def wait_synchronized(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        self.connected = False

    async def get_symbol_price(self, symbol: str) -> dict[str, float]:
        self.calls.append(("get_symbol_price", (symbol,), {}))
        return self._symbol_price.get(symbol, {"bid": 1.0, "ask": 1.0, "last": 1.0})

    async def get_account_information(self) -> dict[str, Any]:
        self.calls.append(("get_account_information", (), {}))
        return {"balance": 10000.0, "equity": 10050.0, "currency": "USD", "margin": 100.0}

    async def get_positions(self) -> list[dict[str, Any]]:
        self.calls.append(("get_positions", (), {}))
        return self._positions[:]

    async def create_market_buy_order(self, symbol: str, volume: float, sl, tp) -> dict[str, Any]:
        self.calls.append(("create_market_buy_order", (symbol, volume, sl, tp), {}))
        return self._next_result

    async def create_market_sell_order(self, symbol: str, volume: float, sl, tp) -> dict[str, Any]:
        self.calls.append(("create_market_sell_order", (symbol, volume, sl, tp), {}))
        return self._next_result

    async def create_limit_buy_order(self, symbol, volume, price, sl, tp) -> dict[str, Any]:
        self.calls.append(("create_limit_buy_order", (symbol, volume, price, sl, tp), {}))
        return self._next_result

    async def create_limit_sell_order(self, symbol, volume, price, sl, tp) -> dict[str, Any]:
        self.calls.append(("create_limit_sell_order", (symbol, volume, price, sl, tp), {}))
        return self._next_result

    async def create_stop_buy_order(self, symbol, volume, price, sl, tp) -> dict[str, Any]:
        self.calls.append(("create_stop_buy_order", (symbol, volume, price, sl, tp), {}))
        return self._next_result

    async def create_stop_sell_order(self, symbol, volume, price, sl, tp) -> dict[str, Any]:
        self.calls.append(("create_stop_sell_order", (symbol, volume, price, sl, tp), {}))
        return self._next_result

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        self.calls.append(("cancel_order", (order_id,), {}))
        return {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE"}

    async def close_position(self, position_id: str, *args, **kwargs) -> dict[str, Any]:
        self.calls.append(("close_position", (position_id,), kwargs))
        return {"numericCode": 10009, "stringCode": "TRADE_RETCODE_DONE"}


class FakeAccount:
    """Fake MetaApi MetatraderAccount."""

    def __init__(self) -> None:
        self.state = "DEPLOYED"
        self.broker = "PlexyTrade"
        self.server = "Server01"
        self.currency = "USD"
        self._rpc = FakeRpcConnection()
        self.deployed = False
        self.undeployed = False

    async def deploy(self) -> None:
        self.deployed = True
        self.state = "DEPLOYED"

    async def undeploy(self) -> None:
        self.undeployed = True
        self.state = "UNDEPLOYED"

    async def wait_connected(self) -> None:
        pass

    def get_rpc_connection(self) -> FakeRpcConnection:
        return self._rpc

    def get_streaming_connection(self) -> Any:
        # Not used in tests below; provide a MagicMock so connect() doesn't crash.
        return MagicMock()


class FakeAccountApi:
    def __init__(self) -> None:
        self._account = FakeAccount()

    async def get_account(self, account_id: str) -> FakeAccount:
        return self._account


class FakeMetaApi:
    """Top-level MetaApi SDK fake — installed via sys.modules monkeypatch."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.metatrader_account_api = FakeAccountApi()

    @staticmethod
    def format_error(err: Exception) -> str:
        return str(err)


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_metaapi_module(monkeypatch):
    """Install a fake `metaapi_cloud_sdk` module so the adapter's lazy import works."""
    import sys
    import types

    fake_module = types.ModuleType("metaapi_cloud_sdk")
    fake_module.MetaApi = FakeMetaApi
    monkeypatch.setitem(sys.modules, "metaapi_cloud_sdk", fake_module)
    return fake_module


@pytest.fixture
def configured_config(tmp_path, monkeypatch):
    """A HermesConfig with `metaapi` venue enabled + valid creds."""
    # load_config is @lru_cache'd — clear it so each test sees fresh env vars.
    from hermes.core.config import load_config
    load_config.cache_clear()

    # Set env vars BEFORE load_config reads them.
    monkeypatch.setenv("METAAPI_API_TOKEN", "eyJfake-token-for-testing")
    monkeypatch.setenv("METAAPI_ACCOUNT_ID", "3b84eb58-9aee-48b6-9b63-00d13eefd797")
    monkeypatch.setenv("METAAPI_REGION", "vint-hill")

    config = load_config()
    # Force-enable the metaapi venue for this test
    if "metaapi" in config.venues:
        config.venues["metaapi"].enabled = True

    # Auto-clear on teardown so subsequent tests get a fresh load
    yield config
    load_config.cache_clear()


# ────────────────────────────────────────────────────────────────────────────
# Venue enum + config plumbing
# ────────────────────────────────────────────────────────────────────────────


def test_venue_metaapi_enum_registered():
    """Venue.METAAPI exists with the right string value."""
    from hermes.schemas.market import Venue
    assert Venue.METAAPI.value == "metaapi"
    assert Venue("metaapi") is Venue.METAAPI


def test_config_has_metaapi_venue_block():
    """config/default.yaml has a metaapi venue block with the expected keys."""
    from hermes.core.config import load_config
    config = load_config()
    assert "metaapi" in config.venues
    v = config.venues["metaapi"]
    assert "metaapi_token" in v.credentials
    assert "metaapi_account_id" in v.credentials
    assert "MT4" in v.features.get("supported_exchanges", [])
    assert "MT5" in v.features.get("supported_exchanges", [])


def test_env_example_has_metaapi_entries():
    """.env.example documents METAAPI_API_TOKEN + METAAPI_ACCOUNT_ID."""
    from pathlib import Path
    env_example = Path(__file__).parent.parent / ".env.example"
    text = env_example.read_text()
    assert "METAAPI_API_TOKEN=" in text
    assert "METAAPI_ACCOUNT_ID=" in text


# ────────────────────────────────────────────────────────────────────────────
# Construction + symbol normalization
# ────────────────────────────────────────────────────────────────────────────


def test_adapter_constructs_unconfigured_when_creds_missing(monkeypatch):
    """Adapter with empty/placeholder creds marks itself _configured=False."""
    # delenv (not setenv to "") so the secrets resolver sees no value
    for k in ("METAAPI_API_TOKEN", "METAAPI_ACCOUNT_ID", "METAAPI_REGION"):
        monkeypatch.delenv(k, raising=False)
    from hermes.core.config import load_config
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    load_config.cache_clear()  # ensure we see the missing env vars
    config = load_config()
    adapter = MetaApiAdapter(config)
    # With no creds in env, _configured should be False
    assert adapter._configured is False
    load_config.cache_clear()  # reset for subsequent tests


def test_adapter_constructs_configured_when_creds_present(configured_config, fake_metaapi_module):
    """Adapter with real creds marks itself _configured=True."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter
    adapter = MetaApiAdapter(configured_config)
    assert adapter._configured is True


def test_normalize_symbol_strips_qualifier(configured_config, fake_metaapi_module):
    """MT5:EURUSD → EURUSD; COINBASE:BTCUSD → BTCUSD; EURUSD → EURUSD."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter
    adapter = MetaApiAdapter(configured_config)
    assert adapter.normalize_symbol("MT5:EURUSD") == "EURUSD"
    assert adapter.normalize_symbol("COINBASE:BTCUSD") == "BTCUSD"
    assert adapter.normalize_symbol("EURUSD") == "EURUSD"
    assert adapter.normalize_symbol("XAUUSD") == "XAUUSD"


# ────────────────────────────────────────────────────────────────────────────
# connect / disconnect
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_connect_happy_path(configured_config, fake_metaapi_module):
    """connect() deploys account + opens RPC connection + waits for sync."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    assert adapter._api is not None
    assert adapter._account is not None
    assert adapter._rpc is not None
    assert adapter._rpc.connected is True


@pytest.mark.asyncio
async def test_connect_with_undeployed_account(configured_config, fake_metaapi_module):
    """connect() deploys the account if it's not already DEPLOYED."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    # Pre-set account state to UNDEPLOYED by patching FakeAccount default
    # (FakeAccount defaults to DEPLOYED, so simulate undeployed by overriding state)
    adapter._configured = True  # bypass the early return

    # Override FakeAccount default state to UNDEPLOYED via monkeypatch on the class
    original_init = FakeAccount.__init__
    def undeployed_init(self):
        original_init(self)
        self.state = "UNDEPLOYED"
    FakeAccount.__init__ = undeployed_init
    try:
        await adapter.connect()
        assert adapter._account.deployed is True
        assert adapter._owned_deploy is True
    finally:
        FakeAccount.__init__ = original_init


@pytest.mark.asyncio
async def test_disconnect_closes_rpc(configured_config, fake_metaapi_module):
    """disconnect() closes the RPC connection."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    rpc = adapter._rpc
    await adapter.disconnect()
    assert rpc.closed is True
    assert adapter._rpc is None


# ────────────────────────────────────────────────────────────────────────────
# Market data
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_current_price_returns_last(configured_config, fake_metaapi_module):
    """get_current_price returns the 'last' field from get_symbol_price."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    price = await adapter.get_current_price("EURUSD")
    assert price == 1.0850


@pytest.mark.asyncio
async def test_get_current_price_returns_mid_when_no_last(configured_config, fake_metaapi_module):
    """When 'last' is missing, falls back to (bid+ask)/2."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    # Patch the symbol price to omit 'last'
    adapter._rpc._symbol_price["EURUSD"] = {"bid": 1.0840, "ask": 1.0860}
    price = await adapter.get_current_price("EURUSD")
    assert price == pytest.approx(1.0850)


@pytest.mark.asyncio
async def test_get_current_price_returns_none_when_not_connected(configured_config, fake_metaapi_module):
    """When not connected, returns None (matches alpaca/hl pattern)."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    # Don't call connect()
    price = await adapter.get_current_price("EURUSD")
    assert price is None


# ────────────────────────────────────────────────────────────────────────────
# submit_order — market orders
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_market_buy_fills_and_records_slippage(configured_config, fake_metaapi_module):
    """Market BUY: SDK create_market_buy_order called + order FILLED + fill emitted."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    order = Order(
        trade_id="meta-1",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=0.1,
    )

    fills_seen: list = []
    events_seen: list = []
    adapter.set_callbacks(
        event_callback=lambda oid, ev: events_seen.append((oid, ev)),
        fill_callback=lambda f: fills_seen.append(f),
    )

    # current_price=1.0850 (mid); broker fills at ask=1.0851 → 0.92 bps slippage
    await adapter.submit_order(order, current_price=1.0850, annualized_vol=0.10)

    assert order.status == OrderStatus.FILLED
    assert order.qty_filled == 0.1
    assert order.avg_fill_price == 1.0851  # filled at ask
    assert order.venue_order_id == "test-order-1"

    # SDK was called with right args
    call_names = [c[0] for c in adapter._rpc.calls]
    assert "create_market_buy_order" in call_names
    buy_call = next(c for c in adapter._rpc.calls if c[0] == "create_market_buy_order")
    assert buy_call[1][0] == "EURUSD"  # symbol
    assert buy_call[1][1] == 0.1       # volume

    # Fill + event callbacks fired
    assert len(fills_seen) == 1
    assert fills_seen[0].price == 1.0851
    assert fills_seen[0].side == OrderSide.BUY
    assert fills_seen[0].venue_fill_id == "test-order-1"
    assert fills_seen[0].slippage_bps > 0  # buy at ask > mid
    assert len(events_seen) >= 2  # SUBMITTED + fill event

    # Stats updated
    stats = adapter.get_stats()
    assert stats["orders_submitted"] == 1
    assert stats["orders_filled"] == 1


@pytest.mark.asyncio
async def test_submit_market_sell_fills_at_bid(configured_config, fake_metaapi_module):
    """Market SELL: filled at bid (1.0849), slippage negative for seller."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    order = Order(
        trade_id="meta-2",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=0.1,
    )

    await adapter.submit_order(order, current_price=1.0850, annualized_vol=0.10)

    assert order.status == OrderStatus.FILLED
    assert order.avg_fill_price == 1.0849  # filled at bid

    call_names = [c[0] for c in adapter._rpc.calls]
    assert "create_market_sell_order" in call_names


# ────────────────────────────────────────────────────────────────────────────
# submit_order — pending orders (limit / stop / post_only)
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_limit_buy_stays_pending(configured_config, fake_metaapi_module):
    """LIMIT BUY: SDK create_limit_buy_order called; order stays in SUBMITTED."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    order = Order(
        trade_id="meta-3",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        qty_requested=0.1,
        price_limit=1.0800,
    )

    await adapter.submit_order(order, current_price=1.0850, annualized_vol=0.10)

    # Pending — not FILLED, not REJECTED
    assert order.status == OrderStatus.SUBMITTED
    assert order.venue_order_id == "test-order-1"
    assert order.qty_filled == 0.0  # no fill yet

    call_names = [c[0] for c in adapter._rpc.calls]
    assert "create_limit_buy_order" in call_names
    limit_call = next(c for c in adapter._rpc.calls if c[0] == "create_limit_buy_order")
    assert limit_call[1][2] == 1.0800  # limit price


@pytest.mark.asyncio
async def test_submit_limit_requires_price_limit(configured_config, fake_metaapi_module):
    """LIMIT order without price_limit raises MetaApiExecutorError → REJECTED."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    order = Order(
        trade_id="meta-4",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        qty_requested=0.1,
        # no price_limit
    )

    await adapter.submit_order(order, current_price=1.0850, annualized_vol=0.10)

    assert order.status == OrderStatus.REJECTED
    stats = adapter.get_stats()
    assert stats["orders_rejected"] == 1


# ────────────────────────────────────────────────────────────────────────────
# submit_order — broker rejections + SDK exceptions
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broker_rejection_transitions_to_rejected(configured_config, fake_metaapi_module):
    """Broker returns numericCode 10004 (REJECT) → order REJECTED."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    adapter._rpc.set_next_result({
        "numericCode": 10004,  # TRADE_RETCODE_REJECTED
        "stringCode": "TRADE_RETCODE_REJECTED",
        "orderId": None,
        "positionId": None,
    })

    order = Order(
        trade_id="meta-5",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=0.1,
    )

    await adapter.submit_order(order, current_price=1.0850)

    assert order.status == OrderStatus.REJECTED
    assert adapter.get_stats()["orders_rejected"] == 1


@pytest.mark.asyncio
async def test_sdk_exception_transitions_to_rejected(configured_config, fake_metaapi_module):
    """SDK raises mid-call → order REJECTED + error logged."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    # Patch create_market_buy_order to raise
    async def boom(*a, **kw):
        raise RuntimeError("simulated SDK failure")
    adapter._rpc.create_market_buy_order = boom

    order = Order(
        trade_id="meta-6",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=0.1,
    )

    await adapter.submit_order(order, current_price=1.0850)

    assert order.status == OrderStatus.REJECTED
    assert adapter.get_stats()["orders_rejected"] == 1


@pytest.mark.asyncio
async def test_submit_before_connect_raises(configured_config, fake_metaapi_module):
    """submit_order before connect() raises MetaApiExecutorError."""
    from hermes.execution.orders import Order, OrderSide, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter, MetaApiExecutorError

    adapter = MetaApiAdapter(configured_config)
    # Bypass connect()

    order = Order(
        trade_id="meta-7",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=0.1,
    )

    with pytest.raises(MetaApiExecutorError):
        await adapter.submit_order(order, current_price=1.0850)


# ────────────────────────────────────────────────────────────────────────────
# cancel_order
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_pending_limit_order(configured_config, fake_metaapi_module):
    """cancel_order on a pending LIMIT order calls SDK cancel_order + transitions to CANCELED."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    order = Order(
        trade_id="meta-8",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        time_in_force=TimeInForce.GTC,
        qty_requested=0.1,
        price_limit=1.0800,
    )
    await adapter.submit_order(order, current_price=1.0850)
    assert order.status == OrderStatus.SUBMITTED

    ok = await adapter.cancel_order(order.order_id)
    assert ok is True
    assert order.status == OrderStatus.CANCELED
    assert adapter.get_stats()["orders_canceled"] == 1

    call_names = [c[0] for c in adapter._rpc.calls]
    assert "cancel_order" in call_names


@pytest.mark.asyncio
async def test_cancel_terminal_order_returns_false(configured_config, fake_metaapi_module):
    """cancel_order on a FILLED order returns False (nothing to cancel)."""
    from hermes.execution.orders import Order, OrderSide, OrderStatus, OrderType, TimeInForce
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()

    order = Order(
        trade_id="meta-9",
        symbol="EURUSD",
        venue="metaapi",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.IOC,
        qty_requested=0.1,
    )
    await adapter.submit_order(order, current_price=1.0850)
    assert order.status == OrderStatus.FILLED

    ok = await adapter.cancel_order(order.order_id)
    assert ok is False


@pytest.mark.asyncio
async def test_cancel_unknown_order_returns_false(configured_config, fake_metaapi_module):
    """cancel_order with an unknown order_id returns False."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    ok = await adapter.cancel_order("nonexistent-order-id")
    assert ok is False


# ────────────────────────────────────────────────────────────────────────────
# get_position / get_account_information
# ────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_information(configured_config, fake_metaapi_module):
    """get_account_information passes through to the SDK."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    info = await adapter.get_account_information()
    assert info is not None
    assert info["balance"] == 10000.0
    assert info["currency"] == "USD"


@pytest.mark.asyncio
async def test_get_position_returns_match(configured_config, fake_metaapi_module):
    """get_position finds the position by ID in the SDK's positions list."""
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    adapter = MetaApiAdapter(configured_config)
    await adapter.connect()
    adapter._rpc.set_positions([
        {"id": "111", "symbol": "EURUSD", "volume": 0.1, "type": "POSITION_TYPE_BUY"},
        {"id": "222", "symbol": "XAUUSD", "volume": 0.05, "type": "POSITION_TYPE_SELL"},
    ])

    pos = await adapter.get_position("222")
    assert pos is not None
    assert pos["symbol"] == "XAUUSD"
    assert pos["type"] == "POSITION_TYPE_SELL"

    missing = await adapter.get_position("999")
    assert missing is None


# ────────────────────────────────────────────────────────────────────────────
# ExecutionEngine venue dispatch (orchestrator integration)
# ────────────────────────────────────────────────────────────────────────────


def test_orchestrator_init_registers_live_executor_when_not_paper(configured_config, fake_metaapi_module):
    """ExecutionEngine(paper_mode=False) registers a MetaApi live executor."""
    from hermes.execution.orchestrator import ExecutionEngine
    from hermes.portfolio.state import PortfolioStateService

    portfolio_state = PortfolioStateService(initial_equity=10000.0, config_hash="test")
    engine = ExecutionEngine(configured_config, portfolio_state, paper_mode=False)

    assert "metaapi" in engine._live_executors
    assert "mt4_mt5" in engine._live_executors  # alias
    assert engine._live_executors["metaapi"] is engine._live_executors["mt4_mt5"]


def test_orchestrator_init_skips_live_executors_in_paper_mode(configured_config, fake_metaapi_module):
    """ExecutionEngine(paper_mode=True) does NOT register any live executors."""
    from hermes.execution.orchestrator import ExecutionEngine
    from hermes.portfolio.state import PortfolioStateService

    portfolio_state = PortfolioStateService(initial_equity=10000.0, config_hash="test")
    engine = ExecutionEngine(configured_config, portfolio_state, paper_mode=True)

    assert engine._live_executors == {}


def test_orchestrator_select_executor_picks_live_for_metaapi_venue(configured_config, fake_metaapi_module):
    """_select_executor returns the MetaApi adapter for venue='metaapi' when not paper."""
    from hermes.execution.orchestrator import ExecutionEngine
    from hermes.portfolio.state import PortfolioStateService
    from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

    portfolio_state = PortfolioStateService(initial_equity=10000.0, config_hash="test")
    engine = ExecutionEngine(configured_config, portfolio_state, paper_mode=False)

    executor = engine._select_executor("metaapi")
    assert isinstance(executor, MetaApiAdapter)

    # paper_mode=False but venue not in live_executors → paper fallback
    paper_executor = engine._select_executor("alpaca")
    assert paper_executor is engine._paper_engine


def test_orchestrator_select_executor_paper_mode_always_returns_paper(configured_config, fake_metaapi_module):
    """In paper_mode=True, _select_executor always returns paper_engine (even for metaapi venue)."""
    from hermes.execution.orchestrator import ExecutionEngine
    from hermes.portfolio.state import PortfolioStateService

    portfolio_state = PortfolioStateService(initial_equity=10000.0, config_hash="test")
    engine = ExecutionEngine(configured_config, portfolio_state, paper_mode=True)

    assert engine._select_executor("metaapi") is engine._paper_engine
    assert engine._select_executor("mt4_mt5") is engine._paper_engine
