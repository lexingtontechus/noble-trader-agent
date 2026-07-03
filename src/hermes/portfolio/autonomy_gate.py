"""
Autonomy Gate — tier-based approval for Hermes actions.

Every Hermes action passes through this gate before reaching L3 (execution).
Implements the 5-tier autonomy matrix from §3.5.

Tier 0: autonomous (read & analyze only)
Tier 1: autonomous within size cap (small trades)
Tier 2: auto-promote config, human notified
Tier 3: human approval required (large / novel)
Tier 4: hard block (structural changes)
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class AutonomyDecision(BaseModel):
    """Output of the autonomy gate."""

    tier: int
    approved: bool
    requires_human_approval: bool
    reason: str = ""
    timeout_hours: float = 0.0
    notify_channels: list[str] = Field(default_factory=list)


class AutonomyGate:
    """
    Tier-based autonomy gate.

    Classifies each action into a tier and determines if it needs human approval.
    """

    def __init__(
        self,
        tier1_max_notional: float = 5000,
        tier1_max_position_pct: float = 0.02,
        tier3_max_notional: float = 25000,
        active_hours_start: str = "09:30",
        active_hours_end: str = "16:00",
        crypto_24_7: bool = True,
        degrade_outside_hours: bool = True,
        timezone_name: str = "America/Los_Angeles",
    ) -> None:
        self._tier1_max = tier1_max_notional
        self._tier1_pct = tier1_max_position_pct
        self._tier3_max = tier3_max_notional
        self._active_start = active_hours_start
        self._active_end = active_hours_end
        self._crypto_24_7 = crypto_24_7
        self._degrade = degrade_outside_hours
        self._tz = timezone_name
        self._stats = {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0, "tier4": 0}

    def classify(
        self,
        action_type: str,
        notional_usd: float,
        equity: float,
        is_crypto: bool = False,
        is_novel_strategy: bool = False,
    ) -> AutonomyDecision:
        """
        Classify an action into an autonomy tier.

        Args:
            action_type: "enter_trade" | "close_trade" | "promote_config" | "structural_change" | "query"
            notional_usd: Position size in USD
            equity: Current account equity
            is_crypto: Whether this is a crypto trade (24/7 market)
            is_novel_strategy: Whether the strategy hasn't been seen recently

        Returns:
            AutonomyDecision with tier, approved, requires_human_approval
        """
        # Tier 4: structural changes — always blocked
        if action_type == "structural_change":
            self._stats["tier4"] += 1
            return AutonomyDecision(
                tier=4,
                approved=False,
                requires_human_approval=True,
                reason="structural_change_requires_human",
            )

        # Tier 0: read-only actions
        if action_type in ("query", "run_backtest", "generate_report", "run_optimization"):
            self._stats["tier0"] += 1
            return AutonomyDecision(
                tier=0,
                approved=True,
                requires_human_approval=False,
                reason="read_only_action",
            )

        # Tier 2: config promotion
        if action_type == "promote_config":
            self._stats["tier2"] += 1
            return AutonomyDecision(
                tier=2,
                approved=True,
                requires_human_approval=False,
                reason="config_promotion_notify_only",
                notify_channels=["discord", "email"],
            )

        # Trade actions: tier 1 or 3 based on size
        position_pct = notional_usd / equity if equity > 0 else 0

        # Check active hours (skip for crypto if 24/7)
        outside_hours = self._is_outside_active_hours() and not (is_crypto and self._crypto_24_7)

        if outside_hours and self._degrade:
            # Degrade tier 1 → tier 3 outside active hours
            if notional_usd <= self._tier1_max:
                self._stats["tier3"] += 1
                return AutonomyDecision(
                    tier=3,
                    approved=False,
                    requires_human_approval=True,
                    reason=f"outside_active_hours_degraded",
                    timeout_hours=4.0,
                )

        # Tier 3: large or novel
        if notional_usd > self._tier3_max or is_novel_strategy:
            self._stats["tier3"] += 1
            return AutonomyDecision(
                tier=3,
                approved=False,
                requires_human_approval=True,
                reason=f"{'large_size' if notional_usd > self._tier3_max else 'novel_strategy'}",
                timeout_hours=4.0,
            )

        # Tier 1: small trade within caps
        if notional_usd <= self._tier1_max and position_pct <= self._tier1_pct:
            self._stats["tier1"] += 1
            return AutonomyDecision(
                tier=1,
                approved=True,
                requires_human_approval=False,
                reason="within_tier1_caps",
            )

        # Between tier 1 and tier 3 — treat as tier 3
        self._stats["tier3"] += 1
        return AutonomyDecision(
            tier=3,
            approved=False,
            requires_human_approval=True,
            reason="between_tier1_and_tier3",
            timeout_hours=4.0,
        )

    def _is_outside_active_hours(self) -> bool:
        """Check if current time is outside active trading hours."""
        try:
            from zoneinfo import ZoneInfo

            tz = ZoneInfo(self._tz)
            now = datetime.now(tz)
            current_time = now.strftime("%H:%M")

            # Simple string comparison works for HH:MM format
            return not (self._active_start <= current_time <= self._active_end)
        except Exception:
            return False  # If timezone fails, assume active

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
