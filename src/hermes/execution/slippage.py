"""
Slippage Modeler — estimates slippage for order sizing + post-trade analysis.

Uses the square-root impact model:
    slip = k * σ * sqrt(participation_rate)

where:
    k = venue-specific constant (default 0.1)
    σ = current volatility (annualized)
    participation_rate = order_size / adv

See roadmap §2.4.
"""

from __future__ import annotations

import math

import structlog

log = structlog.get_logger(__name__)


class SlippageModeler:
    """
    Estimates slippage for market and limit orders.

    For market orders: square-root impact model.
    For limit orders: 0 slippage (price is fixed) but may not fill.
    For post-only: negative slippage (maker rebate).
    """

    def __init__(
        self,
        k_constant: float = 0.1,
        default_adv_usd: float = 10_000_000,  # default average daily volume
    ) -> None:
        self._k = k_constant
        self._default_adv = default_adv_usd

    def estimate_market_slippage_bps(
        self,
        order_size_usd: float,
        annualized_vol: float,
        adv_usd: float | None = None,
    ) -> float:
        """
        Estimate slippage for a market order in basis points.

        Args:
            order_size_usd: Order notional in USD
            annualized_vol: Annualized volatility (e.g., 0.60 for 60%)
            adv_usd: Average daily volume in USD (defaults to 10M)

        Returns:
            Slippage in basis points (positive = unfavorable)
        """
        adv = adv_usd or self._default_adv
        participation_rate = order_size_usd / adv

        # Square-root impact: slip = k * σ * sqrt(participation)
        slip_decimal = self._k * annualized_vol * math.sqrt(participation_rate)
        slip_bps = slip_decimal * 10000

        return slip_bps

    def estimate_limit_slippage_bps(self) -> float:
        """Limit orders have 0 slippage (price is fixed)."""
        return 0.0

    def estimate_post_only_slippage_bps(self, maker_rebate_bps: float = 2.0) -> float:
        """Post-only orders have negative slippage (maker rebate)."""
        return -maker_rebate_bps

    def estimate_slippage(
        self,
        order_type: str,
        order_size_usd: float,
        annualized_vol: float = 0.60,
        adv_usd: float | None = None,
        maker_rebate_bps: float = 2.0,
    ) -> float:
        """
        Estimate slippage for any order type.

        Returns slippage in bps (positive = unfavorable, negative = favorable).
        """
        if order_type == "market":
            return self.estimate_market_slippage_bps(order_size_usd, annualized_vol, adv_usd)
        elif order_type in ("limit", "limit_at_brick_boundary"):
            return self.estimate_limit_slippage_bps()
        elif order_type == "post_only":
            return self.estimate_post_only_slippage_bps(maker_rebate_bps)
        elif order_type == "iceberg":
            # Iceberg: smaller child orders → less slippage per fill
            return self.estimate_market_slippage_bps(
                order_size_usd * 0.1, annualized_vol, adv_usd  # 10% per child
            )
        elif order_type == "twap_over_n_bricks":
            # TWAP: split into N slices → less slippage per slice
            return self.estimate_market_slippage_bps(
                order_size_usd / 3, annualized_vol, adv_usd  # assume 3 slices
            )
        else:
            return 0.0

    @staticmethod
    def compute_actual_slippage_bps(
        arrival_price: float,
        fill_price: float,
        side: str,
    ) -> float:
        """
        Compute actual slippage from a fill.

        For BUY: slippage = (fill_price - arrival_price) / arrival_price * 10000
        For SELL: slippage = (arrival_price - fill_price) / arrival_price * 10000

        Positive = unfavorable (paid more / received less).
        """
        if arrival_price <= 0:
            return 0.0

        if side == "buy":
            return ((fill_price - arrival_price) / arrival_price) * 10000
        else:
            return ((arrival_price - fill_price) / arrival_price) * 10000
