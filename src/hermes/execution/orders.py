"""
Order schemas + Order State Machine.

Order lifecycle:
  DRAFT → SUBMITTED → PARTIAL → FILLED
                     ↘ CANCELED
                     ↘ REJECTED
                     ↘ EXPIRED

See roadmap §2.4 + §6.2.4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class OrderStatus(str, Enum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    POST_ONLY = "post_only"
    ICEBERG = "iceberg"


class TimeInForce(str, Enum):
    GTC = "GTC"  # Good Till Cancel
    IOC = "IOC"  # Immediate Or Cancel
    FOK = "FOK"  # Fill Or Kill
    DAY = "DAY"  # Day order


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class ExecutionMethod(str, Enum):
    MARKET = "market"
    LIMIT_AT_BRICK = "limit_at_brick_boundary"
    TWAP = "twap_over_n_bricks"
    ICEBERG = "iceberg"
    POST_ONLY = "post_only"


class Order(BaseModel):
    """A trade order."""

    model_config = {"extra": "allow"}

    order_id: str = Field(default_factory=lambda: str(uuid4()))
    trade_id: str  # groups parent + child orders
    signal_id: str = ""  # BlendedSignal that triggered this order
    risk_decision_id: str = ""  # RiskDecision that approved this order
    ts_created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    symbol: str
    venue: str  # alpaca | hyperliquid
    side: OrderSide
    order_type: OrderType
    time_in_force: TimeInForce = TimeInForce.GTC

    # Requested
    qty_requested: float = Field(..., gt=0)
    price_limit: float | None = None  # for limit orders
    leverage: float = 1.0

    # Filled (updated incrementally)
    qty_filled: float = 0.0
    avg_fill_price: float | None = None
    status: OrderStatus = OrderStatus.DRAFT

    # Routing
    algo: str = ""  # twap | vwap | iceberg | direct
    venue_order_id: str | None = None

    # Cost
    total_fees: float = 0.0
    total_slippage: float = 0.0  # vs arrival price
    maker_rebate: float = 0.0

    # Audit
    config_hash: str = ""
    position_id: str = ""  # position this order opens/closes


class OrderEvent(BaseModel):
    """A lifecycle event for an order."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    order_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    event_type: str  # draft | submit | ack | partial_fill | fill | cancel | reject | expire
    payload: dict = Field(default_factory=dict)
    seq_num: int = 0


class Fill(BaseModel):
    """A fill (execution) for an order."""

    fill_id: str = Field(default_factory=lambda: str(uuid4()))
    order_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str
    venue: str
    side: OrderSide
    qty: float = Field(..., gt=0)
    price: float = Field(..., gt=0)
    fee: float = 0.0
    fee_currency: str = "USD"
    is_maker: bool = False
    liquidity: str = "taker"  # maker | taker
    arrival_price: float = 0.0  # for slippage calc
    slippage_bps: float = 0.0
    venue_fill_id: str | None = None


class OrderStateMachine:
    """
    Manages order lifecycle transitions.

    Valid transitions:
    DRAFT → SUBMITTED
    SUBMITTED → PARTIAL | FILLED | CANCELED | REJECTED | EXPIRED
    PARTIAL → FILLED | CANCELED
    """

    VALID_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
        OrderStatus.DRAFT: {OrderStatus.SUBMITTED, OrderStatus.CANCELED},
        OrderStatus.SUBMITTED: {
            OrderStatus.PARTIAL,
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        },
        OrderStatus.PARTIAL: {OrderStatus.FILLED, OrderStatus.CANCELED},
        OrderStatus.FILLED: set(),  # terminal
        OrderStatus.CANCELED: set(),  # terminal
        OrderStatus.REJECTED: set(),  # terminal
        OrderStatus.EXPIRED: set(),  # terminal
    }

    @classmethod
    def can_transition(cls, from_status: OrderStatus, to_status: OrderStatus) -> bool:
        return to_status in cls.VALID_TRANSITIONS.get(from_status, set())

    @classmethod
    def transition(cls, order: Order, to_status: OrderStatus, event_payload: dict | None = None) -> OrderEvent:
        """
        Transition an order to a new status.

        Returns the OrderEvent for this transition.
        Raises ValueError if transition is invalid.
        """
        if not cls.can_transition(order.status, to_status):
            raise ValueError(
                f"Invalid transition: {order.status} → {to_status} for order {order.order_id}"
            )

        old_status = order.status
        order.status = to_status

        event = OrderEvent(
            order_id=order.order_id,
            event_type=to_status.value,
            payload={
                "old_status": old_status.value,
                "new_status": to_status.value,
                **(event_payload or {}),
            },
        )

        return event

    @staticmethod
    def apply_fill(order: Order, fill: Fill) -> tuple[OrderEvent, OrderStatus | None]:
        """
        Apply a fill to an order.

        Returns (fill_event, new_status_if_changed).
        """
        order.qty_filled += fill.qty

        # Update avg fill price
        if order.avg_fill_price is None:
            order.avg_fill_price = fill.price
        else:
            # Weighted average
            total_value = (
                order.avg_fill_price * (order.qty_filled - fill.qty)
                + fill.price * fill.qty
            )
            order.avg_fill_price = total_value / order.qty_filled

        order.total_fees += fill.fee
        order.total_slippage += abs(fill.price - fill.arrival_price) * fill.qty

        if fill.is_maker:
            order.maker_rebate += fill.fee  # simplified: rebate = fee saved

        # Determine new status
        new_status = None
        if order.qty_filled >= order.qty_requested:
            new_status = OrderStatus.FILLED
        elif order.qty_filled > 0:
            new_status = OrderStatus.PARTIAL

        fill_event = OrderEvent(
            order_id=order.order_id,
            event_type="partial_fill" if new_status == OrderStatus.PARTIAL else "fill",
            payload={
                "fill_id": fill.fill_id,
                "qty": fill.qty,
                "price": fill.price,
                "fee": fill.fee,
                "is_maker": fill.is_maker,
                "slippage_bps": fill.slippage_bps,
                "cumulative_filled": order.qty_filled,
            },
        )

        if new_status:
            order.status = new_status

        return fill_event, new_status
