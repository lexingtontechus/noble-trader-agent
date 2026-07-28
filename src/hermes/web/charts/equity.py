"""
Portfolio equity curve — /api/charts/equity.png

Replaces the uPlot equityCurve chart on /portfolio. Reads from the local
DuckDB `portfolio_snapshots` table (same source as web.status.get_equity_curve).
Renders the equity curve + drawdown shading as a matplotlib PNG.

Caching: 60s in-process TTL.
"""

from __future__ import annotations

import io
from typing import Any

import structlog
from matplotlib import pyplot as plt

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.web.charts._cache import get_or_render
from hermes.web.charts._theme import NOBLE_PALETTE, apply_dark_theme, render_empty_chart

log = structlog.get_logger(__name__)


def _fetch_equity_curve(config: HermesConfig, limit: int = 500) -> list[dict[str, Any]]:
    """Pull the last N portfolio snapshots from DuckDB.

    Mirrors web.status.get_equity_curve() but returns the raw rows instead
    of pre-formatting — the chart renderer does its own formatting.
    """
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            df = conn.execute(
                """
                SELECT ts, equity_total, realized_pnl, unrealized_pnl,
                       drawdown_pct, drawdown_usd, peak_equity
                FROM portfolio_snapshots
                ORDER BY ts DESC
                LIMIT ?
                """,
                [int(limit)],
            ).fetchdf()
            if df.empty:
                return []
            return list(reversed(df.to_dict("records")))
    except Exception as e:
        log.warning("fetch_equity_curve_failed", error=str(e)[:120])
        return []


def _render_equity_png_impl(config: HermesConfig, limit: int = 500) -> bytes:
    """Actual render — called by the cache layer on cache miss."""
    rows = _fetch_equity_curve(config, limit=limit)
    if not rows:
        return render_empty_chart("Portfolio", "No equity snapshots yet", figsize=(12, 5))

    ts_labels = [r["ts"] for r in rows]
    equity = [float(r.get("equity_total") or 0) for r in rows]
    peak = [float(r.get("peak_equity") or 0) for r in rows]
    dd_pct = [float(r.get("drawdown_pct") or 0) * 100 for r in rows]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 6), dpi=150, constrained_layout=True,
        gridspec_kw={"height_ratios": [3, 1]}, sharex=True,
    )
    fig.patch.set_facecolor(NOBLE_PALETTE["base_100"])

    # Top panel: equity curve + peak line
    apply_dark_theme(fig, ax1)
    x = list(range(len(equity)))
    ax1.plot(x, equity, color=NOBLE_PALETTE["success"], linewidth=1.5, label="Equity")
    ax1.plot(x, peak, color=NOBLE_PALETTE["primary"], linewidth=0.8, alpha=0.6, linestyle="--", label="Peak")
    ax1.fill_between(x, equity, min(equity), color=NOBLE_PALETTE["success"], alpha=0.10)
    ax1.set_ylabel("Equity (USD)", color=NOBLE_PALETTE["text"], fontsize=9)
    ax1.legend(loc="upper left", fontsize=8, facecolor=NOBLE_PALETTE["base_200"], edgecolor=NOBLE_PALETTE["base_300"], labelcolor=NOBLE_PALETTE["text"])
    last_eq = equity[-1]
    ax1.set_title(
        f"Portfolio Equity · last=${last_eq:,.2f}",
        color=NOBLE_PALETTE["text"], fontsize=11, loc="left",
    )
    ax1.text(
        len(equity) - 1, last_eq, f" ${last_eq:,.0f}",
        color=NOBLE_PALETTE["success"], fontsize=9, va="center",
    )

    # Bottom panel: drawdown %
    apply_dark_theme(fig, ax2)
    ax2.fill_between(x, dd_pct, 0, color=NOBLE_PALETTE["error"], alpha=0.4)
    ax2.plot(x, dd_pct, color=NOBLE_PALETTE["error"], linewidth=1.0)
    ax2.set_ylim(min(dd_pct + [0]) - 1, 1)
    ax2.set_ylabel("Drawdown %", color=NOBLE_PALETTE["text"], fontsize=9)
    ax2.set_xlabel(f"Last {len(rows)} snapshots", color=NOBLE_PALETTE["text"], fontsize=9)
    last_dd = dd_pct[-1]
    ax2.set_title(
        f"Drawdown · {last_dd:.2f}%",
        color=NOBLE_PALETTE["error"] if last_dd < -5 else NOBLE_PALETTE["warning"],
        fontsize=10, loc="left",
    )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()


def render_equity_png(config: HermesConfig, limit: int = 500) -> bytes:
    """Cached entry point — 60s TTL."""
    return get_or_render(
        key=("equity", limit),
        ttl_sec=60.0,
        render_fn=lambda: _render_equity_png_impl(config, limit=limit),
    )
