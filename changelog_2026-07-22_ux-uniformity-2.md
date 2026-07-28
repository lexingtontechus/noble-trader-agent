# Changelog — UX-UNIFORMITY-2: kelly_badge tuning v2 + 5 deprecated-dashboard patterns ported

**Date/Time:** 2026-07-22 (PDT)
**Agent:** main (Super Z)
**Scope:** Update kelly_badge macro with delta + live + adaptive precision. Port the
5 high-value patterns from the deprecated Next.js dashboard that were documented
for future work in UX-UNIFORMITY-1.

---

## 1. kelly_badge macro updates (`components/ui.html`)

Three enhancements added to the existing `kelly_badge(kelly)` macro from
UX-UNIFORMITY-1. New signature: `kelly_badge(kelly, delta=None, live=False)`.

### 1.1 Delta indicator (▲/▼/—)
When `delta` is provided (positive/negative float representing the change in
`effective_kelly` since the previous heartbeat for the same symbol), an arrow
glyph is prepended to the badge value. Threshold: `|delta| ≥ 0.005` (0.5pp)
to filter rounding noise. Below threshold → no arrow.

| delta | glyph | meaning |
|---|---|---|
| `None` / `0` | (none) | no prior heartbeat to compare |
| `≥ +0.005` | ▲ | kelly increased since last hb |
| `≤ -0.005` | ▼ | kelly decreased since last hb |
| `±0.001–0.004` | (none) | insignificant change |

The `/heartbeats` route now computes `kelly_delta` for each heartbeat by
walking the DESC-sorted list in reverse (oldest→newest), tracking
`prior_kelly[symbol]` and storing `cur - prev` on each heartbeat.

### 1.2 Pulse animation for live values
When `live=True`, the badge gets the `kelly-pulse` CSS class — a subtle 1.5s
opacity+scale pulse animation (defined in `app.css` as `kelly-pulse-anim`
keyframe). This distinguishes fresh heartbeats (within 60s of `now`) from
cached historical ones at a glance.

The `/heartbeats` route now computes `is_live` per heartbeat: `True` if
`0 ≤ age_sec ≤ 60` where `age_sec = (now - ts_received).total_seconds()`.

### 1.3 Adaptive precision
Minimal tier (`k < 0.02`) and conservative tier (`0.02 ≤ k < 0.05`) now
render with **3 decimal places** (`k=0.020`, `k=0.025`) — at these small
magnitudes, the 3rd decimal is meaningfully different. Standard / aggressive
/ very-aggressive tiers still render with 2 decimals (`k=0.10`, `k=0.20`,
`k=0.30`) where the 3rd decimal would be noise.

### 1.4 New helper macro: `kelly_delta_glyph(delta)`
Extracted for reuse. Returns the appropriate ▲/▼/— span based on delta
threshold. Used internally by `kelly_badge`.

### 1.5 Wiring
Existing single-arg callers (`kelly_badge(hb.effective_kelly)`) are
backward-compatible — both new params have defaults. Two call sites
upgraded to use the new params:
- `heartbeats.html:81-82` — `kelly_badge(hb.kelly_f, live=hb.is_live)` and
  `kelly_badge(hb.effective_kelly, delta=hb.kelly_delta, live=hb.is_live)`
- `index.html:95` — `kelly_badge(hb.effective_kelly, live=hb.is_live)`
  (forward-compatible — index.html isn't currently served by any route
  since `/` redirects to `/portfolio`, but the wiring is in place for
  when it's reinstated).

---

## 2. Interactive Decision Tree Viz (`agent.html`)

Replaced the 47-line ASCII-art decision tree in `agent.html` with a pure
CSS/Jinja2 port of the deprecated dashboard's `DecisionTreeViz.tsx` React
component (archived at `.archive/dashboard-2026-07-16/src/components/charts/
DecisionTreeViz.tsx`).

### 2.1 New macro: `decision_tree_viz(node, depth=0)`
- **Recursive** — invokes itself for each child branch.
- **Pure CSS** — uses `<details>`/`<summary>` for click-to-collapse behavior.
  No JS dependency. Auto-collapses nodes deeper than depth=1 (mirrors
  deprecated `TreeNode.tsx:34` behavior).
- **Color tiers** — `dtree-color-error/success/warning/info/primary/neutral`
  CSS classes mirror the deprecated `colorMap`. Applied via
  `node.color` field.
- **Thresholds + question display** — renders `node.question` and
  `node.thresholds` (as `key=value` chips) when present, matching the
  deprecated component's display.
- **Connector lines** — `.dtree-connector` is a 12px vertical line
  between parent and child branches, mirroring the deprecated `w-px h-4`
  connector.

### 2.2 Data source
Uses the existing `/api/agent/decision_tree` endpoint
(`status.py:get_decision_tree_definition()` at line 941) — same JSON
structure the React component consumed. The `/agent` route now passes
`decision_tree` into the template context.

### 2.3 Card "extra" slot
The decision tree card uses the new `card(extra=...)` slot from
UX-UNIFORMITY-1 to display the hint "click any branch node to expand /
collapse · thresholds from HermesDecisionTree defaults" between the title
and the tree visualization.

---

## 3. Portfolio Allocation Donut (`portfolio.html`)

New server-rendered matplotlib PNG endpoint `/api/charts/allocation.png`
replacing the deprecated dashboard's `AllocationPie.tsx` React component.

### 3.1 New chart module: `charts/portfolio_allocation.py`
- **Donut shape** — inner radius 60, outer 100 (matches deprecated
  `AllocationPie.tsx:66-67`).
- **Center label** — `$XX,XXX` total + "Total Exposure" subtitle, replacing
  the deprecated component's lack of a center label.
- **DaisyUI palette** — uses `NOBLE_PALETTE` (primary/accent/info/success/
  warning/error/neutral/text_dim) — mirrors the deprecated component's
  `oklch(var(--p)/--s/...)` palette.
- **Legend** — right-side, with `$value (XX.X%)` format per slice.
- **Empty-state** — falls back to `render_empty_chart()` with the hint
  "No exposure yet — run: platform risk --equity 100000".
- **Caching** — 60s TTL via `chart_cache.get_or_render`.

### 3.2 Data source
Uses the existing `web.status.get_portfolio_exposure_breakdown()`. Strategy:
1. If `by_venue` is populated → use it (PnL contribution per venue).
2. Else if `totals.long_exposure_usd` or `totals.short_exposure_usd` is
   non-zero → use 2-slice long/short donut (current exposure shape).
3. Else → empty-state.

### 3.3 Template wiring
Side-by-side grid with `ExposureBars` (next section) using
`grid-cols-1 lg:grid-cols-2 gap-4` — mirrors the deprecated dashboard's
`lg:grid-cols-2` two-card layout pattern.

---

## 4. Exposure Bars (`portfolio.html`)

New server-rendered matplotlib PNG endpoint
`/api/charts/exposure_bars.png` replacing the deprecated dashboard's
`ExposureBars.tsx` React component.

### 4.1 New chart module: `charts/exposure_bars.py`
- **Horizontal bars** — `ax.barh()`, sorted by `|value|` descending.
- **Negative-value tinting** — bars with `value < 0` get `error` (red)
  color regardless of palette position, making direction obvious.
- **Value labels** — `$X,XXX` at end of each bar (right-aligned for
  positive, left-aligned for negative).
- **Adaptive height** — `0.45 * n_bars + 1.5` inches, min 2.5", so 2 bars
  and 20 bars both look right.
- **USD-formatted x-axis** — `FuncFormatter(lambda x, _: f"${x:,.0f}")`.
- **Empty-state** — same `render_empty_chart()` pattern as allocation.

### 4.2 Data source
Same `get_portfolio_exposure_breakdown()` as allocation. Strategy:
1. If `by_venue` populated → sorted venue bars.
2. Else → 4 bars: Long / Short / Net / Gross from latest snapshot.

### 4.3 Complementarity with allocation
- **Allocation** shows proportional share (donut + center total + %).
- **ExposureBars** shows absolute magnitude (sorted horizontal bars + $ labels).
Both consume the same endpoint — the donut answers "what's the share?" and
the bars answer "how big is each?".

---

## 5. VaR Distribution Histogram (`pnl.html`)

New server-rendered matplotlib PNG endpoint
`/api/charts/var_histogram.png` replacing the deprecated dashboard's
`VarDistHistogram.tsx` React component.

### 5.1 New chart module: `charts/var_histogram.py`
- **20 bins** (matches deprecated `bins=20` default).
- **Sign-tinted bars** — bins whose midpoint is `< 0` get `error` (red),
  bins whose midpoint is `≥ 0` get `success` (green). Makes it visually
  obvious whether the portfolio has been spending more time in tail-risk
  territory or safe territory.
- **Zero reference line** — dashed primary-colored vertical line at VaR=0
  (only shown when `v_min < 0 < v_max`).
- **Stats box** — top-right `n=NNN  mean=$X  median=$Y  current=$Z`
  in a semi-transparent `base_300` background.
- **Empty-state** — `render_empty_chart()` with hint "No VaR history yet
  — accumulate account snapshots".

### 5.2 Data source
Uses existing `web.status.get_portfolio_var_history(limit=500)`. Pulls
the last 500 rows from `account_snapshots` where `var_1d_99 IS NOT NULL`.

### 5.3 Placement on PnL page
Inserted between the "Drawdown + Trading" two-card grid and the
"PnL by Meta-Regime" card. Sits in the natural reading flow: tear sheet
summary → returns/risk ratios → drawdown/trading tables → **VaR
distribution histogram** → by-regime PnL → equity curve → trade history.

---

## 6. Backtest Click-to-Select Drill-Down (`backtest.html`)

Replaced the static `backtest.html` table with a click-to-select drill-down
pattern ported from the deprecated dashboard's `Backtest.tsx` React
component.

### 6.1 Row click behavior
- Each row has `class="bt-row"` + `data-run-id="..."`.
- Click → row gets `.active` class (primary-tinted background + left
  border accent), fetches `/api/backtest/runs/{run_id}` (already existed
  at `app.py:1339`), renders detail panel below the table.
- Click same row again → collapses the panel.
- Click different row → swaps to new detail (deactivates previous row).

### 6.2 Detail panel contents
Mirrors the deprecated `BacktestRunDetailPanel`:
- **Card title**: `Run abc12345... — Detail` (truncated UUID).
- **Extra slot**: `mode · symbols · ts_started → ts_finished` + JSON link.
- **6-col stat grid** (using existing `stat_grid` macro): Initial Equity,
  Final Equity, Total Return, Net PnL, Max DD, Duration.
- **Second 6-col stat grid**: Heartbeats, Signals, Approved, Rejected,
  Orders, Fills.
- **Error alert** if `run.error` is non-empty.
- **Trades table** (max 100 rows, with truncation note if more): #, Symbol,
  Regime, Net PnL, R-Multiple, Hold (sec).

### 6.3 JS implementation
- Pure vanilla JS — no React, no jQuery, no fetch polyfill.
- Wrapped in IIFE to avoid leaking locals to global scope.
- Same-origin credentials (consistent with `charts.js` `getJSON` helper).
- Smooth fade-in animation via `bt-fade-in` keyframe (defined in `app.css`).

### 6.4 CSS additions (`app.css`)
- `.bt-row` — `cursor: pointer` + `transition: background 0.1s`.
- `.bt-row:hover` — subtle `base_300/50` background tint.
- `.bt-row.active` — primary-tinted background + 3px left primary border.
- `.bt-detail-panel` — `bt-fade-in 0.2s ease-out` animation.

---

## 7. Files changed

| File | Type | Summary |
|---|---|---|
| `src/hermes/web/templates/components/ui.html` | EDIT | `kelly_badge` +`delta`/`live` params + adaptive precision; +`kelly_delta_glyph` macro; +`decision_tree_viz` recursive macro |
| `src/hermes/web/templates/agent.html` | EDIT | Replaced 47-line ASCII art tree with `decision_tree_viz` macro |
| `src/hermes/web/templates/portfolio.html` | EDIT | +allocation donut + exposure_bars side-by-side cards |
| `src/hermes/web/templates/pnl.html` | EDIT | +VaR distribution histogram card |
| `src/hermes/web/templates/backtest.html` | EDIT | +click-to-select rows + JS detail panel + IIFE script block |
| `src/hermes/web/templates/heartbeats.html` | EDIT | kelly_badge calls now use `live=hb.is_live` + `delta=hb.kelly_delta` |
| `src/hermes/web/templates/index.html` | EDIT | kelly_badge call now uses `live=hb.is_live` (forward-compat) |
| `src/hermes/web/static/app.css` | EDIT | +`kelly-pulse-anim` keyframe + `dtree-*` classes + `bt-row`/`bt-detail-panel` classes |
| `src/hermes/web/charts/portfolio_allocation.py` | NEW | Allocation donut render module (60s TTL cache) |
| `src/hermes/web/charts/exposure_bars.py` | NEW | Exposure bars render module (60s TTL cache) |
| `src/hermes/web/charts/var_histogram.py` | NEW | VaR histogram render module (60s TTL cache) |
| `src/hermes/web/app.py` | EDIT | +3 new `/api/charts/*.png` routes; `/agent` route now passes `decision_tree`; `/heartbeats` route now computes `is_live` + `kelly_delta` per heartbeat |

---

## 8. Verification

- **`scripts/smoke_test_ux_uniformity_2.py`** — 8 test categories all pass:
  1. All 5 modified templates parse cleanly
  2. agent.html renders with decision tree (12,356 chars)
  3. portfolio.html renders with allocation + exposure_bars cards (14,885 chars)
  4. pnl.html renders with var_histogram card (12,330 chars)
  5. backtest.html renders with click-to-select rows (19,319 chars)
  6. app imports cleanly — 66 routes (was 63, +3 new chart endpoints)
  7. All 3 new chart modules import + render empty-state PNGs (no exceptions)
  8. All 14 new CSS classes present in app.css
- **`scripts/render_visual_smoke_tests.py`** — Renders 5 visual artifacts
  to `/home/z/my-project/download/ux_uniformity_2_*`:
  - `allocation.png` (1,200 × 900, 65 KB)
  - `exposure_bars.png` (1,500 × 495, 28 KB)
  - `var_histogram.png` (1,500 × 750, 43 KB)
  - `decision_tree.html` (10 KB standalone HTML for visual verification)
  - `kelly_badge_showcase.html` (5 KB — all 7 tiers × delta × live combinations)
- **`scripts/smoke_test_dtree.py`** — 5 decision-tree-specific tests pass:
  ui.html parses, decision_tree_viz renders 7-tier nested tree (2,906 chars),
  kelly_badge handles all 12 (kelly, delta, live) combinations, agent.html
  renders end-to-end (12,356 chars).

---

## 9. Notes / future work

- The `/agent` route now calls `get_decision_tree_definition()` on every
  request — it's a pure-function dict literal (no DB / I/O), so this is
  fine. If thresholds ever become config-driven, the call could be cached.
- The `/heartbeats` route's `kelly_delta` computation is O(N) per request
  but runs over at most 500 heartbeats — negligible cost.
- The backtest click-to-select JS could be extracted to
  `static/backtest.js` if a second page ever needs the same pattern —
  for now, the inline `<script>` keeps the change self-contained.
- The 3 new chart endpoints all use the existing
  `charts/_cache.py:get_or_render()` 60s TTL cache — consistent with the
  other 6 chart endpoints.
- The `kelly_delta_glyph` macro is exported from `components/ui.html` for
  reuse if any future component needs delta arrows outside the kelly_badge
  context.
