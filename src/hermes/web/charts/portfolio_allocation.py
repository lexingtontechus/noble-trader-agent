"""
Portfolio allocation donut chart — /api/charts/allocation.png

Server-rendered matplotlib PNG that replaces the deprecated dashboard's
AllocationPie.tsx (archived at .archive/dashboard-2026-07-16/src/components/
charts/AllocationPie.tsx). Renders a donut chart of current portfolio
exposure broken down by venue (and a secondary breakdown by direction).

Data source: `get_portfolio_exposure_breakdown()` in web.status —
returns a dict with `by_venue` (dict[venue→pnl_usd]) and `totals` (long/
short/gross/net exposure). When `by_venue` is empty (no closed trades in
the last 30d), we fall back to the latest snapshot's long/short exposure
numbers so the donut always renders something useful.

Caching: 60s in-process TTL (consistent with all other chart endpoints).
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

# DaisyUI semantic color palette — mirrors AllocationPie.tsx palette order.
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
    """Thin wrapper around web.status.get_portfolio_exposure_breakdown."""
    try:
        from hermes.web.status import get_portfolio_exposure_breakdown
        return get_portfolio_exposure_breakdown(config)
    except Exception as e:
        log.warning("fetch_allocation_breakdown_failed", error=str(e)[:120])
        return {"by_venue": {}, "by_asset_class": {}, "totals": {}}


def _build_slices(breakdown: dict[str, Any]) -> list[tuple[str, float]]:
    """Convert the breakdown dict into a list of (label, value) slices.

    Strategy:
      1. If `by_venue` is populated → use it (shows which venues hold the
         most PnL contribution — useful for live trading).
      2. Else if `totals.long_exposure_usd` or `totals.short_exposure_usd`
         is non-zero → use long/short as a 2-slice donut (current exposure
         shape, not historical PnL).
      3. Else → empty list (caller will render an empty-state PNG).
    """
    by_venue = breakdown.get("by_venue") or {}
    if by_venue:
        return [(str(k), float(v or 0)) for k, v in by_venue.items() if v is not None]

    totals = breakdown.get("totals") or {}
    long_e = float(totals.get("long_exposure_usd") or 0)
    short_e = float(totals.get("short_exposure_usd") or 0)
    slices: list[tuple[str, float]] = []
    if long_e > 0:
        slices.append(("Long exposure", long_e))
    if short_e > 0:
        slices.append(("Short exposure", short_e))
    return slices


def _render_allocation_png_impl(config: HermesConfig) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    breakdown = _fetch_breakdown(config)
    slices = _build_slices(breakdown)

    if not slices:
        return render_empty_chart(
            "Portfolio Allocation",
            "No exposure yet — run: platform risk --equity 100000",
            figsize=(8, 6),
        )

    # Filter out zero/negative slices (they don't render meaningfully on a pie)
    slices = [(lbl, val) for lbl, val in slices if val > 0]
    if not slices:
        return render_empty_chart(
            "Portfolio Allocation",
            "All exposure values are zero",
            figsize=(8, 6),
        )

    labels = [s[0] for s in slices]
    values = [s[1] for s in slices]
    total = sum(values)
    colors = [_PALETTE[i % len(_PALETTE)] for i in range(len(slices))]

    fig, ax = plt.subplots(figsize=(8, 6), dpi=150, constrained_layout=True)
    fig.patch.set_facecolor(NOBLE_PALETTE["base_100"])
    ax.set_facecolor(NOBLE_PALETTE["base_100"])

    # Donut: inner radius = 60, outer = 100 (mirrors AllocationPie.tsx)
    wedges, _texts = ax.pie(
        values,
        colors=colors,
        startangle=90,
        wedgeprops=dict(width=0.4, edgecolor=NOBLE_PALETTE["base_100"], linewidth=1.5),
        counterclock=False,
    )

    # Center text: total + label
    ax.text(
        0, 0.1, f"${total:,.0f}",
        ha="center", va="center",
        fontsize=16, fontweight="bold", color=NOBLE_PALETTE["text"],
    )
    ax.text(
        0, -0.15, "Total Exposure",
        ha="center", va="center",
        fontsize=9, color=NOBLE_PALETTE["text_dim"],
    )

    # Legend with values + percentages
    legend_labels = [
        f"{lbl}  ${val:,.0f}  ({val / total * 100:.1f}%)"
        for lbl, val in zip(labels, values)
    ]
    ax.legend(
        wedges,
        legend_labels,
        loc="center left",
        bbox_to_anchor=(1.0, 0.5),
        fontsize=9,
        facecolor=NOBLE_PALETTE["base_200"],
        edgecolor=NOBLE_PALETTE["base_300"],
        labelcolor=NOBLE_PALETTE["text"],
        frameon=True,
    )

    ax.set_title(
        "Portfolio Allocation",
        color=NOBLE_PALETTE["text"], fontsize=12, loc="left", pad=12,
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_allocation_png(config: HermesConfig) -> bytes:
    """Cached entry point — 60s TTL."""
    return get_or_render(
        key=("allocation",),
        ttl_sec=60.0,
        render_fn=lambda: _render_allocation_png_impl(config),
    )
