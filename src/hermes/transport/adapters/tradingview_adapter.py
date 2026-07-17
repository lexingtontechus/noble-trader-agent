"""TradingViewApiAdapter — venue-agnostic price source via TradingViewAPI (RapidAPI).

This adapter backs the `tradingview` venue so Hermes no longer depends on Alpaca /
Hyperliquid for *pricing*. It is the same upstream Noble Trader's backend normalizes
from, keeping signal + price layers on one source. Implements the VenueAdapter
interface (transport/adapters/base.py) so `monitor` and `price_feed.py` consume it
unchanged.

Pricing endpoints (RapidAPI, Ultra plan):
  - POST /api/price/batch   body {"requests":[{"symbol":"EURUSD"}, ...]}  -> one call, many quotes
  - GET  /api/price/{symbol}                                                -> single-symbol fallback
  - GET  /api/history/{symbol}?tf=&from=&to=&limit=                         -> historical bars

Real-time streaming (Ultra/Mega plans):
  - WebSocket: wss://ws.tradingviewapi.com/ws?token=YOUR_TOKEN
    On connect, send per-symbol subscribe frames:
        {"action": "subscribe", "symbol": "EURUSD"}
    Server pushes quote frames; we extract the close/last price (same shape as
    the REST `current.close`). When the WS is healthy it is the PRIMARY quote
    path (zero REST budget burned); REST polling is the fallback when WS is
    unavailable or the plan has no WS entitlement.

429 handling (REST fallback): RapidAPI returns 429 when the plan's rate limit is
hit. We honor Retry-After (or exponential backoff), split a failed batch into
per-symbol GET fallbacks, and cap retries — degrading gracefully (return None)
rather than crashing the monitor loop.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.core.secrets import get_secret_or_none
from hermes.schemas.market import Bar, Tick, Venue
from hermes.transport.adapters.base import VenueAdapter

log = structlog.get_logger(__name__)

# RapidAPI publisher host (used as the X-RapidAPI-Host header when the
# RapidAPI-proxied TradingView API is used — the Ultra plan the user subscribed to).
_RAPIDAPI_HOST = "tradingview-data1.p.rapidapi.com"
_RAPIDAPI_BASE = f"https://{_RAPIDAPI_HOST}"

# TradingViewAPI native WebSocket (Ultra/Mega). token == the TradingViewAPI key.
_WS_URL = "wss://ws.tradingviewapi.com/ws?token={token}"


def _hhmm_to_min(hhmm: str) -> int:
    """Parse 'HH:MM' (or 'H:MM' / 'HHMM') to minutes-since-midnight."""
    hhmm = str(hhmm).strip().replace(":", "")
    if len(hhmm) >= 3:
        return int(hhmm[:-2]) * 60 + int(hhmm[-2:])
    return int(hhmm) * 60


class TradingViewApiAdapter(VenueAdapter):
    """TradingViewAPI (RapidAPI + WebSocket) price adapter.

    WS-first: if the plan supports streaming, a single persistent socket pushes
    quotes for all subscribed symbols. REST polling is used only as a fallback
    (no WS entitlement, or WS connection lost) and for historical bars.
    """

    venue = Venue.TRADINGVIEW

    def __init__(self, config: HermesConfig) -> None:
        vc = config.venues.get("tradingview") or config.venues.get("tradingview".upper()) or None
        creds = getattr(vc, "credentials", {}) or {}
        feats = getattr(vc, "features", {}) or {}
        # Resolve key: config (secret:tradingview.api_key -> TRADINGVIEW_API_KEY)
        # takes precedence; falls back to explicit env vars.
        self._api_key = (
            creds.get("api_key", "")
            or creds.get("rapidapi_key", "")
            or (get_secret_or_none("tradingview.api_key") or "")
        )
        # Base URL + RapidAPI host header. RapidAPI proxy uses a fixed host
        # (tradingview-data1.p.rapidapi.com) regardless of the base URL path.
        self._base_url = (
            (feats.get("api_url") or "").rstrip("/")
            or get_secret_or_none("tradingview.base_url")
            or _RAPIDAPI_BASE
        )
        self._rapid_host = (
            feats.get("api_host")
            or get_secret_or_none("tradingview.api_host")
            or _RAPIDAPI_HOST
        )
        self._poll_interval = float(feats.get("poll_interval_sec", 5.0))
        self._max_retries = int(feats.get("max_retries", 3))
        # WS enabled by default (Ultra/Mega); disable via config for REST-only plans.
        self._use_ws = bool(feats.get("use_websocket", True)) and bool(self._api_key)
        # WS url template (the {token} is the raw key for display only; the
        # real connect url comes from POST /api/token/generate as a JWT).
        self._ws_url_template = feats.get("ws_url", _WS_URL)

        # --- Plan + scheduling (silent defaults; user needs no extra setup) ---
        # ws_plan pairs the WS entitlement/cooldown with the TradingViewAPI plan.
        # SILENT DEFAULT = ultra (6 WS-hours/day). mega = 24h/day; none = REST-only.
        self._ws_plan = (feats.get("ws_plan") or "ultra").lower()
        self._ws_daily_budget_sec = {
            "none": 0.0,
            "basic": 0.0,
            "pro": 0.0,
            "ultra": 6 * 3600.0,
            "mega": 24 * 3600.0,
        }.get(self._ws_plan, 6 * 3600.0)
        if self._ws_daily_budget_sec <= 0:
            self._use_ws = False  # plan has no WS entitlement -> REST-only
        # ws_mode: on_demand (signal-gated, default) | always | scheduled.
        self._ws_mode = (feats.get("ws_mode") or "on_demand").lower()
        # ws_schedule: "use_active_hours" reuses the existing active_hours block
        # (same user timezone) so no extra config is needed; or "HH:MM-HH:MM".
        self._ws_schedule = feats.get("ws_schedule", "use_active_hours")
        self._ws_schedule_window = self._resolve_ws_schedule(config)
        # Always bind the user's timezone (from active_hours) so the daily WS
        # budget resets at local midnight, even in on_demand mode (no window).
        ah = getattr(config, "active_hours", None)
        self._ws_tz = getattr(ah, "timezone", "UTC") or "UTC"
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(self._ws_tz)
        except Exception:
            self._ws_tz = "UTC"
        # Cost floor when WS is off (no entitlement / budget spent / outside window).
        self._rest_fallback_interval = float(feats.get("rest_fallback_interval_sec", 60.0))
        # How long WS stays up per ACTIVE signal before the cooldown reverts to REST.
        self._active_ws_ttl = float(feats.get("active_ws_ttl_sec", 300.0))

        self._configured = bool(self._api_key) and "YOUR_" not in self._api_key
        self._http: Any | None = None
        self._stop = False
        # WebSocket runtime state
        self._ws: Any | None = None
        self._ws_task: asyncio.Task | None = None
        self._ws_connected = False
        self._ws_symbols: set[str] = set()
        self._ws_token: str | None = None
        self._ws_token_expires_at: float = 0.0  # epoch seconds
        # symbol -> asyncio.Queue[float] of latest prices pushed by WS
        self._ws_prices: dict[str, "asyncio.Queue[float]"] = {}
        # Per-symbol poll interval overrides (seconds). Set by the monitor when a
        # signal-driven urgency tier arrives (ACTIVE=5, WATCH=15, IDLE=60). Only
        # affects the REST-poll fallback path; WS streaming ignores it.
        self._symbol_intervals: dict[str, float] = {}
        # WS daily budget tracking (paired to plan). Keyed by local day string in
        # the user's timezone so the 6h/24h entitlement resets at local midnight.
        self._ws_budget_day: str = ""
        self._ws_secs_used_today: float = 0.0
        self._ws_budget_exhausted: bool = False
        # Epoch time WS was last opened (for TTL/cooldown in on_demand mode).
        self._ws_opened_at: float = 0.0

    def set_symbol_interval(self, symbol: str, seconds: float) -> None:
        """Set a per-symbol poll interval (urgency tier). Effective on REST fallback."""
        self._symbol_intervals[symbol] = max(0.1, float(seconds))

    def get_symbol_interval(self, symbol: str) -> float:
        return self._symbol_intervals.get(symbol, self._poll_interval)

    def get_ws_status(self) -> dict:
        """Surface TradingView WS plan / budget / connection state for monitor UI.

        Read by PriceMonitor.get_stats() so the CLI [stats] line and the web
        /monitor page both show WS budget usage (paired to ws_plan).
        """
        self._ws_budget_reset_if_new_day()
        remaining = max(0.0, self._ws_daily_budget_sec - self._ws_secs_used_today)
        active = sorted(
            s for s, iv in self._symbol_intervals.items() if iv <= 5.0
        )
        return {
            "venue": "tradingview",
            "plan": self._ws_plan,
            "mode": self._ws_mode,
            "use_ws": self._use_ws,
            "connected": self._ws_connected,
            "schedule_window": list(self._ws_schedule_window) if self._ws_schedule_window else None,
            "timezone": self._ws_tz,
            "budget_sec": self._ws_daily_budget_sec,
            "used_sec_today": round(self._ws_secs_used_today, 1),
            "remaining_sec": round(remaining, 1),
            "remaining_hours": round(remaining / 3600.0, 2),
            "budget_exhausted": self._ws_budget_exhausted,
            "fallback_interval_sec": self._rest_fallback_interval,
            "active_ws_ttl_sec": self._active_ws_ttl,
            "active_symbols": active,
        }

    # ------------------------------------------------------------------ #
    # WS plan + schedule helpers
    # ------------------------------------------------------------------ #
    def _resolve_ws_schedule(self, config: HermesConfig) -> "tuple[int, int] | None":
        """Resolve the (start_min, end_min) WS window in the user's timezone.

        Returns None if no window applies (always-on). Defaults to the existing
        `active_hours` block so the user's locale/timezone is reused silently.
        """
        sched = self._ws_schedule
        if sched in (None, "", "always", "none"):
            return None
        if sched == "use_active_hours":
            ah = getattr(config, "active_hours", None)
            if ah is None:
                return None
            tz = getattr(ah, "timezone", "UTC") or "UTC"
            start = getattr(ah, "start", "09:30")
            end = getattr(ah, "end", "16:00")
        else:
            # Explicit "HH:MM-HH:MM" — timezone comes from active_hours if present.
            ah = getattr(config, "active_hours", None)
            tz = getattr(ah, "timezone", "UTC") or "UTC"
            try:
                a, b = sched.split("-")
                start, end = a.strip(), b.strip()
            except Exception:
                return None
        try:
            from zoneinfo import ZoneInfo
            ZoneInfo(tz)  # validate tz exists
        except Exception:
            tz = "UTC"
        self._ws_tz = tz
        return (_hhmm_to_min(start), _hhmm_to_min(end))

    def _ws_budget_reset_if_new_day(self) -> None:
        """Reset the daily WS-second counter at local midnight (user timezone)."""
        if not hasattr(self, "_ws_tz"):
            self._ws_tz = "UTC"
        from datetime import datetime as _dt
        try:
            from zoneinfo import ZoneInfo
            now = _dt.now(ZoneInfo(self._ws_tz))
        except Exception:
            now = _dt.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        if day != self._ws_budget_day:
            self._ws_budget_day = day
            self._ws_secs_used_today = 0.0
            self._ws_budget_exhausted = False

    def _ws_budget_available(self) -> bool:
        if self._ws_daily_budget_sec <= 0:
            return False
        self._ws_budget_reset_if_new_day()
        return not self._ws_budget_exhausted and (
            self._ws_secs_used_today < self._ws_daily_budget_sec
        )

    def _ws_in_schedule_window(self) -> bool:
        if self._ws_schedule_window is None:
            return True  # always-on (no window)
        from datetime import datetime as _dt
        try:
            from zoneinfo import ZoneInfo
            now = _dt.now(ZoneInfo(self._ws_tz))
        except Exception:
            now = _dt.now(timezone.utc)
        cur = now.hour * 60 + now.minute
        s, e = self._ws_schedule_window
        return s <= cur <= e

    def _ws_should_connect(self) -> bool:
        """Decide whether WS should be open right now, given mode + budget + window."""
        if not self._use_ws:
            return False
        if not self._ws_budget_available():
            return False
        if self._ws_mode == "always":
            return True
        if self._ws_mode == "scheduled":
            return self._ws_in_schedule_window()
        # on_demand (default): open only while >=1 symbol is in ACTIVE tier.
        # Check the interval map directly (the monitor can set ACTIVE tiers for
        # symbols before/without stream_ticks having registered them yet).
        return any(iv <= 5.0 for iv in self._symbol_intervals.values())

    def _ws_budget_tick(self, secs: float) -> None:
        """Record WS usage against the daily plan budget."""
        self._ws_budget_reset_if_new_day()
        self._ws_secs_used_today += secs
        if self._ws_secs_used_today >= self._ws_daily_budget_sec:
            self._ws_budget_exhausted = True
            log.info(
                "tradingview_ws_budget_exhausted",
                plan=self._ws_plan,
                used_sec=self._ws_secs_used_today,
                budget_sec=self._ws_daily_budget_sec,
            )

    # ------------------------------------------------------------------ #
    async def connect(self) -> None:
        if not self._configured:
            log.warning("tradingview_not_configured", note="set TRADINGVIEW_API_KEY (venues.tradingview.credentials.api_key)")
            return
        import httpx

        # RapidAPI auth: X-RapidAPI-Key + X-RapidAPI-Host. NOT Authorization: Bearer.
        headers = {
            "X-RapidAPI-Key": self._api_key,
            "X-RapidAPI-Host": self._rapid_host,
        }
        self._http = httpx.AsyncClient(timeout=20.0, headers=headers)
        log.info("tradingview_connected", base=self._base_url, host=self._rapid_host)

        if self._use_ws:
            self._ws_task = asyncio.create_task(self._ws_loop())

    async def disconnect(self) -> None:
        self._stop = True
        if self._ws_task is not None:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._ws_connected = False
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ------------------------------------------------------------------ #
    # WebSocket loop (primary quote path when available)
    # ------------------------------------------------------------------ #
    async def _ws_loop(self) -> None:
        """Maintain the WS connection per plan budget / schedule / urgency tier.

        - on_demand (default): WS opens only while >=1 symbol is in ACTIVE tier
          (set by a trade=true signal via monitor.control) and closes after
          active_ws_ttl_sec (the "cooldown"). This spends the plan's daily WS
          budget only on actionable windows.
        - always: WS stays open whenever budget + window allow.
        - scheduled: WS open only inside the ws_schedule window (user timezone).
        The daily WS-second budget (_ws_daily_budget_sec, from ws_plan) is
        tracked and, once exhausted, WS is left closed until local midnight.
        """
        import websockets

        backoff = 1.0
        while not self._stop:
            # Decide whether WS should be up right now.
            if not self._ws_should_connect():
                # WS not wanted/allowed now — sleep, then re-evaluate. REST
                # fallback (stream_ticks else-branch) covers the gap.
                await asyncio.sleep(2.0)
                backoff = 1.0
                continue

            ws_url = await self._ws_get_url()
            if ws_url is None:
                log.warning("tradingview_ws_no_token", note="REST-only plan or token endpoint failed")
                await asyncio.sleep(30.0)
                continue

            opened_at = time.time()
            self._ws_opened_at = opened_at
            try:
                log.info("tradingview_ws_connecting", url=ws_url.split("?")[0], mode=self._ws_mode)
                async with websockets.connect(
                    ws_url, ping_interval=20, ping_timeout=10, close_timeout=5
                ) as ws:
                    self._ws = ws
                    self._ws_connected = True
                    log.info("tradingview_ws_connected", plan=self._ws_plan)
                    for sym in list(self._ws_symbols):
                        await self._ws_send_subscribe(sym)
                    async for raw in ws:
                        if self._stop:
                            break
                        self._ws_on_message(raw)
                        # Token refresh before 30-min JWT expiry.
                        if time.time() >= self._ws_token_expires_at - 120:
                            log.info("tradingview_ws_token_refresh_due")
                            break
                        # on_demand: close after the ACTIVE TTL cooldown, or as
                        # soon as no symbol remains in the ACTIVE tier.
                        if self._ws_mode == "on_demand":
                            still_active = any(
                                self._symbol_intervals.get(s, 99) <= 5.0 for s in self._ws_symbols
                            )
                            if (time.time() - opened_at) >= self._active_ws_ttl or not still_active:
                                log.info("tradingview_ws_cooldown", reason="ttl_or_no_active")
                                break
                        # Budget guard: if we blew the daily WS budget, close.
                        if not self._ws_budget_available():
                            log.info("tradingview_ws_budget_exhausted_close")
                            break
                # Record budget usage for the time we were connected.
                self._ws_budget_tick(time.time() - opened_at)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws_connected = False
                self._ws = None
                log.warning("tradingview_ws_error", err=str(e)[:160])
            finally:
                self._ws_connected = False
                self._ws = None
            if self._stop:
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 15.0)

    async def _ws_get_url(self) -> str | None:
        """Mint a JWT and return the WS url (or None if not entitled)."""
        if self._http is None:
            return None
        # Reuse a valid (non-expiring) token if we have one.
        if self._ws_token and time.time() < self._ws_token_expires_at - 120:
            return self._ws_url_template.format(token=self._ws_token)
        data = await self._request("POST", f"{self._base_url}/api/token/generate", json={})
        if not data or not data.get("success"):
            return None
        self._ws_token = data.get("token")
        # Record expiry (ms epoch in response; default 30 min if absent).
        exp_ms = data.get("expiresAt")
        self._ws_token_expires_at = (
            (exp_ms / 1000.0) if isinstance(exp_ms, (int, float)) else time.time() + 1800.0
        )
        # Prefer the wsUrl from the response (already includes ?token=).
        ws_url = data.get("wsUrl") or (data.get("url"))
        if ws_url:
            return ws_url
        if self._ws_token:
            return self._ws_url_template.format(token=self._ws_token)
        return None

    async def _ws_send_subscribe(self, symbol: str) -> None:
        if self._ws is None:
            return
        try:
            frame = json.dumps({"action": "subscribe", "symbol": self.normalize_symbol(symbol)})
            await self._ws.send(frame)
        except Exception as e:
            log.warning("tradingview_ws_subscribe_failed", symbol=symbol, err=str(e)[:120])

    def _ws_on_message(self, raw: Any) -> None:
        """Parse a WS frame and enqueue the price for the relevant symbol."""
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except Exception:
            return
        px = self._extract_one(msg)
        if px is None:
            return
        sym = self._ws_symbol_of(msg)
        if sym is None:
            return
        q = self._ws_prices.get(sym)
        if q is not None:
            try:
                q.put_nowait(px)
            except asyncio.QueueFull:
                # Drop oldest to keep latest; monitor only needs the freshest.
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(px)
                except asyncio.QueueFull:
                    pass

    @staticmethod
    def _ws_symbol_of(msg: Any) -> str | None:
        """Pull the symbol identity from a WS quote frame."""
        if not isinstance(msg, dict):
            return None
        sym = msg.get("symbol") or (msg.get("data", {}) or {}).get("symbol")
        if isinstance(sym, str) and sym:
            return sym.upper()
        return None

    def _ensure_ws_queue(self, symbol: str) -> "asyncio.Queue[float]":
        if symbol not in self._ws_prices:
            self._ws_prices[symbol] = asyncio.Queue(maxsize=64)
        return self._ws_prices[symbol]

    # ------------------------------------------------------------------ #
    # Core REST call with 429-aware retry
    # ------------------------------------------------------------------ #
    async def _request(self, method: str, url: str, **kw: Any) -> Any | None:
        if self._http is None:
            return None
        backoff = 1.0
        for _ in range(self._max_retries + 1):
            try:
                resp = await self._http.request(method, url, **kw)
            except Exception as e:  # network error -> back off, don't crash loop
                log.warning("tradingview_neterr", err=str(e)[:120])
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 8.0)
                continue
            if resp.status_code == 429:
                retry_after = resp.headers.get("retry-after")
                wait = float(retry_after) if (retry_after and retry_after.isdigit()) else backoff
                log.warning("tradingview_429", wait=wait)
                await asyncio.sleep(wait)
                backoff = min(backoff * 2, 8.0)
                continue
            if resp.status_code >= 400:
                log.warning("tradingview_http", status=resp.status_code, url=url)
                return None
            try:
                return resp.json()
            except Exception:
                return None
        log.error("tradingview_exhausted", url=url)
        return None

    # ------------------------------------------------------------------ #
    # VenueAdapter interface
    # ------------------------------------------------------------------ #
    async def stream_ticks(self, symbols: list[str]) -> AsyncIterator[Tick]:
        """Yield ticks for symbols.

        WS-first: each symbol gets a queue fed by the WS loop. If the WS is
        connected we yield prices as they arrive (real-time, no poll). If the
        WS is unavailable we fall back to the REST poll loop at poll_interval.
        """
        if not self._configured:
            return
        # Register symbols for WS subscription.
        for sym in symbols:
            self._ws_symbols.add(sym)
            self._ensure_ws_queue(sym)
            if self._ws_connected:
                await self._ws_send_subscribe(sym)

        if self._use_ws and self._ws_connected:
            # WS path: yield from per-symbol queues while the socket is live.
            queues = {sym: self._ws_prices[sym] for sym in symbols}
            while not self._stop:
                yielded = False
                for sym, q in queues.items():
                    try:
                        px = q.get_nowait()
                    except asyncio.QueueEmpty:
                        continue
                    yielded = True
                    yield Tick(ts=datetime.now(timezone.utc), venue=self.venue, symbol=sym, price=px, size=0.0)
                if yielded:
                    continue
                await asyncio.sleep(0.05)
        else:
            # REST poll fallback: WS disabled, not yet connected, or closed
            # (on_demand cooldown / budget spent / outside schedule window).
            # Honor per-symbol urgency tiers + the IDLE cost floor.
            while not self._stop:
                quotes = await self.get_quotes(symbols)
                ts = datetime.now(timezone.utc)
                for sym, px in quotes.items():
                    if px is None:
                        continue
                    yield Tick(ts=ts, venue=self.venue, symbol=sym, price=px, size=0.0)
                # Sleep the min interval across symbols (urgency-aware); fall
                # back to the configured IDLE cost floor when no tier is set.
                intervals = [self.get_symbol_interval(s) for s in symbols]
                interval = min(intervals) if intervals else self._rest_fallback_interval
                await asyncio.sleep(interval)

    async def stream_order_book(self, symbols: list[str]) -> AsyncIterator[Any]:
        # TradingViewAPI (Ultra) does not provide L2; yield nothing.
        return
        yield  # pragma: no cover - keeps this an async generator, never reached

    async def get_current_price(self, symbol: str) -> float | None:
        q = await self.get_quotes([symbol])
        return q.get(symbol)

    async def fetch_historical_bars(
        self, symbol: str, timeframe: str, start: datetime, end: datetime, limit: int = 10000
    ) -> list[Bar]:
        url = f"{self._base_url}/api/history/{self.normalize_symbol(symbol)}"
        params = {
            "tf": timeframe,
            "from": int(start.timestamp()),
            "to": int(end.timestamp()),
            "limit": limit,
        }
        data = await self._request("GET", url, params=params)
        bars: list[Bar] = []
        if not data:
            return bars
        rows = data.get("data", data.get("bars", data if isinstance(data, list) else []))
        if isinstance(rows, dict):
            rows = rows.get("history", [])
        for row in rows:
            try:
                ts = int(row.get("time", row.get("t", 0)))
                bars.append(
                    Bar(
                        ts_open=datetime.fromtimestamp(ts, tz=timezone.utc),
                        ts_close=datetime.fromtimestamp(ts, tz=timezone.utc),
                        venue=self.venue,
                        symbol=symbol,
                        timeframe=timeframe,
                        open=float(row["open"]),
                        high=float(row.get("max", row.get("high", row["open"]))),
                        low=float(row.get("min", row.get("low", row["open"]))),
                        close=float(row.get("close", row.get("c", row["open"]))),
                        volume=float(row.get("volume", 0) or 0),
                    )
                )
            except (KeyError, ValueError, TypeError):
                continue
        bars.sort(key=lambda b: b.ts_open)
        return bars

    # ------------------------------------------------------------------ #
    # Batch-first quote fetch with per-symbol fallback
    # ------------------------------------------------------------------ #
    async def get_quotes(self, symbols: list[str]) -> dict[str, float | None]:
        """Return {symbol: price|None}. Batch endpoint first; fall back per-symbol.

        TradingViewAPI batch schema (RapidAPI):
          POST /api/price/batch  body {"requests":[{"symbol":"EURUSD"}, ...]}
          resp {"success":true,"data":{"data":[{"symbol","current":{"close":..}}, ...]}}
        Single fallback:
          GET  /api/price/{symbol}  -> {"data":{"symbol","current":{"close":..}}}
        """
        out: dict[str, float | None] = {s: None for s in symbols}
        if not symbols or not self._configured:
            return out

        batch = await self._request(
            "POST",
            f"{self._base_url}/api/price/batch",
            json={"requests": [{"symbol": self.normalize_symbol(s)} for s in symbols]},
        )
        if batch:
            out.update(self._extract_prices(batch, symbols))
        missing = [s for s in symbols if out[s] is None]
        for sym in missing:
            single = await self._request("GET", f"{self._base_url}/api/price/{self.normalize_symbol(sym)}")
            out[sym] = self._extract_one(single) if single else None
            if out[sym] is None:
                await asyncio.sleep(0.1)  # space out fallback storm on 429s
        return out

    # ------------------------------------------------------------------ #
    @staticmethod
    def _extract_prices(payload: Any, symbols: list[str]) -> dict[str, float | None]:
        if not isinstance(payload, dict):
            return {}
        # resp.data.data is the list of per-symbol results
        inner = payload.get("data", {})
        results = inner.get("data", []) if isinstance(inner, dict) else []
        out: dict[str, float | None] = {}
        for item in results:
            if not isinstance(item, dict):
                continue
            sym = item.get("symbol")
            px = TradingViewApiAdapter._extract_one(item)
            if sym is not None:
                out[sym] = px
        # Also satisfy any original (pre-normalized) symbol names,
        # handling exchange-qualified keys (COINBASE:BTCUSD).
        out2 = {}
        for s in symbols:
            if s in out:
                out2[s] = out[s]
            else:
                bare = s.split(":")[-1].replace("/", "").upper() if ":" in s else s.replace("/", "").upper()
                out2[s] = out.get(s, out.get(bare))
        return out2

    @staticmethod
    def _extract_one(node: Any) -> float | None:
        """Pull a close price from a TradingViewAPI item / single-response / WS frame."""
        if not isinstance(node, dict):
            return None
        # single: {"data":{"symbol","current":{"close":..}}}
        data = node.get("data", node)
        if isinstance(data, dict):
            cur = data.get("current", data)
            if isinstance(cur, dict):
                for k in ("close", "last", "price", "lp"):
                    if isinstance(cur.get(k), (int, float)):
                        return float(cur[k])
        # Some frames put the price at top level (e.g. {"symbol":..,"price":..})
        for k in ("close", "last", "price", "lp"):
            if isinstance(node.get(k), (int, float)):
                return float(node[k])
        return None

    def normalize_symbol(self, hermes_symbol: str) -> str:
        """TradingView uses e.g. 'EURUSD' (no slash).

        Accepts exchange-qualified keys (COINBASE:BTCUSD) and strips the
        exchange prefix before hitting the API (the API takes the bare
        ticker); the exchange is recorded in the symbols registry, not the
        price request.
        """
        s = hermes_symbol.replace("/", "").upper()
        return s.split(":")[-1] if ":" in s else s
