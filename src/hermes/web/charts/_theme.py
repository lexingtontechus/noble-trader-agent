"""
Dark-theme matplotlib helpers for the Talaria dashboard.

CRITICAL — backend selection:
    matplotlib's default backend depends on the OS:
      macOS  → 'macosx' (Cocoa)  — crashes/deadlocks in a FastAPI worker
      Windows → 'TkAgg'/'Qt5Agg' — requires optional deps, may deadlock
      Linux  → 'Agg' (auto)      — works

    We force 'Agg' at module import so the SAME code renders identical PNG
    bytes on all three OSes. No display, no GUI toolkit, just a software
    rasterizer that writes to a BytesIO.

Fonts:
    matplotlib's font discovery is OS-specific (macOS /System/Library/Fonts/,
    Windows C:\\Windows\\Fonts\\, Linux /usr/share/fonts/). To eliminate this
    variability we bundle two font files in-repo and register them explicitly
    via fm.fontManager.addfont(). This guarantees identical typography on
    all platforms without depending on system font installation.

    If the bundled fonts are missing (e.g. during early development before
    `npm run build:fonts` was run), we fall back to whatever the OS provides
    via the standard sans-serif stack — charts still render, just with
    slightly different metrics.

NOBLE_PALETTE mirrors the DaisyUI dark theme colors used in base.html so
chart colors match the surrounding UI. See DASHBOARD-UPGRADE-SCOPING.md §3.3.
"""

from __future__ import annotations

import os
from pathlib import Path

# --- Backend selection (MUST happen before pyplot import) ----------------- #
# Use setdefault so an explicit MPLBACKEND env var (e.g. set in CI) wins.
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib  # noqa: E402

matplotlib.use("Agg", force=True)  # belt + suspenders

import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402

# --- Font registration (bundled, OS-independent) --------------------------- #
_FONTS_DIR = Path(__file__).parent / "fonts"
_BUNDLED_FONTS = [
    _FONTS_DIR / "NotoSansSC-Regular.ttf",  # CJK + Latin
    _FONTS_DIR / "DejaVuSans.ttf",           # math symbols + Latin fallback
]
for _f in _BUNDLED_FONTS:
    if _f.exists():
        try:
            fm.fontManager.addfont(str(_f))
        except Exception:
            pass  # non-fatal — fall back to system fonts

# Set the font stack: bundled first, then OS fallbacks. matplotlib 3.9+
# supports per-glyph fallback, so missing glyphs in the primary font
# automatically fall back to the next font in the list.
plt.rcParams["font.sans-serif"] = [
    "Noto Sans SC",      # bundled — CJK + Latin
    "DejaVu Sans",        # bundled — math symbols, Latin fallback
    "Liberation Sans",   # Linux fallback
    "Arial",              # Windows / macOS fallback
    "Helvetica",         # macOS fallback
]
plt.rcParams["axes.unicode_minus"] = False


# --- Color palette (mirrors DaisyUI dark theme) ---------------------------- #
# Locked per DASHBOARD-UPGRADE-SCOPING.md §12.2 Q3 (user-approved).
NOBLE_PALETTE: dict[str, str] = {
    "base_100": "#1a1a2e",  # figure facecolor — locked per §12.2 Q3
    "base_200": "#232338",  # axes facecolor
    "base_300": "#2c2c44",  # grid lines, spine color
    "text":     "#f4f1e8",  # primary text / labels
    "text_dim": "#9ca3af",  # secondary text / ticks
    "primary":  "#e3b765",  # gold — confidence, primary actions, last-price line
    "accent":   "#37b9a3",  # teal — trend state, accent elements
    "info":     "#5a9bd8",  # blue — volatility state, HH/LL swings
    "success":  "#22c55e",  # green — UP, bull, positive PnL
    "warning":  "#f59e0b",  # amber — neutral regime, HH/LL swings
    "error":    "#ef4444",  # red — DOWN, bear, negative PnL
    "neutral":  "#313144",  # gray — unknown / ghost
}

# Regime stroke + fill pairs. Mirrors components/ui.html regime_color_class().
# Order matters — first substring match wins.
REGIME_COLOR_MAP: list[tuple[str, str, str]] = [
    # (substring, stroke, fill_rgba)
    ("bull",          NOBLE_PALETTE["success"], "rgba(34,197,94,0.18)"),
    ("calm_trend",    NOBLE_PALETTE["success"], "rgba(34,197,94,0.18)"),
    ("up",            NOBLE_PALETTE["success"], "rgba(34,197,94,0.18)"),
    ("bear",          NOBLE_PALETTE["error"],   "rgba(239,68,68,0.18)"),
    ("risk_off",      NOBLE_PALETTE["error"],   "rgba(239,68,68,0.18)"),
    ("funding_stress",NOBLE_PALETTE["error"],   "rgba(239,68,68,0.18)"),
    ("down",          NOBLE_PALETTE["error"],   "rgba(239,68,68,0.18)"),
    ("neutral",       NOBLE_PALETTE["warning"], "rgba(245,158,11,0.18)"),
    ("flat",          NOBLE_PALETTE["warning"], "rgba(245,158,11,0.18)"),
    ("choppy",        NOBLE_PALETTE["warning"], "rgba(245,158,11,0.18)"),
    ("transition",    NOBLE_PALETTE["warning"], "rgba(245,158,11,0.18)"),
    ("high_vol",      NOBLE_PALETTE["warning"], "rgba(245,158,11,0.18)"),
    ("liquidity_drained", NOBLE_PALETTE["warning"], "rgba(245,158,11,0.18)"),
]

# 7-state meta-regime colors — used by regime_probs + meta_regime_radial charts.
META_REGIME_COLORS: dict[str, str] = {
    "calm_trend":          NOBLE_PALETTE["success"],
    "choppy_range":        NOBLE_PALETTE["warning"],
    "high_vol_breakout":   NOBLE_PALETTE["info"],
    "regime_transition":   NOBLE_PALETTE["warning"],
    "risk_off":            NOBLE_PALETTE["error"],
    "funding_stress":      NOBLE_PALETTE["error"],
    "liquidity_drained":   NOBLE_PALETTE["neutral"],
}


def regime_color(regime_label: str | None) -> tuple[str, str]:
    """Return (stroke, fill_rgba) by substring match — mirrors components/ui.html.

    Returns ghost gray for unknown / None / empty labels.
    """
    label = (regime_label or "").lower()
    for substring, stroke, fill in REGIME_COLOR_MAP:
        if substring in label:
            return stroke, fill
    return NOBLE_PALETTE["text_dim"], "rgba(156,163,175,0.10)"


def apply_dark_theme(fig: Figure, ax) -> None:
    """Apply the NOBLE dark theme to a Figure + Axes pair.

    Call immediately after `fig, ax = plt.subplots(...)` — before adding any
    artists. Do NOT also call plt.tight_layout() or pass bbox_inches='tight'
    to savefig (constrained_layout handles margins; mixing breaks it).
    """
    fig.patch.set_facecolor(NOBLE_PALETTE["base_100"])
    ax.set_facecolor(NOBLE_PALETTE["base_200"])
    ax.tick_params(colors=NOBLE_PALETTE["text"], labelsize=8)
    ax.xaxis.label.set_color(NOBLE_PALETTE["text"])
    ax.yaxis.label.set_color(NOBLE_PALETTE["text"])
    ax.title.set_color(NOBLE_PALETTE["text"])
    for spine in ax.spines.values():
        spine.set_color(NOBLE_PALETTE["base_300"])
        spine.set_linewidth(0.5)
    ax.grid(True, color=NOBLE_PALETTE["base_300"], linewidth=0.5, alpha=0.5)


def render_empty_chart(symbol: str, message: str, figsize: tuple[float, float] = (12, 4)) -> bytes:
    """Render a placeholder PNG with an error message — used when data fetch fails."""
    import io

    fig, ax = plt.subplots(figsize=figsize, dpi=150, constrained_layout=True)
    apply_dark_theme(fig, ax)
    ax.set_axis_off()
    ax.text(
        0.5, 0.6, symbol, ha="center", va="center",
        fontsize=14, fontweight="bold", color=NOBLE_PALETTE["text"],
        transform=ax.transAxes,
    )
    ax.text(
        0.5, 0.4, message, ha="center", va="center",
        fontsize=10, color=NOBLE_PALETTE["text_dim"],
        transform=ax.transAxes,
    )
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    return buf.getvalue()
