"""
Entry Timing Optimizer + Execution Method Optimizer.

Given Noble Trader's signal (direction, entry, stop, TP) and the current
brick pattern, decides:
1. WHEN to enter (enter_now / wait_for_brick_close / wait_for_pullback / wait_for_retest / skip)
2. HOW to execute (market / limit_at_brick / twap / iceberg / post_only)

See roadmap §2.2.3 for full design.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

import structlog
from pydantic import BaseModel, Field

from hermes.signals.meta_regime import MetaRegimeResult
from hermes.signals.renko_engine import BrickPattern, RenkoBrick, RenkoConstructor

log = structlog.get_logger(__name__)

EntryStrategy = Literal[
    "enter_now",
    "wait_for_brick_close",
    "wait_for_pullback",
    "wait_for_retest",
    "skip_entry",
    "block",
    "maker_only",
]

ExecutionMethod = Literal[
    "market",
    "limit_at_brick_boundary",
    "twap_over_n_bricks",
    "iceberg",
    "post_only",
]


class EntryDecision(BaseModel):
    """Output of the entry timing optimizer."""

    strategy: EntryStrategy
    execution_method: ExecutionMethod
    entry_price_target: float | None = None
    limit_price: float | None = None
    twap_n_bricks: int | None = None
    iceberg_child_pct: float | None = None
    reason: str = ""
    brick_pattern: str = ""
    expected_entry_alpha_bps: float = 0.0  # estimated improvement vs NT entry


class EntryTimingOptimizer:
    """
    Decides WHEN to enter based on meta-regime + brick pattern + NT signal.

    Logic:
    - calm_trend + trend/breakout pattern → enter_now (aggressive)
    - choppy_range + any pattern → wait_for_brick_close (patient)
    - high_vol_breakout + breakout → wait_for_pullback (cautious)
    - regime_transition + any → wait_for_retest (defensive)
    - risk_off → block
    - funding_stress → block (crypto)
    - liquidity_drained → maker_only
    """

    def __init__(
        self,
        brick_confirmation_count: int = 2,
        pullback_depth_brick_fraction: float = 0.5,
    ) -> None:
        self._brick_confirmation_count = brick_confirmation_count
        self._pullback_depth = pullback_depth_brick_fraction

    def decide(
        self,
        meta_regime: MetaRegimeResult,
        brick_pattern: BrickPattern,
        nt_signal: str,  # buy / sell / neutral
        current_price: float,
        nt_entry_price: float,
        bricks: list[RenkoBrick] | None = None,
    ) -> EntryDecision:
        """
        Decide entry timing based on meta-regime and brick pattern.

        Args:
            meta_regime: Current portfolio-level regime
            brick_pattern: Pattern from BrickPatternAnalyzer
            nt_signal: Noble Trader's signal direction (buy/sell/neutral)
            current_price: Current market price
            nt_entry_price: NT's suggested entry price
            bricks: Recent renko bricks (for pullback/retest calculations)

        Returns:
            EntryDecision with strategy, execution_method, and estimated entry alpha
        """
        # Block on neutral signal
        if nt_signal == "neutral":
            return EntryDecision(
                strategy="block",
                execution_method="market",
                reason="neutral_signal",
                brick_pattern=brick_pattern.value,
            )

        # Block on risk_off
        if meta_regime.state == "risk_off":
            return EntryDecision(
                strategy="block",
                execution_method="market",
                reason="risk_off_regime",
                brick_pattern=brick_pattern.value,
            )

        # Block on funding_stress for crypto
        if meta_regime.state == "funding_stress":
            return EntryDecision(
                strategy="block",
                execution_method="market",
                reason="funding_stress_regime",
                brick_pattern=brick_pattern.value,
            )

        # Maker-only on liquidity_drained
        if meta_regime.state == "liquidity_drained":
            return EntryDecision(
                strategy="maker_only",
                execution_method="post_only",
                entry_price_target=nt_entry_price,
                limit_price=nt_entry_price,
                reason="liquidity_drained_maker_only",
                brick_pattern=brick_pattern.value,
                expected_entry_alpha_bps=2.0,  # maker rebate + avoid slippage
            )

        # Regime transition: defensive — wait for retest
        if meta_regime.state == "regime_transition":
            if brick_pattern in (BrickPattern.BREAKOUT_UP, BrickPattern.BREAKOUT_DOWN):
                return EntryDecision(
                    strategy="wait_for_retest",
                    execution_method="limit_at_brick_boundary",
                    entry_price_target=nt_entry_price,
                    limit_price=nt_entry_price,
                    reason="regime_transition_wait_retest",
                    brick_pattern=brick_pattern.value,
                    expected_entry_alpha_bps=5.0,
                )
            else:
                return EntryDecision(
                    strategy="skip_entry",
                    execution_method="market",
                    reason="regime_transition_no_breakout",
                    brick_pattern=brick_pattern.value,
                )

        # High vol breakout: cautious — wait for pullback
        if meta_regime.state == "high_vol_breakout":
            if brick_pattern in (BrickPattern.BREAKOUT_UP, BrickPattern.TREND_UP, BrickPattern.BREAKOUT_DOWN, BrickPattern.TREND_DOWN):
                # Wait for pullback to brick boundary
                pullback_price = self._compute_pullback_price(
                    current_price, nt_entry_price, nt_signal, bricks or []
                )
                return EntryDecision(
                    strategy="wait_for_pullback",
                    execution_method="limit_at_brick_boundary",
                    entry_price_target=pullback_price,
                    limit_price=pullback_price,
                    reason="high_vol_wait_pullback",
                    brick_pattern=brick_pattern.value,
                    expected_entry_alpha_bps=8.0,
                )
            else:
                return EntryDecision(
                    strategy="wait_for_brick_close",
                    execution_method="limit_at_brick_boundary",
                    entry_price_target=nt_entry_price,
                    limit_price=nt_entry_price,
                    reason="high_vol_wait_brick_confirm",
                    brick_pattern=brick_pattern.value,
                    expected_entry_alpha_bps=3.0,
                )

        # Choppy range: patient — wait for brick close
        if meta_regime.state == "choppy_range":
            return EntryDecision(
                strategy="wait_for_brick_close",
                execution_method="limit_at_brick_boundary",
                entry_price_target=nt_entry_price,
                limit_price=nt_entry_price,
                reason="choppy_wait_brick_close",
                brick_pattern=brick_pattern.value,
                expected_entry_alpha_bps=4.0,
            )

        # Calm trend: aggressive — enter now if pattern confirms
        if meta_regime.state == "calm_trend":
            pattern_confirms = self._pattern_confirms_signal(brick_pattern, nt_signal)
            if pattern_confirms:
                return EntryDecision(
                    strategy="enter_now",
                    execution_method="market",
                    entry_price_target=current_price,
                    reason="calm_trend_pattern_confirmed",
                    brick_pattern=brick_pattern.value,
                    expected_entry_alpha_bps=-2.0,  # market order = slight negative alpha (slippage)
                )
            else:
                return EntryDecision(
                    strategy="wait_for_brick_close",
                    execution_method="limit_at_brick_boundary",
                    entry_price_target=nt_entry_price,
                    limit_price=nt_entry_price,
                    reason="calm_trend_pattern_not_confirmed",
                    brick_pattern=brick_pattern.value,
                    expected_entry_alpha_bps=3.0,
                )

        # Default: wait for brick close
        return EntryDecision(
            strategy="wait_for_brick_close",
            execution_method="limit_at_brick_boundary",
            entry_price_target=nt_entry_price,
            limit_price=nt_entry_price,
            reason="default_wait_brick_close",
            brick_pattern=brick_pattern.value,
            expected_entry_alpha_bps=2.0,
        )

    def _compute_pullback_price(
        self,
        current_price: float,
        nt_entry_price: float,
        nt_signal: str,
        bricks: list[RenkoBrick],
    ) -> float:
        """Compute the pullback price to wait for."""
        if not bricks:
            return nt_entry_price

        last_brick = bricks[-1]
        brick_size = last_brick.brick_size

        # For buy: wait for pullback down to last brick boundary
        # For sell: wait for pullback up to last brick boundary
        if nt_signal == "buy":
            return min(current_price, nt_entry_price) - brick_size * self._pullback_depth
        else:
            return max(current_price, nt_entry_price) + brick_size * self._pullback_depth

    @staticmethod
    def _pattern_confirms_signal(pattern: BrickPattern, nt_signal: str) -> bool:
        """Check if brick pattern confirms NT's signal direction."""
        if nt_signal == "buy":
            return pattern in (
                BrickPattern.BREAKOUT_UP,
                BrickPattern.TREND_UP,
                BrickPattern.REVERSAL_UP,
                BrickPattern.PULLBACK_TO_SUPPORT,
            )
        elif nt_signal == "sell":
            return pattern in (
                BrickPattern.BREAKOUT_DOWN,
                BrickPattern.TREND_DOWN,
                BrickPattern.REVERSAL_DOWN,
                BrickPattern.PULLBACK_TO_RESISTANCE,
            )
        return False


class ExecutionMethodOptimizer:
    """
    Decides HOW to execute based on entry decision, position size, and venue.

    Logic:
    - Large size → TWAP over N bricks (split to avoid impact)
    - liquidity_drained → iceberg (hide true size)
    - wait_for_brick_close → post_only limit at brick boundary (maker rebate)
    - enter_now in calm_trend → market (fast execution)
    - enter_now in high_vol_breakout → limit_at_brick (avoid slippage)
    """

    def __init__(
        self,
        large_size_threshold_usd: float = 10000,
        twap_n_bricks: int = 3,
        iceberg_child_pct: float = 10,
        post_only_preference: bool = True,
    ) -> None:
        self._large_size_threshold = large_size_threshold_usd
        self._twap_n_bricks = twap_n_bricks
        self._iceberg_child_pct = iceberg_child_pct
        self._post_only_preference = post_only_preference

    def select(
        self,
        entry_decision: EntryDecision,
        position_size_usd: float,
        meta_regime_state: str,
        venue_supports_post_only: bool = True,
    ) -> ExecutionMethod:
        """Select execution method based on entry decision and constraints."""
        # Large size → TWAP
        if position_size_usd > self._large_size_threshold:
            if meta_regime_state == "liquidity_drained":
                return "iceberg"
            return "twap_over_n_bricks"

        # Maker-only regime
        if meta_regime_state == "liquidity_drained":
            return "iceberg" if venue_supports_post_only else "market"

        # Already decided in entry decision
        if entry_decision.execution_method:
            # Refine: prefer post_only if venue supports it and we're using limit
            if (
                self._post_only_preference
                and venue_supports_post_only
                and entry_decision.execution_method == "limit_at_brick_boundary"
            ):
                return "post_only"
            return entry_decision.execution_method

        return "market"
