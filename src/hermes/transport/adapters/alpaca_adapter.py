"""
Alpaca venue adapter — stocks, commodities, and crypto.

Live data: WebSocket for trades + quotes
Historical data: REST API for bars

Crypto symbols (e.g. "BTC/USD", "SOL/USD") are routed to Alpaca's v1beta3
crypto endpoints; equity symbols (e.g. "AAPL", "GLD") use the v2 stocks
endpoints. The adapter auto-detects based on the presence of a "/" in the
symbol string.

Docs:
  - Stocks WS:  https://docs.alpaca.markets/docs/realtime-stock-prices
  - Stocks REST: https://docs.alpaca.markets/reference/stockbars-1
  - Crypto WS:  https://docs.alpaca.markets/docs/crypto-api-documentation
  - Crypto REST: https://docs.alpaca.markets/reference/cryptoapi
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

# Alpaca stocks timeframe mapping (crypto uses lowercase already)
TIMEFRAME_MAP_STOCKS = {
    "1s": "1Sec",
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}

# Crypto timeframes match Alpaca's v1beta3 spec directly (1Min, 5Min, 1Hour, 1Day)
TIMEFRAME_MAP_CRYPTO = {
    "1s": "1Min",   # crypto REST has no 1Sec; smallest is 1Min
    "1m": "1Min",
    "5m": "5Min",
    "15m": "15Min",
    "1h": "1Hour",
    "1d": "1Day",
}


def _is_crypto(symbol: str) -> bool:
    """Alpaca crypto pairs contain a slash (BTC/USD, SOL/USD, ETH/USD, ...)."""
    return "/" in symbol


class AlpacaAdapter(VenueAdapter):
    """Alpaca adapter for stocks + commodities + crypto."""

    venue = Venue.ALPACA

    # Endpoint URLs — split by asset class
    STOCK_WS_URL   = "wss://stream.data.alpaca.markets/v2/iex"
    CRYPTO_WS_URL  = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"

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

    # ────────────────────────────────────────────────────────────
    # Connection
    # ────────────────────────────────────────────────────────────

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

        # Test connection (brokerage account check — same for both asset classes)
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

    # ────────────────────────────────────────────────────────────
    # Live tick streaming
    # ────────────────────────────────────────────────────────────

    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """Stream live trades via Alpaca WebSocket.

        Symbols are split into crypto/equity buckets and each bucket opens
        its own WebSocket connection (different URLs and message shapes).
        """
        if not self._configured:
            log.warning("alpaca_stream_skipped", reason="not configured")
            return

        crypto_syms  = [s for s in symbols if _is_crypto(s)]
        equity_syms  = [s for s in symbols if not _is_crypto(s)]

        tasks: list[AsyncIterator[Tick]] = []
        if crypto_syms:
            tasks.append(self._stream_crypto_trades(crypto_syms))
        if equity_syms:
            tasks.append(self._stream_equity_trades(equity_syms))

        if not tasks:
            return

        # Merge the async iterators into a single stream.
        async for tick in _merge_iterators(tasks):
            yield tick

    async def _stream_equity_trades(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """IEX feed — trade message shape: {"T":"t","S":"AAPL","p":..,"s":..}."""
        import websockets

        try:
            async with websockets.connect(self.STOCK_WS_URL) as ws:
                self._ws = ws
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "trades": symbols,
                }))
                log.info("alpaca_equity_subscribed", symbols=symbols, url=self.STOCK_WS_URL)

                async for raw in ws:
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        messages = [messages]
                    for msg in messages:
                        if msg.get("T") == "t":
                            yield Tick(
                                ts=datetime.fromisoformat(
                                    msg["t"].replace("Z", "+00:00")
                                ),
                                venue=Venue.ALPACA,
                                symbol=msg["S"],
                                price=float(msg["p"]),
                                size=float(msg.get("s", 0)),
                                trade_id=str(msg.get("i", "")),
                            )
        except asyncio.CancelledError:
            log.info("alpaca_equity_stream_cancelled")
            raise
        except Exception as e:
            log.error("alpaca_equity_stream_error", error=str(e))
            raise

    async def _stream_crypto_trades(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """v1beta3 crypto feed — trade message shape: {"T":"t","S":"BTC/USD","p":..,"s":..}.

        Crypto trades 24/7 and the message format matches the equity feed
        (the symbols simply contain a slash).
        """
        import websockets

        try:
            async with websockets.connect(self.CRYPTO_WS_URL) as ws:
                # Crypto WS requires an auth message first, then subscribe.
                await ws.send(json.dumps({
                    "action": "auth",
                    "key": self._api_key,
                    "secret": self._api_secret,
                }))
                # Wait for auth ack
                auth_ack = json.loads(await ws.recv())
                if not (isinstance(auth_ack, list) and auth_ack and auth_ack[0].get("T") == "success"):
                    log.error("alpaca_crypto_auth_failed", ack=auth_ack)
                    return

                await ws.send(json.dumps({
                    "action": "subscribe",
                    "trades": symbols,
                }))
                log.info("alpaca_crypto_subscribed", symbols=symbols, url=self.CRYPTO_WS_URL)

                async for raw in ws:
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        messages = [messages]
                    for msg in messages:
                        if msg.get("T") == "t":
                            yield Tick(
                                ts=datetime.fromisoformat(
                                    msg["t"].replace("Z", "+00:00")
                                ),
                                venue=Venue.ALPACA,
                                symbol=msg["S"],
                                price=float(msg["p"]),
                                size=float(msg.get("s", 0)),
                                trade_id=str(msg.get("i", "")),
                            )
        except asyncio.CancelledError:
            log.info("alpaca_crypto_stream_cancelled")
            raise
        except Exception as e:
            log.error("alpaca_crypto_stream_error", error=str(e))
            raise

    # ────────────────────────────────────────────────────────────
    # Live order book (best bid/ask)
    # ────────────────────────────────────────────────────────────

    async def stream_order_book(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        """Stream live quotes (best bid/ask) via Alpaca WebSocket."""
        if not self._configured:
            return

        crypto_syms = [s for s in symbols if _is_crypto(s)]
        equity_syms = [s for s in symbols if not _is_crypto(s)]

        tasks: list[AsyncIterator[OrderBookL2]] = []
        if crypto_syms:
            tasks.append(self._stream_crypto_quotes(crypto_syms))
        if equity_syms:
            tasks.append(self._stream_equity_quotes(equity_syms))
        if not tasks:
            return

        async for book in _merge_iterators(tasks):
            yield book

    async def _stream_equity_quotes(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        import websockets

        try:
            async with websockets.connect(self.STOCK_WS_URL) as ws:
                await ws.send(json.dumps({"action": "subscribe", "quotes": symbols}))
                log.info("alpaca_equity_quotes_subscribed", symbols=symbols)

                async for raw in ws:
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        messages = [messages]
                    for msg in messages:
                        if msg.get("T") == "q":
                            yield self._quote_to_book(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("alpaca_equity_quotes_error", error=str(e))
            raise

    async def _stream_crypto_quotes(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        """Crypto quote messages also use 'q' type but arrive from the crypto WS."""
        import websockets

        try:
            async with websockets.connect(self.CRYPTO_WS_URL) as ws:
                await ws.send(json.dumps({
                    "action": "auth",
                    "key": self._api_key,
                    "secret": self._api_secret,
                }))
                auth_ack = json.loads(await ws.recv())
                if not (isinstance(auth_ack, list) and auth_ack and auth_ack[0].get("T") == "success"):
                    log.error("alpaca_crypto_auth_failed", ack=auth_ack)
                    return

                await ws.send(json.dumps({"action": "subscribe", "quotes": symbols}))
                log.info("alpaca_crypto_quotes_subscribed", symbols=symbols)

                async for raw in ws:
                    messages = json.loads(raw)
                    if not isinstance(messages, list):
                        messages = [messages]
                    for msg in messages:
                        if msg.get("T") == "q":
                            yield self._quote_to_book(msg)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("alpaca_crypto_quotes_error", error=str(e))
            raise

    @staticmethod
    def _quote_to_book(msg: dict[str, Any]) -> OrderBookL2:
        ts = datetime.fromisoformat(msg["t"].replace("Z", "+00:00"))
        return OrderBookL2(
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

    # ────────────────────────────────────────────────────────────
    # Historical bars
    # ────────────────────────────────────────────────────────────

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list[Bar]:
        if not self._configured or not self._http_client:
            log.warning("alpaca_historical_skipped", reason="not connected")
            return []

        if _is_crypto(symbol):
            return await self._fetch_crypto_bars(symbol, timeframe, start, end, limit)
        return await self._fetch_equity_bars(symbol, timeframe, start, end, limit)

    async def _fetch_equity_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, limit: int,
    ) -> list[Bar]:
        alpaca_tf = TIMEFRAME_MAP_STOCKS.get(timeframe, timeframe)
        params = {
            "timeframe": alpaca_tf,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": min(limit, 10000),
            "adjustment": "raw",
        }
        try:
            response = await self._http_client.get(
                f"{self._data_url}/v2/stocks/{symbol}/bars",
                params=params,
            )
            if response.status_code != 200:
                log.error(
                    "alpaca_equity_historical_failed",
                    symbol=symbol, status=response.status_code,
                    body=response.text[:200],
                )
                return []
            bars_data = response.json().get("bars", [])
            return self._parse_bars(bars_data, symbol, timeframe)
        except Exception as e:
            log.error("alpaca_equity_historical_error", symbol=symbol, error=str(e))
            return []

    async def _fetch_crypto_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, limit: int,
    ) -> list[Bar]:
        """v1beta3 crypto bars endpoint. Note: symbols go in query string, not path."""
        alpaca_tf = TIMEFRAME_MAP_CRYPTO.get(timeframe, timeframe)
        params = {
            "symbols": symbol,
            "timeframe": alpaca_tf,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": min(limit, 10000),
        }
        try:
            response = await self._http_client.get(
                f"{self._data_url}/v1beta3/crypto/us/bars",
                params=params,
            )
            if response.status_code != 200:
                log.error(
                    "alpaca_crypto_historical_failed",
                    symbol=symbol, status=response.status_code,
                    body=response.text[:200],
                )
                return []
            # v1beta3 returns {"bars":{"BTC/USD":[{...},...]}}
            bars_map = response.json().get("bars", {})
            bars_data = bars_map.get(symbol, []) if isinstance(bars_map, dict) else bars_map
            return self._parse_bars(bars_data, symbol, timeframe)
        except Exception as e:
            log.error("alpaca_crypto_historical_error", symbol=symbol, error=str(e))
            return []

    @staticmethod
    def _parse_bars(bars_data: list[dict], symbol: str, timeframe: str) -> list[Bar]:
        bars: list[Bar] = []
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

    # ────────────────────────────────────────────────────────────
    # Latest price
    # ────────────────────────────────────────────────────────────

    async def get_current_price(self, symbol: str) -> float | None:
        """Get latest price via REST.

        Crypto: GET /v1beta3/crypto/us/latest/trades?symbols=BTC/USD
        Equity: GET /v2/stocks/{symbol}/trades/latest
        """
        if not self._configured or not self._http_client:
            return None
        try:
            if _is_crypto(symbol):
                response = await self._http_client.get(
                    f"{self._data_url}/v1beta3/crypto/us/latest/trades",
                    params={"symbols": symbol},
                )
                if response.status_code == 200:
                    # Response shape: {"trades":{"BTC/USD":{"t":..,"p":..,"s":..}}}
                    trades_map = response.json().get("trades", {})
                    trade = trades_map.get(symbol, {}) if isinstance(trades_map, dict) else {}
                    return float(trade.get("p", 0)) or None
            else:
                response = await self._http_client.get(
                    f"{self._data_url}/v2/stocks/{symbol}/trades/latest"
                )
                if response.status_code == 200:
                    trade = response.json().get("trade", {})
                    return float(trade.get("p", 0)) or None
        except Exception as e:
            log.warning("alpaca_price_failed", symbol=symbol, error=str(e))
        return None

    # ────────────────────────────────────────────────────────────
    # Symbol normalization
    # ────────────────────────────────────────────────────────────

    def normalize_symbol(self, hermes_symbol: str) -> str:
        """Pass-through.

        Alpaca equities are bare tickers ("AAPL", "GLD").
        Alpaca crypto pairs are slash-separated ("BTC/USD", "SOL/USD").
        The Hermes internal symbol is the same as the Alpaca symbol — no
        transformation needed.
        """
        return hermes_symbol


# ────────────────────────────────────────────────────────────────────
# Helper: merge multiple async iterators into one
# ────────────────────────────────────────────────────────────────────

async def _merge_iterators(iters: list[AsyncIterator]) -> AsyncIterator:
    """Round-robin merge of N async iterators into a single stream.

    Used to interleave equity + crypto WebSocket feeds into one Tick stream.
    Cancellation-safe: any iterator raising will cancel the others.
    """
    import asyncio as _asyncio

    queue: _asyncio.Queue = _asyncio.Queue()

    async def pipe(it: AsyncIterator) -> None:
        try:
            async for item in it:
                await queue.put(item)
        except Exception as e:
            await queue.put(e)
        finally:
            await queue.put(_StopSentinel)

    tasks = [_asyncio.create_task(pipe(it)) for it in iters]
    stop_count = 0
    try:
        while stop_count < len(tasks):
            item = await queue.get()
            if item is _StopSentinel:
                stop_count += 1
                continue
            if isinstance(item, Exception):
                raise item
            yield item
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()


class _StopSentinel:
    """Sentinel value used by _merge_iterators to signal iterator exhaustion."""
    pass
