"""
Sizing module — Trust NT's effective_kelly + portfolio overlay.

Hermes does NOT re-derive Kelly or Masaniello from scratch. It trusts
NT's effective_kelly as the baseline and applies a portfolio-level
multiplier from the 7-state meta-regime.

See roadmap §2.2.2 for full design.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, Field

from hermes.signals.meta_regime import MetaRegimeResult

log = structlog.get_logger(__name__)


class SizingResult(BaseModel):
    """Output of the sizing module."""

    baseline_size_usd: float
    sizing_multiplier: float
    dd_adjustment: float
    size_after_dd: float
    final_size_usd: float
    final_size_pct_of_equity: float
    risk_amount_usd: float
    limits_hit: list[str] = Field(default_factory=list)
    reason: str = ""


class SizingEngine:
    """
    Computes final position size using trust + overlay approach.

    baseline_size = equity * NT_effective_kelly * meta_regime_sizing_multiplier
    dd_adjusted = baseline * clip(1 - portfolio_dd / max_dd, 0.25, 1.0)
    final_size = min(dd_adjusted, max_position_pct, max_notional, gross_exposure_headroom, risk_amount_cap)
    """

    def __init__(
        self,
        max_position_size_pct: float = 0.05,
        max_position_notional: float = 25000,
        max_gross_exposure_pct: float = 1.50,
        risk_amount_cap: float = 1000,
        max_portfolio_drawdown_pct: float = 0.15,
        dd_floor: float = 0.25,  # minimum sizing multiplier even in deep DD
    ) -> None:
        self._max_position_pct = max_position_size_pct
        self._max_notional = max_position_notional
        self._max_gross_exposure = max_gross_exposure_pct
        self._risk_amount_cap = risk_amount_cap
        self._max_dd = max_portfolio_drawdown_pct
        self._dd_floor = dd_floor

    def compute(
        self,
        equity: float,
        nt_effective_kelly: float,
        meta_regime: MetaRegimeResult,
        portfolio_drawdown_pct: float,
        current_gross_exposure_usd: float,
        stop_distance_pct: float,  # |entry - stop| / entry
    ) -> SizingResult:
        """
        Compute final position size.

        Args:
            equity: Current account equity in USD
            nt_effective_kelly: NT's effective_kelly from heartbeat (e.g., 0.12)
            meta_regime: Current meta-regime result (provides sizing_multiplier)
            portfolio_drawdown_pct: Current portfolio drawdown (0.0 = no DD, 0.15 = 15% DD)
            current_gross_exposure_usd: Current total exposure across all positions
            stop_distance_pct: Stop-loss distance as fraction of entry price

        Returns:
            SizingResult with final_size_usd and all intermediate values
        """
        limits_hit: list[str] = []

        # 1. Baseline size from NT's effective_kelly × meta-regime multiplier
        baseline = equity * nt_effective_kelly * meta_regime.sizing_multiplier
        if meta_regime.sizing_multiplier == 0.0:
            return SizingResult(
                baseline_size_usd=baseline,
                sizing_multiplier=meta_regime.sizing_multiplier,
                dd_adjustment=0.0,
                size_after_dd=0.0,
                final_size_usd=0.0,
                final_size_pct_of_equity=0.0,
                risk_amount_usd=0.0,
                limits_hit=["meta_regime_blocks"],
                reason=f"blocked_by_{meta_regime.state}",
            )

        # 2. Drawdown adjustment
        dd_mult = max(1.0 - (portfolio_drawdown_pct / self._max_dd), self._dd_floor)
        dd_mult = min(dd_mult, 1.0)  # never scale up beyond 1.0
        size_after_dd = baseline * dd_mult

        # 3. Apply caps
        final_size = size_after_dd

        # Cap 1: max position size % of equity
        max_by_pct = equity * self._max_position_pct
        if final_size > max_by_pct:
            final_size = max_by_pct
            limits_hit.append("max_position_size_pct")

        # Cap 2: max notional
        if final_size > self._max_notional:
            final_size = self._max_notional
            limits_hit.append("max_position_notional")

        # Cap 3: gross exposure headroom
        max_exposure = equity * self._max_gross_exposure
        available_headroom = max_exposure - current_gross_exposure_usd
        if final_size > available_headroom:
            final_size = max(0, available_headroom)
            limits_hit.append("max_gross_exposure_pct")

        # Cap 4: risk amount cap (stop distance × size must not exceed cap)
        if stop_distance_pct > 0:
            max_by_risk = self._risk_amount_cap / stop_distance_pct
            if final_size > max_by_risk:
                final_size = max_by_risk
                limits_hit.append("risk_amount_cap")

        # Compute risk amount
        risk_amount = final_size * stop_distance_pct

        reason = "ok" if not limits_hit else f"capped_by:{','.join(limits_hit)}"

        return SizingResult(
            baseline_size_usd=round(baseline, 2),
            sizing_multiplier=meta_regime.sizing_multiplier,
            dd_adjustment=round(dd_mult, 4),
            size_after_dd=round(size_after_dd, 2),
            final_size_usd=round(final_size, 2),
            final_size_pct_of_equity=round(final_size / equity, 6) if equity > 0 else 0,
            risk_amount_usd=round(risk_amount, 2),
            limits_hit=limits_hit,
            reason=reason,
        )
