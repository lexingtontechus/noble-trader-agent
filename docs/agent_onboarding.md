# Hermes Agent Operational Runbook

> **This is the Hermes agent's operational runbook.** Hermes is the AI operator
> that runs the Noble Trader platform on its host. The agent reads this document
> and executes the steps below on its own machine — it does not wait for a
> human operator to issue commands. Recurring tasks (daily / weekly / monthly)
> are scheduled by the agent on its own host via `crontab -e`. No recurring-task
> scripts live in this repository; the cron schedule is the agent's, not the
> repo's.

---

## Table of Contents

1. [Onboarding Overview](#1-onboarding-overview)
2. [At-Startup](#2-at-startup)
3. [Connection Verification](#3-connection-verification)
4. [Historical Data Backfill](#4-historical-data-backfill)
5. [The Trading Loop](#5-the-trading-loop)
6. [End-of-Day Analysis & Self-Learning](#6-end-of-day-analysis--self-learning)
7. [Ongoing Operations](#7-ongoing-operations)
8. [Disaster Recovery](#8-disaster-recovery)
9. [Dashboard Reference](#9-dashboard-reference)
10. [CLI Command Reference](#10-cli-command-reference)
11. [Troubleshooting Guide](#11-troubleshooting-guide)
12. [Appendix: Cron Schedule Summary](#appendix-cron-schedule-summary)
13. [Appendix: Advanced Circuit Breaker Configuration](#appendix-advanced-circuit-breaker-configuration)
14. [Appendix: Performance Attribution](#appendix-performance-attribution)
15. [Appendix: What You Might Have Missed](#appendix-what-you-might-have-missed)

---

## 1. Onboarding Overview

This runbook defines the agent's lifecycle with the platform. The agent
executes every step on its host machine.

```
Stage 1: At-Startup                  → install, configure, verify, start loop
    ↓
Stage 2: Connection Verification     → verify all 6 subsystems are reachable
    ↓
Stage 3: Historical Data Backfill    → cold-start only: pull NT + market history
    ↓
Stage 4: The Trading Loop            → continuous, 6 long-running processes
    ↓
Stage 5: End-of-Day Analysis         → daily cron: postmortems + hypotheses + auto-rollback
    ↓
Stage 6: Ongoing Operations          → weekly + monthly cron (optimize, rigor, retrain)
    ↓
Stage 7: Disaster Recovery           → when things go wrong, take prescriptive action
```

**Time to first trade**: ~30 minutes (install + config + connection verification).

**Time to first learning cycle**: ~1–2 hours (after accumulating trade history,
the first EOD cron run generates postmortems + hypotheses).

**Cron ownership**: the agent installs its own cron schedule on its host via
`crontab -e`. No cron definitions or recurring-task scripts are committed to
the repository. The complete schedule the agent installs is in
[Appendix: Cron Schedule Summary](#appendix-cron-schedule-summary).

---

## 2. At-Startup

When the platform starts (after a fresh install, a restart, a deploy, or crash
recovery), the agent must perform the steps in this section in order. Stage 1
is the only stage the agent runs every time the host comes up; the daily /
weekly / monthly work is handled by cron in §6 and §7.

### 2.1 Prerequisites

The agent verifies each prerequisite before doing anything else.

| Requirement | How the agent verifies |
|---|---|
| Python 3.12+ | `python --version` |
| Git | `git --version` |
| Node.js 18+ (for SPA dashboard build) | `node --version` |
| Redis (local or remote) | `redis-cli ping` → `PONG` |
| Paper-trading credentials for all venues | see §2.3 |

### 2.2 First-Time Install

The agent runs these steps once on a fresh host. Subsequent restarts skip to
§2.4.

```bash
# 1. Clone the repo (skip if already cloned)
git clone <repo-url> noble-trader-agent
cd noble-trader-agent

# 2. Create the Python virtual environment and install deps
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -e . --no-deps

# 3. Copy the env template (real values are filled in §2.3)
cp .env.example .env

# 4. Web dashboard (FastAPI + Jinja2, self-hosted Tailwind+DaisyUI)
#    Served by `platform dashboard` at http://127.0.0.1:8080 — no separate
#    build step. Assets (tailwind.bundle.css, uPlot) are vendored under
#    src/hermes/web/static; charts render server-side. The old Next.js/React
#    SPA in dashboard/ was retired and archived to .archive/dashboard-2026-07-16.
#    (Optional: rebuild the CSS bundle after editing tailwind.config.js:
#     cd src/hermes/web && npm install && npm run build:css)

# 5. Bootstrap the platform: load config, open DuckDB, apply schema,
#    seed symbols, write the baseline audit entry to config_history
platform init
```

`platform init` is the canonical setup command. It replaces the old
`python scripts/init_duckdb.py` invocation. Specifically, it:

- Loads config from `config/default.yaml`
- Resolves all `secret:` prefixed values from `.env`
- Opens DuckDB and applies all schema migrations (9 migrations, 24 tables)
- Writes the **real** current config to `config_history` as the baseline audit
  entry — every future change is diffed against this hash. (Earlier versions
  wrote a placeholder test row; that is no longer the case.)
- Seeds the `symbols` table from `config/default.yaml → portfolio.initial_symbols`
- Pings Redis (non-fatal if unreachable)
- Prints a config summary

### 2.3 Configure `.env`

The agent fills in `.env` with paper-only credentials. There are 8 venue
credentials and 4 `HERMES_*` auth vars. Without the auth vars the agent cannot
log in to the dashboard or call the API programmatically.

| Credential | Source | What it enables |
|---|---|---|
| `NOBLE_TRADER_REDIS_URL` | Noble Trader operator | Real-time heartbeat ingestion |
| `SUPABASE_URL` | Your Supabase project | Historical heartbeat backfill |
| `SUPABASE_ANON_KEY` | Supabase dashboard (Settings → API → anon public) | Read access to NT tables (subject to RLS) |
| `ALPACA_API_KEY` | https://app.alpaca.markets/paper/dashboard/overview | Paper stock/commodity/crypto trading |
| `ALPACA_API_SECRET` | Same as above | Paper stock/commodity/crypto trading |
| `HYPERLIQUID_WALLET_ADDRESS` | Generate dedicated wallet | Paper crypto trading |
| `HYPERLIQUID_PRIVATE_KEY` | Same wallet (NEVER main wallet) | Paper crypto trading |
| `HERMES_REDIS_URL` | Local Redis instance | Internal pub/sub between layers |

The 4 `HERMES_*` auth vars are mandatory. The auth model is a session cookie
for browsers and a bearer token for the agent — **not** Clerk, **not** JWT,
**not** localStorage. See [roadmap §14](roadmap.md#14-dashboard--api-auth) for
the full model.

```bash
# === Dashboard / API auth (single-user + agent) ===
HERMES_ADMIN_USERNAME=admin
HERMES_ADMIN_PASSWORD=<your-strong-password>
HERMES_SESSION_SECRET=<64-char-random-string>    # signs session cookies
HERMES_AGENT_TOKEN=<long-random-string>          # for programmatic agent access
```

The agent generates strong secrets with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

(Generate two distinct values — one for `HERMES_SESSION_SECRET`, one for
`HERMES_AGENT_TOKEN`. Do not reuse.)

### 2.4 Verify Health

```bash
platform health
```

Expected output: every subsystem shows `✓` or at least `not_configured` —
never `error`.

### 2.5 Verify Config Audit Baseline

```bash
platform config history --limit 1
```

If the output is empty (no rows), the agent runs `platform init` again — the
baseline audit entry is required before any config change can be diffed or
rolled back. If a row is present, the agent notes the baseline `config_hash`;
every future `platform config set` / `promote` / `rollback` writes a new row
that diffs against this baseline.

### 2.6 Verify Symbol Registry

```bash
platform symbols list --active-only
```

If the output is empty, the agent runs:

```bash
platform symbols sync     # re-seed from config/default.yaml → portfolio.initial_symbols
```

The active symbols in the `symbols` DuckDB table are the runtime source of
truth for which symbols participate in `stream`, `monitor`, `synthesize`,
`optimize`, `rigor`, `shadow`, and `simulate`. The `--symbols` CLI argument on
those commands is **optional** and defaults to the active rows from this table
— the agent does not pass `--symbols` unless it wants to override the active
set.

### 2.7 Start the Trading Loop

The trading loop is 6 long-running processes. The agent starts each in its own
terminal session (or under a process supervisor like `systemd` or `tmux`) and
leaves them running. **The agent does not pass `--symbols`** — every command
defaults to the active symbols in the registry.

```bash
# Terminal 1 — dashboard (FastAPI + SPA bundle)
platform dashboard

# Terminal 2 — L0: heartbeat subscriber
platform ingest

# Terminal 3 — L2.8: active price monitor
platform monitor

# Terminal 4 — L4: signal synthesizer
platform synthesize

# Terminal 5 — L5: portfolio & risk engine
platform risk --equity 100000

# Terminal 6 — L3: execution engine (paper)
platform execute --equity 100000 --paper
```

### 2.8 Verify the Loop Is Alive

After starting all 6 processes, the agent waits 60 seconds and then verifies:

```bash
# 1. Health check — all subsystems connected
platform health

# 2. Dashboard reachable and logged in
#    (open http://127.0.0.1:8080 in a browser, sign in with
#    HERMES_ADMIN_USERNAME / HERMES_ADMIN_PASSWORD, and confirm the Status
#    page shows 6 green badges)
curl -s http://127.0.0.1:8080/health | head
```

If any badge is red, the agent consults §11 Troubleshooting before proceeding.

---

## 3. Connection Verification

Before the agent allows the trading loop to place any paper trade, it verifies
all 6 subsystems are connected. The agent performs this verification once
after the first startup, and again any time a credential or upstream URL is
changed.

### 3.1 Start the Dashboard and Check the Status Page

```bash
platform dashboard
```

The agent opens **http://127.0.0.1:8080** in a browser, signs in with
`HERMES_ADMIN_USERNAME` / `HERMES_ADMIN_PASSWORD`, and confirms the Status page
shows `connected` badges for all 6 subsystems:

| Subsystem | What it checks | Badge when ready |
|---|---|---|
| DuckDB | Opens read-only, counts tables | `connected` |
| Hermes Redis | Pings internal Redis | `connected` |
| Noble Trader Redis | Pings NT upstream Redis | `connected` |
| Supabase | REST API reachable | `connected` |
| Alpaca | `/v2/account` returns account info | `connected` |
| Hyperliquid | `/info` meta endpoint returns asset count | `connected` |

### 3.2 Test Alert Channels

```bash
platform alert-test
```

The agent verifies Discord webhook and Telegram bot are configured correctly.
If either channel is not configured, the agent will not receive kill-switch or
circuit-breaker notifications — fix before going live.

### 3.3 Test Redis Connectivity

```bash
redis-cli -u "$HERMES_REDIS_URL" ping       # → PONG
redis-cli -u "$NOBLE_TRADER_REDIS_URL" ping # → PONG (or NT-side echo)
```

The agent verifies both the internal Hermes Redis and the Noble Trader upstream
Redis respond to `PING` and that pub/sub round-trips work.

### 3.4 Dry-Run Ingest

```bash
platform ingest --dry-run
```

The agent verifies the Noble Trader heartbeat subscriber config is correct
without actually subscribing. This catches typos in `NOBLE_TRADER_REDIS_URL`
and malformed channel names before they cause silent failures in production.

---

## 4. Historical Data Backfill

On a cold start (fresh DuckDB), the agent backfills historical data before
enabling live trading. This is a one-time operation per fresh install; the
agent skips this section on subsequent restarts.

### 4.1 Pull Noble Trader Historical Data from Supabase

```bash
platform backfill --days-back 365
```

This pulls from:

- `nt_sweep_result` — weekly heavy + light sweeps (optimal brick_size / sl / tp
  per symbol)
- `nt_regime_log` — periodic regime snapshots (every 5–15 min per symbol)

Applies data quality checks on ingest:

- `sharpe_too_high` — flags absurd Sharpe ratios (> 20)
- `max_dd_zero` — flags impossible zero drawdown
- `profit_factor_zero` — flags suspicious zero profit factor
- `regime_strategy_disagree` — flags when regime says bull but strategy is
  losing (this is Hermes's value-add signal)

### 4.2 Pull Historical Market Data from Venues

```bash
platform backfill-market --symbol BTC/USD    --venue alpaca       --timeframe 1m --days-back 90
platform backfill-market --symbol BTC-PERP   --venue hyperliquid  --timeframe 1m --days-back 90
```

Stores in Parquet (partitioned by `venue/symbol/tf/date`) for offline analysis
and backtesting.

### 4.3 Verify Data

The agent opens the dashboard and confirms:

- **Heartbeats page** (`/heartbeats`) — should show historical NT heartbeats
- **PnL page** (`/pnl`) — should show "insufficient data" (no trades yet)

---

## 5. The Trading Loop

### 5.0 Symbol Registry

The trading universe is stored in the DuckDB `symbols` table — it is the
runtime source of truth for which symbols participate in `stream`, `monitor`,
`synthesize`, `optimize`, `rigor`, `shadow`, and `simulate`. The `--symbols`
argument on those commands is **optional** and defaults to all **active** rows
from this table. The agent does not pass `--symbols` unless it wants to
override the active set for a single run.

After running `platform init` (which seeds the table from
`config/default.yaml → portfolio.initial_symbols`), check what was seeded:

```bash
platform symbols list                 # all symbols, active + inactive
platform symbols list --active-only   # the ones that will be used by runs
```

To add a new symbol and confirm the venue can fetch a price for it:

```bash
platform symbols add ETH/USD --venue alpaca --asset-class crypto
platform symbols validate ETH/USD
```

To pause a symbol without losing its historical rows (positions, fills, PnL
remain joinable):

```bash
platform symbols deactivate SOL/USD --reason "paused for review"
# Later:
platform symbols activate SOL/USD
```

The same operations are available in the UI on the `/symbols` dashboard page
(add / activate / deactivate / validate / sync-from-config buttons, plus a
"Validate all active" sweep).

### 5.1 Trading Loop Overview

The trading loop is the real-time pipeline that runs continuously during
market hours. The agent keeps all 6 processes (§2.7) running for the entire
session.

```
Noble Trader
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│  L0: Upstream Ingestion (platform ingest)                   │
│  - Subscribe to NT Redis heartbeat channel                  │
│  - Parse + validate heartbeat schema                        │
│  - Dedup (SHA-256, 5s window)                              │
│  - Staleness check (reject >30s old)                       │
│  - Regime shift detection                                   │
│  - Write to DuckDB signal_heartbeats (immutable)           │
│  - Re-publish on signal.raw.hermes.{symbol}                │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  L2.8: Active Price Monitor (platform monitor)              │
│  - Stream live ticks from venue WebSockets                  │
│  - Build renko bars from ticks using NT brick_size          │
│  - Compute indicators (ATR, EMA, RSI, VWAP, Hurst)         │
│  - Detect price anomalies (5σ returns, spread widening)     │
│  - Watch stop-loss / take-profit for open positions         │
│  - Track cross-asset correlation                           │
│  - Monitor Hyperliquid funding rates                       │
│  - Write events to DuckDB price_monitor_events             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  L4: Signal Synthesis (platform synthesize)                 │
│  - Consume heartbeat from signal.raw.hermes.{symbol}        │
│  - Classify 7-state meta-regime (portfolio-level overlay)   │
│  - Construct renko bars from venue ticks                    │
│  - Analyze brick pattern (breakout, trend, reversal, etc.)  │
│  - Entry timing decision (enter_now / wait / block)         │
│  - Execution method selection (market / limit / TWAP)       │
│  - Sizing: trust NT effective_kelly × meta-regime multiplier│
│  - Produce BlendedSignal → DuckDB trade_signals_blended     │
│  - Publish on signal.blended.{symbol}                       │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  L5: Portfolio & Risk Engine (platform risk)                │
│  - Consume BlendedSignal from signal.blended.*              │
│  - Autonomy gate: classify action into tier 0-4             │
│  - Risk gate: 8 pre-trade checks                            │
│    1. Kill switch not active                                │
│    2. Volatility circuit breaker < Level 2                  │
│    3. Risk circuit breaker not tripped                      │
│    4. Account allocation ≤ max_gross_exposure               │
│    5. Risk fraction ≤ cap                                   │
│    6. Risk amount ≤ cap                                     │
│    7. Reward:risk ≥ min                                     │
│    8. Autonomy tier allows autonomous execution             │
│  - VaR/CVaR computation (pre and post-trade)                │
│  - Write RiskDecision → DuckDB risk_decisions               │
│  - Publish on risk.decision.{signal_id}                     │
│  - Periodic risk breaker checks (every 10s)                 │
│  - Account snapshots every 60s → DuckDB account_snapshots   │
└──────────────────────┬──────────────────────────────────────┘
                       │ (approved only)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  L3: Execution Engine (platform execute)                    │
│  - Consume RiskDecision from risk.decision.*                │
│  - Smart order router: create Order from decision           │
│    - market → single market order                           │
│    - limit_at_brick → single limit order                    │
│    - post_only → single post-only limit (maker rebate)      │
│    - twap_over_n_bricks → N child market orders             │
│    - iceberg → many small child orders                      │
│  - Paper trading engine: simulated fills with slippage      │
│    - Square-root impact model: slip = k × σ × √(part_rate)  │
│    - Venue-specific fees (maker/taker for Alpaca + HL)      │
│  - Order state machine: DRAFT→SUBMITTED→PARTIAL→FILLED     │
│  - On fill: register position in PortfolioStateService      │
│  - Write orders + order_events + fills → DuckDB             │
└──────────────────────┬──────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  Hermes Agent Decision Tree (runs within L3/L5)             │
│  - Evaluates existing positions every tick:                 │
│    1. HARD SL: pnl ≤ -1% → close (always fires)            │
│    2. Signal present?                                       │
│       YES → Agent manages (native 2.5% TP suspended):      │
│         a. Same direction:                                  │
│            - pnl > 0 + fading (2+ adverse bricks) → trail   │
│            - pnl ≥ 4.5% + not fading → early profit take   │
│            - no exit condition → hold                       │
│         b. Opposite direction:                              │
│            - strong (conviction ≥ 0.7) → flip close         │
│            - not strong → hold with native stops            │
│       NO → Native stops manage:                             │
│         - pnl ≥ 2.5% → close (native TP)                    │
│         - otherwise → hold with native SL/TP                │
│  - Evaluates new signals:                                   │
│    - Signal present + not blocked → enter_new               │
│    - No signal → skip                                       │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 Trading Loop Steps (Detailed)

#### Step 1: Noble Trader Publishes Heartbeat

Noble Trader (NT) runs its own strategy brain — it owns:

- Strategy direction (buy/sell/neutral)
- Renko brick_size optimization (weekly full sweep + 5/15min light sweeps)
- Per-asset 4×4 HMM regime detection (vol × trend = 16 cells)
- EV Engine v4 (p_win blending via log-odds pooling)
- Kelly + Masaniello sizing
- Signal generation (direction, entry, stop, TP)

NT publishes a heartbeat to its Redis channel every ~5 min (crypto/forex) /
~15 min (stocks/commodities).

#### Step 2: L0 Receives and Validates Heartbeat

**What happens**: The `platform ingest` process subscribes to NT's Redis
channel and processes each heartbeat.

**Sub-steps**:
1. **Receive**: async Redis subscriber with consumer group (survives disconnects)
2. **Parse**: JSON → `NobleTraderHeartbeat` Pydantic model (validates all 28+ fields)
3. **Dedup**: SHA-256 hash of `(symbol, ts, signal, entry, stop, TP)` — drops duplicates within 5s window
4. **Staleness check**: reject if heartbeat older than 30s (configurable)
5. **Regime shift detection**: if `regime_shift == "true"`, emit high-priority `regime.shift.{symbol}` event
6. **Persist**: write to DuckDB `signal_heartbeats` table (immutable provenance chain)
7. **Re-publish**: on internal `signal.raw.hermes.{symbol}` channel for downstream consumption

**Failure handling**: malformed payloads quarantined in `signal_heartbeats_quarantine`; heartbeat gap > 60s triggers `upstream.stale` alert.

#### Step 3: L2.8 Monitors Live Market Data

**What happens**: The `platform monitor` process streams live ticks from venue WebSockets and runs real-time analysis.

**Sub-steps**:
1. **Stream ticks**: Alpaca WebSocket (IEX feed) + Hyperliquid WebSocket (trades + L2 book)
2. **Build bars**: TickAggregator builds OHLCV bars at 6 timeframes (1s/5s/1m/5m/15m/1h)
3. **Compute indicators**: ATR(14), EMA(20/50/200), RSI(14), realized vol, VWAP deviation, Hurst exponent, return z-score
4. **Detect anomalies**: 5σ tick-to-tick returns, 5× spread widening, 3σ book imbalance flips
5. **Watch positions**: stop-loss / take-profit watcher (sub-50ms target), trailing stop engine, PnL warning
6. **Track correlation**: rolling 1h correlation between all symbol pairs → feeds meta-regime `risk_off` state
7. **Monitor funding**: Hyperliquid funding rate polling → `funding_spike` events for `funding_stress` state
8. **Write events**: all events to DuckDB `price_monitor_events` + publish on Redis `price.{event_type}.{symbol}`

#### Step 4: L4 Synthesizes Blended Signal

**What happens**: The `platform synthesize` process consumes the normalized heartbeat from L0 and enriches it with Hermes's own analysis.

**Sub-steps**:
1. **Receive heartbeat** from `signal.raw.hermes.{symbol}` internal Redis channel
2. **Classify meta-regime** using the 7-state classifier (rule-based waterfall):
   - Check crisis conditions first: `risk_off` (corr > 0.75), `funding_stress` (funding > 50% annualized), `liquidity_drained` (depth < 10th pct)
   - Check transition: `regime_transition` (upstream shift or high entropy)
   - Map upstream regime to normal states: `calm_trend`, `choppy_range`, `high_vol_breakout`
3. **Construct renko bars** from venue ticks using NT's `brick_size` (trusted from NT)
4. **Analyze brick pattern**: classify last N bricks into 12 patterns (breakout, trend, reversal, double top/bottom, pullback, consolidation)
5. **Entry timing decision** (Hermes's core value-add):
   - `calm_trend` + confirming pattern → `enter_now` (aggressive, market order)
   - `choppy_range` → `wait_for_brick_close` (patient, limit at brick)
   - `high_vol_breakout` + breakout → `wait_for_pullback` (cautious, limit at pullback)
   - `regime_transition` + breakout → `wait_for_retest` (defensive, limit at retest)
   - `risk_off` / `funding_stress` → `block`
   - `liquidity_drained` → `maker_only` (post_only)
6. **Execution method selection**: market / limit_at_brick / post_only / TWAP / iceberg based on size + regime
7. **Sizing** (trust + overlay, NOT re-derivation):
   - Baseline = equity × NT's `effective_kelly` × meta-regime sizing_multiplier
   - Drawdown adjustment = clip(1 - dd/max_dd, 0.25, 1.0)
   - Final = min(baseline × dd_adj, max_position_pct, max_notional, gross_exposure_headroom, risk_amount_cap / stop_distance)
8. **Produce BlendedSignal** → write to DuckDB `trade_signals_blended` + publish on `signal.blended.{symbol}`

**Key principle**: Hermes trusts NT's direction, entry, stop, TP, brick_size, and effective_kelly. It optimizes WHEN to enter and HOW to execute — not WHAT to trade.

#### Step 5: L5 Evaluates Risk

**What happens**: The `platform risk` process consumes BlendedSignals and applies the full risk gate.

**Sub-steps**:
1. **Receive BlendedSignal** from `signal.blended.*` Redis channel
2. **Autonomy gate**: classify the action into one of 5 tiers:
   - Tier 0: read-only (query, backtest) — autonomous
   - Tier 1: small trade (≤ $5k, ≤ 2% equity) — autonomous
   - Tier 2: config promotion — auto-promote + notify (per-key tier table; see §7.4)
   - Tier 3: large/novel trade (> $25k) — human approval required (4h timeout)
   - Tier 4: structural change — hard block (agent cannot do; human only)
   - Active hours check: tier 1 degrades to tier 3 outside market hours (crypto 24/7 exempt)
3. **Risk gate** (8 checks, all must pass for approval):
   - Kill switch not active
   - Volatility circuit breaker < Level 2 (ATR ratio check)
   - Risk circuit breaker not tripped (portfolio DD, daily loss, VaR, margin)
   - Account allocation ≤ max_gross_exposure_pct (caps size, doesn't reject)
   - Risk fraction ≤ cap (caps size)
   - Risk amount ≤ cap (caps size)
   - Reward:risk ≥ min (rejects if too low)
   - Autonomy tier allows autonomous execution (rejects if tier 3+ needs human)
4. **VaR/CVaR computation**: historical VaR at 99% confidence, pre and post-trade
5. **Produce RiskDecision** → write to DuckDB `risk_decisions` + publish on `risk.decision.{signal_id}`
6. **Periodic checks** (every 10s): portfolio DD, daily loss, VaR breach, margin proximity → `circuit_breaker_events`
7. **Account snapshots** (every 60s): equity, exposure, drawdown → `account_snapshots`

> **Post-Phase-10 wiring:** `PortfolioRiskEngine` now also integrates three ops-grade components on every signal path:
> - **CircuitBreakerManager** — initialized from `config/default.yaml` → `circuit_breakers.manager`. On every `evaluate_signal()`, checks all 8 categories (portfolio_exposure, position_size, daily_loss, var, drawdown, funding_rate, consecutive_losses, trip_frequency), applies a size multiplier (0.0–1.0) to the signal, blocks the entry if multiplier=0, records trips for frequency tracking, and emits alerts. On every `check_risk_breakers()` (the 10s periodic loop), it re-evaluates so transient breaches self-heal via `cooldown_sec`.
> - **DeadMansSwitch** — started on `engine.start()`. `heartbeat()` is called on every `evaluate_signal()` AND every `check_risk_breakers()`. If the risk engine stops making progress for 60s (wedge, deadlock, crash), the DMS activates the kill switch, sends an EMERGENCY alert, and optionally flattens all positions. Auto-deactivates when heartbeats resume.
> - **AlertManager** — started on `engine.start()`. Sends alerts on CB trips (WARNING for `reduce_*`, CRITICAL for `block_entries`/`halt_all`/`liquidate`), kill switch activation (EMERGENCY), and DMS activation (EMERGENCY). Graceful no-op when Discord/Telegram webhooks are unconfigured.
>
> New stats exposed: `cb_manager_trips`, `dms_activations`, `alerts_sent`. New getters: `get_cb_manager()`, `get_dms()`, `get_alert_manager()`.

**Key principle**: Risk gate caps size for soft limits (doesn't always reject). Only hard blockers (kill switch, circuit breakers, low R:R, autonomy tier 3+) cause rejection.

#### Step 6: L3 Executes Order (Paper Trading)

**What happens**: The `platform execute` process consumes approved RiskDecisions and executes paper trades.

**Sub-steps**:
1. **Receive RiskDecision** from `risk.decision.*` Redis channel
2. **Fetch original BlendedSignal** from DuckDB `trade_signals_blended` (for entry/stop/TP details)
3. **Smart order router** creates Order(s) from decision:
   - `market` → single market order (IOC)
   - `limit_at_brick_boundary` → single limit order (GTC)
   - `post_only` → single post-only limit order (maker rebate)
   - `twap_over_n_bricks` → single parent order with algo="twap" (paper engine splits into 3 children)
   - `iceberg` → single parent order with algo="iceberg" (paper engine splits into 10% children)
4. **Paper trading engine** simulates fills:
   - Market: fill at current_price ± slippage (square-root impact model)
   - Limit: fill at limit_price (assume fills for paper)
   - Post-only: fill at limit_price with maker rebate
   - TWAP: 3 child fills with delay between each
   - Iceberg: 10 child fills at 10% each
   - Venue-specific fees: Alpaca (0/1 bps maker/taker), Hyperliquid (0.5/2 bps)
5. **Order state machine**: DRAFT → SUBMITTED → PARTIAL → FILLED / CANCELED / REJECTED
6. **On fill**: register position in PortfolioStateService (deducts cash, tracks exposure)
7. **Write to DuckDB**: `orders`, `order_events`, `fills` tables

> **Post-Phase-10 wiring:** `ExecutionEngine` now also integrates four agent-grade components on every trade:
> - **HermesDecisionTree** — on each new signal, evaluates existing positions for that symbol before placing a new order: checks SL / TP / early-profit / trail / flip / hold per the validated Phase 9 decision tree, and closes the position if the tree says to. Without this, positions would hold blindly between signals.
> - **DecisionBranchTracker** — on fill, calls `record_entry(AgentAction)` to log the entry decision; on position close, calls `record_exit(AgentAction)` to log the exit decision. This is what feeds `analyze_branch_performance()` and `get_threshold_feedback()` with real trade data instead of empty results.
> - **PnLService** — on position close, records realized PnL with full attribution (directional / timing / sizing / regime decomposition) to the `pnl_realized` DuckDB table.
> - **DecisionJournalWriter** — on position close, writes a postmortem with entry thesis + lessons learned + hypothesis IDs. Was dead code before this wiring.
>
> A `_signal_map` / `_position_signals` dict pair tracks which signal created each position so **entry alpha** (bps better/worse than the NT-suggested entry) can be computed at close time and fed into `record_exit()`. `CircuitBreakerManager` is also optionally accepted (via the `cb_manager` constructor param) so the engine can record trade win/loss into the `consecutive_losses` rolling window.
>
> New stats exposed: `positions_closed`, `branch_attributions`, `postmortems_written`, `pnl_records`. New getters: `get_branch_tracker()`, `get_decision_tree()`, `get_pnl_service()`, `get_journal_writer()`.

#### Step 7: Hermes Agent Manages Position

**What happens**: The agent decision tree evaluates every open position on each new signal/tick.

**Decision tree** (validated — see Phase 9 for full validation):
1. **HARD stop-loss**: pnl ≤ -1% → close immediately (always fires, overrides everything)
2. **Signal present?**
   - **YES** → Agent takes over (native 2.5% TP suspended):
     - Same direction:
       - pnl > 0 + 2+ adverse renko bricks → **trail stop** (protect gains, trend might resume)
       - pnl ≥ 4.5% + not fading → **early profit take** (lock in outsized gain)
       - no exit condition → **hold** (trend still confirmed)
     - Opposite direction:
       - strong signal (conviction ≥ 0.7 + regime confirms) → **flip close** (close + reverse)
       - not strong → **hold with native stops** (don't flip on weak signals)
   - **NO** → Native stops manage:
     - pnl ≥ 2.5% → **close** (native take-profit)
     - otherwise → **hold with native SL/TP**

**Key insight**: When a same-direction signal is present, the agent suspends the native 2.5% TP and uses its own 4.5% threshold — letting profits run further when the trend is still confirmed.

#### Step 8: PnL Recording

**What happens**: When a position is closed, PnL is recorded with full attribution.

**Sub-steps**:
1. **Compute gross PnL**: (exit - entry) × qty, signed by direction
2. **Compute net PnL**: gross - fees - slippage + funding
3. **Compute R-multiple**: net_pnl / risk_amount
4. **Attribution** (decompose PnL into components):
   - Directional: PnL from pure price move
   - Timing: PnL from Hermes's entry timing (NT_entry vs actual_entry)
   - Sizing: PnL from position sizing deviation
   - Regime: PnL attributed to regime multiplier
5. **Write to DuckDB**: `pnl_realized` table
6. **Update drawdown tracker**: peak equity, current DD, max DD, time-in-DD, ulcer index

#### Step 9: Position Closed → Trade Journal

**What happens**: Every closed trade gets an automated postmortem.

**Sub-steps**:
1. **Generate postmortem**: automated analysis of what happened
   - Outcome (profitable/unprofitable)
   - PnL decomposition (directional vs timing vs regime)
   - Regime performance context
   - Entry timing assessment (positive/negative alpha)
2. **Extract lessons**: actionable takeaways
   - "Entry timing was negative — review entry strategy for this regime"
   - "Low win rate in choppy_range — consider skipping signals in this regime"
3. **Write to DuckDB**: `trade_journal` table with postmortem + lessons

---

## 6. End-of-Day Analysis & Self-Learning

This is the agent's self-learning loop. The agent **owns its own cron schedule
on its host** — these cron entries are NOT committed to the repository. The
agent installs them via `crontab -e` on the host it runs on. The complete
schedule is in
[Appendix: Cron Schedule Summary](#appendix-cron-schedule-summary); the EOD
entries are reproduced in §6.1 below.

### 6.1 EOD Cron Schedule

The agent installs the following cron entries on its host (PT = Pacific Time,
market close):

```bash
crontab -e
```

Add:

```bash
# EOD analysis — weekdays 16:30 PT
30 16 * * 1-5 cd /path/to/noble-trader-agent && .venv/bin/platform agent --eod >> logs/eod.log 2>&1
# Shadow promotion check — weekdays 16:35 PT
35 16 * * 1-5 cd /path/to/noble-trader-agent && .venv/bin/platform agent --check-shadow-promotions >> logs/eod.log 2>&1
# Underperformance check — weekdays 16:40 PT
40 16 * * 1-5 cd /path/to/noble-trader-agent && .venv/bin/platform agent --check-underperformance >> logs/eod.log 2>&1
```

The three commands run in sequence, 5 minutes apart, to ensure each completes
before the next starts. All output is appended to `logs/eod.log`.

### 6.2 What EOD Does

`platform agent --eod` calls `SelfLearningLoop.run_eod_analysis()` which
executes these 5 steps:

1. **Observe**: pull all closed trades from `pnl_realized` for today
2. **Attribute**: decompose PnL by meta-regime (win rate, total PnL, avg PnL per regime)
3. **Postmortems**: write automated postmortems for each trade with lessons
4. **Hypothesize**: generate improvement hypotheses from regime performance:
   - Low win-rate regime (< 40%, 3+ trades) → propose reducing sizing multiplier
   - High win-rate regime (> 65%, 3+ trades, positive PnL) → propose increasing sizing multiplier
5. **Store hypotheses**: write to `hermes_hypotheses` table (status = `proposed`)

### 6.3 Shadow Promotion Check

`platform agent --check-shadow-promotions` runs at 16:35 PT, 5 minutes after
EOD. For each hypothesis in `shadow` state, it checks whether the shadow
config's live Sharpe ≥ 80% of its backtest Sharpe over the shadow window. If
yes:

- **Tier 2 keys** (13 keys, e.g. `entry.brick_confirmation_count`,
  `execution.limit_offset_bps`) — **auto-promoted** via
  `platform config promote --hypothesis-id ID --change k=v`. The AutonomyGate
  permits agent promotion of tier 2 keys. A notification is sent; no human
  action required.
- **Tier 3 keys** (8 keys, e.g. `circuit_breakers.volatility.vol_mult_threshold`,
  `account.daily_loss_limit_pct`) — **blocked**. The hypothesis is marked
  `awaiting_human`. The agent sends a notification asking a human to run
  `platform config set <key> <value> --rationale "..." --author <human>`.
- **Tier 4 keys** (9 keys, e.g. `account.max_gross_exposure_pct`,
  `venues.*.enabled`, `autonomy.tier_*.max_notional_usd`) — **hard blocked**,
  structural, human-only. Same `awaiting_human` flow.

### 6.4 Underperformance Check

`platform agent --check-underperformance` runs at 16:40 PT. For each config
promotion with `source = 'hermes'` in `config_history` that has been live for
≥ 14 days, the agent computes live Sharpe over the live window and compares it
to the backtest Sharpe stored in the promotion's rationale.

If **live Sharpe < 50% of backtest Sharpe**, the agent **auto-rolls back** to
the previous config hash via `rollback_config()`. The rollback is itself
written to `config_history` with
`rationale = "auto-rollback: live Sharpe X < 50% of backtest Y over N days"`
and `author = "hermes"`. A notification is sent.

### 6.5 Hypothesis Lifecycle

```
proposed → backtested → shadow → live
                    ↘ rejected
                         ↗ retired
```

- **Propose**: EOD analysis generates hypothesis (e.g., "Reduce sizing in choppy_range")
- **Backtest**: run through simulation engine with 6 rigor checks
- **Shadow**: paper-trade in parallel at 10% of live size for 7 days
- **Promote**: auto-promote if shadow Sharpe ≥ 80% of backtest Sharpe (tier 2
  only — see §6.3)
- **Reject**: if rigor checks fail or shadow underperforms
- **Retire**: if promoted config underperforms in live for 14 days →
  auto-rollback (see §6.4)

Promotion is gated by `AutonomyGate.classify_config_change(key_path, caller)`:

- `caller='hermes'` (used by `platform config promote`) — tier 2 allowed,
  tier 3/4 blocked with exit 1
- `caller='human'` (used by `platform config set`) — all tiers allowed, tier
  3/4 prints a warning

See [§7.4 Config Management Workflow](#74-config-management-workflow) for the
per-key tier table and the exact CLI commands.

### 6.6 Optimization Sweep (Weekly Cron)

The agent runs the optimization sweep weekly via cron (Saturday 02:00 PT — see
[Appendix: Cron Schedule Summary](#appendix-cron-schedule-summary)). The
`--symbols` argument is **optional** and the agent does not pass it — the
sweep defaults to all active symbols.

```bash
# Saturday 02:00 PT
0 2 * * 6 cd /path/to/noble-trader-agent && .venv/bin/platform optimize --days-back 90 --n-trials 200 >> logs/optimize.log 2>&1
```

**What it does**:
1. Run baseline backtest ("blindly execute at market")
2. Bayesian optimization (Optuna TPESampler) over 17 parameters:
   - Entry strategies per meta-regime
   - Brick confirmation count, pullback depth
   - Execution method, TWAP N, iceberg %, limit offset
   - Trailing stop method, ATR mult, brick count
   - Exit strategy, momentum threshold
   - Sizing multipliers per regime
3. Each trial: run backtest → compute entry alpha vs baseline → 6 rigor checks
4. Accept only trials that: pass rigor AND beat baseline
5. Top candidates enter shadow mode (then flow through §6.3 → §6.4)

### 6.7 Counterfactual Analysis (On Demand)

The agent runs counterfactuals on demand when investigating a specific closed
trade:

```bash
platform counterfactual --trade-id <uuid>
```

Replays the trade under alternative entry strategies (enter_now vs
wait_for_brick_close vs wait_for_pullback) and computes what the PnL would
have been.

### 6.8 Statistical Rigor Checks (Weekly Cron)

The agent runs rigor checks weekly via cron (Saturday 03:00 PT) — see
[Appendix: Cron Schedule Summary](#appendix-cron-schedule-summary).

```bash
# Saturday 03:00 PT
0 3 * * 6 cd /path/to/noble-trader-agent && .venv/bin/platform rigor --days-back 90 >> logs/rigor.log 2>&1
```

6 checks (all must pass for a strategy to be accepted):
1. **Walk-forward validation**: OOS Sharpe within 80% of IS Sharpe (purged k-fold CV)
2. **Deflated Sharpe > 1.0**: Bailey & López de Prado multiple-testing correction
3. **Monte Carlo 5th percentile > 0**: bootstrap resampling, 1000 iterations
4. **Bootstrap CI lower bound > 0**: 1000 bootstrap samples, 5th percentile
5. **Regime coverage**: positive expectancy in 4+ of 7 meta-regimes
6. **Capacity check**: backtested notional < 10× median daily volume

---

## 7. Ongoing Operations

The agent owns its weekly and monthly cron schedule on its host (NOT in the
repo). The complete schedule is in
[Appendix: Cron Schedule Summary](#appendix-cron-schedule-summary).

### 7.1 Daily Startup

The agent does **not** restart the 6 trading-loop processes every day. They
run continuously. The agent only restarts them after:

- A config change that requires a restart (see §7.4 — after `platform config
  set` / `promote` / `rollback`, restart all 6 processes from §2.7)
- A host reboot or crash (run §2.4 through §2.8)
- A deploy or code change (rebuild SPA with `cd dashboard && npm run build`,
  then restart all 6 processes)

If a restart is needed, the agent follows §2.7 → §2.8 exactly. There is no
separate "daily startup checklist" — at-startup is at-startup.

### 7.2 Weekly Cron (Saturday)

The agent installs the following cron entries on its host:

```bash
# Weekly optimization sweep — Saturday 02:00 PT
0 2 * * 6 cd /path/to/noble-trader-agent && .venv/bin/platform optimize --days-back 90 --n-trials 200 >> logs/optimize.log 2>&1
# Weekly rigor checks — Saturday 03:00 PT
0 3 * * 6 cd /path/to/noble-trader-agent && .venv/bin/platform rigor --days-back 90 >> logs/rigor.log 2>&1
# DuckDB VACUUM — Saturday 04:00 PT
0 4 * * 6 cd /path/to/noble-trader-agent && .venv/bin/python -c "import duckdb; duckdb.connect('data/hermes.duckdb').execute('VACUUM')" >> logs/vacuum.log 2>&1
```

The agent also reviews `circuit_breaker_events` weekly to look for patterns
(this is a manual review the agent performs when triaging incidents, not a
cron job):

```bash
# Review alert history (manual, not cron)
duckdb data/hermes.duckdb -c "SELECT * FROM circuit_breaker_events WHERE ts >= now() - INTERVAL '7 days'"
```

### 7.3 Monthly Cron (1st of Month)

The agent installs the following cron entries on its host:

```bash
# Monthly maintenance — 1st of month 03:00 PT
0 3 1 * * cd /path/to/noble-trader-agent && .venv/bin/platform agent --monthly-maintenance >> logs/monthly.log 2>&1
# Monthly meta-regime retrain — 1st of month 04:00 PT
0 4 1 * * cd /path/to/noble-trader-agent && .venv/bin/platform meta-regime --retrain >> logs/monthly.log 2>&1
```

`platform agent --monthly-maintenance` performs:

- Archive old Parquet data (> 90 days) to cold storage
- DuckDB VACUUM
- Hypothesis review (list stuck hypotheses > 14 days in shadow)
- DR test (run a scenario from `docs/dr_runbook.md`)
- HMM retrain reminder (logs a note — the actual HMM lives upstream in Noble
  Trader; this is a reminder to coordinate with the NT operator)
- Rotation reminders (every 90 days per security policy) for API keys,
  Supabase anon/publishable key, Hyperliquid wallet

`platform meta-regime --retrain` recalibrates the rule-based meta-regime
classifier thresholds from the trailing 30-day distribution. Hermes's
meta-regime classifier is **rule-based** (the HMM lives upstream in Noble
Trader). The retrain proposes threshold changes; because the threshold keys
are **tier 3** (human approval required), the retrain only **proposes** the
changes — it does not auto-apply them. A human must run `platform config set
<meta_regime.thresholds.*> <value> --rationale "monthly retrain proposal"`.
The agent sends a notification with the proposed values.

### 7.4 Config Management Workflow

This section replaces the old "Config Tuning Guide". The agent does not edit
`config/default.yaml` directly. Every config change flows through
`platform config` and is recorded in the `config_history` audit table.

#### Manual change (human path)

```bash
platform config set <key.path> <value> --rationale "why this change" --author <human>
```

- Single-key change. Always approved for humans (tier 2/3/4 all allowed).
- Tier 3/4 keys print a warning but still apply.
- Writes a new row to `config_history` with `source='human'`, the rationale,
  the author, and the diff against the previous hash.

#### Optimization promotion (agent path)

```bash
platform config promote \
  --hypothesis-id <ID> \
  --change key1=val1 \
  --change key2=val2 \
  --rationale "shadow Sharpe X ≥ 80% of backtest Y; tier 2 auto-promote"
```

- Multi-key change, used by `platform agent --check-shadow-promotions` and
  by the agent when promoting a hypothesis.
- `--author` defaults to `hermes` (the agent).
- AutonomyGate classifies each key:
  - All keys tier 2 → applied, audit row with `source='hermes'`.
  - Any key tier 3 → **blocked** (exit 1). The agent must mark the hypothesis
    `awaiting_human` and send a notification. A human then uses
    `platform config set` for each tier 3 key.
  - Any key tier 4 → **hard blocked** (exit 1). Same `awaiting_human` flow.

#### View audit trail

```bash
platform config history              # last 20 entries
platform config history --limit 50   # last 50 entries
platform config history --json       # machine-readable
```

#### Diff two configs

```bash
platform config diff <hash_a> <hash_b>
```

Shows field-level diff between two config hashes.

#### Rollback

```bash
platform config rollback <target_hash> --rationale "live Sharpe collapsed after promotion"
```

Restores the config identified by `<target_hash>`, writes a new row to
`config_history` with `source='rollback'` and the provided rationale. Used by
both `platform agent --check-underperformance` (auto-rollback, `author='hermes'`)
and by humans (manual rollback, `author=<human>`).

#### Per-key tier table

The tier of each config key is defined in `config/default.yaml → autonomy.config_keys`.
Uncategorized keys default to **tier 3** (conservative).

| Tier | How many keys | Who can change | Behavior |
|---|---|---|---|
| Tier 2 | 13 (e.g. `signal.staleness_ms`, `execution.limit_offset_bps`, `entry.brick_confirmation_count`, `position_management.trailing.atr_mult`, `renko.rolling_window_bricks`) | agent + human | Auto-promote + notify |
| Tier 3 | 8 (e.g. `circuit_breakers.volatility.vol_mult_threshold`, `account.daily_loss_limit_pct`, `meta_regime.hmm_n_components`, all `meta_regime.thresholds.*`) | human only | Agent blocked, awaiting_human |
| Tier 4 | 9 (e.g. `account.max_gross_exposure_pct`, `account.max_leverage_total`, `circuit_breakers.kill_switch.auto_triggers`, `venues.*.enabled`, `autonomy.tier_*.max_notional_usd`) | human only | Hard block, structural |

**Rule**: Never change more than one parameter (or one hypothesis) at a time.
The audit trail must remain diffable. After any config change, the agent
restarts all 6 trading-loop processes (§2.7) so the new config is loaded —
config is read at startup, not hot-reloaded.

---

## 8. Disaster Recovery

When things go wrong, the agent takes these prescriptive actions.

### 8.1 Kill Switch (Emergency Stop)

```bash
# Activate (halts all new entries, cancels orders, optionally flattens)
redis-cli PUBLISH agent.command '{"action": "flatten"}'

# Deactivate (resume trading)
redis-cli PUBLISH agent.command '{"action": "resume"}'
```

The agent activates the kill switch when:

- Daily loss limit hit (configurable in `config/default.yaml`)
- Portfolio drawdown exceeds `account.max_portfolio_drawdown_pct`
- Dead man's switch fires (see §8.2)
- A circuit breaker hits `halt_all` or `liquidate` severity

### 8.2 Dead Man's Switch

Automatically activates if no heartbeat from any component for 60 seconds.
Triggers:

1. Kill switch activation
2. Cancel all open orders
3. Optionally flatten all positions
4. Send critical alert to Discord/Telegram

The agent does not need to do anything to arm the DMS — it is armed on
`engine.start()` and disarmed automatically when heartbeats resume.

### 8.3 Common Scenarios

See `docs/dr_runbook.md` for 7 detailed scenarios:

1. Process crash
2. DuckDB corruption
3. Redis disconnect
4. Noble Trader upstream down
5. Venue API down
6. Daily loss limit hit
7. **Config change rollback** — the agent runs:
   ```bash
   platform config history --limit 5                # find the bad hash
   platform config diff <bad_hash> <good_hash>      # confirm what changed
   platform config rollback <good_hash> --rationale "revert <reason>"
   ```
   Then restarts all 6 trading-loop processes (§2.7).

### 8.4 Forensic Replay

```bash
# Replay any time period to see exactly what happened
platform replay --start 2026-07-01T14:00:00 --end 2026-07-01T15:00:00
```

Reconstructs the full timeline from DuckDB: heartbeats, signals, risk
decisions, orders, fills, monitor events, circuit breaker events, and
account snapshots — all in chronological order. The `--symbols` argument is
optional and defaults to all symbols.

---

## 9. Dashboard Reference

The dashboard is served by the FastAPI backend at `http://127.0.0.1:8080`.
The SPA bundle is built by `cd dashboard && npm run build` and served as
static files from `dashboard/dist/` via a `StaticFiles` mount — this is the
**single-host deploy** model. Same-origin cookies, no CORS, no separate
static host.

The agent signs in with `HERMES_ADMIN_USERNAME` / `HERMES_ADMIN_PASSWORD`
(browser session cookie) or with `HERMES_AGENT_TOKEN` as
`Authorization: Bearer <token>` (programmatic agent access).

There are 12 Jinja2 pages and 8 SPA pages.

### 9.1 Jinja2 Pages (server-rendered, 12)

| Page | URL | What it shows |
|---|---|---|
| **Status** | `/` | Connection badges, ingest stats, recent heartbeats (auto-refresh 10s) |
| **Heartbeats** | `/heartbeats` | Full NT heartbeat table with all 28+ fields, symbol filter |
| **Signals** | `/signals` | Blended signals (L4 output) with meta-regime, entry strategy, sizing |
| **Portfolio** | `/portfolio` | Account metrics (equity, cash, leverage, DD, VaR) + risk decisions |
| **Orders** | `/orders` | Order lifecycle (status, fills, fees, slippage) + fills table |
| **PnL** | `/pnl` | Tear sheet (Sharpe, Sortino, Calmar, DD, win rate, by-regime) + equity curve |
| **Backtest** | `/backtest` | Backtest run history (heartbeats replayed, signals, orders, return) |
| **Optimize** | `/optimize` | Simulation runs (entry alpha, rigor checks, accepted/rejected) |
| **Agent** | `/agent` | Decision tree diagram + hypotheses + trade journal with postmortems |
| **Monitor** | `/monitor` | Live price monitor stats, positions, correlation matrix, events |
| **Config** | `/config` | Loaded config (secrets redacted) |
| **Health JSON** | `/health` | JSON health endpoint (for monitoring/CI) |

### 9.2 SPA Pages (Vite + React + DaisyUI, 8)

These pages replace their Jinja2 equivalents with interactive charts
(Recharts), reactive data (TanStack Query), and live WebSocket streams.
The remaining 4 Jinja2 pages (Heartbeats, Orders, Optimize, Config) stay
server-rendered indefinitely — they are pure tables with no UX gain from
migration.

| SPA Page | Route | What it adds over Jinja2 |
|---|---|---|
| **Dashboard** | `/spa/` | 6-cell account grid + equity curve (Recharts area chart) + performance stats + open positions + risk/VaR card + recent risk decisions |
| **Status** | `/spa/status` | Polls `/api/status` every 10s via TanStack Query (no full-page reload) |
| **Monitor** | `/spa/monitor` | First page to consume the WebSocket infrastructure — live tick stream |
| **Symbols** | `/spa/symbols` | Full CRUD UI mirroring Jinja2 `/symbols` with optimistic mutations |
| **PnL** | `/spa/pnl` | Full tear sheet — equity curve (1000 pts), by-regime breakdown |
| **Backtest** | `/spa/backtest` | Runs table with drill-down: tear sheet + equity curve + trade list |
| **Portfolio** | `/spa/portfolio` | 4 chart types (pie, bars, histogram, stat grids) + full risk decisions table |
| **Agent** | `/spa/agent` | Collapsible decision tree viz + hypotheses table + trade journal |

---

## 10. CLI Command Reference

37 commands total. The `--symbols` argument on `stream`, `monitor`,
`synthesize`, `optimize`, `rigor`, `shadow`, and `simulate` is **optional**
and defaults to all active symbols in the registry.

| Command | Purpose |
|---|---|
| `platform init` | Bootstrap: load config, open DuckDB, apply schema (9 migrations, 24 tables), seed symbols, write baseline config_history entry |
| `platform health` | Check health of all subsystems |
| `platform version` | Print version |
| `platform dashboard` | Start web dashboard at http://127.0.0.1:8080 |
| `platform config show` | Print loaded config (secrets redacted) |
| `platform config set <key> <value> --rationale "..."` | Manual single-key change (human path); writes audit row |
| `platform config history [--limit N] [--json]` | View config audit trail |
| `platform config diff <hash_a> <hash_b>` | Field-level diff between two configs |
| `platform config rollback <hash> --rationale "..."` | Restore a previous config; writes audit row |
| `platform config promote --hypothesis-id ID --change k=v [...] --rationale "..."` | Multi-key agent promotion; tier 2 only (tier 3/4 blocked) |
| `platform symbols list [--active-only] [--venue V] [--asset-class C] [--json]` | List symbols in registry |
| `platform symbols add <symbol> --venue V --asset-class C [...]` | Add a new symbol |
| `platform symbols activate <symbol>` | Reactivate a paused symbol |
| `platform symbols deactivate <symbol> --reason "..."` | Soft-delete (historical rows preserved) |
| `platform symbols validate <symbol>` | Live-probe venue's `get_current_price` |
| `platform symbols sync [--overwrite-active]` | Re-seed from `config/default.yaml → portfolio.initial_symbols` |
| `platform symbols show <symbol>` | Show one symbol's full row |
| `platform ingest [--dry-run]` | Start Noble Trader heartbeat subscriber (L0) |
| `platform backfill --days-back N` | Pull historical heartbeats from Supabase |
| `platform stream` | Stream live market data from venue WebSockets |
| `platform monitor` | Start Active Price Monitor (L2.8) |
| `platform backfill-market --symbol S --venue V --timeframe T --days-back N` | Pull historical bars from venue REST API |
| `platform synthesize` | Start L4 Signal Synthesizer (BEV combiner) |
| `platform risk --equity N` | Start L5 Portfolio & Risk Engine |
| `platform execute --equity N --paper` | Start L3 Execution Engine (paper trading) |
| `platform pnl` | Generate PnL tear sheet |
| `platform backtest` | Run backtest by replaying historical heartbeats |
| `platform rigor [--days-back N]` | Run statistical rigor checks (6 checks) |
| `platform optimize [--days-back N] [--n-trials N]` | Run entry/execution optimization sweep (Optuna) |
| `platform shadow` | Start shadow mode for a new config |
| `platform simulate` | Run a simulation (counterfactual what-if) |
| `platform counterfactual --trade-id <uuid>` | Run counterfactual analysis on a closed trade |
| `platform agent [--eod] [--list-hypotheses] [--check-shadow-promotions] [--check-underperformance] [--monthly-maintenance]` | Hermes Agent — self-learning loop, hypothesis tracking, decision journal. Daily/weekly/monthly tasks run via agent-owned cron. |
| `platform meta-regime [--retrain]` | Meta-regime classifier management. `--retrain` recalibrates rule thresholds from 30-day distribution (monthly cron; proposes tier-3 changes for human approval). |
| `platform replay --start T --end T` | Replay a historical session for forensic analysis |
| `platform alert-test` | Send a test alert to Discord/Telegram |
| `platform load-test` | Run a load test on the DuckDB writer |

---

## 11. Troubleshooting Guide

### "DuckDB init failed"
- Delete `data/hermes.duckdb` and `data/hermes.duckdb.wal`
- Run `platform init` again
- If persists: check DuckDB version (`pip show duckdb`)

### "Redis unreachable"
- Linux: `sudo systemctl restart redis`
- Docker: `docker start hermes-redis`
- Test: `redis-cli ping`

### "No heartbeats received"
- Verify NT Redis URL in `.env` is correct (no placeholders)
- Verify NT is running and publishing to the channel
- Check `signal_heartbeats_quarantine` table for malformed payloads
- Run `platform ingest --dry-run` to verify config

### "No signals produced"
- Verify `platform ingest` is running (heartbeats flowing)
- Verify `platform synthesize` is running and subscribed to correct channels
- Check DuckDB `signal_heartbeats` table has rows
- Check logs for `heartbeat_parse_failed` warnings

### "All signals rejected"
- Check `risk_decisions` table for `limits_hit` column
- Common causes: kill switch active, circuit breaker tripped, daily loss hit
- Check `circuit_breaker_events` table for what tripped
- Run `platform health` to see subsystem status

### "Dashboard shows no data"
- Ensure DuckDB has been initialized (`platform init`)
- Ensure at least one process (ingest/monitor/synthesize) has run
- Check the specific page — each page reads from a different table

### "Config change not taking effect"
- Config is loaded at startup — **restart all 6 processes** (see §2.7) after
  any `platform config set` / `promote` / `rollback`.
- **There is no hot-reload.** The old `redis-cli PUBLISH config.update ...`
  trick no longer applies — use `platform config set` and restart.
- Verify the change was recorded: `platform config history --limit 1`
- Verify the loaded config: `platform config show`

### "Hypotheses not being generated"
- Run `platform agent --eod` manually
- Ensure there are closed trades in `pnl_realized` table
- EOD analysis only generates hypotheses when it finds patterns (low win rate in a regime with 3+ trades)

### "Optimization is slow"
- Reduce `--n-trials` (default 200)
- Reduce `--days-back` (default 90)
- Use fewer symbols (pass `--symbols` explicitly to override the active set)
- Each trial runs a full backtest — expect ~1–5 seconds per trial depending on data volume

### "Shadow promotion didn't fire"
- Check the hypothesis tier — tier 3/4 keys are **blocked**, not auto-promoted
  (see §6.3). The hypothesis is marked `awaiting_human`; a human must run
  `platform config set` for each blocked key.
- Run `platform agent --check-shadow-promotions` manually to see the
  per-hypothesis action (`promoted`, `blocked`, `awaiting_human`).

### "Auto-rollback didn't fire"
- The promotion must have `source='hermes'` in `config_history` (manual
  `config set` changes are not auto-rolled back).
- The promotion must be ≥ 14 days live.
- The backtest Sharpe must be parseable from the rationale string. If
  `--rationale` was empty or didn't include a Sharpe number, the
  underperformance check skips that promotion.

---

## Appendix: Cron Schedule Summary

The complete cron schedule the agent installs on its host via `crontab -e`.
**This schedule is NOT committed to the repository.** The agent owns it.

```bash
# === EOD analysis — weekdays 16:30 PT ===
30 16 * * 1-5 cd /path/to/noble-trader-agent && .venv/bin/platform agent --eod >> logs/eod.log 2>&1
# === Shadow promotion check — weekdays 16:35 PT ===
35 16 * * 1-5 cd /path/to/noble-trader-agent && .venv/bin/platform agent --check-shadow-promotions >> logs/eod.log 2>&1
# === Underperformance check — weekdays 16:40 PT ===
40 16 * * 1-5 cd /path/to/noble-trader-agent && .venv/bin/platform agent --check-underperformance >> logs/eod.log 2>&1

# === Weekly optimization — Saturday 02:00 PT ===
0 2 * * 6 cd /path/to/noble-trader-agent && .venv/bin/platform optimize --days-back 90 --n-trials 200 >> logs/optimize.log 2>&1
# === Weekly rigor — Saturday 03:00 PT ===
0 3 * * 6 cd /path/to/noble-trader-agent && .venv/bin/platform rigor --days-back 90 >> logs/rigor.log 2>&1
# === Weekly DuckDB VACUUM — Saturday 04:00 PT ===
0 4 * * 6 cd /path/to/noble-trader-agent && .venv/bin/python -c "import duckdb; duckdb.connect('data/hermes.duckdb').execute('VACUUM')" >> logs/vacuum.log 2>&1

# === Monthly maintenance — 1st of month 03:00 PT ===
0 3 1 * * cd /path/to/noble-trader-agent && .venv/bin/platform agent --monthly-maintenance >> logs/monthly.log 2>&1
# === Monthly meta-regime retrain — 1st of month 04:00 PT ===
0 4 1 * * cd /path/to/noble-trader-agent && .venv/bin/platform meta-regime --retrain >> logs/monthly.log 2>&1
```

**Notes:**

- All entries assume the agent installed the repo at
  `/path/to/noble-trader-agent` and the venv at `.venv/`. Adjust the path to
  match the actual install location.
- All log output goes to `logs/` under the repo. The agent is responsible for
  log rotation on its host (e.g. `logrotate`).
- Times are in the host's local timezone. The agent adjusts to PT if its host
  is in another timezone.
- The agent does NOT commit this cron schedule to the repo. Each host the
  agent runs on gets its own `crontab -e` install.

---

## Appendix: Advanced Circuit Breaker Configuration

Beyond the per-asset volatility breaker (`VolatilityCircuitBreaker`) and the portfolio-level `RiskCircuitBreaker` covered in Phase 4, Hermes ships a unified **CircuitBreakerManager** (`src/hermes/portfolio/cb_manager.py`) that adds 8 tiered categories, time-decay, and rolling windows. All configuration lives under `circuit_breakers.manager` in `config/default.yaml`.

### The 8 breaker categories (default thresholds)

| Category | What it watches | Default tiers (threshold → action → cooldown) |
|---|---|---|
| `portfolio_exposure` | Gross exposure as % of equity | 80% → reduce_25pct (0s) · 90% → reduce_50pct (0s) · 100% → block_entries (0s) · 150% → halt_all (1h) |
| `position_size` | Absolute $ notional per position | $50k → reduce_25pct · $75k → reduce_50pct · $100k → block_entries |
| `daily_loss` | Absolute $ daily loss | $5k → reduce_50pct (0s) · $10k → block_entries (4h) · $15k → halt_all (24h) |
| `var` | Absolute $ VaR (1-day, 99% confidence) | $50k → reduce_50pct · $100k → block_entries (1h) |
| `drawdown` | Portfolio drawdown % from peak equity | 15% → reduce_50pct · 20% → block_entries (4h) · 25% → liquidate (24h) |
| `funding_rate` | Daily funding cost in $ for crypto perps | $50/day → temp_block (30min) · $200/day → block_entries (2h) |
| `consecutive_losses` | Rolling 24h: consecutive losing trades | 3 → reduce_50pct · 5 → block_entries (1h) |
| `trip_frequency` | Rolling 24h: number of CB trips | 5 → reduce_50pct · 10 → halt_all (24h — system unstable) |

The 7 available actions are: `reduce_25pct`, `reduce_50pct`, `temp_block`, `block_entries`, `tighten_stops`, `halt_all`, `liquidate`. Each tier in each category picks the action that matches its severity — small breaches get soft responses (reduce), only severe breaches trigger hard actions (halt, liquidate).

### How time-decay works (`cooldown_sec`)

Every tier has a `cooldown_sec` field. When a breaker trips, the manager records `expires_at = trip_time + cooldown_sec`. On each `evaluate()` pass, the manager automatically transitions `tripped → expired` once the cooldown elapses — so transient conditions (a 30-minute funding spike, a 4-hour drawdown blip) self-heal without operator intervention.

- `cooldown_sec: 0` → manual-clear only (operator must explicitly clear the trip)
- `cooldown_sec: 3600` → auto-clears 1 hour after the trip timestamp
- `cooldown_sec: 86400` → auto-clears after 24 hours (used for the most severe tiers)

This eliminates the most common ops headache with the original breakers: "trip happened, condition cleared, but breaker is still tripped because nobody cleared it."

### How rolling windows work

The `RollingWindowTracker` class (`deque`-backed, bounded memory) supports the two rolling categories:

- **`consecutive_losses`** — counts the current losing streak (resets on a win). Trips when 3 or 5 consecutive losses accumulate.
- **`trip_frequency`** — counts total CB trips within the trailing 24-hour window. Trips when 5 (reduce) or 10 (halt_all — "system is thrashing, something is structurally wrong").

The tracker exposes `add(value)`, `sum()`, `count()`, and `recent_events(within_sec)` for the rolling aggregates the manager consults on each evaluation pass.

### How to configure

All 8 categories live under `circuit_breakers.manager` in `config/default.yaml`. Each category has the same shape:

```yaml
circuit_breakers:
  manager:
    portfolio_exposure:
      enabled: true
      description: "Gross exposure as % of equity"
      tiers:
        - threshold: 0.80
          action: reduce_25pct
          label: "80% exposure"
          cooldown_sec: 0
        - threshold: 0.90
          action: reduce_50pct
          label: "90% exposure"
          cooldown_sec: 0
        - threshold: 1.00
          action: block_entries
          label: "100% exposure (max)"
          cooldown_sec: 0
        - threshold: 1.50
          action: halt_all
          label: "150% exposure (over-leveraged)"
          cooldown_sec: 3600
```

To disable a category, set `enabled: false`. To add a new tier, append to the `tiers` list — the manager picks the highest threshold whose value is exceeded. To make a trip auto-clear, set `cooldown_sec` to a non-zero value.

The agent changes circuit-breaker config through `platform config set` (see §7.4), not by editing YAML directly. Most `circuit_breakers.*` keys are tier 3 (human approval required) — the agent cannot auto-promote changes to breaker thresholds.

> **Layered, not replacing.** This manager coexists with `circuit_breakers.py` (per-asset volatility + portfolio DD/VaR) and `risk_gate.py` (8 pre-trade checks). It adds new categories and time-decay; the original breakers remain the fast-path pre-trade gate.

---

## Appendix: Performance Attribution

The Phase 9 decision tree was validated structurally, but Hermes had no way to answer: *"Which branches actually make money?"* `src/hermes/agent/attribution.py` closes that gap with three components.

### How DecisionBranchTracker attributes PnL to decision branches

Every trade gets a `TradeDecisionRecord` that captures the `AgentAction` taken at entry AND at exit, plus the meta-regime, brick pattern, conviction score, sizing multiplier, net PnL, R-multiple, hold duration, MFE/MAE, and entry alpha (bps). The tracker then aggregates these into `BranchStats` per branch:

- `analyze_branch_performance()` — exit-action stats: win rate, avg R-multiple, expectancy, profit factor, avg hold duration, avg entry alpha (bps) per branch. Tells you whether `close_early_profit`, `close_flip`, `trail_stop`, etc. are actually adding value.
- `analyze_entry_branch_performance()` — entry-action stats: was `enter_now` better than `wait_for_brick_close`?
- `analyze_hypothesis_performance()` — PnL attributed back to specific hypothesis IDs, closing the loop with the Phase 9 hypothesis tracker.

### The regime × branch matrix

`analyze_regime_branch_matrix()` returns a `RegimeBranchMatrix` — a `branch × regime` table of `BranchStats`. This is the killer view: a branch that looks bad overall might be excellent in `calm_trend` and terrible in `choppy_range`. The matrix surfaces this and enables **regime-conditional tuning** (e.g., "disable `trail_stop` in `choppy_range`, keep it in `calm_trend`").

### Threshold feedback for auto-tuning

`get_threshold_feedback()` produces concrete, evidence-backed tuning recommendations by comparing each branch's actual avg R-multiple against its expected behavior. Each recommendation includes `current` value, `issue` description, `suggestion`, and `evidence` (n_trades + avg R):

| Threshold | Issue detected | Suggested change |
|---|---|---|
| `stop_loss_pct` | avg R < -1.2 → SL too loose · avg R > -0.8 → SL too tight (cutting winners) | tighten to -0.8% / loosen to -1.2% |
| `take_profit_pct` | native TP avg R < 0.3 → too tight | raise from 2.5% to 3.0% |
| `early_profit_pct` | avg R < 0.5 → exiting before full profit | raise from 4.5% to 5.5% |
| `fading_brick_count` | trail trades avg R < 0 → trail trigger too sensitive | increase from 2 to 3 adverse bricks |
| `strong_conviction_threshold` | flip trades avg R < 0 → flipping on weak signals | raise from 0.7 to 0.8 |

Feedback only fires when n_trades ≥ 5 per branch (statistical noise filter). These recommendations can be fed directly into a hypothesis proposal — closing the attribution → feedback → tuning loop.

`get_decision_quality_report()` rolls everything up: branch stats + entry stats + regime matrix + hypothesis stats + threshold feedback + best/worst performing branches in one call.

### A/B testing framework

`ABTestFramework.compare(config_a_name, config_a_returns, config_b_name, config_b_returns, significance_level=0.05)` runs two configs in parallel and compares them with proper statistics:

- **Paired t-test** — are the mean daily returns statistically different?
- **Diebold-Mariano test** — forecast accuracy comparison (the standard test in quant literature for predictive comparisons)
- **Sharpe ratio comparison** — annualized Sharpe for each config

Returns `winner`, `confidence` (1 - p_value), `significant` flag (p < 0.05), both p-values and t-stats. Requires n ≥ 10 returns before declaring significance (prevents spurious wins on tiny samples). Falls back to a normal approximation if `scipy` isn't installed.

### Signal window optimization

`SignalWindowOptimizer.optimize_window(signals, price_data, windows=[5,10,15,20,30,45,60,90])` finds the optimal `signal_expiry_minutes` — how long after a Noble Trader heartbeat Hermes will still act on the signal. For each candidate window it simulates: "if we entered at the best price within N minutes of the signal, what would the PnL be?" Returns per-window `{n_signals, n_filled, avg_entry_alpha_bps, total_pnl}` plus `best_window` + `rationale`. Too short → miss opportunities; too long → act on stale signals.

> **Attribution → feedback → tuning.** This was the biggest gap in the original Phase 9 self-learning loop. Combined with the hypothesis lifecycle (`proposed → backtested → shadow → live`), Hermes can now attribute PnL to specific decisions, generate tuning recommendations, A/B test the change, and promote only statistically significant winners.

---

## Appendix: What You Might Have Missed

### Items Added Beyond the Original Request

1. **Dead man's switch** — automatic kill switch activation if Hermes stops responding (protects against process crashes, OOM, network partition)

2. **Alerting system** — Discord + Telegram notifications for critical events (circuit breaker trips, kill switch, DMS activation, daily loss)

3. **Load testing** — `platform load-test` verifies the system can handle target throughput (100k heartbeats/day)

4. **Forensic replay** — `platform replay` reconstructs any historical session for debugging ("what happened at 3:42 PM?")

5. **Disaster recovery runbook** — 7 detailed scenarios with step-by-step recovery procedures

6. **Post-incident checklist** — ensures nothing is missed after an incident

7. **Config management & audit trail** — `platform config set/history/diff/rollback/promote` with per-key tier enforcement (tier 2 auto-promote, tier 3 human approval, tier 4 structural)

8. **Agent-owned cron schedule** — daily EOD, shadow promotion, underperformance checks; weekly optimization + rigor; monthly maintenance + meta-regime retrain. The agent installs the schedule on its host via `crontab -e`; no cron scripts live in the repo.

9. **Troubleshooting guide** — common issues with solutions

10. **Dashboard reference** — every page explained with what it shows and when to use it (12 Jinja2 + 8 SPA)

### Items to Consider for Future Phases

1. **Live trading mode** — currently paper-only; live mode would use real venue APIs for order submission (requires additional testing + smaller position sizes initially)

2. **Multi-region failover** — Alpaca East/West, Hyperliquid multi-API endpoint (currently single-region)

3. **Secrets management upgrade** — currently `.env` file; production should use HashiCorp Vault or AWS Secrets Manager

4. **Tax lot tracking** — FIFO/LIFO/HIFO for real fund accounting

5. **Forex venue** — 15% of portfolio reserved but no venue adapter yet (OANDA/IBKR)

6. **Liquidation heatmap** — deferred from Phase 2 (needs liquidation feed parsing)

7. **Scenario path runner** — deferred from Phase 2 (needs Monte Carlo PnL projection)

8. **Macro clock** — deferred from Phase 2 (needs economic calendar data source)

9. **Human-in-the-loop UI** — for tier 3 autonomy approvals (currently CLI-only via `platform config set`)

10. **Multi-strategy capital allocation** — rotate capital between strategies by Sharpe + capacity
