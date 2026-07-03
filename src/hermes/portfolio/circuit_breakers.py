"""
Circuit Breakers — volatility + risk + kill switch.

Volatility CB (per-asset, pre-trade):
  Level 1: reduce size by 50%
  Level 2: block new entries
  Level 3: tighten stops
  Level 4: liquidate

Risk CB (portfolio, continuous):
  - Portfolio DD breach → halt + hedge
  - Asset DD breach → close that asset
  - VaR/CVaR breach → de-risk
  - Margin proximity → emergency deleverage
  - Funding spike → close funding-negative
  - Venue disconnect → cancel orders

Kill Switch:
  - Manual (Redis agent.command or human)
  - Auto triggers: daily loss, venue disconnect, audit failure

See roadmap §4.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class BreakerLevel(IntEnum):
    NONE = 0
    REDUCE_50 = 1
    BLOCK_ENTRIES = 2
    TIGHTEN_STOPS = 3
    LIQUIDATE = 4


class CircuitBreakerEvent(BaseModel):
    """An event emitted when a circuit breaker trips."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    breaker_type: str  # volatility | risk | kill_switch
    level: int
    symbol: str | None = None
    trigger_value: float
    threshold: float
    action_taken: str
    payload: dict = Field(default_factory=dict)


class VolatilityCircuitBreaker:
    """
    Per-asset volatility circuit breaker.

    Compares current ATR to baseline ATR:
    - Level 1 (reduce 50%): atr_current / atr_baseline > vol_mult_threshold
    - Level 2 (block entries): stronger violation
    - Level 3 (tighten stops): extreme vol
    - Level 4 (liquidate): confirmed risk_off regime
    """

    def __init__(
        self,
        vol_mult_threshold: float = 2.5,
        k_constant: float = 0.3,
    ) -> None:
        self._vol_mult_threshold = vol_mult_threshold
        self._k = k_constant
        self._current_level: dict[str, BreakerLevel] = {}
        self._stats = {"trips": 0}

    def check(
        self,
        symbol: str,
        atr_baseline: float,
        atr_current: float,
        expected_edge_bps: float = 0.0,
        meta_regime: str = "",
    ) -> tuple[BreakerLevel, CircuitBreakerEvent | None]:
        """
        Check volatility circuit breaker for a symbol.

        Returns (level, event_if_tripped).
        """
        if atr_baseline <= 0:
            return BreakerLevel.NONE, None

        ratio = atr_current / atr_baseline
        edge_vs_vol = expected_edge_bps / (atr_current * 10000) if atr_current > 0 else 0

        # Level 4: liquidate on confirmed risk_off
        if meta_regime == "risk_off":
            level = BreakerLevel.LIQUIDATE
        # Level 3: extreme vol (4x baseline)
        elif ratio > self._vol_mult_threshold * 1.6:
            level = BreakerLevel.TIGHTEN_STOPS
        # Level 2: high vol + edge too small
        elif ratio > self._vol_mult_threshold and edge_vs_vol < self._k:
            level = BreakerLevel.BLOCK_ENTRIES
        # Level 1: vol elevated
        elif ratio > self._vol_mult_threshold:
            level = BreakerLevel.REDUCE_50
        else:
            level = BreakerLevel.NONE

        prev = self._current_level.get(symbol, BreakerLevel.NONE)
        self._current_level[symbol] = level

        if level > prev:
            self._stats["trips"] += 1
            actions = {
                BreakerLevel.REDUCE_50: "reduce_size_50pct",
                BreakerLevel.BLOCK_ENTRIES: "block_new_entries",
                BreakerLevel.TIGHTEN_STOPS: "tighten_stops",
                BreakerLevel.LIQUIDATE: "liquidate",
            }
            event = CircuitBreakerEvent(
                breaker_type="volatility",
                level=level,
                symbol=symbol,
                trigger_value=ratio,
                threshold=self._vol_mult_threshold,
                action_taken=actions.get(level, "none"),
                payload={
                    "atr_baseline": atr_baseline,
                    "atr_current": atr_current,
                    "ratio": ratio,
                    "expected_edge_bps": expected_edge_bps,
                    "meta_regime": meta_regime,
                },
            )
            log.warning(
                "volatility_cb_tripped",
                symbol=symbol,
                level=level,
                ratio=ratio,
                threshold=self._vol_mult_threshold,
            )
            return level, event

        return level, None

    def get_level(self, symbol: str) -> BreakerLevel:
        return self._current_level.get(symbol, BreakerLevel.NONE)

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()


class RiskCircuitBreaker:
    """
    Portfolio-level risk circuit breaker (continuous monitoring).

    Checks:
    - Portfolio DD breach → halt + hedge
    - Asset DD breach → close that asset
    - VaR/CVaR breach → de-risk
    - Margin proximity → emergency deleverage
    - Funding spike → close funding-negative
    """

    def __init__(
        self,
        max_portfolio_drawdown_pct: float = 0.15,
        max_asset_drawdown_pct: float = 0.08,
        daily_loss_limit_pct: float = 0.03,
        var_breach_threshold: float = 0.05,  # 5% of equity
        margin_proximity_pct: float = 0.80,  # 80% margin used
    ) -> None:
        self._max_portfolio_dd = max_portfolio_drawdown_pct
        self._max_asset_dd = max_asset_drawdown_pct
        self._daily_loss_limit = daily_loss_limit_pct
        self._var_threshold = var_breach_threshold
        self._margin_proximity = margin_proximity_pct
        self._tripped: dict[str, CircuitBreakerEvent] = {}  # check_name → event
        self._stats = {"trips": 0}

    def check_portfolio(
        self,
        drawdown_pct: float,
        daily_pnl_pct: float,
        var_1d_99: float | None,
        equity: float,
        margin_used_pct: float,
    ) -> list[CircuitBreakerEvent]:
        """Check portfolio-level risk conditions. Returns list of tripped events."""
        events: list[CircuitBreakerEvent] = []
        now = datetime.now(timezone.utc)

        # Portfolio DD breach
        if drawdown_pct > self._max_portfolio_dd:
            event = CircuitBreakerEvent(
                breaker_type="risk",
                level=4,
                trigger_value=drawdown_pct,
                threshold=self._max_portfolio_dd,
                action_taken="halt_and_hedge",
                payload={"check": "portfolio_dd", "drawdown_pct": drawdown_pct},
            )
            events.append(event)
            self._tripped["portfolio_dd"] = event

        # Daily loss limit
        if daily_pnl_pct < -self._daily_loss_limit:
            event = CircuitBreakerEvent(
                breaker_type="risk",
                level=3,
                trigger_value=daily_pnl_pct,
                threshold=-self._daily_loss_limit,
                action_taken="halt_new_entries",
                payload={"check": "daily_loss", "daily_pnl_pct": daily_pnl_pct},
            )
            events.append(event)
            self._tripped["daily_loss"] = event

        # VaR breach
        if var_1d_99 is not None and equity > 0:
            var_pct = abs(var_1d_99) / equity
            if var_pct > self._var_threshold:
                event = CircuitBreakerEvent(
                    breaker_type="risk",
                    level=2,
                    trigger_value=var_pct,
                    threshold=self._var_threshold,
                    action_taken="de_risk",
                    payload={"check": "var_breach", "var_1d_99": var_1d_99, "var_pct": var_pct},
                )
                events.append(event)
                self._tripped["var_breach"] = event

        # Margin proximity
        if margin_used_pct > self._margin_proximity:
            event = CircuitBreakerEvent(
                breaker_type="risk",
                level=3,
                trigger_value=margin_used_pct,
                threshold=self._margin_proximity,
                action_taken="emergency_deleverage",
                payload={"check": "margin_proximity", "margin_used_pct": margin_used_pct},
            )
            events.append(event)
            self._tripped["margin_proximity"] = event

        self._stats["trips"] += len(events)
        return events

    def is_tripped(self) -> bool:
        """Returns True if any risk circuit breaker is currently tripped."""
        return len(self._tripped) > 0

    def get_tripped(self) -> dict[str, CircuitBreakerEvent]:
        return self._tripped.copy()

    def clear(self, check_name: str | None = None) -> None:
        """Clear tripped breakers (after conditions normalize)."""
        if check_name:
            self._tripped.pop(check_name, None)
        else:
            self._tripped.clear()

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()


class KillSwitch:
    """
    Global kill switch — stops all trading activity.

    Triggers:
    - Manual (via Redis `agent.command` channel with `{"action": "flatten"}`)
    - Daily loss limit hit
    - Venue connectivity loss > N seconds
    - Audit log write failure

    When active:
    - No new entries
    - All resting orders cancelled
    - Optionally flatten all positions
    """

    def __init__(self) -> None:
        self._active = False
        self._reason: str | None = None
        self._activated_at: datetime | None = None
        self._flatten_requested = False
        self._stats = {"activations": 0}

    def activate(self, reason: str, flatten: bool = False) -> None:
        """Activate the kill switch."""
        if self._active:
            return
        self._active = True
        self._reason = reason
        self._activated_at = datetime.now(timezone.utc)
        self._flatten_requested = flatten
        self._stats["activations"] += 1
        log.critical(
            "kill_switch_activated",
            reason=reason,
            flatten=flatten,
            activated_at=self._activated_at.isoformat(),
        )

    def deactivate(self) -> None:
        """Deactivate the kill switch (manual recovery)."""
        if not self._active:
            return
        log.info("kill_switch_deactivated", was_reason=self._reason)
        self._active = False
        self._reason = None
        self._activated_at = None
        self._flatten_requested = False

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def reason(self) -> str | None:
        return self._reason

    @property
    def flatten_requested(self) -> bool:
        return self._flatten_requested

    @property
    def activated_at(self) -> datetime | None:
        return self._activated_at

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["active"] = self._active
        stats["reason"] = self._reason
        return stats
