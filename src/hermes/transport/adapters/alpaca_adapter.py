"""
Alpaca venue adapter — stocks + commodities.

Live data: WebSocket for trades + quotes
Historical data: REST API for bars

Docs:
  - WebSocket: https://docs.alpaca.markets/docs/realtime-stock-prices
  - REST bars: https://docs.alpaca.markets/reference/stockbars-1
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.schemas.market import Bar, OrderBookL2, OrderBookLevel, Side, Tick, Venue
from hermes.transport.adapters.base import VenueAdapter

log = structlog.get_logger(__name__)

# Alpaca timeframe mapping
TIMEFRAME_MAP = {
    "1s": "1Sec",
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}


class AlpacaAdapter(VenueAdapter):
    """Alpaca adapter for stocks + commodities."""

    venue = Venue.ALPACA

    def __init__(self, config: HermesConfig) -> None:
        venue_config = config.venues.get("alpaca", {})
        creds = venue_config.credentials
        self._api_key = creds.get("api_key", "")
        self._api_secret = creds.get("api_secret", "")
        self._base_url = creds.get("base_url", "https://paper-api.alpaca.markets")
        self._data_url = creds.get("data_url", "https://data.alpaca.markets")

        # Detect placeholders
        self._configured = not (
            "<" in self._api_key or "<" in self._api_secret
        )

        self._ws = None
        self._http_client = None

    async def connect(self) -> None:
        if not self._configured:
            log.warning("alpaca_not_configured", note="placeholders in .env")
            return

        import httpx

        self._http_client = httpx.AsyncClient(
            headers={
                "APCA-API-KEY-ID": self._api_key,
                "APCA-API-SECRET-KEY": self._api_secret,
            },
            timeout=30.0,
        )

        # Test connection
        try:
            response = await self._http_client.get(f"{self._base_url}/v2/account")
            if response.status_code == 200:
                account = response.json()
                log.info(
                    "alpaca_connected",
                    account_number=account.get("account_number"),
                    status=account.get("status"),
                )
            else:
                log.error("alpaca_auth_failed", status=response.status_code)
        except Exception as e:
            log.error("alpaca_connect_failed", error=str(e))

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
        if self._http_client:
            await self._http_client.aclose()

    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """Stream live trades via Alpaca WebSocket."""
        if not self._configured:
            log.warning("alpaca_stream_skipped", reason="not configured")
            return

        import websockets

        ws_url = "wss://stream.data.alpaca.markets/v2/iex"

        try:
            async with websockets.connect(ws_url) as ws:
                self._ws = ws

                # Subscribe to trades
                subscribe_msg = {
                    "action": "subscribe",
                    "trades": symbols,
                }
                await ws.send(json.dumps(subscribe_msg))
                log.info("alpaca_subscribed", symbols=symbols, url=ws_url)

                async for raw in ws:
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        messages = [messages]

                    for msg in messages:
                        if msg.get("T") == "t":  # trade message
                            tick = Tick(
                                ts=datetime.fromisoformat(
                                    msg["t"].replace("Z", "+00:00")
                                ),
                                venue=Venue.ALPACA,
                                symbol=msg["S"],
                                price=float(msg["p"]),
                                size=float(msg.get("s", 0)),
                                trade_id=str(msg.get("i", "")),
                            )
                            yield tick

        except asyncio.CancelledError:
            log.info("alpaca_stream_cancelled")
            raise
        except Exception as e:
            log.error("alpaca_stream_error", error=str(e))
            raise

    async def stream_order_book(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        """Stream live quotes (best bid/ask) via Alpaca WebSocket.

        Note: Alpaca IEX feed provides quotes, not full L2 depth.
        We construct a minimal OrderBookL2 from the best bid/ask.
        """
        if not self._configured:
            return

        import websockets

        ws_url = "wss://stream.data.alpaca.markets/v2/iex"

        try:
            async with websockets.connect(ws_url) as ws:
                subscribe_msg = {"action": "subscribe", "quotes": symbols}
                await ws.send(json.dumps(subscribe_msg))
                log.info("alpaca_quotes_subscribed", symbols=symbols)

                async for raw in ws:
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        messages = [messages]

                    for msg in messages:
                        if msg.get("T") == "q":  # quote message
                            ts = datetime.fromisoformat(
                                msg["t"].replace("Z", "+00:00")
                            )
                            book = OrderBookL2(
                                ts=ts,
                                venue=Venue.ALPACA,
                                symbol=msg["S"],
                                bids=[OrderBookLevel(
                                    price=float(msg["bp"]),
                                    size=float(msg.get("bs", 0)),
                                )] if msg.get("bp") else [],
                                asks=[OrderBookLevel(
                                    price=float(msg["ap"]),
                                    size=float(msg.get("as", 0)),
                                )] if msg.get("ap") else [],
                            )
                            yield book

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("alpaca_quotes_error", error=str(e))
            raise

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[Bar]:
        """Fetch historical bars from Alpaca REST API."""
        if not self._configured or not self._http_client:
            log.warning("alpaca_historical_skipped", reason="not connected")
            return []

        alpaca_tf = TIMEFRAME_MAP.get(timeframe, timeframe)
        params = {
            "timeframe": alpaca_tf,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": min(limit, 10000),
            "adjustment": "raw",
        }

        try:
            # Stocks endpoint
            response = await self._http_client.get(
                f"{self._data_url}/v2/stocks/{symbol}/bars",
                params=params,
            )
            if response.status_code != 200:
                log.error(
                    "alpaca_historical_failed",
                    symbol=symbol,
                    status=response.status_code,
                    body=response.text[:200],
                )
                return []

            data = response.json()
            bars_data = data.get("bars", [])

            bars = []
            for b in bars_data:
                ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                bars.append(Bar(
                    ts_open=ts,
                    ts_close=ts,
                    venue=Venue.ALPACA,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(b["o"]),
                    high=float(b["h"]),
                    low=float(b["l"]),
                    close=float(b["c"]),
                    volume=float(b.get("v", 0)),
                    vwap=float(b.get("vw")) if b.get("vw") else None,
                    n_trades=int(b.get("n", 0)) if b.get("n") else None,
                    closed=True,
                ))

            log.info("alpaca_historical_fetched", symbol=symbol, n_bars=len(bars))
            return bars

        except Exception as e:
            log.error("alpaca_historical_error", symbol=symbol, error=str(e))
            return []

    async def get_current_price(self, symbol: str) -> float | None:
        """Get latest price via REST."""
        if not self._configured or not self._http_client:
            return None

        try:
            response = await self._http_client.get(
                f"{self._data_url}/v2/stocks/{symbol}/trades/latest"
            )
            if response.status_code == 200:
                data = response.json()
                trade = data.get("trade", {})
                return float(trade.get("p", 0)) or None
        except Exception as e:
            log.warning("alpaca_price_failed", symbol=symbol, error=str(e))
        return None

    def normalize_symbol(self, hermes_symbol: str) -> str:
        """Alpaca stocks are just ticker symbols (AAPL, GLD, etc.)."""
        return hermes_symbol
