"""
Paper Trading Engine — simulates order fills without real venue interaction.

Used for:
- Phase 5 paper trading mode
- Phase 8 backtesting / simulation engine
- Testing the full pipeline without risking real capital

Simulates:
- Market orders: fill at current_price ± slippage
- Limit orders: fill at limit_price (if price crosses)
- Post-only: fill at limit_price with maker rebate
- TWAP: split into N child orders, fill at intervals
- Iceberg: split into small child orders

See roadmap §2.4.
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

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

log = structlog.get_logger(__name__)


class PaperTradingEngine:
    """
    Simulates order fills for paper trading.

    Usage:
        engine = PaperTradingEngine()
        await engine.submit_order(order, current_price=64000)
        # ... fills arrive via callbacks ...
    """

    def __init__(
        self,
        slippage_modeler: SlippageModeler | None = None,
        maker_fee_bps: float = 0.5,  # Hyperliquid: ~0.5 bps maker
        taker_fee_bps: float = 2.0,  # Hyperliquid: ~2 bps taker
        alpaca_maker_fee_bps: float = 0.0,
        alpaca_taker_fee_bps: float = 1.0,
        fill_delay_ms: int = 100,  # simulated latency
    ) -> None:
        self._slippage = slippage_modeler or SlippageModeler()
        self._maker_fee = maker_fee_bps
        self._taker_fee = taker_fee_bps
        self._alpaca_maker_fee = alpaca_maker_fee_bps
        self._alpaca_taker_fee = alpaca_taker_fee_bps
        self._fill_delay = fill_delay_ms / 1000.0

        self._orders: dict[str, Order] = {}
        self._fills: list[Fill] = []
        self._event_callback = None  # async callback(order_id, event)
        self._fill_callback = None  # async callback(fill)

        self._stats = {
            "orders_submitted": 0,
            "orders_filled": 0,
            "orders_canceled": 0,
            "total_fees": 0.0,
            "total_slippage_bps": 0.0,
        }

    def set_callbacks(
        self,
        event_callback=None,
        fill_callback=None,
    ) -> None:
        """Set async callbacks for order events and fills."""
        self._event_callback = event_callback
        self._fill_callback = fill_callback

    async def submit_order(
        self,
        order: Order,
        current_price: float,
        annualized_vol: float = 0.60,
    ) -> None:
        """
        Submit an order for paper execution.

        Args:
            order: The order to submit
            current_price: Current market price (arrival price)
            annualized_vol: Annualized volatility for slippage estimation
        """
        self._orders[order.order_id] = order
        self._stats["orders_submitted"] += 1

        # Transition to SUBMITTED
        event = OrderStateMachine.transition(
            order, OrderStatus.SUBMITTED, {"arrival_price": current_price}
        )
        await self._emit_event(order.order_id, event)

        # Simulate fill based on order type / algo
        await asyncio.sleep(self._fill_delay)  # simulated latency

        # Check algo first (TWAP/iceberg use market order_type but have algo set)
        if order.algo == "iceberg":
            await self._fill_iceberg(order, current_price, annualized_vol)
        elif order.algo == "twap":
            await self._fill_twap(order, current_price, annualized_vol)
        elif order.order_type == OrderType.MARKET:
            await self._fill_market(order, current_price, annualized_vol)
        elif order.order_type in (OrderType.LIMIT, OrderType.POST_ONLY):
            await self._fill_limit(order, current_price)
        else:
            # Default: treat as market
            await self._fill_market(order, current_price, annualized_vol)

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an order. Returns True if canceled, False if not cancellable."""
        order = self._orders.get(order_id)
        if not order:
            return False

        if order.status in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED):
            return False

        event = OrderStateMachine.transition(order, OrderStatus.CANCELED)
        await self._emit_event(order_id, event)
        self._stats["orders_canceled"] += 1
        return True

    async def _fill_market(
        self,
        order: Order,
        arrival_price: float,
        annualized_vol: float,
    ) -> None:
        """Fill a market order with slippage."""
        order_size_usd = order.qty_requested * arrival_price
        slip_bps = self._slippage.estimate_market_slippage_bps(
            order_size_usd, annualized_vol
        )

        # Apply slippage
        slip_decimal = slip_bps / 10000
        if order.side == OrderSide.BUY:
            fill_price = arrival_price * (1 + slip_decimal)
        else:
            fill_price = arrival_price * (1 - slip_decimal)

        # Compute fee
        fee_bps = self._get_taker_fee(order.venue)
        fee = fill_price * order.qty_requested * fee_bps / 10000

        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            venue=order.venue,
            side=order.side,
            qty=order.qty_requested,
            price=fill_price,
            fee=fee,
            fee_currency="USD" if order.venue == "alpaca" else "USDC",
            is_maker=False,
            liquidity="taker",
            arrival_price=arrival_price,
            slippage_bps=slip_bps,
            venue_fill_id=f"paper-{uuid4().hex[:8]}",
        )

        await self._apply_fill(order, fill)

    async def _fill_limit(
        self,
        order: Order,
        current_price: float,
    ) -> None:
        """Fill a limit order (assume it fills at limit price)."""
        limit_price = order.price_limit or current_price

        # For paper trading: assume limit fills if price is reasonable
        # (in real trading, this depends on market movement)
        is_post_only = order.order_type == OrderType.POST_ONLY
        fee_bps = self._get_maker_fee(order.venue)
        fee = limit_price * order.qty_requested * fee_bps / 10000

        # Compute slippage vs arrival
        slip_bps = SlippageModeler.compute_actual_slippage_bps(
            arrival_price=current_price,
            fill_price=limit_price,
            side=order.side.value,
        )

        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            venue=order.venue,
            side=order.side,
            qty=order.qty_requested,
            price=limit_price,
            fee=fee,
            fee_currency="USD" if order.venue == "alpaca" else "USDC",
            is_maker=True,
            liquidity="maker",
            arrival_price=current_price,
            slippage_bps=slip_bps,
            venue_fill_id=f"paper-{uuid4().hex[:8]}",
        )

        await self._apply_fill(order, fill)

    async def _fill_iceberg(
        self,
        order: Order,
        arrival_price: float,
        annualized_vol: float,
    ) -> None:
        """Fill an iceberg order in small child orders."""
        child_pct = 0.10  # 10% per child
        n_children = int(1 / child_pct)

        for i in range(n_children):
            if order.status == OrderStatus.CANCELED:
                return

            # Last child fills the remaining quantity to avoid float precision issues
            remaining = order.qty_requested - order.qty_filled
            if i == n_children - 1:
                child_qty = remaining
            else:
                child_qty = order.qty_requested * child_pct

            if child_qty <= 0:
                return

            # Each child gets market slippage on its smaller size
            child_size_usd = child_qty * arrival_price
            slip_bps = self._slippage.estimate_market_slippage_bps(
                child_size_usd, annualized_vol
            )
            slip_decimal = slip_bps / 10000

            if order.side == OrderSide.BUY:
                fill_price = arrival_price * (1 + slip_decimal)
            else:
                fill_price = arrival_price * (1 - slip_decimal)

            fee_bps = self._get_taker_fee(order.venue)
            fee = fill_price * child_qty * fee_bps / 10000

            fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                venue=order.venue,
                side=order.side,
                qty=child_qty,
                price=fill_price,
                fee=fee,
                fee_currency="USD" if order.venue == "alpaca" else "USDC",
                is_maker=False,
                liquidity="taker",
                arrival_price=arrival_price,
                slippage_bps=slip_bps,
                venue_fill_id=f"paper-{uuid4().hex[:8]}",
            )

            await self._apply_fill(order, fill)
            if order.status == OrderStatus.FILLED:
                return  # fully filled, stop
            await asyncio.sleep(self._fill_delay)  # delay between children

    async def _fill_twap(
        self,
        order: Order,
        arrival_price: float,
        annualized_vol: float,
    ) -> None:
        """Fill a TWAP order in N slices."""
        n_slices = 3  # default TWAP slices

        for i in range(n_slices):
            if order.status == OrderStatus.CANCELED:
                return

            # Last slice fills the remaining quantity
            remaining = order.qty_requested - order.qty_filled
            if i == n_slices - 1:
                slice_qty = remaining
            else:
                slice_qty = order.qty_requested / n_slices

            if slice_qty <= 0:
                return

            slice_size_usd = slice_qty * arrival_price
            slip_bps = self._slippage.estimate_market_slippage_bps(
                slice_size_usd, annualized_vol
            )
            slip_decimal = slip_bps / 10000

            # Add small random price movement between slices
            price_drift = random.gauss(0, arrival_price * 0.0005)
            slice_price = arrival_price + price_drift

            if order.side == OrderSide.BUY:
                fill_price = slice_price * (1 + slip_decimal)
            else:
                fill_price = slice_price * (1 - slip_decimal)

            fee_bps = self._get_taker_fee(order.venue)
            fee = fill_price * slice_qty * fee_bps / 10000

            fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                venue=order.venue,
                side=order.side,
                qty=slice_qty,
                price=fill_price,
                fee=fee,
                fee_currency="USD" if order.venue == "alpaca" else "USDC",
                is_maker=False,
                liquidity="taker",
                arrival_price=arrival_price,
                slippage_bps=SlippageModeler.compute_actual_slippage_bps(
                    arrival_price, fill_price, order.side.value
                ),
                venue_fill_id=f"paper-{uuid4().hex[:8]}",
            )

            await self._apply_fill(order, fill)
            await asyncio.sleep(self._fill_delay)

    async def _apply_fill(self, order: Order, fill: Fill) -> None:
        """Apply a fill to an order and emit events."""
        self._fills.append(fill)
        self._stats["total_fees"] += fill.fee
        self._stats["total_slippage_bps"] += fill.slippage_bps

        fill_event, new_status = OrderStateMachine.apply_fill(order, fill)
        await self._emit_event(order.order_id, fill_event)

        if self._fill_callback:
            await self._fill_callback(fill)

        if order.status == OrderStatus.FILLED:
            self._stats["orders_filled"] += 1

    async def _emit_event(self, order_id: str, event: OrderEvent) -> None:
        """Emit an order event via callback."""
        if self._event_callback:
            await self._event_callback(order_id, event)

    def _get_taker_fee(self, venue: str) -> float:
        return self._alpaca_taker_fee if venue == "alpaca" else self._taker_fee

    def _get_maker_fee(self, venue: str) -> float:
        return self._alpaca_maker_fee if venue == "alpaca" else self._maker_fee

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
