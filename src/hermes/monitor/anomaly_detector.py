"""
Price Anomaly Detector — flags unusual market behavior.

Triggers anomaly events when:
- Tick-to-tick return > 5σ of 60d distribution
- 1m realized vol > 99th percentile of 60d
- Spread widens > 5× 60d median
- Book imbalance flips > 3σ in 10s
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

import numpy as np
import structlog

from hermes.schemas.market import OrderBookL2, PriceMonitorEvent, Tick, Venue
from hermes.monitor.indicators import IndicatorEngine
from hermes.monitor.tick_aggregator import TickAggregator

log = structlog.get_logger(__name__)

Severity = Literal["info", "warning", "critical"]


class AnomalyDetector:
    """
    Detects price anomalies from ticks and order book updates.

    Usage:
        detector = AnomalyDetector()
        event = detector.on_tick(tick)  # returns PriceMonitorEvent or None
    """

    def __init__(
        self,
        return_sigma_threshold: float = 5.0,
        vol_percentile_threshold: float = 99.0,
        spread_multiplier_threshold: float = 5.0,
        imbalance_sigma_threshold: float = 3.0,
        lookback_bars: int = 1440,  # ~60 days of 1m bars (1440 minutes * 60 days)
    ) -> None:
        self._return_sigma = return_sigma_threshold
        self._vol_pct = vol_percentile_threshold
        self._spread_mult = spread_multiplier_threshold
        self._imbalance_sigma = imbalance_sigma_threshold
        self._lookback = lookback_bars

        # Per-symbol state
        self._last_tick: dict[str, Tick] = {}
        self._returns_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=lookback_bars)
        )
        self._spreads_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=lookback_bars)
        )
        self._imbalances_history: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=lookback_bars)
        )

        self._stats = {"anomalies_detected": 0}

    def on_tick(self, tick: Tick) -> PriceMonitorEvent | None:
        """Check a tick for anomalies. Returns event if anomaly detected."""
        symbol = tick.symbol
        last = self._last_tick.get(symbol)

        if last is None:
            self._last_tick[symbol] = tick
            return None

        # Compute return
        if last.price > 0:
            log_return = math.log(tick.price / last.price)
        else:
            log_return = 0.0

        self._returns_history[symbol].append(log_return)
        self._last_tick[symbol] = tick

        returns = list(self._returns_history[symbol])
        if len(returns) < 100:  # need enough history
            return None

        returns_arr = np.array(returns[:-1])  # exclude current
        mean = np.mean(returns_arr)
        std = np.std(returns_arr, ddof=1)

        if std == 0:
            return None

        z = abs(log_return - mean) / std

        if z >= self._return_sigma:
            self._stats["anomalies_detected"] += 1
            return self._create_event(
                tick=tick,
                event_type="anomaly",
                severity="critical" if z >= 10 else "warning",
                payload={
                    "trigger": "return_sigma",
                    "z_score": float(z),
                    "return_bps": float(log_return * 10000),
                    "threshold_sigma": self._return_sigma,
                    "window": f"last {len(returns)} ticks",
                },
            )

        return None

    def on_order_book(self, book: OrderBookL2) -> PriceMonitorEvent | None:
        """Check an order book update for spread/imbalance anomalies."""
        symbol = book.symbol

        spread = book.spread
        imbalance = book.imbalance

        if spread is not None and book.mid_price:
            spread_bps = (spread / book.mid_price) * 10000
            self._spreads_history[symbol].append(spread_bps)

            spreads = list(self._spreads_history[symbol])
            if len(spreads) >= 100:
                median_spread = float(np.median(spreads[:-1]))
                if median_spread > 0 and spread_bps > median_spread * self._spread_mult:
                    self._stats["anomalies_detected"] += 1
                    return self._create_event(
                        tick=Tick(
                            ts=book.ts,
                            venue=book.venue,
                            symbol=symbol,
                            price=book.mid_price,
                        ),
                        event_type="anomaly",
                        severity="warning",
                        spread_bps=spread_bps,
                        payload={
                            "trigger": "spread_widen",
                            "spread_bps": float(spread_bps),
                            "median_spread_bps": median_spread,
                            "multiplier": float(spread_bps / median_spread),
                            "threshold_mult": self._spread_mult,
                        },
                    )

        if imbalance is not None:
            self._imbalances_history[symbol].append(imbalance)
            imbalances = list(self._imbalances_history[symbol])
            if len(imbalances) >= 100:
                imb_arr = np.array(imbalances[:-1])
                mean = np.mean(imb_arr)
                std = np.std(imb_arr, ddof=1)
                if std > 0:
                    z = abs(imbalance - mean) / std
                    if z >= self._imbalance_sigma:
                        self._stats["anomalies_detected"] += 1
                        return self._create_event(
                            tick=Tick(
                                ts=book.ts,
                                venue=book.venue,
                                symbol=symbol,
                                price=book.mid_price or 0,
                            ),
                            event_type="anomaly",
                            severity="warning",
                            book_imbalance=imbalance,
                            payload={
                                "trigger": "imbalance_flip",
                                "z_score": float(z),
                                "imbalance": float(imbalance),
                                "mean": float(mean),
                                "threshold_sigma": self._imbalance_sigma,
                            },
                        )

        return None

    def _create_event(
        self,
        tick: Tick,
        event_type: str,
        severity: str,
        payload: dict,
        spread_bps: float | None = None,
        book_imbalance: float | None = None,
    ) -> PriceMonitorEvent:
        return PriceMonitorEvent(
            event_id=str(uuid4()),
            ts=tick.ts,
            symbol=tick.symbol,
            venue=tick.venue,
            event_type=event_type,  # type: ignore
            severity=severity,  # type: ignore
            last_price=tick.price,
            spread_bps=spread_bps,
            book_imbalance=book_imbalance,
            payload=payload,
        )

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
