"""
Price area chart with regime-colored fill — /api/charts/price_regime/{symbol}.png

Renders the last N TDVA closes as a step line, with the area underneath
color-tinted by the upstream regime label at each timestamp. Mirrors the
old Next.js PriceChart.jsx behavior.

Caching: 60s in-process TTL, keyed by (symbol, horizon).
"""

from __future__ import annotations

import datetime as dt
import io
from typing import Any

import structlog
from matplotlib import pyplot as plt
from matplotlib.colors import to_rgba

from hermes.core.config import HermesConfig
from hermes.web.charts._cache import get_or_render
from hermes.web.charts._data import fetch_tdva_candles, get_latest_heartbeat
from hermes.web.charts._theme import NOBLE_PALETTE, apply_dark_theme, regime_color, render_empty_chart

log = structlog.get_logger(__name__)


def _render_price_regime_png_impl(
    config: HermesConfig, symbol: str, horizon: int = 200,
) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    closes, ts_list = fetch_tdva_candles(
        config, symbol, limit=horizon, timeframe="15m", with_timestamps=True,
    )
    if len(closes) < 5:
        return render_empty_chart(symbol, f"Insufficient candle data ({len(closes)} bars)")

    # Fetch the latest heartbeat's regime to use as the dominant regime tint
    # (until per-timestamp regime history is available — meta_regime_history
    # is empty per §2.4 of the scoping doc).
    latest_hb = get_latest_heartbeat(config, symbol)
    regime_label = (latest_hb or {}).get("regime") or "unknown"
    stroke, fill_rgba = regime_color(regime_label)

    # Parse the fill_rgba string ("rgba(r,g,b,a)") into a matplotlib-compatible tuple
    rgba = _parse_rgba(fill_rgba)

    fig, ax = plt.subplots(figsize=(12, 5), dpi=150, constrained_layout=True)
    apply_dark_theme(fig, ax)

    x = list(range(len(closes)))
    ax.fill_between(x, closes, min(closes), color=rgba, step="post", alpha=1.0)
    ax.plot(x, closes, color=stroke, linewidth=1.5, drawstyle="steps-post")

    # Last price reference line
    last_price = closes[-1]
    ax.axhline(last_price, color=NOBLE_PALETTE["primary"], linewidth=0.5, alpha=0.6, linestyle="--")
    ax.text(
        len(closes) - 1, last_price,
        f" {last_price:.4f}" if last_price < 1 else f" {last_price:.2f}",
        color=NOBLE_PALETTE["primary"], fontsize=8, va="center",
    )

    # Axes labels
    ax.set_xlim(0, len(closes) - 1)
    y_min, y_max = min(closes), max(closes)
    pad = (y_max - y_min) * 0.05 if y_max > y_min else last_price * 0.01
    ax.set_ylim(y_min - pad, y_max + pad)
    ax.set_xlabel("Candle # (last 200 TDVA bars, 15m)", color=NOBLE_PALETTE["text"], fontsize=9)
    ax.set_ylabel("Price", color=NOBLE_PALETTE["text"], fontsize=9)
    ax.set_title(
        f"{symbol} · regime={regime_label} · last={last_price:.4f}" if last_price < 1
        else f"{symbol} · regime={regime_label} · last={last_price:.2f}",
        color=NOBLE_PALETTE["text"], fontsize=11, loc="left",
    )

    # Regime color legend (top-right)
    ax.text(
        0.99, 0.97, f"█ {regime_label}",
        transform=ax.transAxes, ha="right", va="top",
        color=stroke, fontsize=9, fontweight="bold",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _parse_rgba(s: str) -> tuple[float, float, float, float]:
    """Convert an 'rgba(r,g,b,a)' string to a matplotlib (r,g,b,a) float tuple."""
    inside = s[s.index("(") + 1 : s.rindex(")")]
    parts = [p.strip() for p in inside.split(",")]
    r, g, b = int(parts[0]) / 255, int(parts[1]) / 255, int(parts[2]) / 255
    a = float(parts[3]) if len(parts) > 3 else 1.0
    return (r, g, b, a)


def render_price_regime_png(
    config: HermesConfig, symbol: str, horizon: int = 200,
) -> bytes:
    """Cached entry point — wraps _render with 60s TTL."""
    return get_or_render(
        key=("price_regime", symbol, horizon),
        ttl_sec=60.0,
        render_fn=lambda: _render_price_regime_png_impl(config, symbol, horizon),
    )
