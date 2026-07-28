"""MetaApi market/historical fallback — true fallback when the proxy is down.

Assessment (2026-07-27): the proxy is the primary source for /quotes +
/sse/alerts, but on outage the pricing pipeline only escalates CRITICAL with
no data. Each MT4/MT5 account carries a MetaApi CPU-credit budget for market
+ historical data, so MetaApi is a first-party fallback with no new vendor and
no TradingView rate-limit exposure (the weakness of the deprecated TVDA-direct
fallback).

This module wraps the existing ``MetaApiAdapter`` (VenueAdapter) — it does NOT
modify that file. It adds credit-aware caching so steady-state never burns
credits (the adapter only connects when activated, i.e. on proxy outage).

Design:
  - activate()  : lazily connects the adapter (outage-only).
  - deactivate(): flags off + disconnects (frees the broker connection).
  - get_price() : cached (~PRICE_TTL_SEC) get_current_price.
  - get_bars()  : cached by (symbol, tf, day) fetch_historical_bars; skipped
                   when CPU-credit usage is high (guard).
  - healthy     : active AND adapter RPC connected.
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime
from typing import Any

import structlog

from hermes.core.config import HermesConfig

log = structlog.get_logger(__name__)

# Credit discipline: never poll per-tick. Prices cached 10s; history cached per
# calendar day. The adapter only connects when activated (outage), so steady
# state burns zero MetaApi CPU credits.
PRICE_TTL_SEC = 10.0
# Skip history fetches when CPU-credit usage exceeds this fraction (0-1).
CPU_CREDIT_USAGE_WARN = 0.90


class MetaApiMarketFallback:
    """Credit-aware market-data fallback backed by a MetaApiAdapter."""

    def __init__(self, config: HermesConfig, adapter: Any | None = None) -> None:
        self._config = config
        # Lazy import avoids a hard SDK dependency at module load.
        if adapter is not None:
            self._adapter = adapter
        else:
            from hermes.transport.adapters.metaapi_adapter import MetaApiAdapter

            self._adapter = MetaApiAdapter(config)
        self._active = False
        self._price_cache: dict[str, tuple[float, float]] = {}  # sym -> (ts, price)
        self._bar_cache: dict[str, list] = {}  # key -> bars
        self._stats = {
            "activations": 0,
            "deactivations": 0,
            "price_hits": 0,
            "price_misses": 0,
            "bar_hits": 0,
            "bar_misses": 0,
            "bar_skipped_low_credit": 0,
            "errors": 0,
        }

    # ─── Lifecycle ───────────────────────────────────────────────────────

    async def activate(self) -> None:
        """Connect the adapter (idempotent). Outage-only — call on SSE dead."""
        if self._active:
            return
        try:
            await self._adapter.connect()
            self._active = True
            self._stats["activations"] += 1
            log.info("metaapi_fallback_activated")
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("metaapi_fallback_activate_failed", error=str(exc)[:200])

    async def deactivate(self) -> None:
        """Flag off + disconnect the adapter (frees the broker connection)."""
        if not self._active:
            return
        self._active = False
        self._stats["deactivations"] += 1
        self._price_cache.clear()
        self._bar_cache.clear()
        try:
            await self._adapter.disconnect()
        except Exception as exc:  # noqa: BLE001 - best effort
            log.warning("metaapi_fallback_disconnect_failed", error=str(exc)[:200])
        log.info("metaapi_fallback_deactivated")

    @property
    def active(self) -> bool:
        return self._active

    @property
    def healthy(self) -> bool:
        return self._active and getattr(self._adapter, "_rpc", None) is not None

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    # ─── Market data (credit-aware) ───────────────────────────────────────

    async def get_price(self, symbol: str) -> float | None:
        """Latest price, served from a short-TTL cache to avoid per-tick calls."""
        if not self._active:
            return None
        now = time.time()
        cached = self._price_cache.get(symbol)
        if cached and (now - cached[0]) < PRICE_TTL_SEC:
            self._stats["price_hits"] += 1
            return cached[1]
        self._stats["price_misses"] += 1
        try:
            price = await self._adapter.get_current_price(symbol)
        except Exception as exc:
            self._stats["errors"] += 1
            log.warning("metaapi_fallback_price_failed", symbol=symbol, error=str(exc)[:200])
            return None
        if price is not None:
            self._price_cache[symbol] = (now, price)
        return price

    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 10000,
    ) -> list:
        """Historical bars, cached per (symbol, tf, day); skipped if low credit."""
        if not self._active:
            return []
        cache_key = f"{symbol}|{timeframe}|{start.date().isoformat()}"
        if cache_key in self._bar_cache:
            self._stats["bar_hits"] += 1
            return self._bar_cache[cache_key]

        if self._cpu_credit_low():
            self._stats["bar_skipped_low_credit"] += 1
            log.warning("metaapi_fallback_bars_skipped_low_credit", symbol=symbol)
            return []

        self._stats["bar_misses"] += 1
        try:
            bars = await self._adapter.fetch_historical_bars(
                symbol, timeframe, start, end, limit=limit
            )
        except Exception as exc:
            self._stats["errors"] += 1
            log.warning("metaapi_fallback_bars_failed", symbol=symbol, error=str(exc)[:200])
            return []
        if bars:
            self._bar_cache[cache_key] = bars
        return bars

    # ─── CPU-credit guard ─────────────────────────────────────────────────

    def _cpu_credit_low(self) -> bool:
        """Best-effort CPU-credit check; returns False if unknown/unavailable.

        MetaApi exposes credit usage on the account/connection with varying
        method names across SDK versions, so we probe defensively and treat
        any failure as "unknown" (do not skip — avoid false denials).
        """
        conn = getattr(self._adapter, "_rpc", None) or getattr(self._adapter, "_account", None)
        if conn is None:
            return False
        for meth in ("get_credit_usage", "get_cpu_credit_usage", "retrieve_cpu_credit_usage"):
            fn = getattr(conn, meth, None)
            if callable(fn):
                try:
                    usage = fn() if not asyncio.iscoroutinefunction(fn) else asyncio.get_event_loop().run_in_executor(None, fn)
                    # Some SDK methods are sync; some return a coroutine.
                    if asyncio.iscoroutine(usage):
                        continue  # skip async probing to keep this sync+best-effort
                    if isinstance(usage, dict):
                        used = usage.get("used") or usage.get("creditUsed") or 0
                        total = usage.get("total") or usage.get("creditTotal") or 0
                        if total and (used / total) >= CPU_CREDIT_USAGE_WARN:
                            return True
                except Exception:
                    continue
        return False
