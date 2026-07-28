# Changelog — Dashboard UX uniformity pass + kelly_badge tuning

**Date/Time:** 2026-07-22 (PDT)
**Agent:** main (Super Z)
**Scope:** Review `src/web/templates/` vs the archived deprecated dashboard
(`.archive/dashboard-2026-07-16/`), apply uniform UX patterns across all 16
templates, and tune the `kelly_badge` macro thresholds to match the actual
production `effective_kelly` distribution.

---

## 1. Review & comparison vs the deprecated dashboard

Surveyed the archived Next.js dashboard at `.archive/dashboard-2026-07-16/`:
- `src/components/layout/` — Card.tsx, Footer.tsx, Navbar.tsx, ThemeSwitcher.tsx
- `src/components/charts/` — AllocationPie, DecisionTreeViz, EquityCurve,
  ExposureBars, PositionTable, StatsGrid, VarDistHistogram
- `src/pages/` — Dashboard, Agent, Backtest, Monitor, PnL, Portfolio, Status, Symbols
- `src/app/globals.css` — Tailwind v4 + DaisyUI dark theme + `pulse-dot` keyframe

**Findings (full report in worklog):**

A. The Flask dashboard already has **superior implementations** of:
   - Theme switcher (7-theme dropdown vs deprecated's 2-state toggle)
   - Kelly badge with 4-tier color thresholds (no equivalent in deprecated)
   - Tabbed sub-navigation (market_symbol.html — Overview/Renko/Regime/Signals)
   - Regime strip with kelly_badge + brick_arrow chips (no equivalent in deprecated)
   - Symbol card grid with 4-metric subgrid + confidence bar
   - Page header pattern with title + subtitle + refresh_note

B. The deprecated dashboard has **5 patterns worth porting** (future work):
   1. `StatsGrid` with column-count (2/3/4/6) + size (sm/md/lg) + format props
   2. Side-by-side `lg:grid-cols-2 gap-4` card grid (for portfolio.html)
   3. Click-to-select drill-down table row (for backtest.html)
   4. Interactive `DecisionTreeViz` (replace ASCII art in agent.html)
   5. `VarDistHistogram` + `AllocationPie` + `ExposureBars` chart types

C. **UX inconsistencies found in the 16 Flask templates** (the actionable list):
   - Only 2 of 14 content pages used a `page_header` pattern
   - Only 3 of 9 auto-refreshing pages showed `refresh_note`
   - `regime_strip` was on only 2 of 6 market-context pages
   - `kelly_badge` was on only 2 of 5 Kelly-displaying pages
   - `stat_grid` was hardcoded 4-col (no 6-col option)
   - `card` macro had no `extra` slot for metadata rows
   - `empty_state` was followed by extra `<p>` paragraphs in 4 templates
   - `pagination_note` macro existed but was never called
   - `signal_badge` was used inconsistently (some tables rendered direction as plain text)

---

## 2. Macro upgrades (`components/ui.html`)

### `card` macro — added `extra` slot
Mirrors the deprecated `Card.tsx` `extra` prop. Renders a small `text-xs opacity-50
flex justify-between` row between the title and body — used for metadata rows
(e.g. config page description, run counts).

```jinja2
{% call card(title="Returns", extra="2026-01-01 → 2026-07-22 · 500 data points") %}
... body ...
{% endcall %}
```

### `page_header` macro — NEW
Uniform title + subtitle + refresh-note + actions row. Used at the top of every
content page so all 16 templates share one header pattern (replaces ad-hoc
`<h1>` + `<p>` blocks scattered across templates).

```jinja2
{{ page_header(title="Portfolio", subtitle="3 open positions · $104k equity", refresh=10) }}
{{ page_header(title="Market Overview", subtitle="20 active symbols",
               refresh=30, actions='<a class="btn btn-ghost btn-sm" href="/api/market/overview">JSON</a>') }}
```

### `stat_grid` macro — added `columns` + `size` params
Mirrors the deprecated `StatsGrid.tsx` column-count support.

```jinja2
{{ stat_grid(stats, columns=6, size="text-xl") }}  # compact 6-col
{{ stat_grid(stats, columns=3, size="text-3xl") }}  # headline 3-col
{{ stat_grid(stats) }}                                # default: 4-col, text-2xl
```

### `kelly_legend` macro — NEW
Reusable legend block explaining the 7 kelly tiers. Extracted from `market.html`
so it can be shown on any page that displays `kelly_badge`.

### `kelly_badge` macro — TUNED THRESHOLDS
**Old thresholds (replaced):** `>=0.25` success, `0.10–0.25` warning, `<0.10` error,
`0`/`None` ghost/error. These made a normal 0.02 MVP signal render as red
"defensive" and a standard 0.10 test fixture render as yellow "reduced" —
backwards vs. real production.

**New thresholds (aligned to the actual production distribution):**

| Range | Color | Label | Rationale |
|---|---|---|---|
| `None` | ghost | `k=—` | not available (no heartbeat) |
| `0` | error | `k=0` | kill switch (meta_regime blocks sizing) |
| `0 < k < 0.02` | warning | `k=0.01` | minimal / testing — verify intentional |
| `0.02 ≤ k < 0.05` | info | `k=0.03` | conservative — starter / small position |
| `0.05 ≤ k < 0.15` | success | `k=0.10` | standard — typical production range |
| `0.15 ≤ k < 0.25` | warning | `k=0.20` | aggressive — above typical, attention |
| `k ≥ 0.25` | error | `k=0.30` | very aggressive — review for safety |

Sources for the distribution:
- `docs/mt4_mt5_bridge_spec.md:170` — MVP recommended: 0.01–0.02
- `docs/roadmap.md:1647` — typical `kelly_f` range: 0.039–0.10
- `tests/test_phase3.py:36` — standard fixture: 0.12
- `tests/test_phase3.py:541` — stress test: 0.50
- `signals/sizing.py:84` — docstring example: 0.12

Verified the new thresholds render correctly for all 12 test inputs (None, 0,
0.01, 0.02, 0.04, 0.05, 0.10, 0.14, 0.15, 0.20, 0.25, 0.30, 0.50).

---

## 3. Template uniformity pass (16 templates)

| Template | Changes |
|---|---|
| `index.html` | + `page_header`, + `signal_badge`/`regime_badge`/`kelly_badge` in heartbeats table, stat_grid → 6-col text-xl, removed standalone "Overall" card |
| `heartbeats.html` | + `page_header` + `refresh=15` auto-refresh (was missing), + `kelly_badge` for kelly_f + effective_kelly columns (was plain `%.4f`), + `pagination_note` call |
| `signals.html` | + `page_header`, + `refresh_note`, + `kelly_badge` for nt_effective_kelly (was plain `%.4f`) |
| `portfolio.html` | + `page_header` + `refresh_note`, split 12-stat grid into two 6-col text-xl grids (was one 12-stat 4-col), removed trailing `<p>` after empty_state |
| `monitor.html` | + `page_header`, stat_grid → 6-col text-xl, merged empty_state + trailing `<p>` into single hint |
| `orders.html` | + `page_header` + `refresh_note`, removed trailing `<p>` after empty_state |
| `approvals.html` | + `page_header` + `refresh=15` auto-refresh (was missing), + `signal_badge` for direction (was plain text), + `timestamp` for created_at (was raw string) |
| `pnl.html` | + `page_header` + `refresh_note`, tear-sheet stat_grid → 3-col text-3xl (was 4-col text-2xl), merged trailing `<p>` into empty_state hint |
| `backtest.html` | + `page_header`, merged trailing `<p>` into card body description |
| `agent.html` | + `page_header` with hypothesis + journal counts |
| `symbols.html` | + `page_header`, + 4-stat `stat_grid` for active/inactive/validated/total counts (was inline text), split registry into "Summary" + "Symbols" cards |
| `optimize.html` | + `page_header` |
| `config.html` | + `page_header`, used new `card(extra=...)` slot for group descriptions |
| `market.html` | Refactored inline header to use `page_header` macro, replaced hardcoded Kelly legend with `kelly_legend()` macro |
| `market_symbol.html` | Refactored inline header to use uniform pattern (rich variant preserves symbol + asset/venue + brick_arrow + regime + kelly) |
| `setup.html` | (unchanged — onboarding wizard, intentionally different from rest) |

---

## 4. Route handler changes (`app.py`)

### New helper: `_build_regime_strip(config) -> list[dict]`
Returns the condensed regime-strip payload from cached market overview. Used by
every market-context route handler so the regime strip at the top of every page
renders the same 20-symbol snapshot. Replaces the inline list-comprehension
that was duplicated in `/market` and `/market/{symbol}`.

### Routes that now pass `show_regime_strip=True` + `strip_data`:

| Route | Before | After |
|---|---|---|
| `/portfolio` | no strip | strip shown |
| `/signals` | no strip | strip shown |
| `/heartbeats` | no strip | strip shown |
| `/approvals` | no strip | strip shown |
| `/pnl` | no strip | strip shown |
| `/orders` | no strip | strip shown |
| `/market` | strip (inline comprehension) | strip (via helper) |
| `/market/{symbol}` | strip (inline comprehension) | strip (via helper) |

Routes that remain `show_regime_strip=False` (correct — admin/static pages):
`/config`, `/setup`, `/backtest`, `/optimize`, `/agent`, `/` (status).

### Other route fixes:
- `/heartbeats`: now also passes `environment` (was missing — would have caused
  the navbar to render `{{ environment }}` as empty)

---

## 5. Verification

- `python -c "import ast; ast.parse(open('app.py').read())"` — OK
- Jinja2 parse check on all 19 templates (16 pages + 2 components + base) — OK
- `from hermes.web.app import app, _build_regime_strip` — OK, 63 routes intact
- Smoke-test of `kelly_badge` for 12 threshold values — all render correctly
- Smoke-test of `page_header`, `stat_grid(columns=6)`, `card(extra=...)` — all
  render correctly

---

## 6. Files changed

| File | Type | Summary |
|---|---|---|
| `src/hermes/web/templates/components/ui.html` | EDIT | +`page_header` macro, +`kelly_legend` macro, `card` macro +`extra` slot, `stat_grid` macro +`columns`/`size` params, `kelly_badge` thresholds tuned to production distribution |
| `src/hermes/web/templates/index.html` | EDIT | +page_header, +badges in heartbeats table, 6-col stat_grid |
| `src/hermes/web/templates/heartbeats.html` | EDIT | +page_header, +auto-refresh 15s, +kelly_badge, +pagination_note |
| `src/hermes/web/templates/signals.html` | EDIT | +page_header, +refresh_note, +kelly_badge |
| `src/hermes/web/templates/portfolio.html` | EDIT | +page_header, +refresh_note, split 12-stat into two 6-col, removed trailing `<p>` |
| `src/hermes/web/templates/monitor.html` | EDIT | +page_header, 6-col stat_grid, merged trailing `<p>` |
| `src/hermes/web/templates/orders.html` | EDIT | +page_header, +refresh_note, removed trailing `<p>` |
| `src/hermes/web/templates/approvals.html` | EDIT | +page_header, +auto-refresh 15s, +signal_badge, +timestamp |
| `src/hermes/web/templates/pnl.html` | EDIT | +page_header, +refresh_note, 3-col text-3xl stat_grid, merged trailing `<p>` |
| `src/hermes/web/templates/backtest.html` | EDIT | +page_header, merged trailing `<p>` |
| `src/hermes/web/templates/agent.html` | EDIT | +page_header |
| `src/hermes/web/templates/symbols.html` | EDIT | +page_header, +stat_grid summary card |
| `src/hermes/web/templates/optimize.html` | EDIT | +page_header |
| `src/hermes/web/templates/config.html` | EDIT | +page_header, +card(extra=) for group descriptions |
| `src/hermes/web/templates/market.html` | EDIT | Refactored to use page_header + kelly_legend macros |
| `src/hermes/web/templates/market_symbol.html` | EDIT | Refactored header to uniform pattern (rich variant) |
| `src/hermes/web/app.py` | EDIT | +`_build_regime_strip` helper, refactored /market + /market/{symbol} to use it, +6 routes now pass `show_regime_strip=True` + `strip_data`, +heartbeats route now passes `environment` |

---

## 7. Notes / future work

- The 5 high-value patterns from the deprecated dashboard (interactive decision
  tree, VaR histogram, allocation pie, exposure bars, click-to-select backtest
  drill-down) are documented in the worklog for future implementation.
- The `tab-panel` pattern in `market_symbol.html` (Overview/Renko/Regime/Signals)
  could be extracted into a reusable `tabs(items, active)` macro if any other
  page adopts tabs (e.g. portfolio.html could split into Overview/Risk
  Decisions/VaR History/Exposure tabs).
- The `pulse-dot` CSS class from the deprecated globals.css is not yet ported —
  could be added next to `refresh_note` to make auto-refresh visually obvious.
