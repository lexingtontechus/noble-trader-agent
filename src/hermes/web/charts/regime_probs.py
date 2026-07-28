"""
7-state regime probability bars — /api/charts/regime_probs/{symbol}.png

Renders the meta-regime posterior probabilities as a horizontal bar chart.
The 7 states are: calm_trend, choppy_range, high_vol_breakout,
regime_transition, risk_off, funding_stress, liquidity_drained.

Data source: MetaRegimeClassifier.classify() called live on the latest
heartbeat. (The meta_regime_history table is declared but never written to
per §2.4 of the scoping doc — running the classifier live is the right path
because it's sub-millisecond.)
"""

from __future__ import annotations

import io
from typing import Any

import structlog
from matplotlib import pyplot as plt
from matplotlib.patches import Rectangle

from hermes.core.config import HermesConfig
from hermes.web.charts._cache import get_or_render
from hermes.web.charts._data import get_latest_heartbeat
from hermes.web.charts._theme import META_REGIME_COLORS, NOBLE_PALETTE, apply_dark_theme, render_empty_chart

log = structlog.get_logger(__name__)

# Canonical 7-state ordering — stable across renders for visual consistency.
REGIME_STATES = [
    "calm_trend",
    "choppy_range",
    "high_vol_breakout",
    "regime_transition",
    "risk_off",
    "funding_stress",
    "liquidity_drained",
]


def _render_regime_probs_png_impl(config: HermesConfig, symbol: str) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    hb = get_latest_heartbeat(config, symbol)
    if hb is None:
        return render_empty_chart(symbol, "No heartbeat yet", figsize=(8, 5))

    # Run the MetaRegimeClassifier live on the latest heartbeat.
    posterior = _get_posterior_probs(config, symbol, hb)
    if not posterior:
        return render_empty_chart(symbol, "Meta-regime classifier unavailable", figsize=(8, 5))

    # Build the (state, prob, color) list in canonical order.
    rows = []
    for state in REGIME_STATES:
        prob = float(posterior.get(state, 0.0))
        color = META_REGIME_COLORS.get(state, NOBLE_PALETTE["neutral"])
        rows.append((state, prob, color))
    # Sort descending by probability for the chart (visual hierarchy)
    rows.sort(key=lambda r: r[1], reverse=True)

    fig, ax = plt.subplots(figsize=(8, 5), dpi=150, constrained_layout=True)
    apply_dark_theme(fig, ax)

    y_positions = list(range(len(rows)))
    labels = [r[0] for r in rows]
    probs = [r[1] for r in rows]
    colors = [r[2] for r in rows]

    bars = ax.barh(y_positions, probs, color=colors, edgecolor=NOBLE_PALETTE["base_300"], linewidth=0.5, alpha=0.9)
    ax.set_yticks(y_positions)
    ax.set_yticklabels(labels, color=NOBLE_PALETTE["text"], fontsize=9, fontfamily="monospace")
    ax.invert_yaxis()  # highest prob at top
    ax.set_xlim(0, 1.0)
    ax.set_xlabel("Posterior Probability", color=NOBLE_PALETTE["text"], fontsize=9)

    # Probability labels at end of each bar
    for bar, prob in zip(bars, probs):
        width = bar.get_width()
        ax.text(
            width + 0.02, bar.get_y() + bar.get_height() / 2,
            f"{prob*100:.1f}%",
            color=NOBLE_PALETTE["text"], fontsize=8, va="center",
        )

    # Title with current regime callout
    top_state = rows[0][0]
    top_prob = rows[0][1]
    ax.set_title(
        f"{symbol} · current: {top_state} ({top_prob*100:.1f}%)",
        color=NOBLE_PALETTE["text"], fontsize=11, loc="left",
    )

    ax.grid(True, axis="x", color=NOBLE_PALETTE["base_300"], linewidth=0.5, alpha=0.5)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def _get_posterior_probs(
    config: HermesConfig, symbol: str, heartbeat: dict[str, Any],
) -> dict[str, float] | None:
    """Run MetaRegimeClassifier on the latest heartbeat, return posterior dict."""
    try:
        from hermes.signals.meta_regime import MetaRegimeClassifier

        classifier = MetaRegimeClassifier()

        # Minimal attribute shim — MetaRegimeClassifier reads heartbeat fields
        # via attribute access, so we wrap the dict in a tiny shim object.
        class _HbShim:
            pass

        shim = _HbShim()
        for k, v in heartbeat.items():
            setattr(shim, k, v)
        shim.symbol = symbol

        mr = classifier.classify(heartbeat=shim, symbol=symbol)

        # posterior_probs may be a dict or a list — handle both.
        pp = getattr(mr, "posterior_probs", None)
        if pp is None:
            return None
        if isinstance(pp, dict):
            return {k: float(v) for k, v in pp.items()}
        if isinstance(pp, (list, tuple)):
            # Assume ordered list matching REGIME_STATES
            return {REGIME_STATES[i]: float(p) for i, p in enumerate(pp) if i < len(REGIME_STATES)}
        return None
    except Exception as e:
        log.debug("regime_probs_classify_failed", symbol=symbol, error=str(e)[:120])
        return None


def render_regime_probs_png(config: HermesConfig, symbol: str) -> bytes:
    """Cached entry point — 60s TTL."""
    return get_or_render(
        key=("regime_probs", symbol),
        ttl_sec=60.0,
        render_fn=lambda: _render_regime_probs_png_impl(config, symbol),
    )
