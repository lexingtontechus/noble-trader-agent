"""
Local-agent-rendered matplotlib chart package for the Talaria dashboard.

All chart PNGs served from /api/charts/*.png are rendered here by the local
hermes agent FastAPI process (NOT a remote cloud server). The matplotlib Agg
backend is forced at import time so the same code works identically on macOS,
Windows, and Linux — no display, no Cocoa/Tk/Qt dependency.

Modules:
  _theme.py     — NOBLE_PALETTE, regime_color(), apply_dark_theme()
  _cache.py     — in-memory TTL cache (no Redis dependency)
  _data.py      — fetch_tdva_candles() + DuckDB heartbeat/brick-size lookups
  renko.py      — render_renko_png()  →  /api/charts/renko/{symbol}.png
  price_regime.py    — render_price_regime_png()
  regime_probs.py    — render_regime_probs_png()
  meta_regime_radial.py — render_meta_regime_radial_png()
  equity.py     — render_equity_png()

See DASHBOARD-UPGRADE-SCOPING.md §3.3 + §5 for design rationale.
"""

# Force Agg backend on import — must happen before any pyplot import anywhere
# in the charts package. See _theme.py for the full cross-platform rationale.
from hermes.web.charts._theme import NOBLE_PALETTE, apply_dark_theme, regime_color  # noqa: F401
