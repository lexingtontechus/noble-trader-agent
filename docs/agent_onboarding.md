# Hermes Agent Onboarding Guide

> Complete walkthrough: from project installation through live paper trading, self-learning, and disaster recovery.

---

## Table of Contents

1. [Onboarding Overview](#1-onboarding-overview)
2. [Phase A: Installation & Configuration](#2-phase-a-installation--configuration)
3. [Phase B: Connection Verification](#3-phase-b-connection-verification)
4. [Phase C: Historical Data Backfill](#4-phase-c-historical-data-backfill)
5. [Phase D: The Trading Loop](#5-phase-d-the-trading-loop)
6. [Phase E: End-of-Day Analysis & Self-Learning](#6-phase-e-end-of-day-analysis--self-learning)
7. [Phase F: Ongoing Operations](#7-phase-f-ongoing-operations)
8. [Phase G: Disaster Recovery](#8-phase-g-disaster-recovery)
9. [Dashboard Reference](#9-dashboard-reference)
10. [CLI Command Reference](#10-cli-command-reference)
11. [Troubleshooting Guide](#11-troubleshooting-guide)

---

## 1. Onboarding Overview

Hermes onboards in 7 phases:

```
Phase A: Installation & Configuration
    ↓
Phase B: Connection Verification
    ↓
Phase C: Historical Data Backfill (cold start)
    ↓
Phase D: The Trading Loop (daily operation)
    ↓
Phase E: End-of-Day Analysis & Self-Learning
    ↓
Phase F: Ongoing Operations (weekly/monthly maintenance)
    ↓
Phase G: Disaster Recovery (when things go wrong)
```

**Time to first trade**: ~30 minutes (installation + config + connection verification)

**Time to first learning cycle**: ~1-2 hours (after accumulating trade history)

---

## 2. Phase A: Installation & Configuration

### 2.1 Prerequisites

| Requirement | How to verify |
|---|---|
| Python 3.12+ | `python --version` |
| Git | `git --version` |
| Redis (local or remote) | `redis-cli ping` → PONG |
| Paper trading credentials | See §2.3 below |

### 2.2 Install

```powershell
# Extract the zip
# Navigate to project folder
cd hermes-trading-platform

# Run setup (Windows)
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

# Or manual install
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e . --no-deps
copy .env.example .env
python scripts\init_duckdb.py
```

### 2.3 Gather Paper Credentials

| Credential | Source | What it enables |
|---|---|---|
| `NOBLE_TRADER_REDIS_URL` | Noble Trader operator | Real-time heartbeat ingestion |
| `SUPABASE_URL` | Your Supabase project | Historical heartbeat backfill |
| `SUPABASE_KEY` | Supabase dashboard (service_role) | Read access to NT tables |
| `ALPACA_API_KEY` | https://app.alpaca.markets/paper/dashboard/overview | Paper stock/commodity trading |
| `ALPACA_API_SECRET` | Same as above | Paper stock/commodity trading |
| `HYPERLIQUID_WALLET_ADDRESS` | Generate dedicated wallet | Paper crypto trading |
| `HYPERLIQUID_PRIVATE_KEY` | Same wallet (NEVER main wallet) | Paper crypto trading |
| `HERMES_REDIS_URL` | Local Redis instance | Internal pub/sub between layers |

### 2.4 Configure

Edit `.env` with real values (paper keys only). Then verify config loads:

```powershell
platform init
```

This will:
- Load config from `config/default.yaml`
- Resolve all `secret:` prefixed values from `.env`
- Open DuckDB and apply schema (8 migrations, 23 tables)
- Write a test row to `config_history`
- Ping Redis (non-fatal if unreachable)
- Print config summary

### 2.5 Verify

```powershell
platform health
```

Expected output: all subsystems show ✓ or at least "not_configured" (not "error").

---

## 3. Phase B: Connection Verification

### 3.1 Start the Dashboard

```powershell
platform dashboard
```

Open **http://127.0.0.1:8080** in your browser. The Status page shows connection badges for all 6 subsystems:

| Subsystem | What it checks | Badge when ready |
|---|---|---|
| DuckDB | Opens read-only, counts tables | `connected` |
| Hermes Redis | Pings internal Redis | `connected` |
| Noble Trader Redis | Pings NT upstream Redis | `connected` |
| Supabase | REST API reachable | `connected` |
| Alpaca | `/v2/account` returns account info | `connected` |
| Hyperliquid | `/info` meta endpoint returns asset count | `connected` |

### 3.2 Test Alert Channels

```powershell
platform alert-test
```

Verifies Discord webhook and Telegram bot are configured correctly.

### 3.3 Test Redis Connectivity

```powershell
python scripts\test_redis.py
```

Tests both Hermes internal Redis and Noble Trader upstream Redis with ping + pub/sub round-trip.

### 3.4 Dry-Run Ingest

```powershell
platform ingest --dry-run
```

Validates that the Noble Trader heartbeat subscriber config is correct without actually subscribing.

---

## 4. Phase C: Historical Data Backfill

### 4.1 Pull Noble Trader Historical Data from Supabase

```powershell
platform backfill --days-back 365
```

Pulls from:
- `nt_sweep_result` — weekly heavy + light sweeps (optimal brick_size/sl/tp per symbol)
- `nt_regime_log` — periodic regime snapshots (every 5-15 min per symbol)

Applies data quality checks on ingest:
- `sharpe_too_high` — flags absurd Sharpe ratios (>20)
- `max_dd_zero` — flags impossible zero drawdown
- `profit_factor_zero` — flags suspicious zero profit factor
- `regime_strategy_disagree` — flags when regime says bull but strategy is losing (this is Hermes's value-add signal)

### 4.2 Pull Historical Market Data from Venues

```powershell
platform backfill-market --symbol BTC-PERP --venue hyperliquid --timeframe 1m --days-back 90
platform backfill-market --symbol AAPL --venue alpaca --timeframe 1m --days-back 90
```

Stores in Parquet (partitioned by `venue/symbol/tf/date`) for offline analysis and backtesting.

### 4.3 Verify Data

Check the dashboard:
- **Heartbeats page** (`/heartbeats`) — should show historical NT heartbeats
- **PnL page** (`/pnl`) — should show "insufficient data" (no trades yet)

---

## 5. Phase D: The Trading Loop

### 5.1 Trading Loop Overview

The trading loop is the real-time pipeline that runs continuously during market hours:

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

NT publishes a heartbeat to its Redis channel every ~5min (crypto/forex) / ~15min (stocks/commodities).

#### Step 2: L0 Receives and Validates Heartbeat

**What happens**: The `platform ingest` process subscribes to NT's Redis channel and processes each heartbeat.

**Sub-steps**:
1. **Receive**: async Redis subscriber with consumer group (survives disconnects)
2. **Parse**: JSON → `NobleTraderHeartbeat` Pydantic model (validates all 28+ fields)
3. **Dedup**: SHA-256 hash of `(symbol, ts, signal, entry, stop, TP)` — drops duplicates within 5s window
4. **Staleness check**: reject if heartbeat older than 30s (configurable)
5. **Regime shift detection**: if `regime_shift == "true"`, emit high-priority `regime.shift.{symbol}` event
6. **Persist**: write to DuckDB `signal_heartbeats` table (immutable provenance chain)
7. **Re-publish**: on internal `signal.raw.hermes.{symbol}` channel for downstream consumption

**Failure handling**: malformed payloads quarantined in `signal_heartbeats_quarantine`; heartbeat gap >60s triggers `upstream.stale` alert.

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
   - Tier 2: config promotion — notify-only
   - Tier 3: large/novel trade (> $25k) — human approval required (4h timeout)
   - Tier 4: structural change — hard block
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

## 6. Phase E: End-of-Day Analysis & Self-Learning

### 6.1 EOD Analysis

Run at end of trading day:

```powershell
platform agent --eod
```

**What it does**:
1. **Observe**: pull all closed trades from `pnl_realized` for today
2. **Attribute**: decompose PnL by meta-regime (win rate, total PnL, avg PnL per regime)
3. **Postmortems**: write automated postmortems for each trade with lessons
4. **Hypothesize**: generate improvement hypotheses from regime performance:
   - Low win-rate regime (< 40%, 3+ trades) → propose reducing sizing multiplier
   - High win-rate regime (> 65%, 3+ trades, positive PnL) → propose increasing sizing multiplier
5. **Store hypotheses**: write to `hermes_hypotheses` table (status = "proposed")

### 6.2 Hypothesis Lifecycle

```
proposed → backtested → shadow → live
                    ↘ rejected
                         ↗ retired
```

- **Propose**: EOD analysis generates hypothesis (e.g., "Reduce sizing in choppy_range")
- **Backtest**: run through simulation engine with 6 rigor checks
- **Shadow**: paper-trade in parallel at 10% of live size for 7 days
- **Promote**: auto-promote if shadow Sharpe ≥ 80% of backtest Sharpe
- **Reject**: if rigor checks fail or shadow underperforms
- **Retire**: if promoted config underperforms in live for 14 days → auto-rollback

### 6.3 Optimization Sweep

Run weekly (or on demand):

```powershell
platform optimize --symbols BTC-PERP --days-back 90 --n-trials 200
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
5. Top candidates enter shadow mode

### 6.4 Counterfactual Analysis

On any closed trade:

```powershell
platform counterfactual --trade-id <uuid>
```

**What it does**: replays the trade under alternative entry strategies (enter_now vs wait_for_brick_close vs wait_for_pullback) and computes what the PnL would have been.

### 6.5 Statistical Rigor Checks

```powershell
platform rigor --symbols BTC-PERP --days-back 90
```

6 checks (all must pass for a strategy to be accepted):
1. **Walk-forward validation**: OOS Sharpe within 80% of IS Sharpe (purged k-fold CV)
2. **Deflated Sharpe > 1.0**: Bailey & López de Prado multiple-testing correction
3. **Monte Carlo 5th percentile > 0**: bootstrap resampling, 1000 iterations
4. **Bootstrap CI lower bound > 0**: 1000 bootstrap samples, 5th percentile
5. **Regime coverage**: positive expectancy in 4+ of 7 meta-regimes
6. **Capacity check**: backtested notional < 10× median daily volume

---

## 7. Phase F: Ongoing Operations

### 7.1 Daily Startup Checklist

```powershell
# 1. Verify health
platform health

# 2. Start dashboard (Terminal 1)
platform dashboard

# 3. Start heartbeat subscriber (Terminal 2)
platform ingest

# 4. Start price monitor (Terminal 3)
platform monitor --symbols BTC-PERP,AAPL

# 5. Start signal synthesizer (Terminal 4)
platform synthesize --symbols BTC-PERP,AAPL

# 6. Start risk engine (Terminal 5)
platform risk --equity 100000

# 7. Start execution engine (Terminal 6)
platform execute --equity 100000 --paper
```

### 7.2 Daily Shutdown Checklist

```powershell
# 1. Run EOD analysis
platform agent --eod

# 2. Generate tear sheet
platform pnl

# 3. Stop all processes (Ctrl+C in each terminal)

# 4. Verify no orphaned positions
# Check dashboard /portfolio page

# 5. Backup DuckDB
cp data/hermes.duckdb backups/hermes_$(date +%Y%m%d).duckdb
```

### 7.3 Weekly Maintenance

```powershell
# 1. Run optimization sweep
platform optimize --symbols BTC-PERP,AAPL --days-back 90 --n-trials 200

# 2. Run rigor checks
platform rigor --symbols BTC-PERP,AAPL --days-back 90

# 3. Review hypotheses
platform agent --list-hypotheses

# 4. DuckDB VACUUM
python -c "import duckdb; duckdb.connect('data/hermes.duckdb').execute('VACUUM')"

# 5. Review alert history (check for patterns)
# Query: SELECT * FROM circuit_breaker_events WHERE ts >= now() - INTERVAL '7 days'
```

### 7.4 Monthly Maintenance

- Retrain 7-state meta-regime HMM on rolling 2-year window
- Review and rotate API keys (every 90 days per security policy)
- Archive old Parquet data (>90 days) to cold storage
- Test disaster recovery by running through a scenario in `docs/dr_runbook.md`
- Review hypothesis tracker for promotions/rejections

### 7.5 Config Tuning Guide

The most important tunable parameters (in `config/default.yaml`):

| Parameter | Default | When to tune |
|---|---|---|
| `account.max_portfolio_drawdown_pct` | 0.15 | If you want tighter/looser portfolio risk |
| `account.daily_loss_limit_pct` | 0.03 | Daily risk tolerance |
| `asset.max_position_size_pct` | 0.05 | Per-asset concentration limit |
| `signal.reward_risk_min` | 1.5 | Minimum R:R to take a trade |
| `meta_regime.thresholds.risk_off_corr_threshold` | 0.75 | When to go risk-off |
| `autonomy.tier_1.max_notional_usd` | 5000 | Autonomous trade size cap |
| `execution.max_slippage_bps` | 20 | Max acceptable slippage |

**Rule**: Never change more than one parameter at a time. Backtest before promoting to live.

---

## 8. Phase G: Disaster Recovery

### 8.1 Kill Switch (Emergency Stop)

```powershell
# Activate (halts all new entries, cancels orders, optionally flattens)
redis-cli PUBLISH agent.command '{"action": "flatten"}'

# Deactivate (resume trading)
redis-cli PUBLISH agent.command '{"action": "resume"}'
```

### 8.2 Dead Man's Switch

Automatically activates if no heartbeat from any component for 60 seconds. Triggers:
1. Kill switch activation
2. Cancel all open orders
3. Optionally flatten all positions
4. Send critical alert to Discord/Telegram

### 8.3 Common Scenarios

See `docs/dr_runbook.md` for 7 detailed scenarios:
1. Process crash
2. DuckDB corruption
3. Redis disconnect
4. Noble Trader upstream down
5. Venue API down
6. Daily loss limit hit
7. Config change rollback

### 8.4 Forensic Replay

```powershell
# Replay any time period to see exactly what happened
platform replay --start 2026-07-01T14:00:00 --end 2026-07-01T15:00:00 --symbols BTC-PERP
```

Reconstructs the full timeline from DuckDB: heartbeats, signals, risk decisions, orders, fills, monitor events, circuit breaker events, and account snapshots — all in chronological order.

---

## 9. Dashboard Reference

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

---

## 10. CLI Command Reference

| Command | Phase | Purpose |
|---|---|---|
| `platform init` | 0 | Bootstrap: load config, open DuckDB, apply schema |
| `platform health` | 0 | Check health of all subsystems |
| `platform config show` | 0 | Print loaded config (secrets redacted) |
| `platform version` | 0 | Print version |
| `platform dashboard` | 0.5 | Start web dashboard at http://127.0.0.1:8080 |
| `platform ingest` | 1 | Start Noble Trader heartbeat subscriber (L0) |
| `platform backfill` | 1 | Pull historical heartbeats from Supabase |
| `platform stream` | 2 | Stream live market data from venue WebSockets |
| `platform monitor` | 2 | Start Active Price Monitor (L2.8) |
| `platform backfill-market` | 2 | Pull historical bars from venue REST API |
| `platform synthesize` | 3 | Start L4 Signal Synthesizer (BEV combiner) |
| `platform risk` | 4 | Start L5 Portfolio & Risk Engine |
| `platform execute` | 5 | Start L3 Execution Engine (paper trading) |
| `platform pnl` | 6 | Generate PnL tear sheet |
| `platform backtest` | 7 | Run backtest by replaying historical heartbeats |
| `platform rigor` | 7 | Run statistical rigor checks |
| `platform optimize` | 8 | Run entry/execution optimization sweep (Optuna) |
| `platform shadow` | 8 | Start shadow mode for a new config |
| `platform counterfactual` | 8 | Run counterfactual analysis on a closed trade |
| `platform agent` | 9 | Show decision tree / run EOD analysis / list hypotheses |
| `platform replay` | 10 | Replay a historical session for forensic analysis |
| `platform alert-test` | 10 | Send a test alert to Discord/Telegram |
| `platform load-test` | 10 | Run a load test on the DuckDB writer |

---

## 11. Troubleshooting Guide

### "DuckDB init failed"
- Delete `data/hermes.duckdb` and `data/hermes.duckdb.wal`
- Run `platform init` again
- If persists: check DuckDB version (`pip show duckdb`)

### "Redis unreachable"
- Windows: `Start-Service Memurai` or `docker start hermes-redis`
- Linux: `sudo systemctl restart redis`
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
- Check logs for "heartbeat_parse_failed" warnings

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
- Config is loaded at startup — restart all processes
- Hot-reload via Redis: `redis-cli PUBLISH config.update '{"key": "value"}'`
- Verify with `platform config show`

### "Hypotheses not being generated"
- Run `platform agent --eod` manually
- Ensure there are closed trades in `pnl_realized` table
- EOD analysis only generates hypotheses when it finds patterns (low win rate in a regime with 3+ trades)

### "Optimization is slow"
- Reduce `--n-trials` (default 200)
- Reduce `--days-back` (default 90)
- Use fewer symbols
- Each trial runs a full backtest — expect ~1-5 seconds per trial depending on data volume

---

## What You Might Have Missed

### Items Added Beyond the Original Request

1. **Dead man's switch** — automatic kill switch activation if Hermes stops responding (protects against process crashes, OOM, network partition)

2. **Alerting system** — Discord + Telegram notifications for critical events (circuit breaker trips, kill switch, DMS activation, daily loss)

3. **Load testing** — `platform load-test` verifies the system can handle target throughput (100k heartbeats/day)

4. **Forensic replay** — `platform replay` reconstructs any historical session for debugging ("what happened at 3:42 PM?")

5. **Disaster recovery runbook** — 7 detailed scenarios with step-by-step recovery procedures

6. **Post-incident checklist** — ensures nothing is missed after an incident

7. **Config tuning guide** — the 7 most important tunable parameters with guidance on when to adjust

8. **Daily/weekly/monthly maintenance schedules** — structured operational rhythm

9. **Troubleshooting guide** — 9 common issues with solutions

10. **Dashboard reference** — every page explained with what it shows and when to use it

### Items to Consider for Future Phases

1. **Live trading mode** — currently paper-only; live mode would use real venue APIs for order submission (requires additional testing + smaller position sizes initially)

2. **Multi-region failover** — Alpaca East/West, Hyperliquid multi-API endpoint (currently single-region)

3. **Secrets management upgrade** — currently `.env` file; production should use HashiCorp Vault or AWS Secrets Manager

4. **Tax lot tracking** — FIFO/LIFO/HIFO for real fund accounting

5. **Forex venue** — 15% of portfolio reserved but no venue adapter yet (OANDA/IBKR)

6. **Liquidation heatmap** — deferred from Phase 2 (needs liquidation feed parsing)

7. **Scenario path runner** — deferred from Phase 2 (needs Monte Carlo PnL projection)

8. **Macro clock** — deferred from Phase 2 (needs economic calendar data source)

9. **Human-in-the-loop UI** — for tier 3 autonomy approvals (currently CLI-only)

10. **Multi-strategy capital allocation** — rotate capital between strategies by Sharpe + capacity
