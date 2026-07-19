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
        tier1_max_notional: float = 2000,
        tier1_max_position_pct: float = 0.015,
        tier3_max_notional: float = 25000,
        active_hours_start: str = "09:30",
        active_hours_end: str = "16:00",
        crypto_24_7: bool = True,
        degrade_outside_hours: bool = True,
        timezone_name: str = "America/Los_Angeles",
        # Cold-start overrides (tighter caps for unproven accounts)
        cold_start_enabled: bool = False,
        cold_start_tier1_notional: float = 100,
        cold_start_tier1_pct: float = 0.002,
        cold_start_max_new_positions: int = 3,
        cold_start_max_new_exposure_pct: float = 0.05,
        # Per-tier config key classifications (loaded from config/default.yaml)
        tier2_config_keys: list[str] | None = None,
        tier3_config_keys: list[str] | None = None,
        tier4_config_keys: list[str] | None = None,
    ) -> None:
        self._tier1_max = tier1_max_notional
        self._tier1_pct = tier1_max_position_pct
        self._tier3_max = tier3_max_notional
        self._active_start = active_hours_start
        self._active_end = active_hours_end
        self._crypto_24_7 = crypto_24_7
        self._degrade = degrade_outside_hours
        self._tz = timezone_name
        # Cold-start state
        self._cold_start = cold_start_enabled
        self._cs_tier1_max = cold_start_tier1_notional
        self._cs_tier1_pct = cold_start_tier1_pct
        self._cs_max_new_pos = cold_start_max_new_positions
        self._cs_max_new_exp_pct = cold_start_max_new_exposure_pct
        self._cs_new_positions = 0          # running count of new positions this session
        self._cs_new_exposure = 0.0         # running new exposure this session
        # Per-tier config key lists — used by classify_config_change()
        self._tier2_keys = set(tier2_config_keys or [])
        self._tier3_keys = set(tier3_config_keys or [])
        self._tier4_keys = set(tier4_config_keys or [])
        self._stats = {"tier0": 0, "tier1": 0, "tier2": 0, "tier3": 0, "tier4": 0}
        self._paper_mode = False  # backtest/simulation: bypass human-approval tiers

    def set_paper_mode(self, enabled: bool = True) -> None:
        """Enable paper/backtest mode: trade actions auto-approved (tier 0).

        Used by BacktestEngine so simulated replays don't get blocked by the
        live autonomy gate (human approval). Backtests are tier-0 per config.
        """
        self._paper_mode = enabled

    def classify_config_change(
        self,
        key_path: str,
        caller: str = "human",
    ) -> AutonomyDecision:
        """Classify a config key change into an autonomy tier.

        Used by `platform config set` and `platform config promote` to enforce
        per-key tier rules. The tier depends on which list the key is in:

        - Tier 4 (human_only): structural keys — agent is hard-blocked.
          Humans can change via `platform config set` (authenticated).
        - Tier 3 (human approval required): risk-sensitive keys — agent
          must request approval; humans can change directly.
        - Tier 2 (auto-promote + notify): tunable parameters — agent
          can auto-promote after shadow validation.
        - Default (uncategorized): treated as tier 3 (conservative).

        Args:
            key_path: Dotted config path (e.g. 'entry.brick_confirmation_count')
            caller: 'human' | 'hermes' — who is making the change

        Returns:
            AutonomyDecision with tier, approved, requires_human_approval
        """
        # Tier 4: structural keys — always require human, agent is blocked
        if key_path in self._tier4_keys:
            self._stats["tier4"] += 1
            if caller == "hermes":
                return AutonomyDecision(
                    tier=4,
                    approved=False,
                    requires_human_approval=True,
                    reason=f"structural_key_blocked_for_agent: {key_path}",
                )
            return AutonomyDecision(
                tier=4,
                approved=True,
                requires_human_approval=False,
                reason=f"structural_key_human_override: {key_path}",
            )

        # Tier 3: risk-sensitive keys — human approval required for agent
        if key_path in self._tier3_keys:
            self._stats["tier3"] += 1
            if caller == "hermes":
                return AutonomyDecision(
                    tier=3,
                    approved=False,
                    requires_human_approval=True,
                    reason=f"risk_sensitive_key_requires_approval: {key_path}",
                    timeout_hours=4.0,
                )
            return AutonomyDecision(
                tier=3,
                approved=True,
                requires_human_approval=False,
                reason=f"risk_sensitive_key_human_override: {key_path}",
            )

        # Tier 2: tunable parameters — agent can auto-promote
        if key_path in self._tier2_keys:
            self._stats["tier2"] += 1
            return AutonomyDecision(
                tier=2,
                approved=True,
                requires_human_approval=False,
                reason=f"tunable_key_auto_promote: {key_path}",
                notify_channels=["discord", "email"],
            )

        # Uncategorized key — conservative default to tier 3
        self._stats["tier3"] += 1
        if caller == "hermes":
            return AutonomyDecision(
                tier=3,
                approved=False,
                requires_human_approval=True,
                reason=f"uncategorized_key_requires_approval: {key_path}",
                timeout_hours=4.0,
            )
        return AutonomyDecision(
            tier=3,
            approved=True,
            requires_human_approval=False,
            reason=f"uncategorized_key_human_override: {key_path}",
        )

    def set_cold_start_state(self, active: bool) -> None:
        """Engine flips this when cold-start exit criteria are met."""
        self._cold_start = active

    def note_new_position(self, notional_usd: float) -> None:
        """Record a newly opened position (for cold-start budget tracking)."""
        self._cs_new_positions += 1
        self._cs_new_exposure += notional_usd

    def is_cold_start(self) -> bool:
        return self._cold_start

    def get_cold_start_budget(self) -> dict:
        return {
            "active": self._cold_start,
            "new_positions": self._cs_new_positions,
            "new_exposure": self._cs_new_exposure,
            "max_new_positions": self._cs_max_new_pos,
            "max_new_exposure_pct": self._cs_max_new_exp_pct,
        }

    def classify(
        self,
        action_type: str,
        notional_usd: float,
        equity: float,
        is_crypto: bool = False,
        is_novel_strategy: bool = False,
        # Cold-start live budget (engine supplies current state)
        cs_new_positions: int | None = None,
        cs_new_exposure: float | None = None,
    ) -> AutonomyDecision:
        """
        Classify an action into an autonomy tier.

        Args:
            action_type: "enter_trade" | "close_trade" | "promote_config" | "structural_change" | "query"
            notional_usd: Position size in USD
            equity: Current account equity
            is_crypto: Whether this is a crypto trade (24/7 market)
            is_novel_strategy: Whether the strategy hasn't been seen recently
            cs_new_positions: current new-position count (cold-start budget)
            cs_new_exposure: current new-exposure USD (cold-start budget)

        Returns:
            AutonomyDecision with tier, approved, requires_human_approval
        """
        # Cold-start budget enforcement (enter_trade only)
        if self._cold_start and action_type == "enter_trade":
            cur_pos = self._cs_new_positions if cs_new_positions is None else cs_new_positions
            cur_exp = self._cs_new_exposure if cs_new_exposure is None else cs_new_exposure
            if cur_pos >= self._cs_max_new_pos:
                self._stats["tier3"] += 1
                return AutonomyDecision(
                    tier=3,
                    approved=False,
                    requires_human_approval=True,
                    reason=f"cold_start_max_new_positions_reached:{cur_pos}/{self._cs_max_new_pos}",
                    timeout_hours=4.0,
                )
            if equity > 0 and (cur_exp + notional_usd) > equity * self._cs_max_new_exp_pct:
                self._stats["tier3"] += 1
                return AutonomyDecision(
                    tier=3,
                    approved=False,
                    requires_human_approval=True,
                    reason=f"cold_start_max_new_exposure_reached:{cur_exp + notional_usd:.0f}/{equity * self._cs_max_new_exp_pct:.0f}",
                    timeout_hours=4.0,
                )

        # Paper/backtest mode: auto-approve all trade actions (tier 0)
        if self._paper_mode and action_type not in ("query", "run_backtest", "generate_report", "run_optimization"):
            self._stats["tier0"] += 1
            return AutonomyDecision(
                tier=0, approved=True, requires_human_approval=False,
                reason="paper_mode_backtest_auto_approve",
            )

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
        # In cold-start, use the tighter caps.
        tier1_max = self._cs_tier1_max if self._cold_start else self._tier1_max
        tier1_pct = self._cs_tier1_pct if self._cold_start else self._tier1_pct

        position_pct = notional_usd / equity if equity > 0 else 0

        # Check active hours (skip for crypto if 24/7)
        outside_hours = self._is_outside_active_hours() and not (is_crypto and self._crypto_24_7)

        if outside_hours and self._degrade:
            # Degrade tier 1 → tier 3 outside active hours
            if notional_usd <= tier1_max:
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

        # Tier 1: small trade within caps (cold-start or normal)
        if notional_usd <= tier1_max and position_pct <= tier1_pct:
            self._stats["tier1"] += 1
            return AutonomyDecision(
                tier=1,
                approved=True,
                requires_human_approval=False,
                reason=f"within_tier1_caps{'_cold_start' if self._cold_start else ''}",
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
