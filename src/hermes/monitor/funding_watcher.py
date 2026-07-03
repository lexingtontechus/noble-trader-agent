"""
Funding Rate Watcher — monitors Hyperliquid funding rates for blowouts.

Emits `funding_spike` events when annualized funding > 50% (perp basis blowout).
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import structlog

from hermes.schemas.market import FundingRate, PriceMonitorEvent, Tick, Venue

log = structlog.get_logger(__name__)


class FundingWatcher:
    """
    Monitors funding rates and emits events on blowouts.

    A "blowout" is defined as annualized funding rate > 50%
    (i.e., funding > 0.0004566 per 8h, or ~4.57bps per 8h).
    """

    def __init__(
        self,
        extreme_annualized_pct: float = 50.0,
        warning_annualized_pct: float = 25.0,
    ) -> None:
        self._extreme_pct = extreme_annualized_pct
        self._warning_pct = warning_annualized_pct
        self._last_funding: dict[str, FundingRate] = {}
        self._stats = {"spikes_detected": 0, "warnings_emitted": 0}

    def on_funding(self, funding: FundingRate) -> PriceMonitorEvent | None:
        """Check a funding rate update. Returns event if spike detected."""
        self._last_funding[funding.symbol] = funding

        annualized = funding.annualized_pct
        if annualized is None:
            # Fallback: compute from funding rate
            # funding is per-8h, 3 funding events/day, 365 days
            annualized = funding.funding_rate * 3 * 365 * 100

        if abs(annualized) >= self._extreme_pct:
            self._stats["spikes_detected"] += 1
            return PriceMonitorEvent(
                event_id=str(uuid4()),
                ts=funding.ts,
                symbol=funding.symbol,
                venue=funding.venue,
                event_type="funding_spike",
                severity="critical" if abs(annualized) >= 100 else "warning",
                last_price=0,  # funding events don't carry price; updated externally if needed
                payload={
                    "funding_8h": funding.funding_rate,
                    "annualized_pct": annualized,
                    "predicted_next": None,
                    "threshold_pct": self._extreme_pct,
                },
            )

        if abs(annualized) >= self._warning_pct:
            self._stats["warnings_emitted"] += 1
            log.info(
                "funding_warning",
                symbol=funding.symbol,
                annualized_pct=annualized,
            )

        return None

    def get_current_funding(self, symbol: str) -> FundingRate | None:
        return self._last_funding.get(symbol)

    def get_all_funding(self) -> dict[str, FundingRate]:
        return self._last_funding.copy()

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
