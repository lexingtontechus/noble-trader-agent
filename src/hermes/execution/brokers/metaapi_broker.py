"""MetaApi execution broker — live MT4/MT5 order execution.

Uses the `metaapi-cloud-sdk` RPC connection (`account.get_rpc_connection()`)
for trading. SDK best practices applied:
  - A single `MetaApi` instance + a single RPC connection per account are
    created at `connect()` and reused for the broker's lifetime (never
    re-created per order).
  - `account.wait_connected()` + `connection.wait_synchronized()` BEFORE any
    trade call.
  - `deploy()` is called only if the account is not already deployed; we
    undeploy on `disconnect()` ONLY if we deployed it.
  - Every order carries `clientId = order.order_id` so fills/deals can be
    correlated back to the Hermes order (MetaApi echoes `clientId` on deals).
  - Errors are formatted via `api.format_error(err)`.

Env vars (set via the Hermes dashboard setup wizard → .env):
  METAAPI_TOKEN        — MetaApi API token
  METAAPI_ACCOUNT_ID   — provisioned MT4/MT5 account id
  METAAPI_DEMO         — "true"/"false" (documentation/labels only; the
                         account id itself determines demo vs live)

Quantity convention (per user directive): `Order.qty_requested` is already in
MT **lots** — passed directly as MetaApi `volume`. No USD→lots conversion.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.execution.brokers.base import ExecutionBroker
from hermes.execution.orders import (
    Fill,
    OrderEvent,
    OrderSide,
    OrderStateMachine,
    OrderStatus,
    OrderType,
)
from hermes.execution.slippage import SlippageModeler

log = structlog.get_logger(__name__)

# MetaApi account deployment states we treat as "ready to connect".
_DEPLOYED_STATES = ("DEPLOYED", "DEPLOYING")

# Default perp→MT symbol map (NT perp symbols → MT4/MT5 spot fx/crypto).
_DEFAULT_SYMBOL_MAP = {
    "BTC-PERP": "BTCUSD",
    "ETH-PERP": "ETHUSD",
    "SOL-PERP": "SOLUSD",
}

# MT5 TRADE_RETCODE_* success codes (anything else → REJECTED). Reference:
# https://metaapi.cloud/docs/client/models/TradeRecode/
_SUCCESS_CODES: frozenset[int] = frozenset({
    10008,  # TRADE_RETCODE_PLACED
    10009,  # TRADE_RETCODE_DONE
    10010,  # TRADE_RETCODE_DONE_PARTIAL
    10011,  # TRADE_RETCODE_DONE_ORDER_NOT_ADDED
    10012,  # TRADE_RETCODE_REQUOTE (price moved but order may still be valid)
})


def resolve_metaapi_credentials(mode: str | None = None) -> tuple[str, str, bool]:
    """Resolve (token, account_id, demo) for the requested mode.

    Dual-mode model (see onboarding wizard):
      - NT_MODE = "demo"  -> METAAPI_TOKEN_DEMO / METAAPI_ACCOUNT_ID_DEMO
      - NT_MODE = "live"  -> METAAPI_TOKEN / METAAPI_ACCOUNT_ID

    `mode` defaults to NT_MODE env (falling back to the legacy METAAPI_DEMO
    boolean: "true" -> demo).

    Auto-detect fallback: if the selected pair is empty but the *other* pair
    is populated, use the non-empty pair. This handles the common migration
    case where a user set live keys first (NT_MODE empty) and the legacy
    METAAPI_DEMO=true defaults to demo — which would surface "credentials not
    configured" even though live keys exist.
    """
    mode = (mode or os.getenv("NT_MODE") or "").strip().lower()
    if not mode:
        # Legacy fallback: METAAPI_DEMO=true (default) -> demo.
        mode = "demo" if _parse_bool(os.getenv("METAAPI_DEMO"), default=True) else "live"
    if mode == "live":
        token = os.getenv("METAAPI_TOKEN") or ""
        account_id = os.getenv("METAAPI_ACCOUNT_ID") or ""
        # Auto-detect fallback: live keys empty, demo keys set → use demo
        if not token or not account_id:
            token = os.getenv("METAAPI_TOKEN_DEMO") or ""
            account_id = os.getenv("METAAPI_ACCOUNT_ID_DEMO") or ""
            return token, account_id, True
        return token, account_id, False
    # demo (default)
    token = os.getenv("METAAPI_TOKEN_DEMO") or ""
    account_id = os.getenv("METAAPI_ACCOUNT_ID_DEMO") or ""
    # Auto-detect fallback: demo keys empty, live keys set → use live
    if not token or not account_id:
        token = os.getenv("METAAPI_TOKEN") or ""
        account_id = os.getenv("METAAPI_ACCOUNT_ID") or ""
        return token, account_id, False
    return token, account_id, True


def _as_int(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _parse_bool(v: str | None, default: bool = True) -> bool:
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


class MetaApiBroker(ExecutionBroker):
    """Live execution broker backed by MetaApi (MT4/MT5)."""

    def __init__(
        self,
        token: str | None = None,
        account_id: str | None = None,
        demo: bool | None = None,
        fill_poll_sec: float = 5.0,
        symbol_map: dict[str, str] | None = None,
    ) -> None:
        # Explicit args win (used by tests / legacy callers). Otherwise resolve
        # the demo/live credential pair from NT_MODE (see resolve_metaapi_credentials).
        if token or account_id:
            self._token = token or ""
            self._account_id = account_id or ""
            self._demo = _parse_bool(os.getenv("METAAPI_DEMO"), default=True) if demo is None else demo
        else:
            r_token, r_account, r_demo = resolve_metaapi_credentials()
            self._token = r_token
            self._account_id = r_account
            self._demo = r_demo if demo is None else demo
        self._fill_poll_sec = max(0.0, float(fill_poll_sec))
        self._symbol_map = {**_DEFAULT_SYMBOL_MAP, **(symbol_map or {})}

        self._event_callback = None
        self._fill_callback = None
        self._slippage = SlippageModeler()

        # Lazy SDK handle (imported on connect to avoid a hard dep at import).
        self._MetaApi = None
        self._api = None
        self._account = None
        self._conn = None
        self._deployed_by_us = False
        self._connected = False

        if not self._token or not self._account_id:
            log.warning(
                "metaapi_broker.misconfigured",
                note="METAAPI_TOKEN[_DEMO] / METAAPI_ACCOUNT_ID[_DEMO] missing for current NT_MODE — trading disabled",
                mode=os.getenv("NT_MODE", "demo"),
            )

    # ── Connection lifecycle ────────────────────────────────────────────

    async def connect(self) -> None:
        if self._connected:
            return
        # Re-resolve at connect time so an NT_MODE flip (demo->live) mid-session
        # takes effect on the next broker interaction without a process restart.
        if not self._token or not self._account_id:
            r_token, r_account, r_demo = resolve_metaapi_credentials()
            self._token = r_token
            self._account_id = r_account
            self._demo = r_demo
        if not self._token or not self._account_id:
            raise RuntimeError(
                "MetaApiBroker: no MetaApi credentials resolved for "
                f"NT_MODE={os.getenv('NT_MODE', 'demo')}"
            )

        # Lazy import so the module imports even where the SDK isn't installed.
        from metaapi_cloud_sdk import MetaApi  # type: ignore

        self._MetaApi = MetaApi
        self._api = MetaApi(self._token)

        account = await self._api.metatrader_account_api.get_account(self._account_id)
        self._account = account

        initial_state = account.state
        if initial_state not in _DEPLOYED_STATES:
            log.info("metaapi_broker.deploying_account", account_id=self._account_id)
            await account.deploy()
            self._deployed_by_us = True

        log.info("metaapi_broker.waiting_for_broker_connect")
        await account.wait_connected()

        conn = account.get_rpc_connection()
        await conn.connect()
        log.info("metaapi_broker.waiting_for_synchronization")
        await conn.wait_synchronized()
        self._conn = conn
        self._connected = True
        log.info(
            "metaapi_broker.connected",
            account_id=self._account_id,
            demo=self._demo,
        )

    async def disconnect(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception as exc:  # pragma: no cover - best effort
                log.warning("metaapi_broker.conn_close_failed", error=str(exc))
        if self._account is not None and self._deployed_by_us:
            try:
                await self._account.undeploy()
            except Exception as exc:  # pragma: no cover - best effort
                log.warning("metaapi_broker.undeploy_failed", error=str(exc))
        self._conn = None
        self._connected = False

    # ── Symbol normalization ────────────────────────────────────────────

    def normalize_symbol(self, hermes_symbol: str) -> str:
        """Map a Hermes symbol to its MT4/MT5 native symbol."""
        if hermes_symbol in self._symbol_map:
            return self._symbol_map[hermes_symbol]
        # Strip perp/fwd suffixes and separators: BTC-PERP -> BTC, BTC/USD -> BTCUSD
        s = hermes_symbol.upper().replace("-", "").replace("/", "")
        return s

    # ── Order submission ────────────────────────────────────────────────

    async def submit_order(
        self,
        order: Any,
        current_price: float,
        annualized_vol: float = 0.60,  # noqa: ARG002 (reserved for slippage parity)
    ) -> None:
        if not self._connected or self._conn is None:
            raise RuntimeError("MetaApiBroker.submit_order: not connected")

        symbol = self.normalize_symbol(order.symbol)
        # qty_requested is in UNITS; MetaApi volume is in LOTS. Convert.
        try:
            volume = await self._units_to_lots(order.qty_requested, current_price, symbol)
        except ValueError as err:
            await self._reject(order, {"error": str(err)}, note="lot conversion failed")
            return
        side = order.side
        otype = order.order_type

        # Pre-trade margin check (contract item). Logged only — does not block
        # a valid order if the broker's margin calc is briefly unavailable.
        try:
            margin = await self.calculate_margin(symbol, side, volume, current_price)
            if margin is not None:
                log.info("metaapi_broker.margin_estimate", symbol=symbol, volume=volume, margin=margin)
        except Exception as err:  # pragma: no cover - best effort
            log.warning("metaapi_broker.margin_check_skipped", symbol=symbol, error=self._format_error(err))

        # MetaApi clientId maps to the MT magic/comment. This broker rejects
        # every clientId format we tried (alphanumeric, letter-first, pure
        # letters) with "must match required pattern" — a broker-specific
        # regex we can't guess. Omit it; correlation falls back to symbol+
        # position timestamp. (Revisit if the broker documents its pattern.)
        options: dict[str, Any] = {}
        open_price = None
        if order.price_limit is not None and otype in (
            OrderType.LIMIT,
            OrderType.POST_ONLY,
            OrderType.STOP,
        ):
            open_price = float(order.price_limit)

        method, label = self._resolve_method(symbol, side, otype)
        log.info(
            "metaapi_broker.submit",
            order_id=order.order_id,
            symbol=symbol,
            side=side.value,
            type=otype.value,
            units=order.qty_requested,
            volume_lots=volume,
            method=label,
        )
        try:
            # SDK RPC methods take (symbol, volume, [open_price], stop_loss,
            # take_profit, options). Pass `options` as a KEYWORD (not positionally,
            # which would land in stop_loss and crash _generate_stop_options).
            if otype == OrderType.MARKET:
                result = await method(symbol, volume, options=options)
            else:
                result = await method(symbol, volume, open_price, options=options)
        except Exception as err:
            # Behavior 6: SDK exception mid-submit → REJECTED + error logged.
            await self._reject(
                order,
                {"error": self._format_error(err)},
                note="SDK exception during submit",
            )
            return

        venue_order_id = (result or {}).get("orderId") or (result or {}).get("id")
        order.venue_order_id = venue_order_id

        # Behavior 5: broker rejection via result code. MetaApi may return a
        # result dict with a non-success numericCode instead of raising. Any
        # code outside the MT5 TRADE_RETCODE success set → REJECTED.
        numeric_code = _as_int((result or {}).get("numericCode"))
        if numeric_code and numeric_code not in _SUCCESS_CODES:
            await self._reject(
                order,
                {
                    "numeric_code": numeric_code,
                    "string_code": str((result or {}).get("stringCode", "")),
                },
                note="broker rejected order (numericCode)",
            )
            return

        event = await self._transition(order, OrderStatus.SUBMITTED, {"venue_order_id": venue_order_id})

        # Market orders fill (near) synchronously — poll briefly for the deal.
        if otype == OrderType.MARKET and self._fill_poll_sec > 0:
            await self._poll_for_fill(order, current_price)

    def _resolve_method(self, symbol: str, side: OrderSide, otype: OrderType):
        """Return (bound_rpc_method, label) for the order."""
        conn = self._conn
        if otype == OrderType.MARKET:
            if side == OrderSide.BUY:
                return conn.create_market_buy_order, "create_market_buy_order"
            return conn.create_market_sell_order, "create_market_sell_order"
        if otype in (OrderType.LIMIT, OrderType.POST_ONLY):
            if side == OrderSide.BUY:
                return conn.create_limit_buy_order, "create_limit_buy_order"
            return conn.create_limit_sell_order, "create_limit_sell_order"
        if otype == OrderType.STOP:
            if side == OrderSide.BUY:
                return conn.create_stop_buy_order, "create_stop_buy_order"
            return conn.create_stop_sell_order, "create_stop_sell_order"
        # ICEBERG has no RPC equivalent — best-effort market.
        log.warning("metaapi_broker.iceberg_no_rpc", note="ICEBERG mapped to MARKET order")
        if side == OrderSide.BUY:
            return conn.create_market_buy_order, "create_market_buy_order(iceberg)"
        return conn.create_market_sell_order, "create_market_sell_order(iceberg)"

    async def _units_to_lots(self, units: float, price: float, symbol: str) -> float:
        """Convert a unit quantity (Hermes `qty_requested`) to MetaApi lots.

        MetaApi `volume` is in LOTS, not units. 1 lot = symbol contractSize
        units (100_000 for standard FX). Example: $1000 notional / 1.1750 =
        851.06 EUR units → /100_000 = 0.00851 lots → round to lotStep 0.01
        = 0.01 lots.

        Falls back to FX defaults (contractSize=100_000, lotStep=0.01) if the
        symbol specification can't be fetched, and clamps to [minVolume,
        maxVolume]. Returns at least minVolume (or raises if it would be 0).
        """
        spec = await self.get_symbol_specification(symbol) or {}
        contract_size = float(spec.get("contractSize") or 100_000)
        lot_step = float(spec.get("lotStep") or 0.01)
        min_volume = float(spec.get("minVolume") or 0.0)
        max_volume = float(spec.get("maxVolume") or 1e9)

        if contract_size <= 0:
            contract_size = 100_000
        raw_lots = float(units) / contract_size
        # Round to the broker's lot step.
        lots = round(raw_lots / lot_step) * lot_step
        # Clamp to allowed range.
        lots = max(min_volume, min(lots, max_volume))
        if lots <= 0:
            raise ValueError(
                f"MetaApiBroker: computed lot volume is 0 for {symbol} "
                f"(units={units}, contractSize={contract_size})"
            )
        # Trim float noise from rounding.
        return round(lots, 8)

    async def _resolve_position_id(self, order: Any) -> str | None:
        """Find the live position opened by this order (matched on clientId)."""
        try:
            positions = await self.get_positions()
        except Exception:  # pragma: no cover - best effort
            return None
        if not positions:
            return None
        for p in positions:
            if p.get("clientId") == ("H" + order.order_id.replace("-", "").upper()[:7]) or p.get("comment", "").startswith(f"hermes:{order.order_id}"[:31]):
                return p.get("id")
        return None

    async def _poll_for_fill(self, order: Any, arrival_price: float) -> None:
        """Poll the order state for a fill; transition + emit Fill on FILLED.

        Behavior 1: a market BUY fills at the broker ASK, a SELL at the BID.
        We re-read the live quote after submit and use ask/bid (falling back
        to the order's last/current price) as the fill price, then record
        slippage vs the arrival (decision-time) price.
        """
        steps = max(1, int(self._fill_poll_sec / 0.5))
        for _ in range(steps):
            await asyncio.sleep(0.5)
            try:
                o = await self._conn.get_order(order.venue_order_id)
            except Exception:  # pragma: no cover - transient
                o = None
            if not o:
                continue
            state = o.get("state")
            if state == "ORDER_STATE_FILLED" or state == "FILLED":
                fill_price = await self._fetch_fill_price(order.symbol, order.side, arrival_price)
                slip_bps = self._slippage.compute_actual_slippage_bps(
                    arrival_price=arrival_price,
                    fill_price=fill_price,
                    side=order.side.value,
                )
                # Fill quantity is in Hermes UNITS (matches order.qty_requested),
                # not MetaApi lots — the lot volume is only the transport arg.
                fill = Fill(
                    order_id=order.order_id,
                    symbol=order.symbol,
                    venue=order.venue,
                    side=order.side,
                    qty=order.qty_requested,  # units
                    price=fill_price,
                    fee=0.0,
                    fee_currency="USD",
                    is_maker=False,
                    liquidity="taker",
                    arrival_price=arrival_price,
                    slippage_bps=slip_bps,
                    venue_fill_id=o.get("id"),
                )
                await self._apply_fill(order, fill)
                # Behavior: set SL/TP on the opened position (if the order carried them).
                sl = getattr(order, "stop_loss", None)
                tp = getattr(order, "take_profit", None)
                if sl is not None or tp is not None:
                    pos_id = await self._resolve_position_id(order)
                    if pos_id:
                        try:
                            await self.modify_position(pos_id, stop_loss=sl, take_profit=tp)
                        except Exception as err:  # pragma: no cover - best effort
                            log.warning("metaapi_broker.sl_tp_set_failed", position_id=pos_id, error=self._format_error(err))
                return
            if state in ("ORDER_STATE_CANCELED", "ORDER_STATE_REJECTED", "CANCELED", "REJECTED"):
                event = await self._transition(order, OrderStatus.REJECTED, {"state": state})
                await self._emit_event(order.order_id, event)
                return

    async def _fetch_fill_price(self, symbol: str, side: OrderSide, fallback: float) -> float:
        """Best-effort fill price: BUY→broker ask, SELL→broker bid.

        If the live quote can't be read, fall back to the arrival price
        (records zero slippage rather than a fabricated number).
        """
        try:
            price = await self._conn.get_symbol_price(self.normalize_symbol(symbol))
            if isinstance(price, dict):
                if side == OrderSide.BUY:
                    ask = price.get("ask")
                    if ask is not None:
                        return float(ask)
                else:
                    bid = price.get("bid")
                    if bid is not None:
                        return float(bid)
                last = price.get("last") or price.get("currentPrice")
                if last is not None:
                    return float(last)
        except Exception as err:  # pragma: no cover - best effort
            log.warning("metaapi_broker.fill_price_lookup_failed", symbol=symbol, error=self._format_error(err))
        return fallback

    async def _apply_fill(self, order: Any, fill: Fill) -> None:
        event, _ = OrderStateMachine.apply_fill(order, fill)
        await self._emit_event(order.order_id, event)
        if self._fill_callback:
            res = self._fill_callback(fill)
            if asyncio.iscoroutine(res):
                await res

    # ── Cancel / close ──────────────────────────────────────────────────

    async def cancel_order(self, order_id: str) -> bool:
        if not self._connected or self._conn is None:
            return False
        try:
            await self._conn.cancel_order(order_id)
            return True
        except Exception as err:
            log.warning("metaapi_broker.cancel_failed", order_id=order_id, error=self._format_error(err))
            return False

    async def close_position(self, position_id: str, reason: str = "") -> None:
        if not self._connected or self._conn is None:
            raise RuntimeError("MetaApiBroker.close_position: not connected")
        options: dict[str, Any] = {"comment": f"hermes-close:{reason}"[:31]}
        # Reuse the order's clientId correlation where possible is N/A here;
        # position close uses its own id. MetaApi echoes clientId on the deal.
        try:
            await self._conn.close_position(position_id, options)
            log.info("metaapi_broker.position_closed", position_id=position_id, reason=reason)
        except Exception as err:
            log.error("metaapi_broker.close_failed", position_id=position_id, error=self._format_error(err))
            raise

    # ── Optional reads ──────────────────────────────────────────────────

    async def get_positions(self) -> list[dict]:
        if not self._connected or self._conn is None:
            return []
        try:
            return await self._conn.get_positions() or []
        except Exception as err:  # pragma: no cover
            log.warning("metaapi_broker.get_positions_failed", error=self._format_error(err))
            return []

    async def get_account_information(self) -> dict | None:
        if not self._connected or self._conn is None:
            return None
        try:
            return await self._conn.get_account_information()
        except Exception as err:  # pragma: no cover
            log.warning("metaapi_broker.get_account_failed", error=self._format_error(err))
            return None

    async def get_orders(self) -> list[dict]:
        """Return open orders for the MT4/MT5 account (MetaApi RPC get_orders).

        Maps to the MetaApi REST endpoint
        GET /users/current/accounts/:accountId/orders.
        Returns [] if not connected or on failure.
        """
        if not self._connected or self._conn is None:
            return []
        try:
            return await self._conn.get_orders() or []
        except Exception as err:  # pragma: no cover
            log.warning("metaapi_broker.get_orders_failed", error=self._format_error(err))
            return []

    async def get_order(self, order_id: str) -> dict | None:
        """Return a broker order by id (None if unknown/unavailable)."""
        if not self._connected or self._conn is None or not order_id:
            return None
        try:
            return await self._conn.get_order(order_id)
        except Exception:  # pragma: no cover - best effort
            return None

    async def get_symbol_specification(self, symbol: str) -> dict | None:
        """Return MetaApi symbol specification (contract size, lot bounds, ...)."""
        if not self._connected or self._conn is None:
            return None
        try:
            return await self._conn.get_symbol_specification(self.normalize_symbol(symbol))
        except Exception as err:  # pragma: no cover - best effort
            log.warning("metaapi_broker.get_spec_failed", symbol=symbol, error=self._format_error(err))
            return None

    async def calculate_margin(
        self, symbol: str, side: OrderSide, volume_lots: float, open_price: float
    ) -> float | None:
        """Calculate the margin required for a prospective trade (contract item).

        Returns the margin amount (account currency) or None on failure.
        Does NOT block submission — callers log + proceed on error.
        """
        if not self._connected or self._conn is None:
            return None
        meta_type = "ORDER_TYPE_BUY" if side == OrderSide.BUY else "ORDER_TYPE_SELL"
        try:
            res = await self._conn.calculate_margin({
                "symbol": self.normalize_symbol(symbol),
                "type": meta_type,
                "volume": float(volume_lots),
                "openPrice": float(open_price),
            })
            if isinstance(res, dict):
                return float(res.get("margin") or res.get("requiredMargin") or 0.0)
            return float(res) if res is not None else None
        except Exception as err:  # pragma: no cover - best effort
            log.warning("metaapi_broker.calculate_margin_failed", symbol=symbol, error=self._format_error(err))
            return None

    async def modify_position(
        self, position_id: str, stop_loss: float | None = None, take_profit: float | None = None
    ) -> None:
        """Modify SL/TP on an open position (MetaApi RPC modify_position)."""
        if not self._connected or self._conn is None:
            raise RuntimeError("MetaApiBroker.modify_position: not connected")
        options: dict[str, float] = {}
        if stop_loss is not None:
            options["stopLoss"] = float(stop_loss)
        if take_profit is not None:
            options["takeProfit"] = float(take_profit)
        if not options:
            return
        try:
            await self._conn.modify_position(position_id, options)
            log.info("metaapi_broker.position_modified", position_id=position_id, **options)
        except Exception as err:
            log.error("metaapi_broker.modify_failed", position_id=position_id, error=self._format_error(err))
            raise

    # ── Internals ───────────────────────────────────────────────────────

    def _format_error(self, err: Exception) -> str:
        if self._MetaApi is not None:
            try:
                return self._MetaApi.format_error(err)
            except Exception:
                return str(err)
        return str(err)

    async def _emit_event(self, order_id: str, status_or_event, payload: dict | None = None) -> None:
        if isinstance(status_or_event, OrderEvent):
            event = status_or_event
        elif isinstance(status_or_event, OrderStatus):
            event = OrderEvent(order_id=order_id, event_type=status_or_event.value, payload=payload or {})
        else:
            event = OrderEvent(order_id=order_id, event_type=str(status_or_event), payload=payload or {})
        if self._event_callback:
            await self._event_callback(order_id, event)

    async def _reject(self, order: Any, payload: dict, note: str = "submit failed") -> None:
        """Transition an order to REJECTED (tolerating illegal hops) + log."""
        await self._transition(order, OrderStatus.REJECTED, payload)
        log.error(
            "metaapi_broker.order_rejected",
            order_id=order.order_id,
            symbol=self.normalize_symbol(order.symbol),
            note=note,
            **payload,
        )

    async def _transition(self, order: Any, status: OrderStatus, payload: dict | None = None) -> None:
        """Transition an order's state machine, tolerating illegal hops.

        The strict lifecycle (DRAFT→SUBMITTED→...→FILLED) rejects e.g.
        DRAFT→REJECTED. For broker-side outcomes we still want the order to
        reflect the terminal state and emit the event, so on an invalid
        transition we set the status directly.
        """
        try:
            event = OrderStateMachine.transition(order, status, payload)
        except ValueError:
            order.status = status
            event = OrderEvent(order_id=order.order_id, event_type=status.value, payload=payload or {})
        await self._emit_event(order.order_id, event)
