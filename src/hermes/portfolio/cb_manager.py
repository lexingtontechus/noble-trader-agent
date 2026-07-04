"""
Advanced Circuit Breaker Manager — unified, configurable, with time-decay + rolling windows.

Implements all breaker types from the risk threshold table:
| Category          | Thresholds              | Action              |
|-------------------|-------------------------|---------------------|
| Portfolio Exposure| 80%, 90%, 100% of equity| Reduce/Block/Halt   |
| Position Size     | $50k, $75k, $100k       | Reduce/Block/Halt   |
| Daily Loss Limit  | $5k, $10k, unlimited    | Block/Halt          |
| VaR               | $50k, $100k             | Block/Halt          |
| Drawdown          | 15%, 20%, 25%           | Reduce/Block/Liquid |
| Funding Rate      | >$50/day                | Temp block          |

Advanced features:
- Time-decay: CB auto-clears after configurable timeout (e.g., DD CB clears after 4h)
- Rolling window: CB checks rolling N-period sum (e.g., 5 losses in 1h → block)
- Tiered actions: each threshold has a specific action (reduce → block → liquidate)

See roadmap §4 + user requirements.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

log = structlog.get_logger(__name__)


class BreakerAction(str, Enum):
    """Actions a circuit breaker can trigger."""

    NONE = "none"
    REDUCE_25 = "reduce_25pct"
    REDUCE_50 = "reduce_50pct"
    BLOCK_ENTRIES = "block_entries"
    TIGHTEN_STOPS = "tighten_stops"
    HALT_ALL = "halt_all"
    LIQUIDATE = "liquidate"
    TEMP_BLOCK = "temp_block"  # temporary block with auto-clear


class BreakerStatus(str, Enum):
    """Status of a circuit breaker."""

    OK = "ok"
    WARNING = "warning"
    TRIPPED = "tripped"
    EXPIRED = "expired"  # time-decay expired


class BreakerTier(BaseModel):
    """A single threshold tier for a circuit breaker."""

    threshold: float
    action: BreakerAction
    label: str = ""
    cooldown_sec: float = 0.0  # time-decay: auto-clear after this many seconds (0 = no decay)


class BreakerConfig(BaseModel):
    """Configuration for a single circuit breaker category."""

    name: str
    enabled: bool = True
    tiers: list[BreakerTier] = Field(default_factory=list)
    description: str = ""

    def get_tier_for_value(self, value: float) -> BreakerTier | None:
        """Find the highest tier whose threshold is exceeded."""
        exceeded = [t for t in self.tiers if value >= t.threshold]
        if not exceeded:
            return None
        return max(exceeded, key=lambda t: t.threshold)


class BreakerTrip(BaseModel):
    """A recorded trip of a circuit breaker."""

    trip_id: str = Field(default_factory=lambda: str(uuid4()))
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    breaker_name: str
    category: str  # portfolio_exposure | position_size | daily_loss | var | drawdown | funding_rate
    tier_label: str
    threshold: float
    trigger_value: float
    action: BreakerAction
    cooldown_sec: float = 0.0
    expires_at: datetime | None = None  # when time-decay expires (None = manual clear only)
    active: bool = True
    payload: dict = Field(default_factory=dict)


class RollingWindowTracker:
    """
    Tracks events in a rolling time window for rolling-window circuit breakers.

    Use cases:
    - "5 losses in 1 hour → block"
    - "3 circuit breaker trips in 24h → halt"
    - "10 trades in 5 min → rate limit"
    """

    def __init__(self, window_sec: float, max_events: int = 10000) -> None:
        self._window_sec = window_sec
        self._events: deque[tuple[float, float]] = deque(maxlen=max_events)  # (monotonic_ts, value)

    def add(self, value: float = 1.0) -> None:
        """Add an event to the rolling window."""
        self._events.append((time.monotonic(), value))

    def sum(self) -> float:
        """Sum of all events in the current window."""
        now = time.monotonic()
        cutoff = now - self._window_sec
        return sum(v for ts, v in self._events if ts >= cutoff)

    def count(self) -> int:
        """Count of events in the current window."""
        now = time.monotonic()
        cutoff = now - self._window_sec
        return sum(1 for ts, _ in self._events if ts >= cutoff)

    def clear(self) -> None:
        """Clear all events."""
        self._events.clear()

    def get_stats(self) -> dict[str, Any]:
        return {
            "window_sec": self._window_sec,
            "event_count": self.count(),
            "event_sum": self.sum(),
        }


class CircuitBreakerManager:
    """
    Unified circuit breaker manager.

    Manages all breaker categories:
    1. Portfolio Exposure (gross exposure / equity)
    2. Position Size (absolute $ notional per position)
    3. Daily Loss Limit ($ absolute)
    4. VaR ($ absolute)
    5. Drawdown (% of peak equity)
    6. Funding Rate ($/day cost)

    Advanced features:
    - Time-decay: breakers auto-clear after configurable cooldown
    - Rolling window: track consecutive losses / trip frequency
    - Tiered actions: reduce → block → liquidate based on severity

    Usage:
        manager = CircuitBreakerManager.from_config(config_dict)
        trips = manager.check_portfolio_exposure(gross_exposure=85000, equity=100000)
        if manager.is_any_tripped():
            # halt trading
    """

    # Default configuration — maps to the user's threshold table
    DEFAULT_CONFIG = {
        "portfolio_exposure": {
            "name": "Portfolio Exposure",
            "enabled": True,
            "description": "Gross exposure as % of equity",
            "tiers": [
                {"threshold": 0.80, "action": "reduce_25pct", "label": "80% exposure", "cooldown_sec": 0},
                {"threshold": 0.90, "action": "reduce_50pct", "label": "90% exposure", "cooldown_sec": 0},
                {"threshold": 1.00, "action": "block_entries", "label": "100% exposure (max)", "cooldown_sec": 0},
                {"threshold": 1.50, "action": "halt_all", "label": "150% exposure (over-leveraged)", "cooldown_sec": 3600},
            ],
        },
        "position_size": {
            "name": "Position Size",
            "enabled": True,
            "description": "Absolute $ notional per position",
            "tiers": [
                {"threshold": 50000, "action": "reduce_25pct", "label": "$50k position", "cooldown_sec": 0},
                {"threshold": 75000, "action": "reduce_50pct", "label": "$75k position", "cooldown_sec": 0},
                {"threshold": 100000, "action": "block_entries", "label": "$100k position (max)", "cooldown_sec": 0},
            ],
        },
        "daily_loss": {
            "name": "Daily Loss Limit",
            "enabled": True,
            "description": "Absolute $ daily loss",
            "tiers": [
                {"threshold": 5000, "action": "reduce_50pct", "label": "$5k daily loss", "cooldown_sec": 0},
                {"threshold": 10000, "action": "block_entries", "label": "$10k daily loss", "cooldown_sec": 14400},  # 4h cooldown
                {"threshold": 15000, "action": "halt_all", "label": "$15k daily loss (critical)", "cooldown_sec": 86400},  # 24h cooldown
            ],
        },
        "var": {
            "name": "Value at Risk",
            "enabled": True,
            "description": "Absolute $ VaR (1-day, 99% confidence)",
            "tiers": [
                {"threshold": 50000, "action": "reduce_50pct", "label": "$50k VaR", "cooldown_sec": 0},
                {"threshold": 100000, "action": "block_entries", "label": "$100k VaR", "cooldown_sec": 3600},
            ],
        },
        "drawdown": {
            "name": "Drawdown",
            "enabled": True,
            "description": "Portfolio drawdown % from peak equity",
            "tiers": [
                {"threshold": 0.15, "action": "reduce_50pct", "label": "15% drawdown", "cooldown_sec": 0},
                {"threshold": 0.20, "action": "block_entries", "label": "20% drawdown", "cooldown_sec": 14400},  # 4h
                {"threshold": 0.25, "action": "liquidate", "label": "25% drawdown (critical)", "cooldown_sec": 86400},  # 24h
            ],
        },
        "funding_rate": {
            "name": "Funding Rate Risk",
            "enabled": True,
            "description": "Daily funding cost in $ for crypto perps",
            "tiers": [
                {"threshold": 50, "action": "temp_block", "label": "$50/day funding", "cooldown_sec": 1800},  # 30min
                {"threshold": 200, "action": "block_entries", "label": "$200/day funding (extreme)", "cooldown_sec": 7200},  # 2h
            ],
        },
        # Rolling window: consecutive losses
        "consecutive_losses": {
            "name": "Consecutive Losses",
            "enabled": True,
            "description": "Rolling window: consecutive losing trades",
            "tiers": [
                {"threshold": 3, "action": "reduce_50pct", "label": "3 consecutive losses", "cooldown_sec": 0},
                {"threshold": 5, "action": "block_entries", "label": "5 consecutive losses", "cooldown_sec": 3600},
            ],
        },
        # Rolling window: trip frequency
        "trip_frequency": {
            "name": "Circuit Breaker Trip Frequency",
            "enabled": True,
            "description": "Rolling 24h: number of CB trips",
            "tiers": [
                {"threshold": 5, "action": "reduce_50pct", "label": "5 trips in 24h", "cooldown_sec": 0},
                {"threshold": 10, "action": "halt_all", "label": "10 trips in 24h (system unstable)", "cooldown_sec": 86400},
            ],
        },
    }

    def __init__(self) -> None:
        self._configs: dict[str, BreakerConfig] = {}
        self._trips: dict[str, BreakerTrip] = {}  # category → active trip
        self._rolling_trackers: dict[str, RollingWindowTracker] = {}
        self._consecutive_loss_count: int = 0
        self._stats = {
            "total_checks": 0,
            "total_trips": 0,
            "total_expired": 0,
            "by_category": defaultdict(int),
        }

    @classmethod
    def from_config(cls, config_dict: dict | None = None) -> CircuitBreakerManager:
        """Create manager from configuration dict (uses defaults if None)."""
        manager = cls()
        configs = config_dict or cls.DEFAULT_CONFIG

        for category, cfg in configs.items():
            if isinstance(cfg, dict):
                manager._configs[category] = BreakerConfig(**cfg)
            elif isinstance(cfg, BreakerConfig):
                manager._configs[category] = cfg

        # Initialize rolling window trackers
        manager._rolling_trackers["consecutive_losses"] = RollingWindowTracker(
            window_sec=86400,  # 24h window for loss tracking
        )
        manager._rolling_trackers["trip_frequency"] = RollingWindowTracker(
            window_sec=86400,  # 24h window for trip counting
        )

        return manager

    def check_portfolio_exposure(
        self,
        gross_exposure_usd: float,
        equity: float,
    ) -> list[BreakerTrip]:
        """Check portfolio exposure as % of equity."""
        if equity <= 0:
            return []
        exposure_pct = gross_exposure_usd / equity
        return self._check_tiered("portfolio_exposure", exposure_pct, {
            "gross_exposure_usd": gross_exposure_usd,
            "equity": equity,
            "exposure_pct": exposure_pct,
        })

    def check_position_size(
        self,
        position_notional_usd: float,
        symbol: str = "",
    ) -> list[BreakerTrip]:
        """Check absolute position size in $."""
        return self._check_tiered("position_size", position_notional_usd, {
            "symbol": symbol,
            "position_notional_usd": position_notional_usd,
        })

    def check_daily_loss(
        self,
        daily_loss_usd: float,
    ) -> list[BreakerTrip]:
        """Check absolute daily loss in $."""
        # Only trigger on losses (negative PnL)
        if daily_loss_usd >= 0:
            self._clear_if_tripped("daily_loss")
            return []
        loss_amount = abs(daily_loss_usd)
        return self._check_tiered("daily_loss", loss_amount, {
            "daily_loss_usd": daily_loss_usd,
            "loss_amount": loss_amount,
        })

    def check_var(
        self,
        var_1d_99_usd: float,
    ) -> list[BreakerTrip]:
        """Check absolute VaR in $."""
        var_abs = abs(var_1d_99_usd)
        return self._check_tiered("var", var_abs, {
            "var_1d_99_usd": var_1d_99_usd,
            "var_abs": var_abs,
        })

    def check_drawdown(
        self,
        drawdown_pct: float,
        drawdown_usd: float = 0.0,
    ) -> list[BreakerTrip]:
        """Check portfolio drawdown as %."""
        return self._check_tiered("drawdown", drawdown_pct, {
            "drawdown_pct": drawdown_pct,
            "drawdown_usd": drawdown_usd,
        })

    def check_funding_rate(
        self,
        daily_funding_cost_usd: float,
        symbol: str = "",
    ) -> list[BreakerTrip]:
        """Check daily funding cost in $."""
        # Only trigger on costs (positive = paying funding)
        if daily_funding_cost_usd <= 0:
            self._clear_if_tripped("funding_rate")
            return []
        return self._check_tiered("funding_rate", daily_funding_cost_usd, {
            "symbol": symbol,
            "daily_funding_cost_usd": daily_funding_cost_usd,
        })

    def record_trade_result(self, won: bool) -> list[BreakerTrip]:
        """
        Record a trade result for consecutive loss tracking.

        Args:
            won: True if the trade was profitable
        """
        if won:
            self._consecutive_loss_count = 0
            self._clear_if_tripped("consecutive_losses")
            return []

        self._consecutive_loss_count += 1
        return self._check_tiered("consecutive_losses", self._consecutive_loss_count, {
            "consecutive_losses": self._consecutive_loss_count,
        })

    def record_trip(self) -> list[BreakerTrip]:
        """Record a circuit breaker trip for frequency tracking."""
        tracker = self._rolling_trackers.get("trip_frequency")
        if tracker:
            tracker.add(1)
            return self._check_tiered("trip_frequency", tracker.count(), {
                "trips_24h": tracker.count(),
            })
        return []

    def _check_tiered(
        self,
        category: str,
        value: float,
        payload: dict,
    ) -> list[BreakerTrip]:
        """Check a value against tiered thresholds."""
        self._stats["total_checks"] += 1
        config = self._configs.get(category)

        if not config or not config.enabled:
            return []

        # First, check if existing trip has expired (time-decay)
        self._check_expiry(category)

        # Find the highest exceeded tier
        tier = config.get_tier_for_value(value)

        if tier is None:
            # No tier exceeded — clear any existing trip
            self._clear_if_tripped(category)
            return []

        # Check if already tripped at this tier
        existing = self._trips.get(category)
        if existing and existing.active and existing.threshold == tier.threshold:
            return []  # Already tripped at this level, don't re-trigger

        # Create new trip
        trip = BreakerTrip(
            breaker_name=config.name,
            category=category,
            tier_label=tier.label,
            threshold=tier.threshold,
            trigger_value=value,
            action=tier.action,
            cooldown_sec=tier.cooldown_sec,
            expires_at=(
                datetime.now(timezone.utc) + timedelta(seconds=tier.cooldown_sec)
                if tier.cooldown_sec > 0
                else None
            ),
            payload=payload,
        )

        self._trips[category] = trip
        self._stats["total_trips"] += 1
        self._stats["by_category"][category] += 1

        log.warning(
            "circuit_breaker_tripped",
            category=category,
            tier=tier.label,
            action=tier.action.value,
            trigger_value=value,
            threshold=tier.threshold,
            cooldown_sec=tier.cooldown_sec,
        )

        return [trip]

    def _check_expiry(self, category: str) -> None:
        """Check if a trip has expired (time-decay)."""
        trip = self._trips.get(category)
        if not trip or not trip.active:
            return

        if trip.expires_at and datetime.now(timezone.utc) >= trip.expires_at:
            trip.active = False
            self._stats["total_expired"] += 1
            log.info(
                "circuit_breaker_expired",
                category=category,
                tier=trip.tier_label,
                expired_at=trip.expires_at.isoformat(),
            )
            del self._trips[category]

    def _clear_if_tripped(self, category: str) -> None:
        """Clear a trip if it exists (conditions normalized)."""
        if category in self._trips:
            trip = self._trips[category]
            trip.active = False
            log.info(
                "circuit_breaker_cleared",
                category=category,
                tier=trip.tier_label,
            )
            del self._trips[category]

    def is_any_tripped(self) -> bool:
        """Check if any circuit breaker is currently tripped (active)."""
        # First, expire any stale trips
        for category in list(self._trips.keys()):
            self._check_expiry(category)
        return len(self._trips) > 0

    def is_category_tripped(self, category: str) -> bool:
        """Check if a specific category is tripped."""
        self._check_expiry(category)
        return category in self._trips and self._trips[category].active

    def get_active_trips(self) -> list[BreakerTrip]:
        """Get all currently active trips."""
        for category in list(self._trips.keys()):
            self._check_expiry(category)
        return [t for t in self._trips.values() if t.active]

    def get_blocking_action(self) -> BreakerAction | None:
        """
        Get the most severe blocking action across all active trips.

        Returns the highest-severity action, or None if no trips active.
        """
        if not self.is_any_tripped():
            return None

        severity_order = [
            BreakerAction.NONE,
            BreakerAction.REDUCE_25,
            BreakerAction.REDUCE_50,
            BreakerAction.TEMP_BLOCK,
            BreakerAction.BLOCK_ENTRIES,
            BreakerAction.TIGHTEN_STOPS,
            BreakerAction.HALT_ALL,
            BreakerAction.LIQUIDATE,
        ]

        active = self.get_active_trips()
        if not active:
            return None

        max_severity = BreakerAction.NONE
        for trip in active:
            try:
                trip_severity = severity_order.index(trip.action)
                max_severity_idx = severity_order.index(max_severity)
                if trip_severity > max_severity_idx:
                    max_severity = trip.action
            except ValueError:
                pass

        return max_severity if max_severity != BreakerAction.NONE else None

    def get_size_multiplier(self) -> float:
        """
        Get the position size multiplier based on active trips.

        Returns a multiplier (0.0 to 1.0) that should be applied to new position sizes.
        - No trips: 1.0 (full size)
        - REDUCE_25: 0.75
        - REDUCE_50: 0.50
        - BLOCK_ENTRIES / HALT_ALL / LIQUIDATE: 0.0 (no new entries)
        """
        if not self.is_any_tripped():
            return 1.0

        multiplier = 1.0
        for trip in self.get_active_trips():
            if trip.action == BreakerAction.REDUCE_25:
                multiplier = min(multiplier, 0.75)
            elif trip.action == BreakerAction.REDUCE_50:
                multiplier = min(multiplier, 0.50)
            elif trip.action in (
                BreakerAction.BLOCK_ENTRIES,
                BreakerAction.HALT_ALL,
                BreakerAction.LIQUIDATE,
                BreakerAction.TEMP_BLOCK,
            ):
                return 0.0  # No new entries at all

        return multiplier

    def clear(self, category: str | None = None) -> None:
        """Manually clear trips (after conditions normalize or manual override)."""
        if category:
            self._clear_if_tripped(category)
        else:
            for cat in list(self._trips.keys()):
                self._clear_if_tripped(cat)

    def clear_expired(self) -> int:
        """Force-expire any trips past their cooldown. Returns count cleared."""
        cleared = 0
        for category in list(self._trips.keys()):
            trip = self._trips.get(category)
            if trip and trip.expires_at and datetime.now(timezone.utc) >= trip.expires_at:
                self._check_expiry(category)
                cleared += 1
        return cleared

    def get_config(self) -> dict[str, BreakerConfig]:
        return self._configs.copy()

    def get_rolling_stats(self) -> dict[str, Any]:
        """Get rolling window statistics."""
        stats = {}
        for name, tracker in self._rolling_trackers.items():
            stats[name] = tracker.get_stats()
        stats["consecutive_losses_current"] = self._consecutive_loss_count
        return stats

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["active_trips"] = len(self.get_active_trips())
        stats["blocking_action"] = (
            self.get_blocking_action().value if self.get_blocking_action() else "none"
        )
        stats["size_multiplier"] = self.get_size_multiplier()
        stats["rolling"] = self.get_rolling_stats()
        return stats

    def get_status(self) -> dict[str, Any]:
        """Get full status for dashboard/API."""
        active_trips = self.get_active_trips()
        return {
            "any_tripped": len(active_trips) > 0,
            "blocking_action": (
                self.get_blocking_action().value if self.get_blocking_action() else None
            ),
            "size_multiplier": self.get_size_multiplier(),
            "active_trips": [
                {
                    "category": t.category,
                    "breaker_name": t.breaker_name,
                    "tier_label": t.tier_label,
                    "action": t.action.value,
                    "trigger_value": t.trigger_value,
                    "threshold": t.threshold,
                    "ts": t.ts.isoformat(),
                    "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                    "payload": t.payload,
                }
                for t in active_trips
            ],
            "configs": {
                cat: {
                    "name": cfg.name,
                    "enabled": cfg.enabled,
                    "tiers": [
                        {"threshold": t.threshold, "action": t.action.value, "label": t.label, "cooldown_sec": t.cooldown_sec}
                        for t in cfg.tiers
                    ],
                }
                for cat, cfg in self._configs.items()
            },
            "rolling": self.get_rolling_stats(),
            "stats": {k: v for k, v in self._stats.items() if k != "by_category"},
            "by_category": dict(self._stats["by_category"]),
        }
