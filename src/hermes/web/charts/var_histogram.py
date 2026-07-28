"""
VaR distribution histogram — /api/charts/var_histogram.png

Server-rendered matplotlib PNG that replaces the deprecated dashboard's
VarDistHistogram.tsx (archived at .archive/dashboard-2026-07-16/src/components/
charts/VarDistHistogram.tsx). Renders a histogram of historical VaR 1d 99%
values, color-coded by sign (positive = risk-on confidence, negative =
tail-loss scenarios).

Data source: `get_portfolio_var_history()` in web.status — pulls the last
N rows from `account_snapshots` where `var_1d_99 IS NOT NULL`. Returns
ts, var_1d_99, cvar_1d_99, drawdown_pct, leverage_gross, etc.

Caching: 60s in-process TTL.

Note: the deprecated component showed a histogram of VaR values. Here we
do the same — each bar = the count of snapshots whose VaR fell in that
bin. Bins < 0 are tinted error (loss-side), bins ≥ 0 are tinted success
(gain-side). This makes it visually obvious whether the portfolio has
been spending more time in tail-risk territory or in safe territory.
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


def _fetch_var_history(config: HermesConfig, limit: int = 500) -> list[dict[str, Any]]:
    try:
        from hermes.web.status import get_portfolio_var_history
        return get_portfolio_var_history(config, limit=limit)
    except Exception as e:
        log.warning("fetch_var_history_failed", error=str(e)[:120])
        return []


def _render_var_histogram_png_impl(config: HermesConfig, limit: int = 500) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    rows = _fetch_var_history(config, limit=limit)
    var_values = [float(r.get("var_1d_99") or 0) for r in rows if r.get("var_1d_99") is not None]

    if not var_values:
        return render_empty_chart(
            "VaR Distribution",
            "No VaR history yet — accumulate account snapshots",
            figsize=(10, 5),
        )

    n_bins = 20
    fig, ax = plt.subplots(figsize=(10, 5), dpi=150, constrained_layout=True)
    fig.patch.set_facecolor(NOBLE_PALETTE["base_100"])
    ax.set_facecolor(NOBLE_PALETTE["base_200"])

    # Compute histogram manually so we can color each bar by sign
    v_min, v_max = min(var_values), max(var_values)
    if v_min == v_max:
        # All values identical — add tiny epsilon to avoid zero-width bins
        v_min -= abs(v_min) * 0.01 + 1
        v_max += abs(v_max) * 0.01 + 1
    width = (v_max - v_min) / n_bins
    bin_edges = [v_min + i * width for i in range(n_bins + 1)]
    counts = [0] * n_bins
    for v in var_values:
        idx = min(int((v - v_min) / width), n_bins - 1)
        if idx >= 0:
            counts[idx] += 1

    # Color: bins whose midpoint is negative → error (loss-side),
    # else success (gain-side)
    bar_colors = []
    for i in range(n_bins):
        mid = (bin_edges[i] + bin_edges[i + 1]) / 2
        bar_colors.append(NOBLE_PALETTE["error"] if mid < 0 else NOBLE_PALETTE["success"])

    ax.bar(
        range(n_bins), counts,
        color=bar_colors,
        edgecolor=NOBLE_PALETTE["base_100"],
        linewidth=0.5,
        width=0.95,
    )

    # X-axis: label every 4th bin to avoid crowding
    x_tick_positions = list(range(0, n_bins, 4))
    x_tick_labels = [f"${bin_edges[i]:,.0f}" for i in x_tick_positions]
    ax.set_xticks(x_tick_positions)
    ax.set_xticklabels(x_tick_labels, color=NOBLE_PALETTE["text_dim"], fontsize=8)
    ax.set_xlabel("VaR 1d 99% (USD)", color=NOBLE_PALETTE["text_dim"], fontsize=9)

    # Y-axis: integer counts
    ax.tick_params(axis="y", colors=NOBLE_PALETTE["text_dim"], labelsize=8)
    ax.set_ylabel("Snapshots", color=NOBLE_PALETTE["text_dim"], fontsize=9)
    max_count = max(counts) if counts else 1
    ax.set_ylim(0, max_count * 1.10 + 0.5)

    # Reference line at zero VaR
    if v_min < 0 < v_max:
        zero_bin = (0 - v_min) / width
        ax.axvline(zero_bin - 0.5, color=NOBLE_PALETTE["primary"], linewidth=0.8, linestyle="--", alpha=0.7)
        ax.text(
            zero_bin - 0.5, max_count * 1.05, " VaR=0",
            color=NOBLE_PALETTE["primary"], fontsize=8, va="top",
        )

    # Stats line: mean / median / current
    mean_var = sum(var_values) / len(var_values)
    sorted_vars = sorted(var_values)
    median_var = sorted_vars[len(sorted_vars) // 2]
    current_var = var_values[-1]  # most recent (rows are ASC by ts)
    stats_line = (
        f"n={len(var_values)}  "
        f"mean=${mean_var:,.0f}  "
        f"median=${median_var:,.0f}  "
        f"current=${current_var:,.0f}"
    )
    ax.set_title(
        "VaR 1d 99% Distribution",
        color=NOBLE_PALETTE["text"], fontsize=11, loc="left", pad=10,
    )
    ax.text(
        0.99, 0.97, stats_line,
        transform=ax.transAxes, ha="right", va="top",
        color=NOBLE_PALETTE["text_dim"], fontsize=8,
        bbox=dict(
            facecolor=NOBLE_PALETTE["base_300"],
            edgecolor="none",
            alpha=0.6,
            pad=4,
        ),
    )

    # Style spines + grid
    for spine in ax.spines.values():
        spine.set_color(NOBLE_PALETTE["base_300"])
        spine.set_linewidth(0.5)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, axis="y", color=NOBLE_PALETTE["base_300"], linewidth=0.4, alpha=0.4)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_var_histogram_png(config: HermesConfig, limit: int = 500) -> bytes:
    """Cached entry point — 60s TTL."""
    return get_or_render(
        key=("var_histogram", limit),
        ttl_sec=60.0,
        render_fn=lambda: _render_var_histogram_png_impl(config, limit=limit),
    )
