"""
Market data schemas — unified across all venues.

Every venue adapter normalizes its native format into these schemas so
downstream code never needs to know which venue the data came from.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Venue(str, Enum):
    ALPACA = "alpaca"
    HYPERLIQUID = "hyperliquid"
    TRADINGVIEW = "tradingview"  # TradingViewAPI (RapidAPI) — venue-agnostic price source


class AssetClass(str, Enum):
    EQUITIES = "equities"
    CRYPTO = "crypto"
    COMMODITIES = "commodities"
    FOREX = "forex"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Tick(BaseModel):
    """A single price tick (last traded price)."""

    model_config = {"extra": "allow"}

    ts: datetime = Field(..., description="Tick timestamp (venue time, UTC)")
    venue: Venue
    symbol: str
    price: float = Field(..., gt=0)
    size: float | None = Field(None, ge=0, description="Trade size (0 if not available)")
    side: Side | None = None
    trade_id: str | None = None

    @field_validator("ts", mode="before")
    @classmethod
    def _coerce_ts(cls, v: object) -> datetime:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v / 1000, tz=timezone.utc)
        return v  # type: ignore


class Bar(BaseModel):
    """OHLCV bar at a given timeframe."""

    model_config = {"extra": "allow"}

    ts_open: datetime = Field(..., description="Bar open timestamp (UTC)")
    ts_close: datetime | None = Field(None, description="Bar close timestamp (UTC)")
    venue: Venue
    symbol: str
    timeframe: str = Field(..., description="e.g. '1s', '1m', '5m', '1h', '1d'")
    open: float = Field(..., gt=0)
    high: float = Field(..., gt=0)
    low: float = Field(..., gt=0)
    close: float = Field(..., gt=0)
    volume: float = Field(0, ge=0)
    vwap: float | None = None
    n_trades: int | None = None
    closed: bool = Field(False, description="True if this bar is finalized")

    @field_validator("high")
    @classmethod
    def _high_ge_open(cls, v: float, info) -> float:
        if "open" in info.data and v < info.data["open"]:
            raise ValueError("high must be >= open")
        return v

    @field_validator("low")
    @classmethod
    def _low_le_open(cls, v: float, info) -> float:
        if "open" in info.data and v > info.data["open"]:
            raise ValueError("low must be <= open")
        return v


class OrderBookLevel(BaseModel):
    """A single price level in the order book."""

    price: float = Field(..., gt=0)
    size: float = Field(..., ge=0)


class OrderBookL2(BaseModel):
    """L2 order book snapshot."""

    model_config = {"extra": "allow"}

    ts: datetime
    venue: Venue
    symbol: str
    bids: list[OrderBookLevel] = Field(default_factory=list, description="Sorted by price descending")
    asks: list[OrderBookLevel] = Field(default_factory=list, description="Sorted by price ascending")
    sequence: int | None = None

    @property
    def best_bid(self) -> OrderBookLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> OrderBookLevel | None:
        return self.asks[0] if self.asks else None

    @property
    def mid_price(self) -> float | None:
        if self.best_bid and self.best_ask:
            return (self.best_bid.price + self.best_ask.price) / 2
        return None

    @property
    def spread(self) -> float | None:
        if self.best_bid and self.best_ask:
            return self.best_ask.price - self.best_bid.price
        return None

    @property
    def spread_bps(self) -> float | None:
        if self.spread and self.mid_price:
            return (self.spread / self.mid_price) * 10000
        return None

    @property
    def imbalance(self) -> float | None:
        """Order book imbalance: (bid_qty - ask_qty) / (bid_qty + ask_qty). Range [-1, 1]."""
        bid_qty = sum(l.size for l in self.bids[:10])  # top 10 levels
        ask_qty = sum(l.size for l in self.asks[:10])
        total = bid_qty + ask_qty
        if total == 0:
            return None
        return (bid_qty - ask_qty) / total


class FundingRate(BaseModel):
    """Funding rate update (Hyperliquid perps only)."""

    model_config = {"extra": "allow"}

    ts: datetime
    venue: Venue = Venue.HYPERLIQUID
    symbol: str
    funding_rate: float = Field(..., description="8h funding rate (e.g., 0.0001 = 0.01%)")
    annualized_pct: float | None = None
    next_funding_ts: datetime | None = None

    @property
    def is_extreme(self) -> bool:
        """True if annualized funding > 50% (perp basis blowout)."""
        if self.annualized_pct is not None:
            return abs(self.annualized_pct) > 50
        # Fallback: 0.05% per 8h = ~55% annualized
        return abs(self.funding_rate) > 0.0005


class LiquidationEvent(BaseModel):
    """A liquidation event (Hyperliquid perps)."""

    model_config = {"extra": "allow"}

    ts: datetime
    venue: Venue = Venue.HYPERLIQUID
    symbol: str
    side: Side  # long liquidated = sell side; short liquidated = buy side
    price: float = Field(..., gt=0)
    size: float = Field(..., gt=0, description="Liquidated size in base currency")
    value_usd: float = Field(..., gt=0)


class Position(BaseModel):
    """An open position (for stop/target watcher)."""

    model_config = {"extra": "allow"}

    position_id: str
    symbol: str
    venue: Venue
    direction: Literal["long", "short"]
    qty: float = Field(..., description="Position size (positive)")
    entry_price: float = Field(..., gt=0)
    stop_price: float = Field(..., gt=0)
    target_price: float = Field(..., gt=0)
    opened_at: datetime
    trailing_stop: float | None = None  # current trailing stop, if any
    trailing_method: Literal["brick_boundary", "atr", "percentage"] | None = None
    risk_amount: float = Field(..., gt=0, description="$ at risk (stop distance * qty)")

    @property
    def current_r_multiple(self) -> float | None:
        """R-multiple at current price — needs to be calculated externally."""
        return None


class PriceMonitorEvent(BaseModel):
    """An event emitted by the Active Price Monitor."""

    model_config = {"extra": "allow"}

    event_id: str
    ts: datetime
    symbol: str
    venue: Venue
    event_type: Literal[
        "anomaly",
        "stop_hit",
        "target_hit",
        "trail_update",
        "pnl_warning",
        "correlation_shift",
        "liquidation_cluster",
        "funding_spike",
        "macro_event_window",
    ]
    severity: Literal["info", "warning", "critical"]
    last_price: float
    spread_bps: float | None = None
    book_imbalance: float | None = None
    realized_vol_1m: float | None = None
    realized_vol_1h: float | None = None
    atr_14: float | None = None
    payload: dict  # event-specific data
    position_id: str | None = None
    related_symbols: list[str] | None = None
    meta_regime_at_event: str | None = None
