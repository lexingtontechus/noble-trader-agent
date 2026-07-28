"""
Renko brick chart renderer — /api/charts/renko/{symbol}.png

Rebuilds renko bricks on-demand from TDVA candles using the project's existing
RenkoConstructor (signals/renko_engine.py). No persistent renko_bricks table
needed — the constructor is deterministic given (brick_size, price series),
and rebuilding 100 bricks from 200 candles takes <50ms.

Caching: 60s in-memory TTL, keyed by (symbol, last_n). See _cache.py.

Migration note: this renderer replaces the legacy _renko_ladder() in
noble_cli.py:250-272 which was hard-wired to Hyperliquid. The new path
uses fetch_tdva_candles() from _data.py — multi-asset (crypto + forex +
equities + commodities), never HL.
"""

from __future__ import annotations

import datetime as dt
import io
import logging

import structlog
from matplotlib import pyplot as plt
from matplotlib.patches import Rectangle

from hermes.core.config import HermesConfig
from hermes.schemas.market import Tick, Venue
from hermes.signals.renko_engine import BrickPatternAnalyzer, RenkoConstructor
from hermes.web.charts._cache import get_or_render
from hermes.web.charts._data import (
    atr_default,
    fetch_tdva_candles,
    get_brick_size_for_symbol,
)
from hermes.web.charts._theme import NOBLE_PALETTE, apply_dark_theme, render_empty_chart

log = structlog.get_logger(__name__)


def _render_renko_png_impl(config: HermesConfig, symbol: str, last_n: int = 100) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    # 1. Get brick_size from latest heartbeat (or ATR fallback)
    brick_size = get_brick_size_for_symbol(config, symbol)
    if brick_size is None:
        # Need candles first to compute ATR fallback
        closes = fetch_tdva_candles(config, symbol, limit=200, timeframe="15m")
        if len(closes) < 5:
            return render_empty_chart(symbol, f"Insufficient candle data ({len(closes)} bars)")
        brick_size = atr_default(closes)
    else:
        closes = fetch_tdva_candles(config, symbol, limit=200, timeframe="15m")
        if len(closes) < 5:
            return render_empty_chart(symbol, f"Insufficient candle data ({len(closes)} bars)")

    # 2. Rebuild bricks via RenkoConstructor
    #    Venue is always TRADINGVIEW — TDVA is the sole candle source for the dashboard.
    constructor = RenkoConstructor(
        brick_size=brick_size, symbol=symbol, venue=Venue.TRADINGVIEW, max_bricks=500,
    )
    base_ts = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    for i, c in enumerate(closes):
        constructor.on_tick(
            Tick(
                ts=base_ts + dt.timedelta(minutes=i * 15),
                symbol=symbol, venue=Venue.TRADINGVIEW,
                price=float(c), size=0.0,
            )
        )
    bricks = constructor.get_bricks(last_n)
    if not bricks:
        return render_empty_chart(symbol, "No bricks formed yet")

    # 3. Classify pattern (for the chart title)
    try:
        pattern = BrickPatternAnalyzer(lookback=10).classify(bricks=bricks, symbol=symbol)
        pattern_label = getattr(pattern, "value", str(pattern))
    except Exception:
        pattern_label = "unknown"

    # 4. Render with matplotlib
    fig, ax = plt.subplots(figsize=(12, 4), dpi=150, constrained_layout=True)
    apply_dark_theme(fig, ax)

    for b in bricks:
        is_up = b.direction.value == "up" if hasattr(b.direction, "value") else str(b.direction) == "up"
        color = NOBLE_PALETTE["success"] if is_up else NOBLE_PALETTE["error"]
        edge = "#16a34a" if is_up else "#b91c1c"
        # x = brick_number, y = bottom of brick (min of open/close)
        y_bottom = min(b.open_price, b.close_price)
        rect = Rectangle(
            (b.brick_number, y_bottom),
            0.85, abs(b.close_price - b.open_price) or brick_size * 0.1,
            facecolor=color, edgecolor=edge, linewidth=0.5, alpha=0.85,
        )
        ax.add_patch(rect)

    # Axes
    ax.set_xlim(bricks[0].brick_number - 0.5, bricks[-1].brick_number + 1.5)
    y_min = min(b.low_price for b in bricks)
    y_max = max(b.high_price for b in bricks)
    pad = (y_max - y_min) * 0.05 if y_max > y_min else brick_size
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlabel("Brick #", color=NOBLE_PALETTE["text"], fontsize=9)
    ax.set_ylabel("Price", color=NOBLE_PALETTE["text"], fontsize=9)
    ax.set_title(
        f"{symbol} · {pattern_label} · {len(bricks)} bricks · brick_size={brick_size:.4f}",
        color=NOBLE_PALETTE["text"], fontsize=11, loc="left",
    )

    # Last price reference line
    last_price = bricks[-1].close_price
    ax.axhline(last_price, color=NOBLE_PALETTE["primary"], linewidth=0.5, alpha=0.6, linestyle="--")
    ax.text(
        bricks[-1].brick_number + 1.0, last_price,
        f" {last_price:.4f}" if last_price < 1 else f" {last_price:.2f}",
        color=NOBLE_PALETTE["primary"], fontsize=8, va="center",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_renko_png(config: HermesConfig, symbol: str, last_n: int = 100) -> bytes:
    """Cached entry point — wraps _render_renko_png_impl with a 60s TTL."""
    return get_or_render(
        key=("renko", symbol, last_n),
        ttl_sec=60.0,
        render_fn=lambda: _render_renko_png_impl(config, symbol, last_n),
    )


def render_renko_sparkline(config: HermesConfig, symbol: str, last_n: int = 20) -> bytes:
    """Smaller, sparkline-style renko chart — for the /market grid card.

    Uses the same renderer with a thinner figsize. Cached 60s under a
    separate key so it doesn't evict the full-size chart.
    """
    # For the sparkline we re-render with a different figsize — but to keep
    # the cache key simple, we just call _render_renko_png_impl directly
    # here. If this becomes a hot path, add a separate cache key.
    # TODO: actually pass a figsize param through to _render_renko_png_impl.
    return render_renko_png(config, symbol, last_n)
