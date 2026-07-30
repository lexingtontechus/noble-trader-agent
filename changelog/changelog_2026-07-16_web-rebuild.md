# Changelog 2026-07-16 — Web UI rebuild + reporting skills

## Decision
Keep ONE web UI: `src/hermes/web` (FastAPI + Jinja2, watchdog-supervised on :8080).
Retire `dashboard/` (Next.js 16 SPA) — it was non-functional: `src/lib/` missing
(auth-simple/api-mock unresolved → wouldn't compile), login was "any
username/password" mock, `dist/` never built (live `/app` returned 503). It
duplicated the same feature set with fake data.

## Changes
1. **Archived** `dashboard/` → `.archive/dashboard-2026-07-16` (git R-renames,
   history preserved). 539K, no node_modules.

2. **Self-hosted charts** in `src/hermes/web/static/`:
   - Vendored `uplot.iife.min.js` (50KB) + `uplot.min.css` (no CDN; survives CSP).
   - New `charts.js` — `HermesChart` API: `equityCurve`, `equityCurveFromData`,
     `pnlHistory`, `varHistory`, `exposure`, `line`. Pulls live `/api/*`
     (session cookie) and renders interactive uPlot charts.
   - `base.html` loads uPlot + charts.js; `pnl/portfolio/monitor/backtest`
     templates each got a live chart block.

3. **Backend hardening (`app.py`)**:
   - Removed dead `/app` React mount + `/` → `/app/` redirect (referenced
     deleted `DIST_DIR`, would NameError at import).
   - Added `safe_json()` / `_sanitize()` — recursive nan/inf + numpy
     (scalar **and** array) neutralizer. Applied to ALL 16 `/api/*` routes
     (was `_json.loads(_json.dumps(..., default=str))`, which 500'd on nan).
   - **Real bugs fixed:** `/api/risk/decisions`, `/api/portfolio`,
     `/api/pnl/tear_sheet` (and backtest/signals/simulations) 500-ing on
     nan/numpy floats now return 200 + valid JSON.
   - `pnl_page` pre-serializes `equity_curve` to JSON (raw rows carried
     pandas Timestamps that `|tojson` couldn't serialize).

4. **PnL template crash fixed**: `tear_sheet.trading.win_rate_pct` raised
   `UndefinedError` when `n_trades == 0`. Guarded Trading + By-Regime cards
   with `n_trades > 0` checks. `/pnl` now renders 200 with real equity-curve data.

5. **4 reporting Hermes skills** (under `skills/trading/`):
   - `report-pnl`, `report-portfolio`, `report-monitor`, `report-backtests`
   - Shared `references/hermes-api-protocol.md` (bearer-token auth, endpoint
     map, verified JSON shapes). Each skill is read-only, cron-friendly, and
     verified end-to-end against the live API (report-pnl rendered real
     markdown: equity $107,517.80, return 7.52%, period 2026-07-07→07-16).

## Verification
- Test instance (fresh port) booted clean; **all 16 `/api/*` → 200 valid JSON**,
  all HTML pages (`/`, `/pnl`, `/portfolio`, `/monitor`, `/backtest`, `/config`) → 200.
- `report-pnl` flow executed with real token + live data → real markdown output.

## TODO / notes
- Live `dashboard` loop on :8080 still runs OLD code. Watchdog self-heals every
  5 min but only launches if down. To serve the rebuilt UI NOW, the :8080
  process must be restarted (single-instance; do not hand-launch — let watchdog
  cycle or restart deliberately).
- Global `PYTHONPATH` points at broken `hermes-agent/venv`; run dashboard with
  `PYTHONPATH=` cleared (watchdog already does this).

## Addendum: heartbeats 500 + DB backup (2026-07-16, later)

### Heartbeats bug (pre-existing, exposed by user)
- `/heartbeats` HTML + `/api/heartbeats` both 500'd.
- Cause: template referenced ~13 columns absent from `signal_heartbeats`
  (`ev`, `lag_ms`, `p_regime`, `p_markov`, `p_timesfm`, `tail_risk_score`,
  `tail_risk_action`, `aggression`, `markov_current_state`, ...). Real columns:
  heartbeat_id, ts_received, ts_upstream, symbol, signal, regime, regime_conf,
  regime_shift, entry_price, stop_loss, take_profit, brick_size, kelly_f,
  effective_kelly, p_win, ev_per_dollar, accepted, reject_reason.
- `/api/heartbeats` also 500'd because rows carry pandas Timestamps
  (not JSON-serializable) and the route used a plain JSONResponse.
- Fixes:
  - Rewrote `templates/heartbeats.html` to real columns only.
  - `heartbeats_page` derives `lag_ms` (received-upstream) in Python.
  - `_sanitize` now handles datetime / pandas.Timestamp -> ISO.
  - `/api/heartbeats` wrapped in `safe_json`.
- Verified: both -> 200 with real rows. Screenshot confirms styled table.

### Full page sanity check
All 13 HTML routes return 200 (/status 404 is expected — only /api/status +
/health exist). Zero 500s.

### Auth decision
User chose KEEP AS-IS: API token-gated (/api/* + login), HTML pages already
public on 127.0.0.1. No auth code changes. Charts need one browser login.

### DB backup (new)
- Durable store = `data/hermes.duckdb` (28MB, 26 base tables). Single point
  of failure; no prior backup.
- Added `scripts/backup_duckdb.py`: EXPORT DATABASE -> data/backups/
  hermes.duckdb.YYYYMMDD-HHMMSS + rotation (keep N). Restore via
  `IMPORT DATABASE '<dir>'`.
- Verified real run: 26 tables exported (2214 KB). Restore tested into a
  throwaway db -> 26 tables, 3119 account_snapshots rows recovered.
- Scheduled cron `hermes-duckdb-backup` (job 8824208b6641): daily 03:00,
  keep 7. Local-only (no delivery channel in CLI session).

## Addendum: config page rebuild + default.yaml cleanup (2026-07-16, later)

### config/default.yaml
- Renamed PortfolioConfig.start_small -> start_smart (model + yaml + roadmap.md).
  start_small was defined but never read in code, so rename is safe.
- initial_symbols: reduced to start-small set (BTC/USD@alpaca, BTC-PERP@HL,
  ETH-PERP@HL) + comment tying them to venues' crypto_pairs. Seeds symbols table.
- Added descriptive comments to meta_regime + renko (what each knob does).
- active_hours: commented that timezone is the user-locale tz for scheduling/WS.
- Redis URLs confirmed already env-driven (secret: refs) — no hardcoded URL in yaml.
  (Code fallback defaults redis://localhost:6379 are localhost dev only, not user config.)

### redact_config_for_display (config.py) — hardened
Old: only redacted strings >8 chars containing key/secret/token/password/0x.
New: redacts ANY secret-keyed value + any URL/wallet/hex value, regardless of
length/keyword. secret: refs shown as-is. Verified: page has 0 redis:// literals,
53 redacted markers, 2 secret: refs. No leakage.

### config.html + config_page (app.py) — full rebuild
- New build_config_display() curates config into labelled, described groups
  (Portfolio, Venues, Account/Asset Limits, Signal, Entry, Execution, Position
  Mgmt, Circuit Breakers, Autonomy+active_hours tz, Meta-Regime, Renko, Upstream,
  Data Sources, Secrets Status).
- Template rewritten: responsive 2-col (label+value) grid per card, descriptions,
  collapsible raw JSON at bottom. Replaces the recursive raw-form dump.
- Bug fixed: build_config_display was accidentally bound to @app.get("/config")
  (422). Moved decorator to config_page. Verified /config -> 200, all sections render.

### Verify
- /config -> 200 (75KB), 10 curated sections, 0 secret leakage.
- Screenshot confirms neat dark-theme 2-col layout.
