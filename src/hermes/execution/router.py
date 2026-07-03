"""
Smart Order Router — creates orders based on execution method.

Translates execution_method from BlendedSignal into actual Order objects:
- market → single market order
- limit_at_brick_boundary → single limit order at brick price
- twap_over_n_bricks → N child market orders
- iceberg → many small child orders
- post_only → single post-only limit order

See roadmap §2.4.
"""

from __future__ import annotations

from uuid import uuid4

import structlog

from hermes.execution.orders import Order, OrderSide, OrderType, TimeInForce
from hermes.portfolio.risk_gate import RiskDecision

log = structlog.get_logger(__name__)


class SmartOrderRouter:
    """
    Creates orders from a RiskDecision + BlendedSignal.

    For simple methods (market, limit, post_only): creates 1 order.
    For complex methods (TWAP, iceberg): creates 1 parent order that
    the paper engine splits into children.
    """

    def __init__(
        self,
        twap_n_bricks: int = 3,
        iceberg_child_pct: float = 10,
    ) -> None:
        self._twap_n = twap_n_bricks
        self._iceberg_pct = iceberg_child_pct

    def create_orders(
        self,
        decision: RiskDecision,
        signal,  # BlendedSignal
    ) -> list[Order]:
        """
        Create one or more orders from a risk decision.

        Args:
            decision: Approved RiskDecision from L5
            signal: BlendedSignal that triggered this decision

        Returns:
            List of Order objects (usually 1, but could be more for TWAP/iceberg)
        """
        if not decision.approved or decision.approved_size_usd <= 0:
            return []

        trade_id = str(uuid4())
        side = OrderSide.BUY if signal.direction == "buy" else OrderSide.SELL

        # Compute quantity from approved size + entry price
        entry_price = signal.entry_price_target or signal.nt_entry_price
        qty = decision.approved_size_usd / entry_price

        execution = signal.execution_method

        if execution == "market":
            return [self._create_market_order(
                trade_id=trade_id,
                signal=signal,
                decision=decision,
                side=side,
                qty=qty,
            )]

        elif execution in ("limit_at_brick_boundary", "post_only"):
            order_type = OrderType.POST_ONLY if execution == "post_only" else OrderType.LIMIT
            limit_price = signal.limit_price or entry_price
            return [self._create_limit_order(
                trade_id=trade_id,
                signal=signal,
                decision=decision,
                side=side,
                qty=qty,
                limit_price=limit_price,
                order_type=order_type,
            )]

        elif execution == "twap_over_n_bricks":
            # Single parent order with algo="twap" — paper engine handles splitting
            order = self._create_market_order(
                trade_id=trade_id,
                signal=signal,
                decision=decision,
                side=side,
                qty=qty,
            )
            order.algo = "twap"
            return [order]

        elif execution == "iceberg":
            # Single parent order with algo="iceberg"
            order = self._create_market_order(
                trade_id=trade_id,
                signal=signal,
                decision=decision,
                side=side,
                qty=qty,
            )
            order.algo = "iceberg"
            return [order]

        else:
            # Default: market order
            return [self._create_market_order(
                trade_id=trade_id,
                signal=signal,
                decision=decision,
                side=side,
                qty=qty,
            )]

    @staticmethod
    def _create_market_order(
        trade_id: str,
        signal,
        decision,
        side: OrderSide,
        qty: float,
    ) -> Order:
        return Order(
            trade_id=trade_id,
            signal_id=signal.signal_id,
            risk_decision_id=decision.decision_id,
            symbol=signal.symbol,
            venue=signal.venue,
            side=side,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.IOC,  # market = immediate
            qty_requested=round(qty, 8),
            price_limit=None,
            leverage=1.0,
            algo="direct",
            config_hash=signal.config_hash,
        )

    @staticmethod
    def _create_limit_order(
        trade_id: str,
        signal,
        decision,
        side: OrderSide,
        qty: float,
        limit_price: float,
        order_type: OrderType = OrderType.LIMIT,
    ) -> Order:
        return Order(
            trade_id=trade_id,
            signal_id=signal.signal_id,
            risk_decision_id=decision.decision_id,
            symbol=signal.symbol,
            venue=signal.venue,
            side=side,
            order_type=order_type,
            time_in_force=TimeInForce.GTC,
            qty_requested=round(qty, 8),
            price_limit=limit_price,
            leverage=1.0,
            algo="direct",
            config_hash=signal.config_hash,
        )
