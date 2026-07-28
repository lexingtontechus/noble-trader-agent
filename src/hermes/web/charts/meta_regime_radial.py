"""
Meta-regime radial gauge — /api/charts/meta_regime_radial/{symbol}.png

Renders the 7 meta-regime states as wedges on a polar plot. The dominant
state's wedge is highlighted; the others are dimmed. Shows the current
posterior distribution as a single intuitive dial.

Visual style: inspired by the old Next.js RegimeCard.jsx radial gauge,
but rendered server-side as a PNG.
"""

from __future__ import annotations

import io
import math
from typing import Any

import numpy as np
import structlog
from matplotlib import pyplot as plt

from hermes.core.config import HermesConfig
from hermes.web.charts._cache import get_or_render
from hermes.web.charts._data import get_latest_heartbeat
from hermes.web.charts._theme import META_REGIME_COLORS, NOBLE_PALETTE, apply_dark_theme, render_empty_chart
from hermes.web.charts.regime_probs import REGIME_STATES, _get_posterior_probs

log = structlog.get_logger(__name__)


def _render_meta_regime_radial_png_impl(config: HermesConfig, symbol: str) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    hb = get_latest_heartbeat(config, symbol)
    if hb is None:
        return render_empty_chart(symbol, "No heartbeat yet", figsize=(8, 8))

    posterior = _get_posterior_probs(config, symbol, hb)
    if not posterior:
        return render_empty_chart(symbol, "Meta-regime classifier unavailable", figsize=(8, 8))

    # Build (state, prob, color) in canonical order
    states_probs = []
    for state in REGIME_STATES:
        prob = float(posterior.get(state, 0.0))
        color = META_REGIME_COLORS.get(state, NOBLE_PALETTE["neutral"])
        states_probs.append((state, prob, color))

    # Identify dominant state
    dom_idx = max(range(len(states_probs)), key=lambda i: states_probs[i][1])
    dom_state = states_probs[dom_idx][0]
    dom_prob = states_probs[dom_idx][1]

    # Polar bar plot
    fig = plt.figure(figsize=(8, 8), dpi=150, constrained_layout=True)
    # Dark figure background
    fig.patch.set_facecolor(NOBLE_PALETTE["base_100"])
    ax = fig.add_subplot(111, projection="polar")
    ax.set_facecolor(NOBLE_PALETTE["base_200"])

    n = len(states_probs)
    # Each wedge is centered on its angle; 2π/n width per wedge.
    theta = [i * 2 * math.pi / n for i in range(n)]
    width = [2 * math.pi / n - 0.05] * n  # small gap between wedges
    radii = [p for (_, p, _) in states_probs]
    colors = []
    for i, (_, _, c) in enumerate(states_probs):
        if i == dom_idx:
            colors.append(c)
        else:
            # Dim non-dominant wedges — keep hue, drop saturation/lightness via alpha
            colors.append(c)

    bars = ax.bar(theta, radii, width=width, bottom=0.0, color=colors, alpha=0.85, edgecolor=NOBLE_PALETTE["base_300"], linewidth=0.5)

    # Highlight dominant wedge with a thicker edge
    bars[dom_idx].set_edgecolor(NOBLE_PALETTE["primary"])
    bars[dom_idx].set_linewidth(2.0)

    # State labels around the dial
    ax.set_xticks(theta)
    ax.set_xticklabels(
        [s for (s, _, _) in states_probs],
        color=NOBLE_PALETTE["text"], fontsize=9, fontfamily="monospace",
    )
    ax.set_yticks([0.25, 0.5, 0.75, 1.0])
    ax.set_yticklabels(["25%", "50%", "75%", "100%"], color=NOBLE_PALETTE["text_dim"], fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.tick_params(colors=NOBLE_PALETTE["text"])
    ax.grid(True, color=NOBLE_PALETTE["base_300"], linewidth=0.5, alpha=0.5)

    # Center label: dominant state + probability
    ax.text(
        0, 0, f"{dom_state}\n{dom_prob*100:.1f}%",
        ha="center", va="center",
        color=NOBLE_PALETTE["primary"], fontsize=11, fontweight="bold",
        transform=ax.transData,
    )

    # Title
    fig.suptitle(
        f"{symbol} · meta-regime radial",
        color=NOBLE_PALETTE["text"], fontsize=12, y=0.98,
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_meta_regime_radial_png(config: HermesConfig, symbol: str) -> bytes:
    """Cached entry point — 60s TTL."""
    return get_or_render(
        key=("meta_regime_radial", symbol),
        ttl_sec=60.0,
        render_fn=lambda: _render_meta_regime_radial_png_impl(config, symbol),
    )
