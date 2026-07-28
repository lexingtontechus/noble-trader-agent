"""
Data-fetch helpers for dashboard chart rendering.

Two responsibilities:
  1. fetch_tdva_candles() — drop-in replacement for the legacy _hl_candles()
     in noble_cli.py:205-247. Pulls historical OHLCV bars from TDVA
     (TradingView Data API) via the agent's own TradingViewApiAdapter, with
     the local proxy's /history/{symbol} endpoint as cached fallback.
     Supports multi-asset (crypto + forex + equities + commodities) — NOT
     Hyperliquid (which is crypto-only and being retired).

  2. DuckDB lookups for the latest heartbeat + brick_size per symbol. These
     are tiny read-only queries against signal_heartbeats; the helper opens
     a fresh connection per call (matches the existing pattern in
     web/status.py — no pooling).

See DASHBOARD-UPGRADE-SCOPING.md §7.4 for the full TDVA migration plan.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
from typing import Any

import httpx
import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.schemas.market import Bar, Venue

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# TDVA candle fetch — agent-direct via TradingViewApiAdapter, with proxy fallback
# ---------------------------------------------------------------------------


_adapter: Any = None  # lazy singleton — set on first call
_adapter_lock = asyncio.Lock() if False else None  # placeholder; we use sync below


def _get_tdva_adapter(config: HermesConfig):
    """Lazy-init the TradingViewApiAdapter singleton.

    The adapter is thread-safe for read-only fetch_historical_bars calls
    (no shared mutable state). We hold a process-wide singleton to avoid
    re-creating httpx clients on every chart render.
    """
    global _adapter
    if _adapter is not None:
        return _adapter
    from hermes.transport.adapters.tradingview_adapter import TradingViewApiAdapter

    _adapter = TradingViewApiAdapter(config)
    return _adapter


def _proxy_history_url(config: HermesConfig, symbol: str, timeframe: str, limit: int) -> str:
    """Build the proxy /history/{symbol} URL for fallback."""
    nt = config.upstream.get("noble_trader", {})
    proxy_cfg = nt.get("quote_proxy", {})
    # quote_proxy.url is typically secret:noble_trader.quote_proxy_url — already
    # resolved by the secrets loader. Fall back to localhost:8080 if missing
    # (matches the proxy's PORT default in proxy/core/settings.py:339 and the
    # PORT=8080 entry in noble-trader-proxy/.env.example:65).
    base = proxy_cfg.get("url") or "http://localhost:8080"
    return f"{base}/history/{symbol}?tf={timeframe}&limit={limit}"


def _run_async(coro):
    """Run an async coroutine from a sync context.

    If an event loop is already running (e.g. inside FastAPI's loop), we use
    asyncio.run_coroutine_threadsafe on a background thread to avoid the
    "cannot run from a running event loop" error. Otherwise we just use
    asyncio.run() which is simpler + faster.
    """
    try:
        asyncio.get_running_loop()
        # We're inside a running loop — run the coro on a fresh background
        # loop in a worker thread. This is the path taken when chart PNG
        # endpoints are sync `def` (Starlette runs them in a threadpool).
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result(timeout=30)
    except RuntimeError:
        # No running loop — safe to use asyncio.run() directly.
        return asyncio.run(coro)


async def _fetch_bars_via_adapter(
    config: HermesConfig, symbol: str, timeframe: str, limit: int,
) -> list[Bar]:
    """Path 1: direct TradingViewApiAdapter call (agent-side)."""
    adapter = _get_tdva_adapter(config)
    end = dt.datetime.now(dt.timezone.utc)
    start = end - dt.timedelta(hours=24)
    bars = await adapter.fetch_historical_bars(
        symbol=symbol, timeframe=timeframe, start=start, end=end, limit=limit,
    )
    return bars


def _fetch_bars_via_proxy(
    config: HermesConfig, symbol: str, timeframe: str, limit: int,
) -> list[Bar]:
    """Path 2: HTTP poll the local proxy's /history/{symbol} endpoint.

    The proxy has its own cache layer + persistent TVDA WebSocket, so this
    path is preferred when the adapter's RapidAPI key is missing or
    rate-limited.
    """
    url = _proxy_history_url(config, symbol, timeframe, limit)
    nt = config.upstream.get("noble_trader", {})
    proxy_cfg = nt.get("quote_proxy", {})
    timeout = float(proxy_cfg.get("timeout_sec", 5))

    # Audit 2026-07-22: the proxy's /history/{symbol} requires X-License-Key
    # (proxy/api/history.py:96). Without it, the proxy returns 401 and the
    # fallback always fails in production. Source the license key from the
    # proxy_cfg (config: upstream.noble_trader.quote_proxy.license_key) or
    # from the NOBLE_TRADER_LICENSE_KEY env var.
    license_key = (
        proxy_cfg.get("license_key")
        or os.environ.get("NOBLE_TRADER_LICENSE_KEY")
    )
    headers = {"X-License-Key": license_key} if license_key else {}

    r = httpx.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    payload = r.json()
    bars: list[Bar] = []
    rows = payload.get("bars") or payload.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("history", [])
    for row in rows:
        try:
            # Audit 2026-07-22: proxy emits {"ts": ...} but the original code
            # only looked for "time"/"t". Added "ts" to the lookup chain so
            # bar timestamps are correct on the proxy-fallback path.
            ts_epoch = int(row.get("time", row.get("t", row.get("ts", 0))))
            ts = dt.datetime.fromtimestamp(ts_epoch, tz=dt.timezone.utc)
            bars.append(
                Bar(
                    ts_open=ts, ts_close=ts,
                    venue=Venue.TRADINGVIEW, symbol=symbol, timeframe=timeframe,
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


def fetch_tdva_candles(
    config: HermesConfig,
    symbol: str,
    *,
    limit: int = 200,
    timeframe: str = "15m",
    with_timestamps: bool = False,
) -> list[float] | tuple[list[float], list[dt.datetime]]:
    """Drop-in replacement for the legacy _hl_candles() in noble_cli.py:205-247.

    Returns a list of close prices (or (closes, timestamps) if with_timestamps=True)
    pulled from TDVA (TradingView Data API). Multi-asset — crypto + forex +
    equities + commodities. Never Hyperliquid.

    Tries (1) direct TradingViewApiAdapter, then (2) proxy /history/{symbol}
    fallback. Returns [] if both fail (caller should render an empty_chart).
    """
    # Path 1: direct adapter
    bars: list[Bar] = []
    try:
        bars = _run_async(_fetch_bars_via_adapter(config, symbol, timeframe, limit))
    except Exception as e:
        log.warning(
            "tdva_adapter_failed", symbol=symbol, error=str(e)[:120],
            fallback="proxy /history",
        )
        # Path 2: proxy HTTP fallback
        try:
            bars = _fetch_bars_via_proxy(config, symbol, timeframe, limit)
        except Exception as e2:
            log.error(
                "tdva_both_paths_failed", symbol=symbol,
                adapter_err=str(e)[:120], proxy_err=str(e2)[:120],
            )
            bars = []

    if not bars:
        return ([], []) if with_timestamps else []

    closes = [b.close for b in bars]
    ts_list = [b.ts_open for b in bars]
    if with_timestamps:
        return closes, ts_list
    return closes


# ---------------------------------------------------------------------------
# DuckDB heartbeat lookups — latest per symbol + brick_size + history
# ---------------------------------------------------------------------------


def get_latest_heartbeat(config: HermesConfig, symbol: str) -> dict[str, Any] | None:
    """Return the latest heartbeat row for `symbol` as a dict, or None."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return None
        with duckdb.connect(str(db_path), read_only=True) as conn:
            result = conn.execute(
                """
                SELECT symbol, ts_received, signal, regime, regime_conf,
                       regime_shift, entry_price, stop_loss, take_profit,
                       brick_size, kelly_f, effective_kelly, ev_per_dollar,
                       p_win, p_regime, p_markov, p_timesfm, p_imbalance,
                       ev_scale, markov_current_state, prev_regime, shifts_24h,
                       tail_risk_score, tail_risk_action, p_pattern,
                       sources_used, weights_used,
                       calibration_bias, calibration_status
                FROM signal_heartbeats
                WHERE symbol = ?
                ORDER BY ts_received DESC
                LIMIT 1
                """,
                [symbol],
            ).fetchdf()
            if result.empty:
                return None
            return result.iloc[0].to_dict()
    except Exception as e:
        log.warning("get_latest_heartbeat_failed", symbol=symbol, error=str(e)[:120])
        return None


def get_brick_size_for_symbol(config: HermesConfig, symbol: str) -> float | None:
    """Pull the latest non-null brick_size for `symbol` from signal_heartbeats.

    Returns None if the symbol has no heartbeats yet, or if brick_size is
    NULL on the latest row. The caller should fall back to an ATR-based
    default in that case.
    """
    hb = get_latest_heartbeat(config, symbol)
    if hb is None:
        return None
    bs = hb.get("brick_size")
    if bs is None or bs <= 0:
        return None
    return float(bs)


def atr_default(closes: list[float], period: int = 14) -> float:
    """Compute a fallback brick_size from ATR over `period` closes.

    Used when the latest heartbeat has no brick_size (e.g. brand-new symbol).
    Returns atr(closes, period) * 0.5 — the standard renko-from-ATR heuristic.
    """
    if len(closes) < period + 1:
        # Not enough data — fall back to a tiny percentage of last close.
        if not closes:
            return 0.0001
        return closes[-1] * 0.001  # 0.1% of last close
    trs: list[float] = []
    for i in range(1, period + 1):
        tr = abs(closes[-i] - closes[-i - 1])
        trs.append(tr)
    atr = sum(trs) / len(trs)
    return max(atr * 0.5, closes[-1] * 0.0001)  # never zero
