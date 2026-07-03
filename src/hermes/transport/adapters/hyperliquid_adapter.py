"""
Hyperliquid venue adapter — crypto perps + spot.

Live data: WebSocket for trades, order book, funding, liquidations
Historical data: REST API for candles

Docs:
  - WebSocket: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket
  - REST info: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.schemas.market import (
    Bar,
    FundingRate,
    LiquidationEvent,
    OrderBookL2,
    OrderBookLevel,
    Side,
    Tick,
    Venue,
)
from hermes.transport.adapters.base import VenueAdapter

log = structlog.get_logger(__name__)

# Hyperliquid candle timeframe mapping (in minutes)
TIMEFRAME_MINUTES = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "1h": 60,
    "4h": 240,
    "1d": 1440,
}


class HyperliquidAdapter(VenueAdapter):
    """Hyperliquid adapter for crypto perps + spot."""

    venue = Venue.HYPERLIQUID

    def __init__(self, config: HermesConfig) -> None:
        venue_config = config.venues.get("hyperliquid", {})
        creds = venue_config.credentials
        self._api_url = creds.get("api_url", "https://api.hl.cyber")
        self._wallet_address = creds.get("wallet_address", "")
        self._private_key = creds.get("private_key", "")

        self._configured = not ("<" in self._api_url)

        self._ws = None
        self._http_client = None

    async def connect(self) -> None:
        if not self._configured:
            log.warning("hyperliquid_not_configured", note="placeholders in .env")
            return

        import httpx

        self._http_client = httpx.AsyncClient(timeout=30.0)

        # Test connection with meta endpoint
        try:
            response = await self._http_client.post(
                f"{self._api_url}/info",
                json={"type": "meta"},
            )
            if response.status_code == 200:
                meta = response.json()
                n_assets = len(meta.get("universe", []))
                log.info("hyperliquid_connected", n_assets=n_assets, api_url=self._api_url)
            else:
                log.error("hyperliquid_meta_failed", status=response.status_code)
        except Exception as e:
            log.error("hyperliquid_connect_failed", error=str(e))

    async def disconnect(self) -> None:
        if self._ws:
            await self._ws.close()
        if self._http_client:
            await self._http_client.aclose()

    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """Stream live trades via Hyperliquid WebSocket."""
        if not self._configured:
            return

        import websockets

        ws_url = "wss://api.hl.cyber/ws"

        try:
            async with websockets.connect(ws_url) as ws:
                self._ws = ws

                # Subscribe to trades for each symbol
                for symbol in symbols:
                    sub_msg = {"method": "subscribe", "subscription": {"type": "trades", "coin": symbol}}
                    await ws.send(json.dumps(sub_msg))

                log.info("hyperliquid_trades_subscribed", symbols=symbols)

                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("channel") == "trades" and "data" in data:
                        for trade in data["data"]:
                            ts = datetime.fromtimestamp(
                                trade["time"] / 1000, tz=timezone.utc
                            )
                            tick = Tick(
                                ts=ts,
                                venue=Venue.HYPERLIQUID,
                                symbol=trade["coin"],
                                price=float(trade["price"]),
                                size=float(trade["size"]),
                                side=Side.BUY if trade["side"] == "buy" else Side.SELL,
                                trade_id=str(trade.get("tid", "")),
                            )
                            yield tick

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("hyperliquid_trades_error", error=str(e))
            raise

    async def stream_order_book(self, symbols: list[str]) -> AsyncIterator[OrderBookL2]:
        """Stream live L2 order book via Hyperliquid WebSocket."""
        if not self._configured:
            return

        import websockets

        ws_url = "wss://api.hl.cyber/ws"

        try:
            async with websockets.connect(ws_url) as ws:
                self._ws = ws

                for symbol in symbols:
                    sub_msg = {"method": "subscribe", "subscription": {"type": "l2Book", "coin": symbol}}
                    await ws.send(json.dumps(sub_msg))

                log.info("hyperliquid_l2_subscribed", symbols=symbols)

                async for raw in ws:
                    data = json.loads(raw)
                    if data.get("channel") == "l2Book" and "data" in data:
                        book_data = data["data"]
                        ts = datetime.fromtimestamp(
                            book_data["time"] / 1000, tz=timezone.utc
                        )

                        bids = [
                            OrderBookLevel(price=float(lvl["px"]), size=float(lvl["sz"]))
                            for lvl in book_data.get("levels", {}).get("bids", [])
                        ]
                        asks = [
                            OrderBookLevel(price=float(lvl["px"]), size=float(lvl["sz"]))
                            for lvl in book_data.get("levels", {}).get("asks", [])
                        ]

                        book = OrderBookL2(
                            ts=ts,
                            venue=Venue.HYPERLIQUID,
                            symbol=book_data["coin"],
                            bids=bids,
                            asks=asks,
                        )
                        yield book

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("hyperliquid_l2_error", error=str(e))
            raise

    async def stream_funding_rates(self, symbols: list[str]) -> AsyncIterator[FundingRate]:
        """Poll funding rates periodically (Hyperliquid doesn't stream them)."""
        if not self._configured or not self._http_client:
            return

        while True:
            for symbol in symbols:
                try:
                    response = await self._http_client.post(
                        f"{self._api_url}/info",
                        json={"type": "metaAndAssetCtxs"},
                    )
                    if response.status_code == 200:
                        data = response.json()
                        # data = [meta, asset_ctxs] — find our symbol
                        meta = data[0] if isinstance(data, list) and data else {}
                        asset_ctxs = data[1] if isinstance(data, list) and len(data) > 1 else []

                        universe = meta.get("universe", [])
                        for i, asset in enumerate(universe):
                            if asset.get("name") == symbol and i < len(asset_ctxs):
                                ctx = asset_ctxs[i]
                                funding = float(ctx.get("funding", 0))
                                # Hyperliquid funding is per-8h, already in decimal
                                annualized = funding * 3 * 365 * 100  # 3 funding events/day, 365 days

                                yield FundingRate(
                                    ts=datetime.now(timezone.utc),
                                    symbol=symbol,
                                    funding_rate=funding,
                                    annualized_pct=annualized,
                                )
                                break
                except Exception as e:
                    log.warning("funding_poll_failed", symbol=symbol, error=str(e))

            # Poll every 5 minutes
            await asyncio.sleep(300)

    async def fetch_historical_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 5000,
    ) -> list[Bar]:
        """Fetch historical candles from Hyperliquid REST API."""
        if not self._configured or not self._http_client:
            return []

        tf_minutes = TIMEFRAME_MINUTES.get(timeframe, 60)
        params = {
            "type": "candles",
            "coin": symbol,
            "interval": f"{tf_minutes}m",
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
        }

        try:
            response = await self._http_client.post(
                f"{self._api_url}/info",
                json=params,
            )
            if response.status_code != 200:
                log.error(
                    "hyperliquid_historical_failed",
                    symbol=symbol,
                    status=response.status_code,
                    body=response.text[:200],
                )
                return []

            data = response.json()
            candles = data.get("t", [])  # list of candle dicts

            bars = []
            for c in candles:
                # Hyperliquid returns: {t: [timestamps], o: [opens], ...} or list of dicts
                ts_ms = c.get("t") if isinstance(c, dict) else None
                if ts_ms is None:
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                bars.append(Bar(
                    ts_open=ts,
                    ts_close=ts,
                    venue=Venue.HYPERLIQUID,
                    symbol=symbol,
                    timeframe=timeframe,
                    open=float(c["o"]),
                    high=float(c["h"]),
                    low=float(c["l"]),
                    close=float(c["c"]),
                    volume=float(c.get("v", 0)),
                    n_trades=int(c.get("n", 0)) if c.get("n") else None,
                    closed=True,
                ))

            log.info("hyperliquid_historical_fetched", symbol=symbol, n_bars=len(bars))
            return bars

        except Exception as e:
            log.error("hyperliquid_historical_error", symbol=symbol, error=str(e))
            return []

    async def get_current_price(self, symbol: str) -> float | None:
        """Get latest mark price via REST."""
        if not self._configured or not self._http_client:
            return None

        try:
            response = await self._http_client.post(
                f"{self._api_url}/info",
                json={"type": "allMids"},
            )
            if response.status_code == 200:
                data = response.json()
                mids = data.get("mids", {})
                price = mids.get(symbol)
                return float(price) if price else None
        except Exception as e:
            log.warning("hyperliquid_price_failed", symbol=symbol, error=str(e))
        return None

    def normalize_symbol(self, hermes_symbol: str) -> str:
        """Hyperliquid symbols are like 'BTC', 'ETH' (the perp name without '-PERP')."""
        # Hermes uses 'BTC-PERP', HL API uses 'BTC'
        if hermes_symbol.endswith("-PERP"):
            return hermes_symbol[:-5]
        if hermes_symbol.endswith("-SPOT"):
            return hermes_symbol[:-5]
        return hermes_symbol
