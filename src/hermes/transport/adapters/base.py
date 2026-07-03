"""
Venue adapter interface.

All venue adapters (Alpaca, Hyperliquid, future OANDA/IBKR) implement this
interface so downstream code is venue-agnostic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from datetime import datetime

from hermes.schemas.market import Bar, FundingRate, LiquidationEvent, OrderBookL2, Tick, Venue


class VenueAdapter(ABC):
    """Abstract base class for all venue adapters."""

    venue: Venue

    @abstractmethod
    async def connect(self) -> None:
        """Establish WebSocket + REST connections."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Close all connections."""
        ...

    @abstractmethod
    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """
        Stream live ticks for the given symbols.

        Yields Tick objects indefinitely until disconnected.
        """
        ...

    @abstractmethod
    async def stream_order_book(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        """Stream live L2 order book updates."""
        ...

    async def stream_funding_rates(self, symbols: list[str]) -> AsyncIterator[FundingRate]:
        """
        Stream funding rate updates (Hyperliquid only).

        Default: not supported. Override in Hyperliquid adapter.
        """
        raise NotImplementedError(f"{self.venue} does not support funding rates")
        yield  # type: ignore  # make this an async generator

    async def stream_liquidations(self, symbols: list[str]) -> AsyncIterator[LiquidationEvent]:
        """
        Stream liquidation events (Hyperliquid only).

        Default: not supported. Override in Hyperliquid adapter.
        """
        raise NotImplementedError(f"{self.venue} does not support liquidation streaming")
        yield  # type: ignore

    @abstractmethod
    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[Bar]:
        """
        Fetch historical OHLCV bars from the venue's REST API.

        Args:
            symbol: Trading symbol (venue-native format)
            timeframe: e.g. "1m", "5m", "1h", "1d"
            start: Start datetime (UTC)
            end: End datetime (UTC)
            limit: Max bars to return

        Returns:
            List of Bar objects sorted by ts_open ascending
        """
        ...

    @abstractmethod
    async def get_current_price(self, symbol: str) -> float | None:
        """Get the latest price for a symbol (REST call)."""
        ...

    @abstractmethod
    def normalize_symbol(self, hermes_symbol: str) -> str:
        """
        Convert Hermes symbol to venue-native symbol.

        e.g., "BTC-PERP" → "BTC-PERP" (Hyperliquid)
              "BTC-PERP" → "BTC/USD" (Alpaca, if it existed)
        """
        ...
