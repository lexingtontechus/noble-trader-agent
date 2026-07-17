"""Price feed for backtests / sweep.

Fetches historical OHLCV candles per symbol so the BacktestEngine can mark
positions against a REAL price path (not just the heartbeat entry_price).

Venue mapping (per user directive):
  - Hyperliquid perps (BTC/ETH/SOL ... -> *-PERP): Hyperliquid `candleSnapshot`
  - Alpaca equities / forex / commodities: Alpaca bars API (free tier)
    - Crypto bars:  /v1beta2/crypto/{symbol}/bars   (BTCUSD etc.)
    - Stock bars:   /v2/stocks/bars
    - Forex rates:  /v1beta1/forex/rates  + /v2/stocks/bars?symbols=...
    - Commodities:  /v2/stocks/bars?symbols=... (GLD, SLV, USO, etc.)

Symbol normalization:
  - "BTC-PERP" / "BTC" -> HL coin "BTC"
  - "AAPL"            -> Alpaca "AAPL"
  - "GOLD"            -> Alpaca "GLD" (or raw if allowed)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class PriceSeries:
    symbol: str
    candles: list[Candle] = field(default_factory=list)

    @property
    def closes(self) -> list[float]:
        return [c.close for c in self.candles]

    def close_at(self, idx: int) -> float:
        if not self.candles:
            return 0.0
        return self.candles[min(idx, len(self.candles) - 1)].close


def _hl_coin(symbol: str) -> str:
    """BTC-PERP -> BTC, ETH-PERP -> ETH, SOL -> SOL."""
    s = symbol.upper().replace("-PERP", "").replace("USD", "")
    return s


def _is_hyperliquid(symbol: str, venue: str | None = None) -> bool:
    if venue == "hyperliquid":
        return True
    return symbol.upper().endswith("-PERP") or symbol.upper() in {
        "BTC", "ETH", "SOL", "ARB", "AVAX", "DOGE", "LINK", "WIF", "XRP",
    }


# ----------------------------------------------------------------------------
# Hyperliquid
# ----------------------------------------------------------------------------

async def fetch_hl_candles(
    coin: str,
    hours: int = 72,
    interval: str = "1h",
    hl_api_url: str = "https://api.hyperliquid.xyz",
    client: Any | None = None,
) -> list[Candle]:
    """Fetch hourly candle closes from HyperLiquid mainnet (async).

    Uses the exact payload shape confirmed working against mainnet:
    integer-ms startTime/endTime + interval "1h".
    """
    start_ms = int((time.time() - hours * 3600) * 1000)
    end_ms = int(time.time() * 1000)

    payload = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
        },
    }

    own = client is None
    if own:
        import httpx

        client = httpx.AsyncClient(timeout=20.0)
    try:
        resp = await client.post(f"{hl_api_url}/info", json=payload)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as exc:
        log.warning("hl_candle_fetch_failed", coin=coin, error=str(exc)[:120])
        return []
    finally:
        if own:
            await client.aclose()

    candles: list[Candle] = []
    for k in raw:
        # HL returns dicts: {t, T, s, i, o, h, l, c, v, n}
        try:
            candles.append(
                Candle(
                    ts_ms=int(k["t"]),
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k.get("v", 0) or 0),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.ts_ms)
    return candles


# ----------------------------------------------------------------------------
# Alpaca (equities / forex / commodities) — free API
# ----------------------------------------------------------------------------

def _alpaca_headers(api_key: str, api_secret: str) -> dict:
    return {
        "APCA-API-KEY-ID": api_key,
        "APCA-API-SECRET-KEY": api_secret,
    }


async def fetch_alpaca_bars(
    symbol: str,
    api_key: str,
    api_secret: str,
    days: int = 3,
    timeframe: str = "1H",
    kind: str = "stock",
    data_base: str = "https://data.alpaca.markets",
) -> list[Candle]:
    """Fetch historical bars from Alpaca (free tier).

    kind: 'stock' | 'crypto' | 'forex'
      - stock:  GET /v2/stocks/bars?symbols=SYM
      - crypto: GET /v1beta2/crypto/SYM/bars
      - forex:  GET /v1beta1/forex/SYM/bars  (or rates)
    """
    import httpx

    end = time.time()
    start = end - days * 86400
    start_str = _iso(start)
    end_str = _iso(end)

    headers = _alpaca_headers(api_key, api_secret)
    params = {
        "start": start_str,
        "end": end_str,
        "limit": 200,
        "timeframe": timeframe,
    }

    if kind == "crypto":
        url = f"{data_base}/v1beta2/crypto/{symbol}/bars"
    elif kind == "forex":
        # forex bars use base/quote: e.g. EURUSD
        url = f"{data_base}/v1beta1/forex/{symbol}/bars"
    else:
        url = f"{data_base}/v2/stocks/bars"
        params["symbols"] = symbol

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("alpaca_bars_fetch_failed", symbol=symbol, kind=kind, error=str(exc)[:120])
        return []

    bars = data.get("bars", {})
    rows = bars.get(symbol, []) if isinstance(bars, dict) else bars
    candles: list[Candle] = []
    for b in rows:
        try:
            candles.append(
                Candle(
                    ts_ms=int(b["t"]),
                    open=float(b["o"]),
                    high=float(b["h"]),
                    low=float(b["l"]),
                    close=float(b["c"]),
                    volume=float(b.get("v", 0) or 0),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.ts_ms)
    return candles


def _iso(epoch_sec: float) -> str:
    import datetime

    return datetime.datetime.utcfromtimestamp(epoch_sec).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------
# Unified accessor for the backtest engine
# ----------------------------------------------------------------------------

async def fetch_price_series(
    symbol: str,
    *,
    venue: str | None = None,
    hours: int = 72,
    config: Any | None = None,
) -> PriceSeries:
    """Fetch a real price series for `symbol`, routed by venue/type."""
    # TradingViewAPI: venue-agnostic source (matches Noble Trader backend).
    # Preferred when explicitly requested or when no Alpaca/HL creds exist.
    if venue == "tradingview" or (venue is None and config is not None and _tv_configured(config)):
        candles = await fetch_tv_bars(symbol, hours=hours, config=config)
        return PriceSeries(symbol=symbol, candles=candles)

    if _is_hyperliquid(symbol, venue):
        coin = _hl_coin(symbol)
        hl_url = "https://api.hyperliquid.xyz"
        if config is not None:
            try:
                hl_url = config.venues.hyperliquid.api_url
            except Exception:
                pass
        candles = await fetch_hl_candles(coin, hours=hours, hl_api_url=hl_url)
        return PriceSeries(symbol=symbol, candles=candles)

    # Alpaca path — need creds from config
    api_key = api_secret = None
    if config is not None:
        try:
            api_key = config.venues.alpaca.api_key
            api_secret = config.venues.alpaca.api_secret
        except Exception:
            pass
    if not api_key:
        from hermes.core.secrets import get_secret

        api_key = get_secret("alpaca.api_key")
        api_secret = get_secret("alpaca.api_secret")

    kind = "crypto" if symbol.upper().endswith("USD") else "stock"
    candles = await fetch_alpaca_bars(symbol, api_key, api_secret, days=max(1, hours // 24), kind=kind)
    return PriceSeries(symbol=symbol, candles=candles)


def _tv_configured(config: Any | None) -> bool:
    if config is None:
        return False
    try:
        key = config.venues.tradingview.credentials.get("api_key", "")
        return bool(key) and "YOUR_" not in key
    except Exception:
        return False


async def fetch_tv_bars(
    symbol: str,
    hours: int = 72,
    interval: str = "1h",
    config: Any | None = None,
) -> list[Candle]:
    """Fetch historical bars from TradingViewAPI (single-symbol history endpoint).

    Uses the RapidAPI proxy convention (X-RapidAPI-Key + X-RapidAPI-Host),
    matching the live TradingViewApiAdapter, so backtests/backfill resolve the
    same host/key as the monitor loop.
    """
    import httpx

    base = "https://tradingview-data1.p.rapidapi.com"
    host = "tradingview-data1.p.rapidapi.com"
    key = ""
    if config is not None:
        try:
            base = config.venues.tradingview.api_url or base
            host = config.venues.tradingview.get("api_host", host)
            key = config.venues.tradingview.credentials.get("api_key", "")
        except Exception:
            pass
    # Env override (TRADINGVIEW_* in .env) wins for quick local testing.
    from hermes.core.secrets import get_secret_or_none

    key = key or (get_secret_or_none("tradingview.api_key") or "")
    base = get_secret_or_none("tradingview.base_url") or base
    host = get_secret_or_none("tradingview.api_host") or host

    end = int(time.time())
    start = end - hours * 3600
    url = f"{base.rstrip('/')}/api/history/{symbol.replace('/', '').upper()}"
    params = {"tf": interval, "from": start, "to": end, "limit": 500}
    headers = {"X-RapidAPI-Key": key, "X-RapidAPI-Host": host} if key else {}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        log.warning("tv_bars_fetch_failed", symbol=symbol, error=str(exc)[:120])
        return []
    rows = data.get("bars", data if isinstance(data, list) else [])
    candles: list[Candle] = []
    for k in rows:
        try:
            candles.append(
                Candle(
                    ts_ms=int(k["t"]) * 1000,
                    open=float(k["o"]),
                    high=float(k["h"]),
                    low=float(k["l"]),
                    close=float(k["c"]),
                    volume=float(k.get("v", 0) or 0),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c.ts_ms)
    return candles


def fetch_price_series_sync(symbol: str, **kw) -> PriceSeries:
    """Blocking wrapper (for non-async callers / backtest setup)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(1) as ex:
            return ex.submit(lambda: asyncio.run(fetch_price_series(symbol, **kw))).result(timeout=40)
    return asyncio.run(fetch_price_series(symbol, **kw))
