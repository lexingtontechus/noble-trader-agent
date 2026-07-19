"""
Risk Gate — pre-trade checks consuming BlendedSignal from L4.

Every blended signal passes through this gate before reaching L3 (execution).
If any check fails, the signal is rejected with a reason.

See roadmap §4.2 + §5.4 step 7.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.portfolio.circuit_breakers import (
    BreakerLevel,
    KillSwitch,
    RiskCircuitBreaker,
    VolatilityCircuitBreaker,
)
from hermes.portfolio.state import PortfolioStateService
from hermes.portfolio.var_calculator import VaRCalculator
from hermes.signals.synthesizer import BlendedSignal

log = structlog.get_logger(__name__)


class RiskDecision(BaseModel):
    """Output of the risk gate."""

    decision_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    signal_id: str
    approved: bool
    requested_size_usd: float
    approved_size_usd: float
    limits_hit: list[str] = Field(default_factory=list)
    reason: str = ""
    circuit_breaker_level: int = 0
    var_pre: float | None = None
    var_post: float | None = None
    autonomy_tier: int = 0
    config_hash: str = ""
    # GE — human-approval queue
    requires_human_approval: bool = False
    status: str = "decided"  # decided | pending | approved | expired | rejected


class RiskGate:
    """
    Pre-trade risk gate.

    Consumes BlendedSignal from L4, applies all risk checks, returns RiskDecision.

    Checks (all must pass for approval):
    1. Kill switch not active
    2. Volatility circuit breaker < Level 2
    3. Risk circuit breaker not tripped
    4. Account allocation: resulting exposure ≤ max_gross_exposure_pct
    5. Risk fraction: position risk / equity ≤ risk_fraction_cap
    6. Risk amount: $ at risk ≤ risk_amount_cap
    7. Reward:risk: expected_reward / risk_amount ≥ reward_risk_min
    8. Autonomy gate: tier-appropriate approval
    """

    def __init__(
        self,
        portfolio_state: PortfolioStateService,
        vol_breaker: VolatilityCircuitBreaker,
        risk_breaker: RiskCircuitBreaker,
        kill_switch: KillSwitch,
        var_calculator: VaRCalculator,
        max_gross_exposure_pct: float = 1.50,
        risk_fraction_cap: float = 0.02,
        risk_amount_cap: float = 1000,
        reward_risk_min: float = 1.5,
        config_hash: str = "",
    ) -> None:
        self._state = portfolio_state
        self._vol_breaker = vol_breaker
        self._risk_breaker = risk_breaker
        self._kill_switch = kill_switch
        self._var_calc = var_calculator
        self._max_gross = max_gross_exposure_pct
        self._risk_frac_cap = risk_fraction_cap
        self._risk_amt_cap = risk_amount_cap
        self._rr_min = reward_risk_min
        self._config_hash = config_hash
        self._stats = {"checked": 0, "approved": 0, "rejected": 0}

    def evaluate(
        self,
        signal: BlendedSignal,
        atr_baseline: float | None = None,
        atr_current: float | None = None,
        autonomy_tier: int = 0,
    ) -> RiskDecision:
        """
        Evaluate a blended signal against all risk checks.

        Args:
            signal: BlendedSignal from L4
            atr_baseline: Baseline ATR for volatility CB (optional)
            atr_current: Current ATR for volatility CB (optional)
            autonomy_tier: Autonomy tier from AutonomyGate (0-4)

        Returns:
            RiskDecision (approved or rejected + reason)
        """
        self._stats["checked"] += 1
        limits_hit: list[str] = []
        cb_level = 0
        approved_size = signal.final_size_usd

        # 1. Kill switch
        if self._kill_switch.is_active:
            limits_hit.append("kill_switch_active")
            approved_size = 0.0

        # 2. Skip further checks if signal already blocked by L4
        elif signal.entry_strategy in ("block", "skip_entry"):
            limits_hit.append(f"l4_{signal.entry_strategy}")
            approved_size = 0.0

        # 3. Volatility circuit breaker
        elif atr_baseline and atr_current:
            level, _ = self._vol_breaker.check(
                symbol=signal.symbol,
                atr_baseline=atr_baseline,
                atr_current=atr_current,
                expected_edge_bps=signal.expected_entry_alpha_bps,
                meta_regime=signal.meta_regime,
            )
            cb_level = level
            if level >= BreakerLevel.BLOCK_ENTRIES:
                limits_hit.append(f"volatility_cb_level_{level}")
                approved_size = 0.0
            elif level == BreakerLevel.REDUCE_50:
                approved_size *= 0.5
                limits_hit.append("volatility_cb_reduce_50")

        # 4. Risk circuit breaker
        if approved_size > 0 and self._risk_breaker.is_tripped():
            limits_hit.append("risk_cb_tripped")
            approved_size = 0.0

        # 5. Account allocation check
        if approved_size > 0:
            metrics = self._state.get_metrics()
            resulting_exposure = metrics.gross_exposure_usd + approved_size
            max_exposure = metrics.equity_total * self._max_gross
            if resulting_exposure > max_exposure:
                approved_size = max(0, max_exposure - metrics.gross_exposure_usd)
                limits_hit.append("max_gross_exposure_pct")

        # 6. Risk fraction check
        if approved_size > 0:
            metrics = self._state.get_metrics()
            stop_distance_pct = abs(signal.nt_entry_price - signal.nt_stop_price) / signal.nt_entry_price
            risk_amount = approved_size * stop_distance_pct
            risk_fraction = risk_amount / metrics.equity_total if metrics.equity_total > 0 else 0
            if risk_fraction > self._risk_frac_cap:
                max_by_frac = self._risk_frac_cap * metrics.equity_total / stop_distance_pct if stop_distance_pct > 0 else 0
                approved_size = min(approved_size, max_by_frac)
                limits_hit.append("risk_fraction_cap")

        # 7. Risk amount check
        if approved_size > 0:
            stop_distance_pct = abs(signal.nt_entry_price - signal.nt_stop_price) / signal.nt_entry_price
            risk_amount = approved_size * stop_distance_pct
            if risk_amount > self._risk_amt_cap:
                max_by_risk = self._risk_amt_cap / stop_distance_pct if stop_distance_pct > 0 else 0
                approved_size = min(approved_size, max_by_risk)
                limits_hit.append("risk_amount_cap")

        # 8. Reward:risk check
        if approved_size > 0:
            reward = abs(signal.nt_target_price - signal.nt_entry_price)
            risk = abs(signal.nt_entry_price - signal.nt_stop_price)
            rr = reward / risk if risk > 0 else 0
            if rr < self._rr_min:
                limits_hit.append(f"reward_risk_too_low:{rr:.2f}")
                approved_size = 0.0

        # Compute VaR pre/post (simplified)
        var_pre = None
        var_post = None
        metrics = self._state.get_metrics()
        if metrics.equity_total > 0:
            var_pre, _ = self._var_calc.compute(
                confidence=0.99,
                position_value=metrics.equity_total,
            )
            if approved_size > 0:
                var_post, _ = self._var_calc.compute(
                    confidence=0.99,
                    position_value=metrics.equity_total + approved_size,
                )

        # A size-capped trade is APPROVED at the reduced size. Only a hard limit that
        # drives approved_size to 0 is a true blocker. Capping limits (e.g.
        # volatility_cb_reduce, notional/size caps) reduce size but must not reject.
        approved = approved_size > 0

        if approved:
            self._stats["approved"] += 1
            reason = "approved"
        else:
            self._stats["rejected"] += 1
            reason = f"rejected:{','.join(limits_hit)}" if limits_hit else "rejected:unknown"

        return RiskDecision(
            signal_id=signal.signal_id,
            approved=approved,
            requested_size_usd=signal.final_size_usd,
            approved_size_usd=round(approved_size, 2),
            limits_hit=limits_hit,
            reason=reason,
            circuit_breaker_level=cb_level,
            var_pre=round(var_pre, 2) if var_pre else None,
            var_post=round(var_post, 2) if var_post else None,
            autonomy_tier=autonomy_tier,
            config_hash=self._config_hash,
        )

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
