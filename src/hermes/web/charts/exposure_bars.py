"""
Portfolio exposure horizontal bar chart — /api/charts/exposure_bars.png

Server-rendered matplotlib PNG that replaces the deprecated dashboard's
ExposureBars.tsx (archived at .archive/dashboard-2026-07-16/src/components/
charts/ExposureBars.tsx). Renders a horizontal bar chart of the current
exposure broken down by venue (or by direction when venue data is
unavailable).

Data source: `get_portfolio_exposure_breakdown()` in web.status — same
source as portfolio_allocation.py. The two charts are complementary:
- AllocationPie shows proportional share (donut + center total)
- ExposureBars shows absolute magnitude (sorted horizontal bars)

When `by_venue` is empty, falls back to long_exposure / short_exposure /
net_exposure / gross_exposure from the latest snapshot — gives a useful
"exposure shape" view even before any trades have closed.

Caching: 60s in-process TTL.
"""

from __future__ import annotations

import io
from typing import Any

import structlog
from matplotlib import pyplot as plt

from hermes.core.config import HermesConfig
from hermes.web.charts._cache import get_or_render
from hermes.web.charts._theme import NOBLE_PALETTE, render_empty_chart

log = structlog.get_logger(__name__)

# Same palette order as portfolio_allocation.py for visual consistency
_PALETTE = [
    NOBLE_PALETTE["primary"],
    NOBLE_PALETTE["accent"],
    NOBLE_PALETTE["info"],
    NOBLE_PALETTE["success"],
    NOBLE_PALETTE["warning"],
    NOBLE_PALETTE["error"],
    NOBLE_PALETTE["neutral"],
    NOBLE_PALETTE["text_dim"],
]


def _fetch_breakdown(config: HermesConfig) -> dict[str, Any]:
    try:
        from hermes.web.status import get_portfolio_exposure_breakdown
        return get_portfolio_exposure_breakdown(config)
    except Exception as e:
        log.warning("fetch_exposure_breakdown_failed", error=str(e)[:120])
        return {"by_venue": {}, "by_asset_class": {}, "totals": {}}


def _build_bars(breakdown: dict[str, Any]) -> list[tuple[str, float]]:
    """Build (label, value) bar tuples sorted by absolute value descending."""
    by_venue = breakdown.get("by_venue") or {}
    if by_venue:
        bars = [(str(k), float(v or 0)) for k, v in by_venue.items() if v is not None]
        bars.sort(key=lambda x: abs(x[1]), reverse=True)
        return bars

    # Fallback: long/short/net/gross from latest snapshot
    totals = breakdown.get("totals") or {}
    candidate = [
        ("Long",  float(totals.get("long_exposure_usd")  or 0)),
        ("Short", float(totals.get("short_exposure_usd") or 0)),
        ("Net",   float(totals.get("net_exposure_usd")   or 0)),
        ("Gross", float(totals.get("gross_exposure_usd") or 0)),
    ]
    return [(lbl, val) for lbl, val in candidate if val != 0]


def _render_exposure_bars_png_impl(config: HermesConfig) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    breakdown = _fetch_breakdown(config)
    bars = _build_bars(breakdown)

    if not bars:
        return render_empty_chart(
            "Exposure Breakdown",
            "No exposure yet — run: platform risk --equity 100000",
            figsize=(10, 4),
        )

    labels = [b[0] for b in bars]
    values = [b[1] for b in bars]
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(bars))]

    # Negative values get error-tinted to make the direction obvious
    final_colors = []
    for i, v in enumerate(values):
        if v < 0:
            final_colors.append(NOBLE_PALETTE["error"])
        else:
            final_colors.append(colors[i])

    n_bars = len(bars)
    height_in = max(2.5, 0.45 * n_bars + 1.5)  # adaptive height
    fig, ax = plt.subplots(
        figsize=(10, height_in), dpi=150, constrained_layout=True,
    )
    fig.patch.set_facecolor(NOBLE_PALETTE["base_100"])
    ax.set_facecolor(NOBLE_PALETTE["base_200"])

    y_pos = list(range(n_bars))
    ax.barh(y_pos, values, color=final_colors, edgecolor=NOBLE_PALETTE["base_100"], linewidth=0.5)
    ax.set_yticks(y_pos)
    ax.set_yticklabels(labels, color=NOBLE_PALETTE["text"], fontsize=10)
    ax.invert_yaxis()  # largest at top

    # Value labels at end of each bar
    x_max = max(values) if values else 0
    x_min = min(values) if values else 0
    x_pad = (x_max - x_min) * 0.02 if x_max != x_min else abs(x_max) * 0.05 + 1
    for i, v in enumerate(values):
        ha = "left" if v >= 0 else "right"
        offset = x_pad if v >= 0 else -x_pad
        ax.text(
            v + offset, i, f"${v:,.0f}",
            va="center", ha=ha,
            color=NOBLE_PALETTE["text"], fontsize=9, fontweight="bold",
        )

    # X-axis: USD formatted
    ax.tick_params(axis="x", colors=NOBLE_PALETTE["text_dim"], labelsize=8)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.axvline(0, color=NOBLE_PALETTE["base_300"], linewidth=0.5)
    ax.set_xlabel("USD", color=NOBLE_PALETTE["text_dim"], fontsize=9)
    ax.set_title(
        "Exposure Breakdown",
        color=NOBLE_PALETTE["text"], fontsize=11, loc="left", pad=10,
    )

    # Style spines + grid
    for spine in ax.spines.values():
        spine.set_color(NOBLE_PALETTE["base_300"])
        spine.set_linewidth(0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="x", color=NOBLE_PALETTE["base_300"], linewidth=0.4, alpha=0.4)

    # Add headroom on x-axis for value labels
    ax.set_xlim(x_min - x_pad * 4 if x_min < 0 else -x_pad, x_max + x_pad * 4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_exposure_bars_png(config: HermesConfig) -> bytes:
    """Cached entry point — 60s TTL."""
    return get_or_render(
        key=("exposure_bars",),
        ttl_sec=60.0,
        render_fn=lambda: _render_exposure_bars_png_impl(config),
    )
