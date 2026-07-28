"""
MetaApi venue adapter + live executor — MT4/MT5 via metaapi.cloud SDK.

Two roles in one class:
  1. ``VenueAdapter`` ABC impl  — market data (stream_ticks,
     stream_order_book, fetch_historical_bars, get_current_price,
     normalize_symbol, connect, disconnect).
  2. Live execution engine      — trade submission (submit_order,
     cancel_order, get_position, get_account_information,
     set_callbacks, get_order, get_all_orders, get_fills, get_stats).

The executor surface mirrors ``PaperTradingEngine`` so
``ExecutionEngine`` can swap paper for live behind a venue-keyed dispatch
(see ``execution/orchestrator.py``).

SDK docs:
  - Client:    https://metaapi.cloud/docs/client/
  - Streaming: https://github.com/metaapi/metaapi-python-sdk/blob/main/docs/metaApi/streamingApi.rst

Design notes:
  - ``metaapi_cloud_sdk`` is lazy-imported inside ``connect()`` /
    ``stream_ticks()`` so the module imports cleanly when the SDK isn't
    installed (matches ``alpaca_adapter.py``'s httpx pattern). Tests use
    monkeypatched fakes; production installs the SDK via
    ``pip install metaapi-cloud-sdk``.
  - Uses the RPC connection for trade execution + history (matches
    the official example.py pattern) and the streaming connection for
    tick streaming.
  - Result codes follow MT5 ``TRADE_RETCODE_*`` constants. Success codes
    are 10009 (DONE), 10008 (PLACED), 10010 (DONE_PARTIAL), 10011 (DONE_ORDER_NOT_ADDED),
    10012 (REQUOTE). Anything else → REJECTED.
  - Symbol normalization strips the ``COINBASE:`` / ``MT5:`` qualifier
    so the bare MT5 symbol (``EURUSD``, ``XAUUSD``) is passed to the SDK.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

from hermes.core.config import HermesConfig
from hermes.execution.orders import (
    Fill,
    Order,
    OrderEvent,
    OrderSide,
    OrderStatus,
    OrderStateMachine,
    OrderType,
    TimeInForce,
)
from hermes.execution.slippage import SlippageModeler
from hermes.schemas.market import (
    Bar,
    OrderBookL2,
    OrderBookLevel,
    Side,
    Tick,
    Venue,
)
from hermes.transport.adapters.base import VenueAdapter

log = structlog.get_logger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Errors
# ────────────────────────────────────────────────────────────────────────────


class MetaApiExecutorError(Exception):
    """Raised when the MetaApi SDK returns a non-success result code or
    the adapter is misconfigured (e.g. submit_order before connect)."""


# ────────────────────────────────────────────────────────────────────────────
# Mappings
# ────────────────────────────────────────────────────────────────────────────

# Hermes timeframe → MetaApi MT5 timeframe string.
# MT5 supports: 1m, 2m, 3m, 4m, 5m, 6m, 10m, 12m, 15m, 20m, 30m,
#               1h, 2h, 3h, 4h, 6h, 8h, 12h, 1d, 1w, 1mn (monthly).
TIMEFRAME_MAP: dict[str, str] = {
    "1m": "1m",
    "2m": "2m",
    "3m": "3m",
    "4m": "4m",
    "5m": "5m",
    "6m": "6m",
    "10m": "10m",
    "12m": "12m",
    "15m": "15m",
    "20m": "20m",
    "30m": "30m",
    "1h": "1h",
    "2h": "2h",
    "3h": "3h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1d",
    "1w": "1w",
    "1mn": "1mn",
}

# Hermes TimeInForce → MetaApi ORDER_TIME_*.
# MT5 only supports GTC, IOC, DAY (FOK is rare; map to IOC as a safe fallback).
TIF_MAP: dict[TimeInForce, str] = {
    TimeInForce.GTC: "ORDER_TIME_GTC",
    TimeInForce.IOC: "ORDER_TIME_IOC",
    TimeInForce.FOK: "ORDER_TIME_IOC",  # MT5 has no FOK; IOC is closest
    TimeInForce.DAY: "ORDER_TIME_DAY",
}

# MT5 TRADE_RETCODE_* success codes (anything else → REJECTED).
# Reference: https://metaapi.cloud/docs/client/models/TradeRecode/
_SUCCESS_CODES: frozenset[int] = frozenset({
    10008,  # TRADE_RETCODE_PLACED
    10009,  # TRADE_RETCODE_DONE
    10010,  # TRADE_RETCODE_DONE_PARTIAL
    10011,  # TRADE_RETCODE_DONE_ORDER_NOT_ADDED
    10012,  # TRADE_RETCODE_REQUOTE (price moved but order may still be valid)
})


# ────────────────────────────────────────────────────────────────────────────
# Adapter / Executor
# ────────────────────────────────────────────────────────────────────────────


class MetaApiAdapter(VenueAdapter):
    """MetaApi (MT4/MT5) venue adapter + live execution engine.

    Usage::

        adapter = MetaApiAdapter(config)
        await adapter.connect()
        # Market data:
        async for tick in adapter.stream_ticks(["EURUSD", "XAUUSD"]):
            ...
        # Live execution:
        await adapter.submit_order(order, current_price=1.0850, annualized_vol=0.10)
        await adapter.cancel_order(order.order_id)

    Config keys (read from ``config.venues.metaapi.credentials``)::

        metaapi_token:       secret:metaapi.api_token
        metaapi_account_id:  secret:metaapi.account_id
        metaapi_region:      secret:metaapi.region        # optional (e.g. "vint-hill")
    """

    venue = Venue.METAAPI

    # Polling interval for stream_ticks / stream_order_book (seconds).
    # MetaApi streaming emits events via subscription, but reliable polling
    # of get_symbol_price at 1Hz is the simplest cross-version pattern.
    _TICK_POLL_SEC: float = 1.0

    def __init__(self, config: HermesConfig) -> None:
        venue_config = config.venues.get("metaapi", {})
        creds = venue_config.credentials

        self._token = creds.get("metaapi_token", "") or creds.get("api_token", "")
        self._account_id = creds.get("metaapi_account_id", "") or creds.get("account_id", "")
        self._region = creds.get("metaapi_region", "") or creds.get("region", "")

        # Detect: (a) empty, (b) "<...>" placeholder patterns from .env.example,
        # (c) unresolved "secret:..." references (returned by the secrets
        # resolver when the env var is missing — see core/secrets.py).
        def _is_real(v: str) -> bool:
            return bool(v) and "<" not in v and not v.startswith("secret:")

        self._configured = _is_real(self._token) and _is_real(self._account_id)

        # SDK handles — populated by connect()
        self._api: Any | None = None        # MetaApi instance
        self._account: Any | None = None    # MetatraderAccount
        self._rpc: Any | None = None        # RPC connection (trade + history)
        self._streaming: Any | None = None  # Streaming connection (ticks)
        self._owned_deploy: bool = False    # True if WE deployed (so we undeploy on disconnect)

        # Executor bookkeeping (mirror PaperTradingEngine so ExecutionEngine
        # can swap paper <-> live transparently).
        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._event_callback: Any = None
        self._fill_callback: Any = None
        self._slippage = SlippageModeler()
        self._stats: dict[str, Any] = {
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_canceled": 0,
            "orders_rejected": 0,
            "total_fees": 0.0,
            "total_slippage_bps": 0.0,
        }

    # ────────────────────────────────────────────────────────────────────
    # VenueAdapter: connection lifecycle
    # ────────────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Establish MetaApi SDK connections (RPC + streaming on demand).

        Idempotent — safe to call multiple times. Deploys the broker
        account if not already deployed, waits for sync, then opens the
        RPC connection. The streaming connection is opened lazily inside
        ``stream_ticks()``.
        """
        if not self._configured:
            log.warning(
                "metaapi_not_configured",
                note="token/account_id missing or placeholders in .env",
            )
            return

        if self._rpc is not None:
            return  # already connected

        from metaapi_cloud_sdk import MetaApi  # lazy import

        try:
            self._api = MetaApi(self._token)
            self._account = await self._api.metatrader_account_api.get_account(self._account_id)

            initial_state = self._account.state
            if initial_state not in ("DEPLOYING", "DEPLOYED"):
                log.info("metaapi_deploying_account", account_id=self._account_id)
                await self._account.deploy()
                self._owned_deploy = True

            log.info(
                "metaapi_waiting_for_broker_sync",
                account_id=self._account_id,
                note="this can take 1-3 minutes on first deploy",
            )
            await self._account.wait_connected()

            self._rpc = self._account.get_rpc_connection()
            await self._rpc.connect()
            await self._rpc.wait_synchronized()

            log.info(
                "metaapi_connected",
                account_id=self._account_id,
                broker=getattr(self._account, "broker", None),
                server=getattr(self._account, "server", None),
                currency=getattr(self._account, "currency", None),
                region=self._region or None,
            )
        except Exception as e:
            log.error(
                "metaapi_connect_failed",
                account_id=self._account_id,
                error=str(e)[:300],
            )
            # Reset partial state so connect() can be retried cleanly
            self._rpc = None
            self._account = None
            self._api = None
            raise

    async def disconnect(self) -> None:
        """Close RPC + streaming connections. Undeploys the broker account
        if WE deployed it (leaves pre-deployed accounts alone)."""
        # Close streaming
        if self._streaming is not None:
            try:
                await self._streaming.close()
            except Exception as e:
                log.warning("metaapi_streaming_close_failed", error=str(e)[:120])
            self._streaming = None

        # Close RPC
        if self._rpc is not None:
            try:
                await self._rpc.close()
            except Exception as e:
                log.warning("metaapi_rpc_close_failed", error=str(e)[:120])
            self._rpc = None

        # Undeploy only if WE deployed
        if self._account is not None and self._owned_deploy:
            try:
                await self._account.undeploy()
            except Exception as e:
                log.warning("metaapi_undeploy_failed", error=str(e)[:120])

        self._account = None
        self._api = None
        self._owned_deploy = False

    # ────────────────────────────────────────────────────────────────────
    # VenueAdapter: symbol normalization
    # ────────────────────────────────────────────────────────────────────

    def normalize_symbol(self, hermes_symbol: str) -> str:
        """Convert Hermes symbol to MT5 native form.

        Hermes symbols may carry an exchange qualifier (``COINBASE:BTCUSD``,
        ``MT5:EURUSD``). MetaApi expects the bare MT5 symbol (``EURUSD``,
        ``XAUUSD``, ``BTCUSD``). Strip the qualifier if present.

        Does NOT uppercase — MT5 symbols are case-sensitive and usually
        already uppercase; callers should preserve the original case.
        """
        if ":" in hermes_symbol:
            return hermes_symbol.split(":", 1)[1]
        return hermes_symbol

    # ────────────────────────────────────────────────────────────────────
    # VenueAdapter: market data
    # ────────────────────────────────────────────────────────────────────

    async def get_current_price(self, symbol: str) -> float | None:
        """Latest price for ``symbol`` from the RPC connection.

        Returns the ``last`` trade price if available, else the midpoint
        of bid/ask. Returns ``None`` on any error (matches alpaca/hl).
        """
        if not self._rpc:
            log.error("metaapi_not_connected", method="get_current_price")
            return None

        sym = self.normalize_symbol(symbol)
        try:
            price = await self._rpc.get_symbol_price(sym)
            if isinstance(price, dict):
                last = price.get("last")
                if last is not None:
                    return float(last)
                bid = price.get("bid")
                ask = price.get("ask")
                if bid is not None and ask is not None:
                    return (float(bid) + float(ask)) / 2
            elif isinstance(price, (int, float)):
                return float(price)
            return None
        except Exception as e:
            log.error("metaapi_get_price_failed", symbol=sym, error=str(e)[:200])
            return None

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[Bar]:
        """Fetch OHLCV bars from MetaApi ``get_candles``.

        Returns bars sorted by ``ts_open`` ascending, oldest first.
        Returns ``[]`` on any error (matches alpaca/hl pattern).
        """
        if not self._rpc:
            log.error("metaapi_not_connected", method="fetch_historical_bars")
            return []

        sym = self.normalize_symbol(symbol)
        tf = TIMEFRAME_MAP.get(timeframe, timeframe)
        try:
            # MetaApi signature: get_candles(symbol, timeframe, start, end)
            candles = await self._rpc.get_candles(sym, tf, start, end)
            if not isinstance(candles, list):
                return []

            bars: list[Bar] = []
            for c in candles[-limit:]:
                ts_raw = c.get("time")
                # MetaApi returns time in seconds (brokerTime) OR ms (time)
                if ts_raw is None:
                    continue
                if ts_raw > 10**12:  # ms
                    ts_open = datetime.fromtimestamp(ts_raw / 1000, tz=timezone.utc)
                else:
                    ts_open = datetime.fromtimestamp(ts_raw, tz=timezone.utc)

                bars.append(Bar(
                    ts_open=ts_open,
                    ts_close=None,
                    venue=self.venue,
                    symbol=sym,
                    timeframe=timeframe,
                    open=float(c["open"]),
                    high=float(c["high"]),
                    low=float(c["low"]),
                    close=float(c["close"]),
                    volume=float(c.get("volume", 0) or 0),
                    vwap=None,
                    n_trades=None,
                    closed=True,
                ))
            bars.sort(key=lambda b: b.ts_open)
            return bars
        except Exception as e:
            log.error("metaapi_fetch_bars_failed", symbol=sym, error=str(e)[:200])
            return []

    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """Stream ticks via the MetaApi streaming connection.

        Opens the streaming connection (if not already open), subscribes
        to market data for each symbol, then polls ``get_symbol_price``
        at 1Hz and yields ``Tick`` objects. Yields indefinitely until
        the consumer cancels the iterator.

        NOTE: MetaApi's streaming API also supports event-driven
        ``on_tick`` callbacks; the polling pattern here is simpler and
        version-stable. Swap to events in a future iteration if 1Hz
        is too slow for renko brick generation.
        """
        if not self._account:
            log.error("metaapi_not_connected", method="stream_ticks")
            return
            yield  # type: ignore  # pragma: no cover  - make this an async generator

        from metaapi_cloud_sdk import MetaApi  # noqa: F401  - ensures SDK present

        try:
            if self._streaming is None:
                self._streaming = self._account.get_streaming_connection()
                await self._streaming.connect()

            # Subscribe to each symbol (idempotent in SDK)
            for sym in symbols:
                norm = self.normalize_symbol(sym)
                try:
                    await self._streaming.subscribe_to_market_data(norm)
                    log.info("metaapi_subscribed", symbol=norm)
                except Exception as e:
                    log.warning(
                        "metaapi_subscribe_failed",
                        symbol=norm,
                        error=str(e)[:120],
                    )

            # Polling loop
            while True:
                for sym in symbols:
                    norm = self.normalize_symbol(sym)
                    try:
                        price = await self._streaming.get_symbol_price(norm)
                        if not isinstance(price, dict):
                            continue
                        bid = float(price.get("bid") or 0)
                        ask = float(price.get("ask") or 0)
                        last_raw = price.get("last")
                        if last_raw is not None:
                            last = float(last_raw)
                        elif bid > 0 and ask > 0:
                            last = (bid + ask) / 2
                        else:
                            continue

                        if last <= 0:
                            continue

                        yield Tick(
                            ts=datetime.now(timezone.utc),
                            venue=self.venue,
                            symbol=sym,
                            price=last,
                            size=0.0,
                            side=None,
                            trade_id=None,
                        )
                    except Exception as e:
                        log.warning(
                            "metaapi_tick_poll_failed",
                            symbol=norm,
                            error=str(e)[:100],
                        )
                await asyncio.sleep(self._TICK_POLL_SEC)
        except asyncio.CancelledError:
            log.info("metaapi_tick_stream_cancelled")
            raise
        except Exception as e:
            log.error("metaapi_tick_stream_error", error=str(e)[:200])

    async def stream_order_book(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        """Stream a synthetic L2 book from MT5 bid/ask.

        MT5 doesn't expose a true L2 order book (only L1 bid/ask).
        This yields a 1-level book (best bid + best ask) per poll.
        """
        if not self._rpc:
            log.error("metaapi_not_connected", method="stream_order_book")
            return
            yield  # type: ignore  # pragma: no cover

        try:
            while True:
                for sym in symbols:
                    norm = self.normalize_symbol(sym)
                    try:
                        price = await self._rpc.get_symbol_price(norm)
                        if not isinstance(price, dict):
                            continue
                        bid = float(price.get("bid") or 0)
                        ask = float(price.get("ask") or 0)
                        if bid <= 0 or ask <= 0:
                            continue

                        yield OrderBookL2(
                            ts=datetime.now(timezone.utc),
                            venue=self.venue,
                            symbol=sym,
                            bids=[OrderBookLevel(price=bid, size=0.0)],
                            asks=[OrderBookLevel(price=ask, size=0.0)],
                            sequence=None,
                        )
                    except Exception as e:
                        log.warning(
                            "metaapi_book_poll_failed",
                            symbol=norm,
                            error=str(e)[:100],
                        )
                await asyncio.sleep(self._TICK_POLL_SEC)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("metaapi_book_stream_error", error=str(e)[:200])

    # ────────────────────────────────────────────────────────────────────
    # Live executor — PaperTradingEngine-compatible contract
    # ────────────────────────────────────────────────────────────────────

    def set_callbacks(
        self,
        event_callback: Any = None,
        fill_callback: Any = None,
    ) -> None:
        """Set async callbacks for order events and fills.

        Mirrors ``PaperTradingEngine.set_callbacks`` so the
        ``ExecutionEngine`` orchestrator can wire its event/fill handlers
        to either engine transparently.
        """
        self._event_callback = event_callback
        self._fill_callback = fill_callback

    async def submit_order(
        self,
        order: Order,
        current_price: float,
        annualized_vol: float = 0.60,
    ) -> None:
        """Submit an order to the live MT4/MT5 broker via MetaApi.

        Maps ``Order.order_type`` + ``Order.side`` to the appropriate
        ``create_*`` SDK call. Market orders fill immediately (we fetch
        the fill price from ``get_symbol_price`` post-submit). Limit /
        stop orders stay in SUBMITTED state until filled or canceled.

        Args:
            order: The order to submit (mutated in-place — sets
                ``venue_order_id``, ``status``, ``qty_filled``,
                ``avg_fill_price``, ``total_fees``, ``total_slippage``).
            current_price: Arrival price (best bid/ask at decision time).
            annualized_vol: Used for slippage bookkeeping (MT5 fills at
                broker price; we record the gap as realized slippage).
        """
        if not self._rpc:
            raise MetaApiExecutorError(
                "MetaApi RPC connection not established — call connect() first"
            )

        self._orders[order.order_id] = order
        self._stats["orders_submitted"] += 1

        # DRAFT → SUBMITTED
        event = OrderStateMachine.transition(
            order, OrderStatus.SUBMITTED,
            {"arrival_price": current_price},
        )
        await self._emit_event(order.order_id, event)

        sym = self.normalize_symbol(order.symbol)
        volume = order.qty_requested
        limit_price = order.price_limit
        sl: float | None = None  # SL is enforced by Hermes position watcher, not broker-side
        tp: float | None = None

        try:
            result = await self._dispatch_create(
                order.order_type, order.side, sym, volume, limit_price, sl, tp,
            )
        except MetaApiExecutorError as e:
            # Validation error (missing price_limit, unsupported order_type) —
            # treat as REJECTED so the order doesn't hang in SUBMITTED forever.
            await self._handle_submit_exception(order, sym, e)
            return
        except Exception as e:
            await self._handle_submit_exception(order, sym, e)
            return

        if not isinstance(result, dict):
            await self._handle_submit_exception(
                order, sym,
                MetaApiExecutorError(f"Unexpected SDK result type: {type(result)}"),
            )
            return

        numeric_code = int(result.get("numericCode", 0))
        string_code = str(result.get("stringCode", ""))
        order.venue_order_id = str(
            result.get("orderId") or result.get("positionId") or ""
        )

        if numeric_code not in _SUCCESS_CODES:
            # Rejected by broker — transition to REJECTED
            self._stats["orders_rejected"] += 1
            try:
                reject_event = OrderStateMachine.transition(
                    order, OrderStatus.REJECTED,
                    {
                        "numeric_code": numeric_code,
                        "string_code": string_code,
                        "result": result,
                    },
                )
                await self._emit_event(order.order_id, reject_event)
            except ValueError:
                pass  # already terminal
            log.warning(
                "metaapi_order_rejected",
                order_id=order.order_id,
                symbol=sym,
                numeric_code=numeric_code,
                string_code=string_code,
            )
            return

        # Successfully placed. Market orders fill immediately; limit/stop are pending.
        if order.order_type in (OrderType.MARKET, OrderType.ICEBERG):
            fill_price = await self._fetch_fill_price(sym, order.side, current_price)
            slip_bps = SlippageModeler.compute_actual_slippage_bps(
                arrival_price=current_price,
                fill_price=fill_price,
                side=order.side.value,
            )
            # MT5 commission comes via account info, not per-trade; record 0 here
            # and let the post-fill reconciliation pick it up from get_positions.
            fee = 0.0

            fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                venue=self.venue.value,
                side=order.side,
                qty=order.qty_requested,
                price=fill_price,
                fee=fee,
                fee_currency="USD",
                is_maker=False,
                liquidity="taker",
                arrival_price=current_price,
                slippage_bps=slip_bps,
                venue_fill_id=order.venue_order_id or f"metaapi-{uuid4().hex[:8]}",
            )
            await self._apply_fill(order, fill)
            log.info(
                "metaapi_order_filled",
                order_id=order.order_id,
                symbol=sym,
                fill_price=fill_price,
                slippage_bps=slip_bps,
                venue_order_id=order.venue_order_id,
            )
        else:
            # Pending (limit/stop/post_only) — leaves order in SUBMITTED.
            # Position watcher + cancel_order will advance the state.
            log.info(
                "metaapi_order_pending",
                order_id=order.order_id,
                symbol=sym,
                venue_order_id=order.venue_order_id,
                order_type=order.order_type.value,
            )

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel a pending order or close an open position.

        Returns True if canceled/closed, False if not cancellable (already
        terminal, or unknown order). Matches ``PaperTradingEngine.cancel_order``.
        """
        order = self._orders.get(order_id)
        if not order:
            return False
        if order.status in (
            OrderStatus.FILLED, OrderStatus.CANCELED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED,
        ):
            return False
        if not self._rpc or not order.venue_order_id:
            return False

        try:
            if order.order_type in (OrderType.LIMIT, OrderType.STOP, OrderType.POST_ONLY):
                # Pending order — cancel via SDK
                await self._rpc.cancel_order(order.venue_order_id)
            else:
                # Market order that's now an open position — close it.
                # close_position(position_id, volume, opposite_side_position_id, price, deviation, options)
                await self._rpc.close_position(order.venue_order_id)

            event = OrderStateMachine.transition(
                order, OrderStatus.CANCELED,
                {"venue_order_id": order.venue_order_id},
            )
            await self._emit_event(order_id, event)
            self._stats["orders_canceled"] += 1
            log.info(
                "metaapi_order_canceled",
                order_id=order_id,
                venue_order_id=order.venue_order_id,
                order_type=order.order_type.value,
            )
            return True
        except Exception as e:
            log.error(
                "metaapi_cancel_failed",
                order_id=order_id,
                venue_order_id=order.venue_order_id,
                error=str(e)[:200],
            )
            return False

    async def get_position(self, position_id: str) -> dict[str, Any] | None:
        """Fetch a single open position by broker-side position ID."""
        if not self._rpc:
            return None
        try:
            positions = await self._rpc.get_positions()
            if not isinstance(positions, list):
                return None
            for p in positions:
                if str(p.get("id")) == str(position_id):
                    return p
            return None
        except Exception as e:
            log.error(
                "metaapi_get_position_failed",
                position_id=position_id,
                error=str(e)[:200],
            )
            return None

    async def get_account_information(self) -> dict[str, Any] | None:
        """Fetch broker account info (balance, equity, margin, currency, ...)."""
        if not self._rpc:
            return None
        try:
            return await self._rpc.get_account_information()
        except Exception as e:
            log.error("metaapi_get_account_failed", error=str(e)[:200])
            return None

    # ────────────────────────────────────────────────────────────────────
    # Mirror PaperTradingEngine introspection (so ExecutionEngine can
    # call get_order / get_all_orders / get_fills / get_stats on either).
    # ────────────────────────────────────────────────────────────────────

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_all_orders(self) -> list[Order]:
        return list(self._orders.values())

    def get_fills(self, order_id: str | None = None) -> list[Fill]:
        if order_id:
            return [f for f in self._fills if f.order_id == order_id]
        return self._fills[:]

    def get_stats(self) -> dict[str, Any]:
        return self._stats.copy()

    # ────────────────────────────────────────────────────────────────────
    # Internal helpers
    # ────────────────────────────────────────────────────────────────────

    async def _dispatch_create(
        self,
        order_type: OrderType,
        side: OrderSide,
        sym: str,
        volume: float,
        limit_price: float | None,
        sl: float | None,
        tp: float | None,
    ) -> dict[str, Any]:
        """Map (order_type, side) → SDK create_* call. Returns the SDK result dict."""
        rpc = self._rpc
        assert rpc is not None  # checked by caller

        if order_type == OrderType.MARKET:
            if side == OrderSide.BUY:
                return await rpc.create_market_buy_order(sym, volume, sl, tp)
            return await rpc.create_market_sell_order(sym, volume, sl, tp)

        if order_type == OrderType.LIMIT:
            if limit_price is None:
                raise MetaApiExecutorError(
                    f"LIMIT order requires price_limit (order.symbol={sym})"
                )
            if side == OrderSide.BUY:
                return await rpc.create_limit_buy_order(sym, volume, limit_price, sl, tp)
            return await rpc.create_limit_sell_order(sym, volume, limit_price, sl, tp)

        if order_type == OrderType.STOP:
            if limit_price is None:
                raise MetaApiExecutorError(
                    f"STOP order requires price_limit (order.symbol={sym})"
                )
            if side == OrderSide.BUY:
                return await rpc.create_stop_buy_order(sym, volume, limit_price, sl, tp)
            return await rpc.create_stop_sell_order(sym, volume, limit_price, sl, tp)

        if order_type == OrderType.POST_ONLY:
            # MT5 has no native post-only. Use a limit order priced away from
            # the bid/ask so it cannot cross — caller must set price_limit.
            if limit_price is None:
                raise MetaApiExecutorError(
                    f"POST_ONLY order requires price_limit (order.symbol={sym})"
                )
            if side == OrderSide.BUY:
                return await rpc.create_limit_buy_order(sym, volume, limit_price, sl, tp)
            return await rpc.create_limit_sell_order(sym, volume, limit_price, sl, tp)

        if order_type == OrderType.ICEBERG:
            # MT5 has no native iceberg. SmartOrderRouter already slices
            # iceberg orders into child market orders, so by the time we
            # see one here it's a single child — treat as market.
            if side == OrderSide.BUY:
                return await rpc.create_market_buy_order(sym, volume, sl, tp)
            return await rpc.create_market_sell_order(sym, volume, sl, tp)

        raise MetaApiExecutorError(f"Unsupported order_type: {order_type}")

    async def _fetch_fill_price(
        self,
        symbol: str,
        side: OrderSide,
        fallback: float,
    ) -> float:
        """Best-effort fill price lookup post-market-order.

        For a BUY, the broker fills at the ask; for a SELL, at the bid.
        If the price lookup fails, fall back to the arrival price (which
        records zero slippage — better than a fabricated number).
        """
        try:
            price = await self._rpc.get_symbol_price(symbol)  # type: ignore[union-attr]
            if isinstance(price, dict):
                if side == OrderSide.BUY:
                    ask = price.get("ask")
                    if ask is not None:
                        return float(ask)
                else:
                    bid = price.get("bid")
                    if bid is not None:
                        return float(bid)
        except Exception as e:
            log.warning(
                "metaapi_fill_price_lookup_failed",
                symbol=symbol,
                error=str(e)[:120],
            )
        return fallback

    async def _handle_submit_exception(
        self,
        order: Order,
        sym: str,
        err: Exception,
    ) -> None:
        """Transition a failed submit to REJECTED + log."""
        self._stats["orders_rejected"] += 1
        try:
            reject_event = OrderStateMachine.transition(
                order, OrderStatus.REJECTED,
                {"error": str(err)[:200]},
            )
            await self._emit_event(order.order_id, reject_event)
        except ValueError:
            pass  # already terminal
        log.error(
            "metaapi_submit_failed",
            order_id=order.order_id,
            symbol=sym,
            error=str(err)[:200],
        )

    async def _apply_fill(self, order: Order, fill: Fill) -> None:
        """Apply a fill to an order + emit events. Mirrors PaperTradingEngine."""
        self._fills.append(fill)
        self._stats["total_fees"] += fill.fee
        self._stats["total_slippage_bps"] += fill.slippage_bps

        fill_event, _ = OrderStateMachine.apply_fill(order, fill)
        await self._emit_event(order.order_id, fill_event)

        if self._fill_callback:
            res = self._fill_callback(fill)
            if asyncio.iscoroutine(res):
                await res

        if order.status == OrderStatus.FILLED:
            self._stats["orders_filled"] += 1

    async def _emit_event(self, order_id: str, event: OrderEvent) -> None:
        if self._event_callback:
            res = self._event_callback(order_id, event)
            if asyncio.iscoroutine(res):
                await res
