"""
7-State Meta-Regime Classifier (Hermes's portfolio-level risk overlay).

States:
1. calm_trend         — Low vol + clear trend, low cross-asset correlation
2. choppy_range       — Mean-reverting, no trend, low vol
3. high_vol_breakout  — High vol but directional conviction strong
4. regime_transition  — State-shifting detected (upstream shift or high entropy)
5. risk_off           — Crisis: cross-asset correlation > 0.75, broad selloff
6. funding_stress     — Crypto: perp basis blowout, funding > 50% annualized
7. liquidity_drained  — Thin book, wide spreads, low volume

This is a RULE-BASED classifier that uses inputs from Phase 2 monitors
(cross-asset correlation, funding rates, L2 depth) plus the upstream
Noble Trader regime label. An optional Gaussian HMM can be trained on
portfolio returns for a more sophisticated approach (Phase 8 handles
retraining).

See roadmap §2.2.1 for full design.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.schemas.heartbeat import NobleTraderHeartbeat

log = structlog.get_logger(__name__)

MetaRegimeState = Literal[
    "calm_trend",
    "choppy_range",
    "high_vol_breakout",
    "regime_transition",
    "risk_off",
    "funding_stress",
    "liquidity_drained",
]

# Sizing multipliers per state (how aggressively to act on NT signals)
SIZING_MULTIPLIERS: dict[str, float] = {
    "calm_trend": 1.0,
    "choppy_range": 0.8,
    "high_vol_breakout": 0.6,
    "regime_transition": 0.3,
    "risk_off": 0.0,
    "funding_stress": 0.2,
    "liquidity_drained": 0.3,
}

# Entry aggressiveness per state
ENTRY_AGGRESSIVENESS: dict[str, str] = {
    "calm_trend": "aggressive",
    "choppy_range": "patient",
    "high_vol_breakout": "cautious",
    "regime_transition": "defensive",
    "risk_off": "block",
    "funding_stress": "block",
    "liquidity_drained": "maker_only",
}


class MetaRegimeResult(BaseModel):
    """Output of the 7-state meta-regime classifier."""

    state: MetaRegimeState
    confidence: float = Field(..., ge=0, le=1)
    posterior_probs: dict[str, float]
    sizing_multiplier: float
    entry_aggressiveness: str
    upstream_regime: str | None = None
    upstream_regime_conf: float | None = None
    trigger: str = "manual"  # what triggered this classification
    trigger_detail: dict = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MetaRegimeClassifier:
    """
    Portfolio-level 7-state meta-regime classifier.

    Uses a rule-based waterfall:
    1. Check crisis conditions (risk_off, funding_stress, liquidity_drained)
    2. Check transition conditions (regime_shift, high entropy)
    3. Map upstream regime to calm_trend / choppy_range / high_vol_breakout

    The rule-based approach is fast (<1ms) and interpretable. An optional
    Gaussian HMM can be trained on portfolio returns for the simulation
    engine to test alternative state counts (5/7/9).
    """

    def __init__(
        self,
        risk_off_corr_threshold: float = 0.75,
        funding_stress_annualized_pct: float = 50.0,
        liquidity_depth_percentile: float = 10,
        transition_entropy_threshold: float = 1.5,
        confidence_floor: float = 0.55,
    ) -> None:
        self._risk_off_corr = risk_off_corr_threshold
        self._funding_stress_pct = funding_stress_annualized_pct
        self._liquidity_pct = liquidity_depth_percentile
        self._transition_entropy = transition_entropy_threshold
        self._confidence_floor = confidence_floor

        # Track regime history per symbol for transition detection
        self._last_state: dict[str, MetaRegimeState] = {}
        self._state_history: dict[str, deque] = defaultdict(lambda: deque(maxlen=500))

        self._stats = {
            "classifications": 0,
            "state_changes": 0,
        }

    def classify(
        self,
        heartbeat: NobleTraderHeartbeat | None = None,
        symbol: str | None = None,
        cross_asset_corr_mean: float | None = None,
        funding_annualized_pct: float | None = None,
        book_depth_percentile: float | None = None,
        spread_percentile: float | None = None,
        posterior_entropy: float | None = None,
        upstream_regime_shift: bool = False,
    ) -> MetaRegimeResult:
        """
        Classify the current portfolio-level regime.

        Args:
            heartbeat: Noble Trader heartbeat (provides upstream regime label)
            symbol: Symbol being classified (for per-symbol tracking)
            cross_asset_corr_mean: Mean |ρ| across portfolio (from CrossPriceMonitor)
            funding_annualized_pct: Annualized funding rate (from FundingWatcher)
            book_depth_percentile: L2 depth percentile (1-100, lower = thinner)
            spread_percentile: Spread percentile (1-100, higher = wider)
            posterior_entropy: HMM posterior entropy in bits (if available)
            upstream_regime_shift: True if NT flagged regime_shift="true"

        Returns:
            MetaRegimeResult with state, confidence, sizing_multiplier, entry_aggressiveness
        """
        self._stats["classifications"] += 1
        sym = symbol or (heartbeat.symbol if heartbeat else "portfolio")

        # Extract upstream regime from heartbeat if provided
        upstream_regime = heartbeat.regime if heartbeat else None
        upstream_conf = heartbeat.regime_conf if heartbeat else None

        # Build posterior probabilities (rule-based → one-hot with soft edges)
        probs = {s: 0.0 for s in SIZING_MULTIPLIERS}
        trigger = "default"
        trigger_detail: dict = {}

        # === Waterfall: check crisis conditions first ===

        # 1. risk_off: cross-asset correlation > threshold
        if cross_asset_corr_mean is not None and cross_asset_corr_mean > self._risk_off_corr:
            probs["risk_off"] = 0.9
            probs["regime_transition"] = 0.1
            trigger = "correlation_crisis"
            trigger_detail = {
                "cross_asset_corr": cross_asset_corr_mean,
                "threshold": self._risk_off_corr,
            }

        # 2. funding_stress: annualized funding > threshold
        elif funding_annualized_pct is not None and abs(funding_annualized_pct) > self._funding_stress_pct:
            probs["funding_stress"] = 0.85
            probs["risk_off"] = 0.1
            probs["regime_transition"] = 0.05
            trigger = "funding_blowout"
            trigger_detail = {
                "funding_annualized_pct": funding_annualized_pct,
                "threshold": self._funding_stress_pct,
            }

        # 3. liquidity_drained: thin book
        elif (
            book_depth_percentile is not None and book_depth_percentile < self._liquidity_pct
        ) or (
            spread_percentile is not None and spread_percentile > (100 - self._liquidity_pct)
        ):
            probs["liquidity_drained"] = 0.8
            probs["regime_transition"] = 0.15
            probs["high_vol_breakout"] = 0.05
            trigger = "low_liquidity"
            trigger_detail = {
                "depth_percentile": book_depth_percentile,
                "spread_percentile": spread_percentile,
                "threshold_pct": self._liquidity_pct,
            }

        # 4. regime_transition: upstream flagged shift OR high entropy
        elif upstream_regime_shift or (
            posterior_entropy is not None and posterior_entropy > self._transition_entropy
        ):
            probs["regime_transition"] = 0.7
            # Distribute remaining probability based on upstream regime
            remaining_states = ["calm_trend", "choppy_range", "high_vol_breakout"]
            for s in remaining_states:
                probs[s] = 0.1
            trigger = "regime_shift" if upstream_regime_shift else "high_entropy"
            trigger_detail = {
                "upstream_shift": upstream_regime_shift,
                "posterior_entropy": posterior_entropy,
                "threshold": self._transition_entropy,
            }

        # 5. Map upstream regime to calm_trend / choppy_range / high_vol_breakout
        else:
            mapped = self._map_upstream_regime(upstream_regime)
            if mapped == "calm_trend":
                probs["calm_trend"] = 0.7
                probs["choppy_range"] = 0.2
                probs["high_vol_breakout"] = 0.1
            elif mapped == "choppy_range":
                probs["choppy_range"] = 0.65
                probs["calm_trend"] = 0.2
                probs["regime_transition"] = 0.15
            elif mapped == "high_vol_breakout":
                probs["high_vol_breakout"] = 0.6
                probs["regime_transition"] = 0.25
                probs["choppy_range"] = 0.15
            trigger = f"upstream_map:{upstream_regime}"
            trigger_detail = {"mapped_state": mapped, "upstream_regime": upstream_regime}

        # Determine dominant state
        state = max(probs, key=probs.get)
        confidence = probs[state]

        # Track state changes
        prev_state = self._last_state.get(sym)
        if prev_state != state:
            self._stats["state_changes"] += 1
            log.info(
                "meta_regime_state_change",
                symbol=sym,
                prev_state=prev_state,
                new_state=state,
                confidence=confidence,
                trigger=trigger,
            )
        self._last_state[sym] = state
        self._state_history[sym].append((datetime.now(timezone.utc), state))

        result = MetaRegimeResult(
            state=state,
            confidence=confidence,
            posterior_probs=probs,
            sizing_multiplier=SIZING_MULTIPLIERS[state],
            entry_aggressiveness=ENTRY_AGGRESSIVENESS[state],
            upstream_regime=upstream_regime,
            upstream_regime_conf=upstream_conf,
            trigger=trigger,
            trigger_detail=trigger_detail,
        )

        return result

    @staticmethod
    def _map_upstream_regime(upstream_regime: str | None) -> MetaRegimeState:
        """
        Map Noble Trader's {vol}_{trend} regime to one of our 3 "normal" states.

        NT regime examples: low_vol_bull, high_vol_strong_bear, med_vol_bull, etc.
        """
        if not upstream_regime or upstream_regime == "unknown":
            return "choppy_range"  # default when we don't know

        regime_lower = upstream_regime.lower()

        # Extract vol and trend components
        vol = "med"
        trend = "flat"
        if "low_vol" in regime_lower:
            vol = "low"
        elif "med_vol" in regime_lower or "med_low" in regime_lower:
            vol = "low"
        elif "high_vol" in regime_lower or "med_high" in regime_lower:
            vol = "high"

        if "strong_bull" in regime_lower or "bull" in regime_lower:
            trend = "bull"
        elif "strong_bear" in regime_lower or "bear" in regime_lower:
            trend = "bear"
        elif "flat" in regime_lower:
            trend = "flat"

        # Map to our states
        if vol == "high" and trend in ("bull", "bear"):
            return "high_vol_breakout"
        elif vol == "low" and trend in ("bull", "bear"):
            return "calm_trend"
        elif trend == "flat" or vol == "low":
            return "choppy_range"
        else:
            return "choppy_range"

    def get_last_state(self, symbol: str) -> MetaRegimeState | None:
        return self._last_state.get(symbol)

    def get_state_history(self, symbol: str, n: int = 100) -> list[tuple[datetime, str]]:
        history = list(self._state_history.get(symbol, []))
        return history[-n:]

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
