"""
Hermes Agent Decision Tree — intelligent position management.

Evaluates existing positions and new signals through a decision tree:

For EXISTING positions:
1. Check SL: pnl <= -1% → close (stop-loss)
2. Check TP: pnl >= 2.5% → close (take-profit)
3. Signal present?
   - Same direction + pnl > 0 + fading → trail stop
   - Same direction + pnl > 4.5% → early profit take
   - Same direction + no exit signal → hold
   - Opposite direction + strong signal → flip (close + reverse)
   - No signal → hold, native stops manage

For NEW positions (no existing position):
1. Renko signal present? → proceed to sizing + execution
2. No signal → skip

See user-specified decision tree.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.portfolio.state import PortfolioPosition, PortfolioStateService
from hermes.signals.synthesizer import BlendedSignal

log = structlog.get_logger(__name__)


class AgentAction(str, Enum):
    """Actions the Hermes agent can take."""

    # Position management
    CLOSE_STOP_LOSS = "close_stop_loss"
    CLOSE_TAKE_PROFIT = "close_take_profit"
    CLOSE_EARLY_PROFIT = "close_early_profit"
    CLOSE_FLIP = "close_flip"
    TRAIL_STOP = "trail_stop"
    HOLD = "hold"
    HOLD_NATIVE_STOPS = "hold_native_stops"

    # New positions
    ENTER_NEW = "enter_new"
    SKIP_NO_SIGNAL = "skip_no_signal"

    # Learning
    LOG_HYPOTHESIS = "log_hypothesis"
    WRITE_POSTMORTEM = "write_postmortem"


class AgentDecision(BaseModel):
    """A single decision made by the Hermes agent."""

    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Context
    symbol: str
    action: AgentAction
    reason: str = ""

    # Position context (if evaluating existing position)
    position_id: str | None = None
    current_pnl_pct: float | None = None
    current_pnl_usd: float | None = None
    r_multiple: float | None = None

    # Signal context (if signal present)
    signal_id: str | None = None
    signal_direction: str | None = None  # buy / sell / neutral

    # Decision detail
    detail: dict = Field(default_factory=dict)


class HermesDecisionTree:
    """
    The Hermes agent's decision tree for position management.

    Thresholds are configurable but default to user-specified values:
    - Stop-loss: pnl <= -1%
    - Take-profit: pnl >= 2.5%
    - Early profit: pnl >= 4.5%
    - Fading detection: 2+ consecutive adverse renko bricks
    - Strong opposite signal: conviction > 0.7 + regime confirms
    """

    def __init__(
        self,
        stop_loss_pct: float = -0.01,        # -1%
        take_profit_pct: float = 0.025,       # +2.5%
        early_profit_pct: float = 0.045,      # +4.5%
        fading_brick_count: int = 2,          # 2+ adverse bricks = fading
        strong_conviction_threshold: float = 0.7,
        trail_stop_activation_pct: float = 0.01,  # start trailing at +1%
    ) -> None:
        self._sl_pct = stop_loss_pct
        self._tp_pct = take_profit_pct
        self._early_tp_pct = early_profit_pct
        self._fading_bricks = fading_brick_count
        self._strong_conviction = strong_conviction_threshold
        self._trail_activation = trail_stop_activation_pct
        self._stats = {
            "decisions_evaluated": 0,
            "closes_sl": 0,
            "closes_tp": 0,
            "closes_early": 0,
            "closes_flip": 0,
            "trails": 0,
            "holds": 0,
            "enters": 0,
            "skips": 0,
        }

    def evaluate_existing_position(
        self,
        position: PortfolioPosition,
        signal: BlendedSignal | None = None,
        current_price: float | None = None,
        adverse_brick_count: int = 0,
    ) -> AgentDecision:
        """
        Evaluate an existing position through the decision tree.

        Decision flow (validated):
        1. HARD stop-loss: pnl <= -1% → close (always fires, risk management override)
        2. Signal present?
           - YES → Agent takes over management (native 2.5% TP suspended):
             a. Same direction?
                - pnl > 0 + fading (2+ adverse bricks) → trail stop
                - pnl >= 4.5% → early profit take (agent's own, higher than native)
                - no exit condition → hold
             b. Opposite direction?
                - strong (conviction >= 0.7 + regime confirms) → flip close
                - not strong → hold with native stops
           - NO → Native stops manage:
             - pnl >= 2.5% → close (native TP)
             - otherwise → hold with native SL/TP

        Args:
            position: The open position to evaluate
            signal: Current blended signal (None if no signal)
            current_price: Current market price
            adverse_brick_count: Number of consecutive renko bricks against position

        Returns:
            AgentDecision with action to take
        """
        self._stats["decisions_evaluated"] += 1

        price = current_price or position.current_price
        entry = position.entry_price

        # Compute PnL %
        if position.direction == "long":
            pnl_pct = (price - entry) / entry
            pnl_usd = (price - entry) * position.qty
        else:
            pnl_pct = (entry - price) / entry
            pnl_usd = (entry - price) * position.qty

        risk_amount = position.risk_amount
        
        # Safe R-multiple calculation with comprehensive validation
        if risk_amount <= 0:
            log.warning(
                "invalid_risk_amount_for_r_multiple",
                position_id=position.position_id,
                symbol=position.symbol,
                risk_amount=risk_amount,
                pnl_usd=pnl_usd,
                note="Using safe fallback to avoid division by zero"
            )
            # Use a safe fallback - treat as 1 unit of risk
            r_multiple = pnl_usd if pnl_usd != 0 else 0.0
        elif pnl_usd == 0:
            r_multiple = 0.0
        else:
            r_multiple = pnl_usd / risk_amount

        # === Step 1: HARD stop-loss (always fires — risk management override) ===
        if pnl_pct <= self._sl_pct:
            self._stats["closes_sl"] += 1
            return AgentDecision(
                symbol=position.symbol,
                action=AgentAction.CLOSE_STOP_LOSS,
                reason=f"pnl {pnl_pct:.2%} <= SL threshold {self._sl_pct:.2%}",
                position_id=position.position_id,
                current_pnl_pct=pnl_pct,
                current_pnl_usd=pnl_usd,
                r_multiple=r_multiple,
                detail={"threshold_pct": self._sl_pct, "price": price, "entry": entry},
            )

        # === Step 2: Signal present? ===
        if signal is None or signal.direction == "neutral":
            # No signal → native stops manage
            # Check native TP (2.5%) — only fires when no signal present
            if pnl_pct >= self._tp_pct:
                self._stats["closes_tp"] += 1
                return AgentDecision(
                    symbol=position.symbol,
                    action=AgentAction.CLOSE_TAKE_PROFIT,
                    reason=f"no signal + pnl {pnl_pct:.2%} >= native TP {self._tp_pct:.2%}",
                    position_id=position.position_id,
                    current_pnl_pct=pnl_pct,
                    current_pnl_usd=pnl_usd,
                    r_multiple=r_multiple,
                    detail={"threshold_pct": self._tp_pct, "price": price, "entry": entry},
                )
            # Below native TP → hold with native stops
            self._stats["holds"] += 1
            return AgentDecision(
                symbol=position.symbol,
                action=AgentAction.HOLD_NATIVE_STOPS,
                reason="no signal — holding with native stops",
                position_id=position.position_id,
                current_pnl_pct=pnl_pct,
                current_pnl_usd=pnl_usd,
                r_multiple=r_multiple,
                detail={"native_stop": position.stop_price, "native_target": position.target_price},
            )

        # Signal direction relative to position
        signal_is_buy = signal.direction == "buy"
        position_is_long = position.direction == "long"
        same_direction = (signal_is_buy and position_is_long) or (
            not signal_is_buy and not position_is_long
        )
        opposite_direction = not same_direction

        # === Step 4: Same direction ===
        # Order matters per the user's decision tree:
        #   1. Fading (pnl > 0 + adverse bricks) → trail stop
        #   2. Early profit (pnl >= 4.5%) → close
        #   3. No exit condition → hold
        if same_direction:
            # Check fading FIRST (pnl > 0 + adverse bricks)
            # If the trend is fading, trail the stop instead of taking early profit
            # — the trend might resume, so we protect gains rather than exit
            if pnl_pct > 0 and adverse_brick_count >= self._fading_bricks:
                self._stats["trails"] += 1
                return AgentDecision(
                    symbol=position.symbol,
                    action=AgentAction.TRAIL_STOP,
                    reason=f"pnl {pnl_pct:.2%} > 0 + {adverse_brick_count} adverse bricks (fading) → trail stop",
                    position_id=position.position_id,
                    signal_id=signal.signal_id,
                    signal_direction=signal.direction,
                    current_pnl_pct=pnl_pct,
                    current_pnl_usd=pnl_usd,
                    r_multiple=r_multiple,
                    detail={
                        "adverse_bricks": adverse_brick_count,
                        "trail_activation_pct": self._trail_activation,
                        "new_trailing_stop": self._compute_trailing_stop(position, price),
                    },
                )

            # Check early profit SECOND (pnl >= 4.5% + NOT fading)
            # Trend is still strong (no adverse bricks) → take profit at higher threshold
            if pnl_pct >= self._early_tp_pct:
                self._stats["closes_early"] += 1
                return AgentDecision(
                    symbol=position.symbol,
                    action=AgentAction.CLOSE_EARLY_PROFIT,
                    reason=f"pnl {pnl_pct:.2%} >= early profit threshold {self._early_tp_pct:.2%} + same direction + not fading",
                    position_id=position.position_id,
                    signal_id=signal.signal_id,
                    signal_direction=signal.direction,
                    current_pnl_pct=pnl_pct,
                    current_pnl_usd=pnl_usd,
                    r_multiple=r_multiple,
                    detail={"threshold_pct": self._early_tp_pct, "signal_id": signal.signal_id},
                )

            # No exit signal → hold
            self._stats["holds"] += 1
            return AgentDecision(
                symbol=position.symbol,
                action=AgentAction.HOLD,
                reason="same direction signal + no exit condition → hold",
                position_id=position.position_id,
                signal_id=signal.signal_id,
                signal_direction=signal.direction,
                current_pnl_pct=pnl_pct,
                current_pnl_usd=pnl_usd,
                r_multiple=r_multiple,
                detail={"signal_id": signal.signal_id},
            )

        # === Step 5: Opposite direction ===
        if opposite_direction:
            # Check if signal is strong enough to flip
            conviction = getattr(signal, "meta_regime_confidence", 0.5)
            regime_supports_flip = signal.meta_regime not in ("risk_off", "funding_stress")

            if conviction >= self._strong_conviction and regime_supports_flip:
                self._stats["closes_flip"] += 1
                return AgentDecision(
                    symbol=position.symbol,
                    action=AgentAction.CLOSE_FLIP,
                    reason=f"opposite direction + strong signal (conviction={conviction:.2f} >= {self._strong_conviction}) → flip close",
                    position_id=position.position_id,
                    signal_id=signal.signal_id,
                    signal_direction=signal.direction,
                    current_pnl_pct=pnl_pct,
                    current_pnl_usd=pnl_usd,
                    r_multiple=r_multiple,
                    detail={
                        "conviction": conviction,
                        "threshold": self._strong_conviction,
                        "new_direction": signal.direction,
                        "regime": signal.meta_regime,
                    },
                )

            # Opposite but not strong enough → hold with native stops
            self._stats["holds"] += 1
            return AgentDecision(
                symbol=position.symbol,
                action=AgentAction.HOLD_NATIVE_STOPS,
                reason=f"opposite signal but conviction {conviction:.2f} < {self._strong_conviction} → hold with native stops",
                position_id=position.position_id,
                signal_id=signal.signal_id,
                signal_direction=signal.direction,
                current_pnl_pct=pnl_pct,
                current_pnl_usd=pnl_usd,
                r_multiple=r_multiple,
                detail={"conviction": conviction, "threshold": self._strong_conviction},
            )

        # Fallback (shouldn't reach here)
        self._stats["holds"] += 1
        return AgentDecision(
            symbol=position.symbol,
            action=AgentAction.HOLD,
            reason="fallback — no condition matched",
            position_id=position.position_id,
            current_pnl_pct=pnl_pct,
            current_pnl_usd=pnl_usd,
            r_multiple=r_multiple,
        )

    def evaluate_new_signal(
        self,
        signal: BlendedSignal | None,
        has_existing_position: bool = False,
    ) -> AgentDecision:
        """
        Evaluate a new signal when no position exists.

        Decision tree:
        - Renko signal present? YES → enter_new (proceed to sizing + execution)
        - Renko signal present? NO → skip_no_signal
        """
        self._stats["decisions_evaluated"] += 1

        if signal is None or signal.direction == "neutral":
            self._stats["skips"] += 1
            return AgentDecision(
                symbol=signal.symbol if signal else "unknown",
                action=AgentAction.SKIP_NO_SIGNAL,
                reason="no renko signal present → skip",
                signal_id=signal.signal_id if signal else None,
                signal_direction=signal.direction if signal else None,
            )

        # Check if entry strategy is blocked
        if signal.entry_strategy in ("block", "skip_entry"):
            self._stats["skips"] += 1
            return AgentDecision(
                symbol=signal.symbol,
                action=AgentAction.SKIP_NO_SIGNAL,
                reason=f"entry strategy = {signal.entry_strategy} → skip",
                signal_id=signal.signal_id,
                signal_direction=signal.direction,
                detail={"entry_strategy": signal.entry_strategy, "meta_regime": signal.meta_regime},
            )

        # Signal present → enter new
        self._stats["enters"] += 1
        return AgentDecision(
            symbol=signal.symbol,
            action=AgentAction.ENTER_NEW,
            reason=f"renko signal present ({signal.direction}) → proceed to Kelly sizing + execution",
            signal_id=signal.signal_id,
            signal_direction=signal.direction,
            detail={
                "entry_strategy": signal.entry_strategy,
                "execution_method": signal.execution_method,
                "final_size_usd": signal.final_size_usd,
                "meta_regime": signal.meta_regime,
                "brick_pattern": signal.brick_pattern,
            },
        )

    @staticmethod
    def _compute_trailing_stop(position: PortfolioPosition, current_price: float) -> float:
        """Compute new trailing stop price."""
        # Simple: trail at 50% of current profit
        if position.direction == "long":
            profit = current_price - position.entry_price
            return current_price - profit * 0.5
        else:
            profit = position.entry_price - current_price
            return current_price + profit * 0.5

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
