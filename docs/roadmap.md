# Hermes Trading Platform — Roadmap & System Design

**Objective** — Teach the Hermes agent to operate as a quant hedge fund manager with expert trading, data analysis, market research, portfolio & risk management, and PnL analysis skills.

**Scope** — Python-only, event-driven, multi-venue (Alpaca for stocks/commodities, Hyperliquid for crypto; forex venue TBD), Redis-pubsub backbone, DuckDB local analytical store. Hermes's **core job is renko-bar-driven entry/execution optimization** — it does NOT replicate Noble Trader's strategy-level sweeps.

**Upstream Signal Source** — Subscribes to the **Noble Trader** platform (FastAPI, v7.5.0) heartbeat channel over Redis. Noble Trader owns: strategy parameter optimization (weekly full sweep + 5min light sweeps for crypto/forex, 15min for stocks/commodities), Renko brick_size/sl_bricks/tp_bricks optimization, per-asset 4×4 HMM regime detection, EV Engine v4 (p_win blending), Kelly + Masaniello sizing, and signal generation (direction/entry/stop/TP). Historical heartbeats are persisted in Noble Trader's Supabase — Hermes pulls these for HMM cold-start, backtest replay, and calibration analysis.

**Division of Labor (CRITICAL):**
- **Noble Trader** = strategy brain (what to trade, at what size, with what risk params)
- **Hermes** = entry/execution brain (when exactly to pull the trigger, how to route the order, how to manage the position post-entry)
- Hermes **trusts** Noble Trader's direction, entry/stop/TP, brick_size, and effective_kelly as baseline inputs
- Hermes **optimizes**: entry timing (via renko simulation), execution method (market/limit/TWAP/post-only), portfolio-level risk overlay (7-state meta-regime), and position management (trailing stops, scenario projections)
- Hermes **does NOT** run its own sweeps, does NOT re-optimize brick_size or strategy params, does NOT re-derive p_win from scratch

**Design Principles:**
1. **Everything is configurable** — portfolio allocation, autonomy tiers, venue connections, HMM retrain cadence, circuit breaker thresholds, all in YAML with sensible defaults
2. **Venue-native data only** — no yfinance, no third-party price feeds. Each venue is the source of truth for its own assets. Alpaca prices for Alpaca-traded assets, Hyperliquid prices for HL-traded assets. This means historical data must come from each venue's own historical API.
3. **Multi-venue by design** — venue adapters behind a common interface. Adding a forex broker (OANDA, IBKR, etc.) later = new adapter, no core changes.
4. **Renko-first** — renko bars (constructed from venue ticks using NT's brick_size) are the primary time frame for entry/execution decisions, not candlesticks.
5. **Start small, scale later** — initial deployment is a small asset portfolio; config-driven allocation lets you scale without code changes.

---

## Table of Contents

1. [Architecture](#1-architecture)
2. [Core Modules](#2-core-modules)
3. [Configurable Trade Parameters](#3-configurable-trade-parameters)
4. [Circuit Breakers](#4-circuit-breakers)
5. [Signal Pipeline (Redis Pub/Sub)](#5-signal-pipeline-redis-pubsub)
6. [Local Analytical Storage — DuckDB](#6-local-analytical-storage--duckdb)
7. [Self-Learning Loop](#7-self-learning-loop-for-hermes)
8. [Tech Stack](#8-recommended-tech-stack)
9. [Project Layout](#9-suggested-project-layout)
10. [Implementation Roadmap](#10-implementation-roadmap-phased)
11. [Features Missed in Initial Brief](#11-features-missed-in-initial-brief)
12. [Open Decisions](#12-open-decisions)
13. [Credentials & Secrets Management](#13-credentials--secrets-management)

---

## 1. Architecture

Layered, event-driven. Every layer is a consumer/producer over Redis pub/sub channels with a normalized internal message envelope. **L0 (Upstream Ingestion)** subscribes to Noble Trader's heartbeat channel and is the only legal entry point for external trade signals — every signal received is stamped with a `signal_id` and persisted to DuckDB before any downstream consumption. This lets us swap upstreams, replay any session deterministically, and always answer the question "what did Noble Trader tell us, and what did we do with it?"

```
┌─────────────────────────────────────────────────────────────────────┐
│  L0  Upstream Ingestion (Noble Trader heartbeat subscriber)         │
├─────────────────────────────────────────────────────────────────────┤
│  L7  Hermes Agent Layer (LLM-driven decisioning + self-learning)    │
├─────────────────────────────────────────────────────────────────────┤
│  L6  Learning & Optimization (regime memory, param tuning, walk-fwd)│
├─────────────────────────────────────────────────────────────────────┤
│  L5  Portfolio & Risk Engine (sizing, circuit breakers, rebalancing)│
├─────────────────────────────────────────────────────────────────────┤
│  L4  Signal Synthesis Layer (7-state meta-regime + BEV blending)    │
├─────────────────────────────────────────────────────────────────────┤
│  L3  Execution & Order Routing (Alpaca + Hyperliquid adapters)      │
├─────────────────────────────────────────────────────────────────────┤
│  L2  Market Data & Active Price Monitor (L1/L2/OHLCV, funding, L2) │
├─────────────────────────────────────────────────────────────────────┤
│  L1  Infrastructure (Redis pubsub, DuckDB, Parquet, audit log)      │
└─────────────────────────────────────────────────────────────────────┘

Cross-cutting:
  ┌──────────────────────────────────────────────────────────────────┐
  │  Simulation & Parameter Optimization Engine (offline + shadow)   │
  └──────────────────────────────────────────────────────────────────┘
```

---

## 2. Core Modules

### 2.0 Upstream Signal Subscriber (L0)

The **only** entry point for external trade signals. Subscribes to Noble Trader's Redis heartbeat channel and converts each heartbeat into a normalized internal signal envelope before persisting to DuckDB.

**Why a dedicated layer?**
- Decouples Hermes from any specific upstream vendor (Noble Trader today, could be a different signal stack tomorrow).
- Gives Hermes full provenance: every downstream decision can be traced back to the exact upstream heartbeat that triggered it.
- Lets us replay any historical session tick-by-tick by replaying stored heartbeats.
- Surfaces upstream signal quality issues (stale signals, missing fields, regime flips) BEFORE they reach the risk gate.

**Components:**
- **Redis Subscriber**: async listener on `signal.raw.{strategy_id}` (Noble Trader's heartbeat channel). Subscribes with a consumer group so we can recover from disconnects without losing messages.
- **Heartbeat Parser**: validates the Noble Trader heartbeat schema (see §5.1), rejects malformed payloads, normalizes field names.
- **Deduper**: SHA-256 hash of `(symbol, ts, signal, entry_price, stop_loss, take_profit)` → drops duplicates within a 5s window.
- **Staleness Checker**: rejects any signal older than `signal_staleness_ms` (configurable, default 30s) — Noble Trader's own 5/15min publish cadence gives us healthy margin.
- **Regime Shift Detector**: when `regime_shift == "true"` from upstream, emit a high-priority `regime.shift.{symbol}` event in addition to the standard signal channel so L4/L5 can re-evaluate open positions immediately.
- **DuckDB Writer**: every accepted heartbeat → row in `signal_heartbeats` (see §6.2.6) before any downstream processing. This is the **immutable provenance chain**.
- **Internal Publisher**: re-publishes normalized signal on `signal.raw.hermes.{symbol}` for downstream consumption — L4 never reads from the upstream channel directly.
- **Supabase Historical Backfill Adapter**: pulls historical heartbeats (regime sweeps) from Noble Trader's Supabase on startup and on demand. Used for: (a) 7-state meta-regime HMM cold-start training, (b) backtest replay of past signals, (c) calibration analysis (Noble Trader's p_win vs actual outcomes). Configurable: `supabase.backfill_on_startup`, `supabase.backfill_lookback_days` (default 365).

**Failure modes handled:**
- Redis disconnect → reconnect with backoff, consumer group ensures no message loss.
- Malformed payload → quarantine in `signal_heartbeats_quarantine` table for forensic review.
- Upstream service down → detect via heartbeat gap > 60s → emit `upstream.stale` alert, pause new entries until heartbeat resumes.
- Backpressure → if downstream signal queue grows > 1000 messages, drop oldest non-actionable (signal="neutral") heartbeats first.

### 2.1 Market Data Layer (L2)
- **Venue adapters**: `AlpacaAdapter` (stocks, commodities), `HyperliquidAdapter` (perps, spots)
- **Normalization**: unified `Tick`, `Bar`, `OrderBookL2`, `FundingRate`, `LiquidationEvent`, `Trade` schemas
- **Channels**: `md.tick.{venue}.{symbol}`, `md.bar.{tf}.{symbol}`, `md.book.{symbol}`, `md.funding`
- **Storage**: hot tier in Redis (rolling 24h), warm tier in Parquet (partitioned `venue/symbol/tf/date`) queried via DuckDB
- **Time sync**: each venue clock skew tracked, internal monotonic clock for ordering

### 2.2 Signal Synthesis Layer (L4)

Consumes normalized heartbeats from L0 (Noble Trader signals) plus internal renko bars + market data from L2/L2.8, then produces an **entry/execution decision**. The key principle: **Hermes trusts Noble Trader's strategy decision (direction, entry, stop, TP, brick_size, effective_kelly) and focuses its own compute on entry TIMING and execution METHOD — not on re-deriving the signal.**

#### 2.2.1 7-State Meta-Regime Classifier (portfolio-level risk overlay)

Noble Trader's regime is per-asset (4×4 vol×trend). Hermes needs a **portfolio-level** regime to answer: "given my entire book and current market conditions, how aggressively should I act on this signal?" The 7-state classifier is a **sizing/aggressiveness overlay**, NOT a signal re-derivation.

| # | State | Description | Sizing Multiplier | Entry Aggressiveness |
|---|---|---|---|---|
| 1 | `calm_trend` | Low vol + clear directional trend, low cross-asset correlation | 1.0× NT size | Aggressive — enter at market or first brick |
| 2 | `choppy_range` | Mean-reverting, no sustained trend, low vol | 0.8× NT size | Patient — wait for brick confirmation |
| 3 | `high_vol_breakout` | High vol but directional conviction strong | 0.6× NT size | Cautious — limit at brick boundary, wider slippage tolerance |
| 4 | `regime_transition` | State-shifting detected (upstream `regime_shift==true` OR posterior entropy > threshold) | 0.3× NT size | Defensive — only enter on retest of breakout brick |
| 5 | `risk_off` | Crisis mode: cross-asset correlation > 0.75, VIX > 30, broad selloff | 0.0× NT size | Block new entries; manage existing |
| 6 | `funding_stress` | Crypto-specific: perp basis blowout, funding > 0.05%/8h, liquidation cascade | 0.2× NT size (crypto only) | Block new perp entries; close funding-negative |
| 7 | `liquidity_drained` | Thin book, wide spreads, low volume | 0.3× NT size | Maker-only if entering; expect high slippage |

**Inputs to the classifier (all configurable thresholds):**
- Upstream regime label (Noble Trader's `{vol}_{trend}` string) → mapped to one of states 1–3 primarily
- Cross-asset rolling correlation matrix → state 5 (`risk_off`) when mean |ρ| > `risk_off_corr_threshold` (default 0.75)
- Hyperliquid funding rate + open interest changes → state 6 (`funding_stress`) when annualized funding > `funding_stress_annualized_pct` (default 50%)
- L2 order book depth percentile + spread percentile → state 7 (`liquidity_drained`) when depth < `liquidity_depth_percentile` (default 10th pct)
- Regime shift flags from upstream + Hermes's own posterior entropy → state 4 (`regime_transition`) when entropy > `transition_entropy_threshold` (default 1.5 bits)
- Hermes's own Gaussian HMM on portfolio-level returns → primary classifier

**Output:** `{meta_regime, confidence, posterior_probs[7], sizing_multiplier, entry_aggressiveness, transition_probs}`

The classifier emits a `meta_regime.update.{symbol}` event whenever the dominant state changes OR confidence drops below `meta_regime_confidence_floor` (default 0.55).

#### 2.2.2 Sizing: Trust + Overlay (NOT re-derivation)

Hermes does **not** re-derive Kelly or Masaniello from scratch. Instead:

- **Baseline size** = Noble Trader's `effective_kelly` × equity × NT's implied stake
- **Portfolio overlay** = baseline × `sizing_multiplier` from §2.2.1 meta-regime table
- **Drawdown adjustment** = `clip(1 - portfolio_dd / max_portfolio_dd, 0.25, 1.0)` — reduces size as portfolio DD deepens
- **Final size** = min(overlaid_size, `max_position_size_pct` × equity, `risk_amount_cap` / stop_distance, remaining `max_gross_exposure_pct` headroom)

Optional (configurable, default on): **light Bayesian update** on NT's `effective_kelly` using Hermes's rolling hit-rate for that (symbol, meta_regime) pair. Prior = NT's effective_kelly; likelihood = Hermes's last-50-trades hit rate for this asset in this regime. This gently adjusts sizing based on Hermes's own execution quality, but never overrides NT's direction.

#### 2.2.3 Renko Entry Timing Engine (Hermes's core value-add)

This is where Hermes earns its keep. Given NT says "buy BTC-PERP, entry=$64,441, brick_size=$50, sl_bricks=3, tp_bricks=5", Hermes simulates renko bars from venue ticks and decides **exactly when and how to enter**.

**Sub-modules:**

- **Renko Bar Constructor**: builds renko bricks in real-time from L2 venue ticks using NT's `brick_size` as the brick height. Maintains rolling window of last 500 bricks per symbol. Also constructs bricks at ±0.5×, ±0.75×, ±1.25×, ±1.5× NT's brick_size for simulation comparison (configurable via `renko.simulation_multipliers`).
- **Brick Pattern Analyzer**: classifies the last N bricks into patterns (double-top, head-shoulders, breakout, pullback-to-support, reversal-brick, consolidation). Uses the same pattern taxonomy as NT's Renko pipeline (for consistency), but applied to Hermes's own brick construction.
- **Entry Timing Optimizer**: given the signal (direction, entry, stop, TP) and current brick pattern, computes the optimal entry moment:
  - `enter_now` — market order immediately (when pattern strongly confirms NT's direction)
  - `wait_for_brick_close` — wait for current brick to close, then enter at next tick (when pattern is ambiguous)
  - `wait_for_pullback` — wait for price to retrace to a brick boundary (when in `regime_transition` or `high_vol_breakout`)
  - `wait_for_retest` — wait for price to retest the breakout brick (when signal is a breakout)
  - `skip_entry` — signal window expired or pattern strongly contradicts NT's direction (rare; configurable override)
- **Execution Method Optimizer**: given the entry timing decision, selects the order routing:
  - `market` — for `enter_now` in `calm_trend` with low slippage risk
  - `limit_at_brick_boundary` — for `wait_for_brick_close` (post-only if venue supports, to capture maker rebate)
  - `twap_over_n_bricks` — for large sizes or `high_vol_breakout` (split across N brick closes, configurable N)
  - `iceberg` — for very large sizes or `liquidity_drained` (hide true size)
- **Entry Quality Scorer**: after entry, scores the entry quality vs NT's signal: `entry_alpha_bps = (NT_entry_price - actual_entry_price) / NT_entry_price * 10000` (positive = Hermes entered better than NT suggested). This is the key metric Hermes self-optimizes on.

**Output:** `signal.blended.{symbol}` = `{direction (from NT), entry_decision (timing + method), final_size, entry_price_target, stop_price, target_price, meta_regime, sizing_multiplier, entry_alpha_estimate, brick_pattern_at_entry, config_hash}`

#### 2.2.4 Position Management (post-entry)

Once entered, Hermes manages the position using its own renko-based logic:
- **Trailing stop** — ratchets stop to last brick boundary in profit direction (configurable: `trailing.method` = brick_boundary | atr | percentage)
- **Scenario projection** — continuous PnL distribution from L2.8's Scenario Path Runner
- **Exit timing** — when to exit at TP vs let it run (based on brick momentum, configurable)
- **Regime-change exit** — if meta-regime shifts to `risk_off` or `funding_stress` while in position, auto-close (configurable)

L4 is the only layer that emits trade decisions to L5. L5 never sees raw Noble Trader signals.

### 2.3 Portfolio & Risk Engine (L5)
- **Portfolio State Service**: positions, cash, unrealized/realized PnL, exposure by venue/asset/sector, beta, funding PnL
- **Risk Gate**: runs before every order (see §4)
- **Rebalancer**: triggers when drift > threshold or regime shift detected
- **Portfolio Optimizer**: mean-variance, risk parity, or Black-Litterman — runs on schedule or on regime change
- **Drawdown Tracker**: rolling peak, current DD, time-in-DD, recovery half-life

### 2.4 Execution Layer (L3)
- **Order State Machine**: `DRAFT → SUBMITTED → PARTIAL → FILLED / CANCELED / REJECTED / EXPIRED`
- **Smart Order Router**: per-venue TWAP/VWAP/iceberg/IOC/FOK
- **Slippage Modeler** (live and backtest): square-root impact model `slip = k * σ * sqrt(participation_rate)`
- **Post-Trade Analyzer**: realized vs expected fill, slippage attribution, maker/taker fee split
- **Reconciliation Engine**: hourly venue-vs-internal position reconciliation, auto-flag breaks

### 2.5 Backtesting Framework
- **Event-driven core** (not vectorized for fidelity; vectorized optional for parameter sweeps)
- **Historical data store**: Parquet partitioned by `venue/symbol/tf/date`, queried via DuckDB
- **Features**:
  - Walk-forward optimization with purged k-fold CV (López de Prado style)
  - Monte Carlo trade reshuffling for Sharpe confidence intervals
  - Deflated Sharpe Ratio to counter multiple-testing bias
  - Transaction cost + slippage + funding modeling per venue
  - Regime-tagged performance attribution
- **Output**: tear sheet, regime heatmaps, drawdown curves, exposure plots
- **Noble Trader replay mode**: backtester can replay historical Noble Trader heartbeats (from `signal_heartbeats` table) through the current Hermes stack — useful for evaluating what Hermes *would have done* with last month's signals using today's params.

### 2.6 Learning & Optimization Layer (L6)
- **Market Behavior Profiler**: per-asset distribution of returns, vol-of-vol, autocorrelation, Hurst exponent, tail index
- **Regime Memory**: persistent mapping `(asset, meta_regime) → optimal_strategy_params`
- **Parameter Optimizer**: Bayesian (Optuna/GP) or evolutionary; objective = risk-adjusted return net of costs
- **Scenario Simulator**: perturbs live prices ±Nσ to preview portfolio response
- **Feature Store**: vector of engineered features per bar, versioned
- **Model Registry**: versioned HMM models (Noble Trader's per-asset + Hermes's 7-state meta), Kelly priors, Masaniello configs

### 2.7 Logging, Audit, Analytics
- **Immutable Audit Log**: append-only ledger (every signal, order, fill, risk decision)
- **Metrics Export**: Prometheus for system metrics; DuckDB for trade-level KPIs
- **Replay Engine**: can replay any historical session tick-by-tick through the full stack, including Noble Trader heartbeats

### 2.8 Active Price Monitor (L2 extension)

A continuously-running service that watches live asset prices for every symbol in the trading universe, independent of whether a signal is currently active. This is the **eyes** of Hermes — it sees the market even when no signal is incoming.

**Why separate from L2 market data?** L2 is a passive data pipeline (subscribe, normalize, store). The Active Price Monitor is an **active analyzer** that runs computations on every tick and triggers events when conditions are met. It sits on top of L2 and publishes derived events.

**Subcomponents:**

- **Tick Aggregator**: builds rolling 1s / 5s / 1m / 5m / 15m / 1h bars from L2 ticks in real time. Maintains a rolling window of 500 bars per timeframe per symbol in memory.
- **Real-Time Indicator Engine**: continuously computes ATR(14), EMA(20/50/200), RSI(14), realized vol (5m / 1h / 1d), VWAP deviation, rolling Hurst exponent, z-score of last return vs 60d distribution.
- **Price Anomaly Detector**: triggers `price.anomaly.{symbol}` when (a) tick-to-tick return > 5σ of 60d distribution, (b) 1m realized vol > 99th percentile of 60d, (c) spread widens > 5× 60d median, (d) book imbalance flips > 3σ in 10s.
- **Stop-Loss / Take-Profit Watcher**: for every open position, continuously monitors live mark price vs the position's stop and target. Emits `position.stop_hit.{symbol}` or `position.target_hit.{symbol}` immediately on breach — does NOT wait for downstream polling. Triggers L5 risk gate evaluation automatically.
- **Trailing Stop Engine**: optional per-strategy trailing stops (ATR-based, %, or break-even-after-1R). Re-evaluated on every tick; emits `position.trail_update.{symbol}` when stop moves.
- **Scenario Path Runner**: for every open position, runs a continuous "what-if" projection: given current price, vol, and time-to-horizon, computes the probability distribution of PnL at exit. Triggers `position.pnl_warning.{symbol}` if 5%-tail PnL crosses -1R or -`risk_amount_cap`.
- **Cross-Price Monitor**: tracks correlations between assets in real time (rolling 1h correlation on 1m returns). Triggers `correlation.shift` when any pair's 1h correlation moves > 0.3 from 24h baseline — feeds into meta-regime state 5 (`risk_off`).
- **Liquidation Heatmap Watcher** (Hyperliquid only): subscribes to liquidation events; maintains a rolling heatmap of liquidation clusters by price level. Triggers `liquidation.cluster.{symbol}` when a cluster > $X notional forms within ±2% of current price — feeds into meta-regime state 6 (`funding_stress`).
- **Funding Rate Watcher** (Hyperliquid only): tracks current funding + 8h-annualized + predicted next funding. Triggers `funding.spike.{symbol}` when annualized funding > 50% (perp premium blowout) — feeds into meta-regime state 6.
- **Macro Clock**: tracks time-to-FOMC, time-to-CPI, earnings window per symbol, market session (pre-market / RTH / post-market / Asian / EU / US crypto). Emits `macro.event_window` 30 min before each event — used by L5 to optionally block new entries.

**Output channels (all published to Redis):**
- `price.tick.{symbol}` — every normalized tick (subscribed by L2 callers)
- `price.bar.{tf}.{symbol}` — closed bars
- `price.anomaly.{symbol}` — anomaly events
- `position.stop_hit.{symbol}` / `position.target_hit.{symbol}` / `position.trail_update.{symbol}` / `position.pnl_warning.{symbol}`
- `correlation.shift` / `liquidation.cluster.{symbol}` / `funding.spike.{symbol}` / `macro.event_window`

**Storage:** tick data and bars written to Parquet via L2; anomaly events and monitor state changes written to DuckDB `price_monitor_events` table (see §6.2.7).

**Performance target:** sub-50ms latency from tick arrival to event emission for stop/target watchers (positions need fast exits); <500ms for the heavier analytics (Hurst, correlation, heatmap).

### 2.9 Renko Simulation & Entry/Execution Optimization Engine (cross-cutting)

The **learning workhorse** for Hermes. Hermes does NOT re-run Noble Trader's strategy-level sweeps. Instead, this engine focuses narrowly on **entry/execution optimization**: given NT's signals, what entry timing and execution method would have maximized risk-adjusted PnL?

**What this engine optimizes (in scope):**
- Entry timing strategy (when within the signal window to pull the trigger)
- Execution method selection (market / limit / TWAP / post-only / iceberg)
- Renko simulation multipliers (testing ±variations around NT's brick_size for entry timing only — never replacing NT's brick_size for the live signal)
- Position management params (trailing stop method, exit timing logic, regime-change exit thresholds)
- Portfolio-level risk overlay params (7-state meta-regime thresholds, sizing multipliers per state)

**What this engine does NOT optimize (out of scope — NT owns these):**
- Strategy direction (buy/sell/neutral) — trust NT
- Renko brick_size for live signals — trust NT
- Stop-loss / take-profit brick counts — trust NT
- Kelly fraction or Masaniello base sizing — trust NT
- Per-asset HMM regime detection — trust NT
- EV Engine / p_win blending — trust NT

#### 2.9.1 Simulation Modes

| Mode | Trigger | Data Source | Goal |
|---|---|---|---|
| **Renko Replay** | Manual or scheduled | Historical venue ticks + stored NT heartbeats (from Supabase/DuckDB) | Reconstruct renko bars from historical ticks, replay NT signals, test entry timing strategies |
| **Walk-Forward** | Scheduled daily | Rolling window of history | Detect entry/execution strategy decay |
| **Entry Timing Sweep** | Manual or Hermes-triggered | Grid or Bayesian search over entry timing params | Find optimal entry timing config |
| **Execution Method Sweep** | Manual or Hermes-triggered | Grid over execution methods | Find optimal execution routing per (venue, size, regime) |
| **Monte Carlo Reshuffle** | After every sweep | Resampled trade sequence | Confidence intervals on entry alpha |
| **Stress Test** | On-demand or scheduled | Pre-defined shock scenarios | Validate DD limits under execution stress |
| **Shadow Mode** | Always-on (parallel) | Live market data, paper execution | Test new entry/execution config before live promotion |
| **Counterfactual** | Triggered per closed trade | Replay that trade under alternative entry/execution | "What if we'd waited for the brick close instead of market?" |
| **Regime Slice** | Hermes-triggered | Filter history by meta-regime | "How does my entry timing perform in `choppy_range`?" |

#### 2.9.2 Optimization Objectives

Primary objective (must be met):
```
Maximize:  Entry_Alpha = avg(actual_entry_price vs NT_suggested_entry_price, in bps)
           — positive means Hermes entered better than NT's signal suggested
Subject to:
  max_drawdown_pct       ≤ config.max_portfolio_drawdown_pct
  slippage_bps_avg       ≤ config.max_slippage_bps (per venue)
  fill_rate_pct          ≥ config.min_fill_rate (entries that actually got filled)
  Deflated_Sharpe        ≥ 1.0 (vs baseline of "enter at NT's price immediately, market order")
```

Secondary objective (tiebreaker among configs that meet primary):
```
Maximize:  realized_R_multiple
           + 0.5 * Sortino ratio
           + 0.3 * (1 - ulcer_index)
           - 0.5 * max_drawdown_pct
           - 0.2 * avg_slippage_bps
```

The **baseline** for comparison is always: "blindly execute NT's signal at market price the moment it arrives." Any entry/execution config Hermes proposes must beat this baseline after transaction costs and statistical rigor. If it can't, Hermes defaults to the baseline.

#### 2.9.3 Parameter Spaces (entry/execution only)

| Parameter Group | Examples | Search Space |
|---|---|---|
| **Entry timing** | `entry_strategy` per meta-regime (enter_now / wait_for_brick_close / wait_for_pullback / wait_for_retest) | Categorical per regime |
| **Entry timing thresholds** | `brick_confirmation_count` (1–5), `pullback_depth_brick_fraction` (0.25–1.0) | Continuous |
| **Execution method** | `execution_method` per (meta_regime, size_bucket, venue) | Categorical (market/limit/TWAP/iceberg/post_only) |
| **Execution params** | `twap_n_bricks` (1–10), `iceberg_child_size_pct` (5–25), `limit_offset_bps` (0–20) | Continuous |
| **Renko simulation multipliers** | multipliers for offline analysis only: [0.5, 0.75, 1.0, 1.25, 1.5] × NT brick_size | Fixed grid (never affects live brick_size) |
| **Trailing stop** | `trailing.method` (brick_boundary/atr/percentage), `trailing.atr_mult` (1.0–5.0), `trailing.brick_count` (1–5) | Categorical + continuous |
| **Exit timing** | `exit_strategy` (at_tp / trailing / brick_momentum / time_based), `exit.brick_momentum_threshold` | Categorical + continuous |
| **Regime-change exit** | `regime_exit_trigger_states` (which meta-regimes trigger auto-close) | Multi-select |
| **Meta-regime thresholds** | `risk_off_corr_threshold`, `funding_stress_annualized_pct`, `liquidity_depth_percentile`, `transition_entropy_threshold` | Continuous |
| **Sizing multipliers** | `sizing_multiplier` per meta-regime (0.0–1.5) | Continuous per state |
| **HMM retrain** | `hmm.n_components` (5/7/9), `hmm.retrain_frequency_days` | Categorical + integer |

#### 2.9.4 Optimizer Strategies

- **Bayesian (Optuna TPESampler)** — default for continuous spaces, 200–500 trials, expected improvement acquisition.
- **Grid search** — for small discrete spaces (e.g., entry strategy per regime), exhaustive.
- **CMA-ES** — for high-dimensional continuous spaces (10+ params), via `cma` library.
- **Asynchronous successive halving (ASHA)** — for fast pruning of bad trials via Optuna's `HyperbandPruner`.
- **Multi-objective (Pareto)** — when both entry alpha and drawdown matter, use Optuna's `NSGAIISampler` to surface the Pareto front.

#### 2.9.5 Statistical Rigor (Anti-Overfitting)

Every optimization result must pass ALL of:
1. **Walk-forward validation** — entry/execution params optimized on train slice must outperform baseline on out-of-sample test slice (purged k-fold, gap=10 bricks).
2. **Deflated Sharpe Ratio** — Hermes's entry alpha must produce Deflated Sharpe > 1.0 vs the "blindly execute at market" baseline.
3. **White's Reality Check** or **Hansen's SPA test** — must reject null of "no superior entry/execution vs baseline" at 5%.
4. **Monte Carlo reshuffle (1000 iterations)** — 5th-percentile entry alpha must exceed 0 bps.
5. **Bootstrap confidence interval** on entry alpha — lower bound must be > 0 bps.
6. **Regime coverage** — entry timing must show positive alpha in at least 4 of 7 meta-regimes (no single-regime specialist).
7. **Capacity check** — if backtested notional exceeds 10× median daily volume of any traded asset, flag as capacity-constrained (entry timing may not scale).
8. **Slippage sanity** — avg slippage must not exceed `max_slippage_bps` per venue; maker/taker mix must be physically achievable.

Failing any check → result is rejected; the trial is logged in `param_optimizations` with `status="rejected"` and the reason.

#### 2.9.6 Promotion Pipeline

```
New entry/execution config discovered by optimizer
   ↓
Passed all 8 rigor checks AND beat "blindly execute at market" baseline?
   ↓ Yes
Shadow mode for N days (configurable, default 7) — paper-trade in parallel
   ↓
Shadow entry alpha within 80% of backtest entry alpha? (no decay)
   ↓ Yes
Hermes writes hypothesis + promotion rationale to `hermes_hypotheses`
   ↓
Auto-promoter OR human review (based on autonomy tier from §3.5)
   ↓ Approved
New config_hash written to `config_history`, hot-loaded via Redis `config.update`
   ↓
Old config archived (still queryable in DuckDB for A/B comparison)
   ↓
If new config underperforms baseline in live for 14 days → auto-rollback
```

#### 2.9.7 Self-Learning Schedule (all configurable)

- **Daily EOD (configurable time, default 16:00 ET)**: walk-forward renko replay on last 90 days; if entry alpha decay > 20% vs prior 90 days, trigger entry timing sweep.
- **Weekly (configurable, default Sun 02:00 ET)**: full Bayesian optimization sweep across all tunable entry/execution params; produce Pareto front; surface top 3 candidates for shadow testing.
- **Monthly (configurable, default 1st of month)**: retrain 7-state meta-regime HMM on rolling 2-year window (using historical heartbeats from Supabase); compare to incumbent via walk-forward; promote if superior.
- **On regime shift**: if `meta_regime` stays in state 5/6/7 for > `regime_stress_trigger_hours` (default 2h), trigger immediate stress test of current portfolio + entry/execution params.
- **Hermes-initiated**: Hermes can submit a hypothesis at any time via `agent.command.optimize` channel; engine runs and writes back the verdict.

#### 2.9.8 Output Storage

Every optimization run writes to DuckDB `simulation_runs` (one row per run) and `simulation_trades` (one row per simulated trade). See §6.2.8 for schema. Accepted configs are written to `config_history` with the originating `simulation_run_id` for full traceability.

---

## 3. Configurable Trade Parameters

Centralized in YAML/TOML, hot-reloadable via Redis `config.update` channel. **Everything is configurable** with sensible defaults — the system runs out-of-the-box with the defaults below, but every parameter can be overridden via config file, environment variable, or Redis hot-reload.

### 3.0 Portfolio Allocation & Venue Mapping

```yaml
portfolio:
  # Target allocation by asset class (must sum to 1.0)
  # Forex venue TBD — allocation reserved but unallocated until venue added
  target_allocation:
    equities:    0.50    # Alpaca (stocks)
    crypto:      0.15    # Hyperliquid (perps + spot)
    commodities: 0.20    # Alpaca (commodity futures/ETFs)
    forex:       0.15    # FUTURE VENUE — not active until broker added

  # Rebalancing
  rebalance_threshold_drift_pct: 0.10    # trigger rebalance when allocation drifts >10%
  rebalance_frequency: "on_drift"        # on_drift | daily | weekly | monthly
  rebalance_method: "threshold"          # threshold | target_weight | risk_parity

  # Starting mode
  start_small: true                       # phase in assets gradually
  initial_symbols:                        # start with these, expand later
    - { symbol: "AAPL",        venue: "alpaca",      asset_class: "equities" }
    - { symbol: "BTC-PERP",    venue: "hyperliquid",  asset_class: "crypto" }
    - { symbol: "ETH-PERP",    venue: "hyperliquid",  asset_class: "crypto" }
    - { symbol: "GLD",         venue: "alpaca",      asset_class: "commodities" }

venues:
  # Venue registry — add new venues here, no code changes needed
  alpaca:
    enabled: true
    asset_classes: ["equities", "commodities"]
    credentials:
      api_key:   "secret:alpaca.api_key"        # resolved by SecretResolver (see §13)
      api_secret: "secret:alpaca.api_secret"
      base_url:  "secret:alpaca.base_url"
    rate_limit_per_min: 200
    data_modes:
      live: true
      historical: true                           # Alpaca historical bars API
    features:
      forex: false                                # Alpaca has no forex
      options: false
      shorting: true
      leverage: 4.0

  hyperliquid:
    enabled: true
    asset_classes: ["crypto"]
    credentials:
      wallet_address: "secret:hyperliquid.wallet_address"
      private_key:    "secret:hyperliquid.private_key"
      api_url:        "secret:hyperliquid.api_url"
      vault_address:  "secret:hyperliquid.vault_address"   # optional
    rate_limit_per_min: 1200
    data_modes:
      live: true
      historical: true                           # HL candles API
    features:
      perps: true
      spot: true
      funding: true
      post_only: true
      reduce_only: true
      max_leverage: 50.0

  # Future venues (commented out — uncomment when added)
  # oanda:
  #   enabled: false
  #   asset_classes: ["forex"]
  #   credentials:
  #     api_key:    "secret:oanda.api_key"
  #     account_id: "secret:oanda.account_id"
  #   features: { forex: true, leverage: 30.0 }
  # ibkr:
  #   enabled: false
  #   asset_classes: ["forex", "equities", "options"]
  #   credentials:
  #     host:     "secret:ibkr.host"
  #     port:     "secret:ibkr.port"
  #     client_id: "secret:ibkr.client_id"
  #   features: { forex: true, options: true }

upstream:
  noble_trader:
    redis:
      url:             "secret:noble_trader.redis_url"          # rediss:// for TLS
      channel:         "signal.raw.noble_trader"                # logical name, not secret
      consumer_group:  "hermes-l0"                              # logical name, not secret
    supabase:
      url:             "secret:supabase.url"
      key:             "secret:supabase.key"                    # service_role key, NOT anon
      # Actual NT Supabase tables (confirmed from NT schema):
      sweep_result_table: "nt_sweep_result"                     # weekly heavy + light sweeps: optimal brick_size/sl/tp per symbol
      regime_log_table:   "nt_regime_log"                       # periodic regime snapshots (every 5–15min per symbol)
      backfill_on_startup: true
      backfill_lookback_days: 365
      # See §6.2.10 for full schema of these tables

data_sources:
  # CRITICAL: venue-native data only, no third-party feeds
  policy: "venue_native_only"
  prohibited_sources:
    - "yfinance"
    - "alpha_vantage"
    - "iex"
    - "any_third_party_price_feed"
  rationale: "Each venue has its own prices; using third-party feeds creates arbitrage illusions and execution mismatches. Historical data must come from each venue's own historical API."
  fallback_behavior: "fail_hard"                  # never silently fall back to a third party
```

### 3.1 Account-Level
| Parameter | Default | Description |
|---|---|---|
| `max_portfolio_drawdown_pct` | 0.15 | Halt all new entries when portfolio DD crosses this |
| `daily_loss_limit_pct` | 0.03 | Hard stop for the trading day |
| `weekly_loss_limit_pct` | 0.06 | Cool-down trigger |
| `max_leverage_total` | 2.0 | Aggregate notional / equity cap |
| `max_gross_exposure_pct` | 1.50 | Gross / equity |
| `max_net_exposure_pct` | 0.50 | Net / equity (directional limit) |
| `margin_usage_limit_pct` | 0.70 | Margin used / available |
| `min_cash_buffer_pct` | 0.05 | Always keep this much cash/USDC |

### 3.2 Asset-Level
| Parameter | Default | Description |
|---|---|---|
| `max_position_size_pct` | 0.05 | Per-asset % of equity |
| `max_position_notional` | 25000 | Absolute cap in USD |
| `max_asset_drawdown_pct` | 0.08 | Per-asset DD kill switch |
| `per_asset_leverage_cap` | venue default | Override venue default leverage |
| `max_concentration_pct` | 0.15 | Single-asset weight in portfolio |
| `sector_exposure_cap` | 0.40 | e.g., max 40% in crypto |
| `venue_exposure_cap` | 0.60 | e.g., max 60% on Hyperliquid |

### 3.3 Signal & Entry/Execution-Level

**Signal acceptance (from Noble Trader):**
| Parameter | Default | Description |
|---|---|---|
| `signal_staleness_ms` | 30000 | Reject heartbeats older than this (NT publishes every 5–15min) |
| `min_edge_estimate_bps` | 5 | Reject signals with `ev_per_dollar` below this (in bps) |
| `reward_risk_min` | 1.5 | Min R:R (TP distance / stop distance) to take trade |
| `regime_filter_allowlist` | all 7 states | Which meta-regimes allow new entries |
| `tail_risk_action_override` | "more_conservative" | When NT and Hermes disagree on tail risk, take the more conservative |

**Entry timing (Hermes's optimization targets):**
| Parameter | Default | Description |
|---|---|---|
| `entry_strategy.calm_trend` | "enter_now" | Entry timing in calm_trend regime |
| `entry_strategy.choppy_range` | "wait_for_brick_close" | Entry timing in choppy_range |
| `entry_strategy.high_vol_breakout` | "wait_for_pullback" | Entry timing in high_vol_breakout |
| `entry_strategy.regime_transition` | "wait_for_retest" | Entry timing in regime_transition |
| `entry_strategy.risk_off` | "block" | Block new entries |
| `entry_strategy.funding_stress` | "block" | Block new crypto perp entries |
| `entry_strategy.liquidity_drained` | "maker_only" | Maker orders only |
| `brick_confirmation_count` | 2 | Bricks to confirm before entering (for wait strategies) |
| `pullback_depth_brick_fraction` | 0.5 | How deep a pullback to wait for (fraction of brick) |
| `signal_expiry_minutes` | 30 | After this many minutes, signal is stale and skipped |

**Execution method (Hermes's optimization targets):**
| Parameter | Default | Description |
|---|---|---|
| `execution.default_method` | "limit_at_brick" | Default order type |
| `execution.large_size_threshold_usd` | 10000 | Above this, use TWAP/iceberg |
| `execution.twap_n_bricks` | 3 | Number of bricks to TWAP over |
| `execution.iceberg_child_pct` | 10 | Iceberg child order size (% of total) |
| `execution.limit_offset_bps` | 0 | Offset from brick boundary for limit orders (in bps) |
| `execution.post_only_preference` | true | Prefer post-only when venue supports (maker rebate) |
| `max_slippage_bps` | 20 | Reject fills that would exceed this slippage |

**Position management:**
| Parameter | Default | Description |
|---|---|---|
| `trailing.method` | "brick_boundary" | Trailing stop method (brick_boundary / atr / percentage) |
| `trailing.atr_mult` | 3.0 | ATR multiplier (if method=atr) |
| `trailing.brick_count` | 1 | Trailing in bricks (if method=brick_boundary) |
| `exit.strategy` | "at_tp" | Exit strategy (at_tp / trailing / brick_momentum / time_based) |
| `exit.brick_momentum_threshold` | 0.5 | Momentum threshold for brick_momentum exit |
| `regime_exit.trigger_states` | ["risk_off", "funding_stress"] | Meta-regimes that trigger auto-close |
| `entry_alpha_target_bps` | 5 | Target entry alpha (Hermes's value-add metric) |

### 3.4 Circuit Breaker Thresholds
See §4. All thresholds configurable.

### 3.5 Autonomy Tiers

Defines how much rope Hermes gets before a human must approve. Every Hermes action passes through the `AutonomyGate` (in L5) before reaching L3 (execution). All thresholds configurable.

```yaml
autonomy:
  # Tier 0: autonomous, no human — read & analyze only
  tier_0:
    approval: none
    actions: ["query_duckdb", "run_backtest", "generate_report", "run_optimization"]

  # Tier 1: autonomous within size cap — small trades
  tier_1:
    approval: none
    constraints:
      max_notional_usd: 5000              # configurable
      max_position_pct_of_equity: 0.02    # configurable
      requires_shadow_days: 7             # for new configs
      requires_rigor_pass: 8              # all 8 checks from §2.9.5
    actions: ["enter_trade", "close_trade", "adjust_stop"]

  # Tier 2: auto-promote config, human notified
  tier_2:
    approval: notify_only
    constraints:
      max_size_change_pct: 50             # vs incumbent config
      requires_shadow_sharpe_pct: 80
    notify_channels: ["discord", "email"]
    review_window_hours: 24               # human can veto within 24h
    actions: ["promote_config"]

  # Tier 3: human approval required — large / novel
  tier_3:
    approval: required
    constraints:
      max_notional_usd: 25000
      or_novel_strategy: true             # strategy_id not seen in last 30d
    approval_timeout_hours: 4
    on_timeout: "skip"                    # if no human response, skip
    actions: ["enter_large_trade", "novel_strategy"]

  # Tier 4: hard block — structural changes
  tier_4:
    approval: human_only
    blocks:
      - "changing meta_regime HMM state count"
      - "disabling circuit breakers"
      - "increasing max_gross_exposure_pct beyond 2.0"
      - "promoting config that failed any rigor check"
      - "trading in risk_off / funding_stress / liquidity_drained regimes"
    actions: ["structural_change"]

  # Active hours (outside these, tier 1 degrades to tier 3)
  active_hours:
    timezone: "America/Los_Angeles"
    start: "09:30"                        # ET market open
    end: "16:00"                          # ET market close
    crypto_24_7: true                     # crypto trades outside active hours
    degrade_outside_hours: true           # downgrade tier 1 → tier 3
```

---

## 4. Circuit Breakers

### 4.1 Volatility Circuit Breaker (per-asset, pre-trade)
```
Inputs: atr_baseline (configurable lookback, default 20d ATR),
        atr_current (configurable window, default last 1h ATR),
        expected_edge (from NT signal ev_per_dollar)
Trigger when: atr_current / atr_baseline > vol_mult_threshold (default 2.5)
             OR expected_edge < k * atr_current (default k=0.3)
Action ladder (all levels configurable):
  - Level 1: reduce size by 50%
  - Level 2: new entries blocked, existing positions kept
  - Level 3: tighten stops to breakeven + slippage buffer
  - Level 4: liquidate (rare, only with confirmed meta_regime=risk_off)
```

### 4.2 Risk Circuit Breaker (portfolio + per-trade, pre AND post-trade)
```
Pre-trade checks (all must pass):
  - account_allocation: resulting exposure ≤ max_gross_exposure_pct
  - risk_fraction: position risk / equity ≤ risk_fraction_cap
  - risk_amount: $ at risk (stop distance × size) ≤ risk_amount_cap
  - reward_risk: expected_reward / risk_amount ≥ reward_risk_min

Post-trade monitors (continuous):
  - portfolio DD breach → halt + hedge
  - asset DD breach → close that asset
  - VaR / CVaR breach (rolling 1d, 99%) → de-risk
  - Correlation shock → reduce correlated exposure
  - Margin call proximity (within X% of liquidation on Hyperliquid) → emergency deleveraging
  - Funding rate spike → close funding-negative positions
  - Latency / venue-down → cancel all open orders, freeze entries
```

### 4.3 Global Kill Switch
- Hardware-style kill switch: stops new entries, cancels resting orders, optionally flattens
- Triggers: manual (Hermes or human), daily loss hit, venue connectivity loss > N sec, audit log write failure

---

## 5. Signal Pipeline (Redis Pub/Sub)

### 5.1 Upstream Heartbeat Schema (Noble Trader)

Hermes subscribes to Noble Trader's heartbeat channel. The heartbeat carries the **same schema** whether it's a true actionable signal or a keep-alive (the `signal` field will be `"neutral"` for keep-alives). Full schema reference:

| Field | Type | Description |
|---|---|---|
| `type` | literal `"heartbeat"` | Always "heartbeat" — other types may be added later |
| `symbol` | string | Trading symbol (e.g., "BTC-PERP", "AAPL") |
| `ts` | number (unix ms) | Upstream publish timestamp |
| `regime` | string | Noble Trader's composite regime label `{vol}_{trend}` (e.g., `low_vol_bull`) |
| `regime_conf` | number | HMM posterior probability of dominant state (0–1) |
| `signal` | string | `"buy"` / `"sell"` / `"neutral"` |
| `entry_price` | number | Suggested entry price (Renko-derived) |
| `stop_loss` | number | Stop-loss price |
| `take_profit` | number | Take-profit price |
| `aggression` | string | `"passive"` / `"mid"` / `"aggressive"` — routing hint |
| `brick_size` | number | Renko brick size used upstream |
| `sl_bricks` | number | Stop distance in bricks |
| `tp_bricks` | number | Target distance in bricks |
| `kelly_f` | number | Base Kelly fraction (full-Kelly, pre-cap) |
| `effective_kelly` | number | Capped Kelly actually used upstream |
| `ev` | number | Expected value |
| `ev_per_dollar` | number | EV normalized per dollar risked |
| `p_win` | number | EV Engine v4 blended P_win (regime + imbalance + TimesFM, log-odds pooled) |
| `p_regime` | number | HMM regime component of P_win |
| `p_imbalance` | number | L2 order book imbalance component |
| `p_markov` | number | Markov transition component (now in sizing, kept for audit) |
| `p_timesfm` | number \| null | TimesFM foundation model directional forecast (0–1, null if unavailable) |
| `timesfm_horizon` | string \| null | Forecast window label (e.g., `"12h"`) |
| `ev_scale` | number | EV-scaled Kelly multiplier |
| `markov_current_state` | string | `"UP"` / `"DOWN"` / `"FLAT"` |
| `tail_risk_score` | number \| null | 0 = none, 0.35 = mild, 0.60 = moderate, 0.85 = critical |
| `tail_risk_action` | string \| null | `"none"` / `"reduce_25"` (×0.75) / `"reduce_50"` (×0.5) / `"skip"` (×0.0) |
| `regime_shift` | string | `"true"` / `"false"` — did regime change this cycle? |
| `prev_regime` | string \| null | Previous regime label before shift |
| `shift_at` | number | Unix ms when shift was detected |
| `shifts_24h` | number | Number of regime shifts in last 24h |

**Notes on field semantics (from Noble Trader v7.5.0):**
- `regime` is a composite string like `low_vol_bull` — Noble Trader uses two 4-state HMMs (vol × trend = 16 cells), each cell maps to a risk multiplier 0.10×–1.75×.
- `p_win` uses log-odds pooling: `inv_logit(0.40 × logit(p_regime) + 0.25 × logit(p_imbalance) + 0.35 × logit(p_timesfm))`. When TimesFM is null, falls back to `0.40 × p_regime + 0.30 × p_imbalance + 0.30 × p_markov` (linear).
- `tail_risk_action` is upstream's recommendation — Hermes is free to override (e.g., escalate `reduce_50` → `skip` if Hermes's own meta-regime is `risk_off`).
- `regime_shift == "true"` should trigger immediate re-evaluation of all open positions for that symbol, not just sizing of new entries.
- Noble Trader publishes heartbeats every ~15s per symbol; the agent also receives a full config payload every 15 min and can react immediately.

### 5.2 Internal Redis Channels

Hermes maintains its own internal pub/sub bus. L0 is the **only** subscriber to the upstream Noble Trader channel; all other layers consume internal channels.

| Channel | Direction | Payload (sketch) |
|---|---|---|
| **(upstream)** `signal.raw.{strategy_id}` | Noble Trader → L0 | Heartbeat from §5.1 |
| `signal.raw.hermes.{symbol}` | L0 → L4 | Normalized + deduped heartbeat (post-staleness check) |
| `regime.shift.{symbol}` | L0 → L4/L5 | High-priority event when upstream `regime_shift == "true"` |
| `upstream.stale` | L0 → ops | Heartbeat gap > 60s detected |
| `md.tick.{venue}.{symbol}` | L2 → monitors | Normalized tick |
| `md.bar.{tf}.{symbol}` | L2 → monitors | Closed OHLCV bar |
| `md.book.{symbol}` | L2 → monitors | L2 order book snapshot |
| `md.funding.{symbol}` | L2 → monitors | Funding rate update (Hyperliquid) |
| `meta_regime.update.{symbol}` | L4 → L5 | Hermes's 7-state meta-regime classification |
| `signal.blended.{symbol}` | L4 → L5 | BEV object (Hermes's final trade decision) |
| `risk.decision.{order_id}` | L5 → L3 | `{approved, final_size, reason, limits_hit}` |
| `order.event.{order_id}` | L3 → audit | Order lifecycle transitions |
| `fill.event.{order_id}` | L3 → portfolio | Fill details |
| `circuit.breaker.{level}` | L5 → all | Trip notices |
| `price.anomaly.{symbol}` | L2.8 → L4/L5 | Price anomaly detected |
| `position.stop_hit.{symbol}` | L2.8 → L5 | Stop-loss price breached |
| `position.target_hit.{symbol}` | L2.8 → L5 | Take-profit price breached |
| `position.trail_update.{symbol}` | L2.8 → L5 | Trailing stop moved |
| `position.pnl_warning.{symbol}` | L2.8 → L5 | 5%-tail PnL crossed threshold |
| `correlation.shift` | L2.8 → L4 | Cross-asset correlation regime change |
| `liquidation.cluster.{symbol}` | L2.8 → L4 | Liquidation cluster formed (Hyperliquid) |
| `funding.spike.{symbol}` | L2.8 → L4 | Funding rate blowout (Hyperliquid) |
| `macro.event_window` | L2.8 → L5 | FOMC/CPI/earnings window starting soon |
| `config.update` | Ops → all | Hot-reload config deltas |
| `agent.command` | Hermes → Platform | `{pause, resume, flatten, override, optimize}` |
| `agent.optimize.request` | Hermes → Sim Engine | Hypothesis submission for evaluation |
| `agent.optimize.verdict` | Sim Engine → Hermes | Optimization result with metrics |

### 5.3 Signal Processing Pipeline (Heartbeat → Order)

```
1. Noble Trader publishes heartbeat to upstream Redis channel
   ↓
2. L0 subscriber receives, validates schema, dedupes by SHA-256 hash
   ↓
3. L0 staleness check: ts vs now() — reject if > signal_staleness_ms
   ↓
4. L0 writes raw heartbeat to DuckDB `signal_heartbeats` (immutable)
   ↓
5. L0 re-publishes on `signal.raw.hermes.{symbol}`
   ↓ (if regime_shift=="true") also publishes on `regime.shift.{symbol}`
   ↓
6. L4 receives normalized heartbeat
   ↓
7. L4 queries Hermes's own 7-state meta-regime HMM (updated continuously by L2.8 events)
   ↓
8. L4 re-derives Kelly (Bayesian prior = upstream effective_kelly, posterior from Hermes pnl_realized)
   ↓
9. L4 re-derives Masaniello stake (using Hermes's own p_win, not upstream p_win)
   ↓
10. L4 computes Hermes's own P_win via log-odds pooling (meta_regime + upstream_signal + microstructure)
    ↓
11. L4 BEV combiner produces final {action, conviction, size, entry, stop, target, regime_tag}
    ↓
12. L4 writes `signal.blended.{symbol}` to DuckDB `trade_signals_blended` + Redis channel
    ↓
13. L5 risk gate evaluates BEV against all account/risk/circuit-breaker params
    ↓
14. L5 writes `risk_decisions` row (approved or rejected + reason)
    ↓
15. If approved: L3 OMS creates draft order, submits to venue
    ↓
16. L3 writes `orders` + `order_events` rows
    ↓
17. Venue fill → L3 writes `fills` row, publishes `fill.event.{order_id}`
    ↓
18. L5 portfolio state updates; L2.8 stop/target watchers begin monitoring
    ↓
19. On stop/target/manual exit: L3 closes position, L5 writes `pnl_realized` row
    ↓
20. Hermes writes `trade_journal` postmortem (initial thesis + outcome + lessons)
```

### 5.4 Entry/Execution Decision Algorithm (Trust + Overlay)

Hermes does NOT re-derive the signal. It trusts Noble Trader's direction, entry, stop, TP, brick_size, and effective_kelly — then optimizes **when** to enter and **how** to route the order.

```
1. Receive normalized heartbeat H from L0
   - direction = H.signal (buy/sell/neutral) — TRUST NT
   - entry_price = H.entry_price — TRUST NT
   - stop_price = H.stop_loss — TRUST NT
   - target_price = H.take_profit — TRUST NT
   - brick_size = H.brick_size — TRUST NT
   - baseline_kelly = H.effective_kelly — TRUST NT

2. Receive meta-regime M from L4's 7-state classifier (§2.2.1)

3. Gate checks (any fail → action = "skip"):
   a. If H.signal == "neutral" → action = "hold" (no new entry)
   b. If M.sizing_multiplier == 0.0 (risk_off) → action = "skip", reason = "risk_off"
   c. If M == "funding_stress" AND venue == "hyperliquid" → action = "skip"
   d. If H.ts older than signal_staleness_ms → action = "skip", reason = "stale"
   e. Tail risk: if H.tail_risk_action == "skip" → action = "skip"
      (configurable override: if M != risk_off, can downgrade to "reduce_50")
   f. Reward:risk check:
      rr = |target - entry| / |entry - stop|
      if rr < reward_risk_min → action = "skip", reason = "rr_too_low"
   g. Edge floor:
      if H.ev_per_dollar * 10000 < min_edge_estimate_bps → action = "skip"

4. Sizing (trust + overlay, NOT re-derivation):
   baseline_size_usd = equity * baseline_kelly * M.sizing_multiplier
   dd_adjustment = clip(1 - portfolio_dd / max_portfolio_dd, 0.25, 1.0)
   size_after_dd = baseline_size_usd * dd_adjustment
   final_size_usd = min(size_after_dd,
                        equity * max_position_size_pct,
                        max_position_notional,
                        equity * max_gross_exposure_pct - current_exposure,
                        risk_amount_cap / (|entry - stop| / entry))

5. Entry timing decision (Hermes's core value-add — §2.2.3):
   entry_strategy = config.entry_strategy[M.state]   # e.g., "wait_for_brick_close"
   - Construct renko bars from venue ticks using H.brick_size
   - Analyze current brick pattern (breakout, pullback, consolidation, etc.)
   - Decide: enter_now | wait_for_brick_close | wait_for_pullback | wait_for_retest
   - If "wait_*": set entry timer = signal_expiry_minutes, monitor for trigger

6. Execution method decision (§2.2.3):
   if final_size_usd > execution.large_size_threshold_usd:
       method = "twap_over_n_bricks" (or "iceberg" if liquidity_drained)
   elif M == "liquidity_drained":
       method = "iceberg" or "post_only"
   elif entry_strategy == "wait_for_brick_close":
       method = "limit_at_brick_boundary" (post_only if supported)
   elif entry_strategy == "enter_now":
       method = "market" (if calm_trend) or "limit_at_brick" (if high_vol_breakout)
   else:
       method = config.execution.default_method

7. Autonomy gate (§3.5):
   tier = AutonomyGate.classify(final_size_usd, novelty, action_type)
   if tier.requires_approval:
       if not human_approves(tier.timeout): action = "skip", reason = "approval_timeout"

8. Final entry/execution decision output:
   {
     direction: H.signal,           # from NT
     entry_price_target: H.entry_price,  # from NT
     stop_price: H.stop_loss,       # from NT
     target_price: H.take_profit,   # from NT
     final_size_usd,
     meta_regime: M.state,
     sizing_multiplier: M.sizing_multiplier,
     entry_strategy,                # Hermes's timing decision
     execution_method,              # Hermes's routing decision
     brick_pattern_at_entry,        # Hermes's renko analysis
     expected_entry_alpha_bps,      # Hermes's estimate of entry quality
     autonomy_tier,
     config_hash
   }
```

The key shift from the original BEV: Hermes no longer computes its own P_win, Kelly, or Masaniello from scratch. It trusts NT's `effective_kelly` as the baseline, applies a portfolio-level multiplier from the 7-state meta-regime, and focuses its compute on **entry timing** (via renko simulation) and **execution method** (via order routing optimization). The `entry_alpha_bps` metric — how much better Hermes entered vs NT's suggested entry price — is the primary measure of Hermes's value-add.

---

## 6. Local Analytical Storage — DuckDB

DuckDB serves as the **single embedded analytical store** for everything that isn't hot-path market data. It is:
- **Embedded** — runs in-process with the trading platform, no separate server
- **Columnar + vectorized** — fast aggregation over millions of trade/signal rows
- **SQL + Python** — Hermes can query it directly with natural-language→SQL
- **Parquet-native** — can query historical Parquet market data files directly via `read_parquet()` without importing
- **ACID** — single-writer, multi-reader; safe for concurrent analytical queries while platform writes

### 6.1 Storage Roles

| Store | Purpose | Writer | Readers |
|---|---|---|---|
| `signal_heartbeats` | Every Noble Trader heartbeat received (immutable provenance chain) | L0 Upstream Subscriber | L4, backtest, Hermes, audit |
| `signal_heartbeats_quarantine` | Malformed heartbeats for forensic review | L0 Upstream Subscriber | Ops, audit |
| `trade_journal` | Human + Hermes readable per-trade narrative, hypotheses, lessons | Risk/Portfolio + Hermes | Hermes, dashboards |
| `account_snapshots` | Periodic (1m / 5m / EOD) equity, cash, exposure, margin snapshots | Portfolio Service | Analytics, Hermes, dashboards |
| `trade_signals_raw` + `trade_signals_blended` | Raw normalized + Hermes-blended signals with full provenance | L0 (raw) + L4 (blended) | Backtest, Hermes, audit |
| `order_executions` (orders / order_events / fills) | Full order lifecycle + fills + slippage + fees | Execution Layer | PnL, audit, reconciliation |
| `pnl_attribution` (realized / unrealized) | Realized / unrealized / funding / fees / slippage decomposed | PnL Service | Analytics, Hermes, dashboards |
| `price_monitor_events` | Active price monitor anomaly / stop-hit / correlation / funding events | L2.8 Active Price Monitor | L4, L5, Hermes, dashboards |
| `simulation_runs` + `simulation_trades` | Optimization runs + simulated trade-level results | Simulation Engine | Hermes, dashboards |
| `param_optimizations` | Every optimization trial (accepted / rejected) with metrics + rigor checks | Simulation Engine | Hermes, audit |
| `meta_regime_history` | Every meta-regime state change with posterior probs | L4 | Hermes, analytics |

### 6.2 Schema Design

#### 6.2.1 `trade_journal`
The narrative layer — what a human PM would write in a journal after each trade. Hermes adds its own reasoning and post-mortem.

```sql
CREATE TABLE trade_journal (
    journal_id           VARCHAR PRIMARY KEY,        -- UUID
    trade_id             VARCHAR NOT NULL,           -- FK to order_executions.trade_id
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,           -- alpaca | hyperliquid
    strategy_id          VARCHAR NOT NULL,
    direction            VARCHAR NOT NULL,           -- long | short
    regime_tag           VARCHAR,                    -- calm_up | trend_up | chop | trend_down | panic

    -- Narrative
    entry_thesis         TEXT,                       -- Hermes's pre-trade reasoning
    entry_conviction     DOUBLE,                     -- 0..1 from BEV
    entry_edge_estimate  DOUBLE,                     -- expected edge in bps
    entry_atr            DOUBLE,
    entry_stop_distance  DOUBLE,
    entry_target         DOUBLE,

    -- Outcome (filled in on close)
    exit_reason          VARCHAR,                    -- tp | sl | time | regime_change | manual | kill_switch
    exit_pnl             DOUBLE,
    exit_r_multiple      DOUBLE,                     -- realized R
    hold_duration_sec    INTEGER,
    max_favorable_exc    DOUBLE,                     -- MAE in $
    max_adverse_exc      DOUBLE,                     -- MFE in $

    -- Learning
    postmortem           TEXT,                       -- Hermes's post-trade analysis
    lessons              TEXT[],                     -- list of takeaways
    hypothesis_ids       TEXT[],                     -- link to hermes.hypotheses
    tags                 TEXT[],                     -- e.g., ['fomc_day', 'high_funding', 'corr_break']

    -- Audit
    opened_at            TIMESTAMPTZ NOT NULL,
    closed_at            TIMESTAMPTZ,
    created_by           VARCHAR NOT NULL,           -- hermes | human | auto
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_journal_symbol_time ON trade_journal (symbol, opened_at DESC);
CREATE INDEX idx_journal_strategy    ON trade_journal (strategy_id, opened_at DESC);
CREATE INDEX idx_journal_regime      ON trade_journal (regime_tag, opened_at DESC);
```

#### 6.2.2 `account_snapshots`
Periodic portfolio state — used for equity curves, DD tracking, exposure analysis.

```sql
CREATE TABLE account_snapshots (
    snapshot_id          VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    snapshot_type        VARCHAR NOT NULL,           -- 1m | 5m | 1h | eod | on_event

    -- Equity
    equity_total         DOUBLE NOT NULL,            -- cash + positions mark-to-market
    cash_usd             DOUBLE NOT NULL,            -- available cash (Alpaca)
    cash_usdc            DOUBLE NOT NULL,            -- available cash (Hyperliquid)
    margin_used          DOUBLE NOT NULL,
    margin_available     DOUBLE NOT NULL,
    leverage_gross       DOUBLE NOT NULL,            -- gross notional / equity
    leverage_net         DOUBLE NOT NULL,

    -- PnL (since last snapshot)
    realized_pnl         DOUBLE NOT NULL,
    unrealized_pnl       DOUBLE NOT NULL,
    funding_pnl          DOUBLE NOT NULL,
    fees_paid            DOUBLE NOT NULL,

    -- Exposure breakdown
    gross_exposure_usd   DOUBLE NOT NULL,
    net_exposure_usd     DOUBLE NOT NULL,
    long_exposure_usd    DOUBLE NOT NULL,
    short_exposure_usd   DOUBLE NOT NULL,
    n_open_positions     INTEGER NOT NULL,
    n_venues             INTEGER NOT NULL,

    -- Drawdown
    peak_equity          DOUBLE NOT NULL,
    drawdown_pct         DOUBLE NOT NULL,
    drawdown_usd         DOUBLE NOT NULL,
    time_in_dd_sec       INTEGER NOT NULL,

    -- Risk metrics
    var_1d_99            DOUBLE,                     -- Value at Risk
    cvar_1d_99           DOUBLE,                     -- Conditional VaR
    beta_to_spy          DOUBLE,

    -- Config hash (for reproducibility)
    config_hash          VARCHAR NOT NULL
);

CREATE INDEX idx_snap_ts     ON account_snapshots (ts DESC);
CREATE INDEX idx_snap_type   ON account_snapshots (snapshot_type, ts DESC);
```

#### 6.2.3 `trade_signals`
Every raw signal received and every blended signal emitted. This is the **provenance chain** — we can always reconstruct why a trade was taken.

```sql
-- Raw signals from external producers
CREATE TABLE trade_signals_raw (
    signal_id            VARCHAR PRIMARY KEY,
    ts_received          TIMESTAMPTZ NOT NULL,
    strategy_id          VARCHAR NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    direction            VARCHAR NOT NULL,           -- long | short | flat
    raw_size             DOUBLE,                     -- suggested size 0..1
    edge_estimate        DOUBLE,                     -- in bps
    horizon_sec          INTEGER,
    payload              JSON,                       -- full raw payload for audit
    signal_hash          VARCHAR NOT NULL            -- sha256 of payload for dedup
);

-- Blended signals emitted by L4 (post-HMM/Kelly/Masaniello)
CREATE TABLE trade_signals_blended (
    signal_id            VARCHAR PRIMARY KEY,        -- same as raw if 1:1
    ts_emitted           TIMESTAMPTZ NOT NULL,
    raw_signal_id        VARCHAR NOT NULL,           -- FK to raw

    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    strategy_id          VARCHAR NOT NULL,

    -- Regime
    regime_state         VARCHAR NOT NULL,
    regime_confidence    DOUBLE NOT NULL,            -- 0..1
    regime_probs         JSON NOT NULL,              -- full distribution

    -- Sizing
    kelly_fraction       DOUBLE NOT NULL,            -- computed kelly (pre-cap)
    kelly_capped         DOUBLE NOT NULL,            -- after cap
    masaniello_stake     DOUBLE NOT NULL,            -- stake for this trade in cycle
    masaniello_cycle_id  VARCHAR,                    -- which masaniello cycle
    masaniello_trade_num INTEGER,                    -- trade K of N

    -- Final blended output
    conviction_score     DOUBLE NOT NULL,            -- 0..1
    suggested_size_pct   DOUBLE NOT NULL,            -- % of equity
    suggested_size_usd   DOUBLE NOT NULL,
    entry_price_target   DOUBLE,
    stop_price           DOUBLE,
    target_price         DOUBLE,
    reward_risk          DOUBLE,
    expected_edge_bps    DOUBLE,

    -- Decision
    action               VARCHAR NOT NULL,           -- enter | skip | reduce | increase | close
    reject_reason        VARCHAR,                    -- if action=skip
    weights_used         JSON NOT NULL,              -- {regime:0.4, kelly:0.3, masaniello:0.3}
    config_hash          VARCHAR NOT NULL
);

CREATE INDEX idx_raw_ts        ON trade_signals_raw (ts_received DESC);
CREATE INDEX idx_raw_strategy  ON trade_signals_raw (strategy_id, ts_received DESC);
CREATE INDEX idx_bev_ts        ON trade_signals_blended (ts_emitted DESC);
CREATE INDEX idx_bev_symbol    ON trade_signals_blended (symbol, ts_emitted DESC);
CREATE INDEX idx_bev_action    ON trade_signals_blended (action, ts_emitted DESC);
```

#### 6.2.4 `order_executions`
Full order lifecycle. Multiple `order_events` per `order_id`; multiple orders per `trade_id`.

```sql
CREATE TABLE orders (
    order_id             VARCHAR PRIMARY KEY,
    trade_id             VARCHAR NOT NULL,           -- groups parent + child orders
    signal_id            VARCHAR,                    -- FK to blended signal (NULL for manual)
    ts_created           TIMESTAMPTZ NOT NULL,

    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    side                 VARCHAR NOT NULL,           -- buy | sell
    order_type           VARCHAR NOT NULL,           -- market | limit | stop | post_only
    time_in_force        VARCHAR NOT NULL,           -- GTC | IOC | FOK | DAY

    -- Requested
    qty_requested        DOUBLE NOT NULL,
    price_limit          DOUBLE,                     -- for limit orders
    leverage             DOUBLE,

    -- Filled (updated incrementally)
    qty_filled           DOUBLE NOT NULL DEFAULT 0,
    avg_fill_price       DOUBLE,
    status               VARCHAR NOT NULL,           -- draft | submitted | partial | filled | canceled | rejected | expired

    -- Routing
    algo                 VARCHAR,                    -- twap | vwap | iceberg | direct
    venue_order_id       VARCHAR,

    -- Cost
    total_fees           DOUBLE NOT NULL DEFAULT 0,
    total_slippage       DOUBLE NOT NULL DEFAULT 0,  -- vs arrival price
    maker_rebate         DOUBLE NOT NULL DEFAULT 0,

    -- Audit
    config_hash          VARCHAR NOT NULL,
    risk_decision_id     VARCHAR                     -- FK to risk_decisions
);

CREATE TABLE order_events (
    event_id             VARCHAR PRIMARY KEY,
    order_id             VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL,
    event_type           VARCHAR NOT NULL,           -- draft | submit | ack | partial_fill | fill | cancel | reject | expire
    payload              JSON NOT NULL,              -- venue-specific raw event
    seq_num              BIGINT NOT NULL             -- monotonic per order
);

CREATE TABLE fills (
    fill_id              VARCHAR PRIMARY KEY,
    order_id             VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    side                 VARCHAR NOT NULL,
    qty                  DOUBLE NOT NULL,
    price                DOUBLE NOT NULL,
    fee                  DOUBLE NOT NULL,
    fee_currency         VARCHAR NOT NULL,
    is_maker             BOOLEAN NOT NULL,
    liquidity            VARCHAR NOT NULL,           -- maker | taker
    arrival_price        DOUBLE NOT NULL,            -- for slippage calc
    slippage_bps         DOUBLE NOT NULL,
    venue_fill_id        VARCHAR
);

CREATE INDEX idx_orders_trade   ON orders (trade_id);
CREATE INDEX idx_orders_signal  ON orders (signal_id);
CREATE INDEX idx_orders_status  ON orders (status, ts_created DESC);
CREATE INDEX idx_events_order   ON order_events (order_id, seq_num);
CREATE INDEX idx_fills_ts       ON fills (ts DESC);
CREATE INDEX idx_fills_symbol   ON fills (symbol, ts DESC);
```

#### 6.2.5 `pnl_attribution`
Decomposed PnL — the analytical core. One row per (trade, day) for open positions; one row per closed trade.

```sql
CREATE TABLE pnl_realized (
    pnl_id               VARCHAR PRIMARY KEY,
    trade_id             VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    strategy_id          VARCHAR NOT NULL,
    regime_at_close      VARCHAR,

    -- Components (in USD)
    gross_pnl            DOUBLE NOT NULL,            -- (exit - entry) * qty, signed
    fees_total           DOUBLE NOT NULL,            -- entry + exit fees
    funding_pnl          DOUBLE NOT NULL,            -- for perps, accrues over hold
    slippage_cost        DOUBLE NOT NULL,            -- vs arrival price, both legs
    net_pnl              DOUBLE NOT NULL,            -- gross - fees - slippage + funding (if any)
    net_pnl_bps          DOUBLE NOT NULL,            -- net_pnl / notional * 10000

    -- R-multiple
    risk_amount          DOUBLE NOT NULL,            -- $ at risk (stop distance * qty)
    r_multiple           DOUBLE NOT NULL,            -- net_pnl / risk_amount

    -- Holding
    hold_duration_sec    INTEGER NOT NULL,
    n_fills              INTEGER NOT NULL,

    -- Attribution
    direction_pnl        DOUBLE,                     -- from directional move
    timing_pnl           DOUBLE,                     -- from entry timing
    sizing_pnl           DOUBLE,                     -- from position sizing
    regime_pnl           DOUBLE,                     -- regime-attributed

    config_hash          VARCHAR NOT NULL
);

CREATE TABLE pnl_unrealized (
    snapshot_id          VARCHAR PRIMARY KEY,        -- ties to account_snapshots
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    strategy_id          VARCHAR NOT NULL,
    position_qty         DOUBLE NOT NULL,
    avg_entry_price      DOUBLE NOT NULL,
    mark_price           DOUBLE NOT NULL,
    unrealized_gross     DOUBLE NOT NULL,
    unrealized_funding   DOUBLE NOT NULL,
    unrealized_fees_est  DOUBLE NOT NULL,
    unrealized_net       DOUBLE NOT NULL,
    position_notional    DOUBLE NOT NULL,
    position_risk        DOUBLE NOT NULL             -- current $ at risk
);

CREATE INDEX idx_pnl_realized_trade   ON pnl_realized (trade_id);
CREATE INDEX idx_pnl_realized_ts      ON pnl_realized (ts DESC);
CREATE INDEX idx_pnl_realized_strat   ON pnl_realized (strategy_id, ts DESC);
CREATE INDEX idx_pnl_realized_regime  ON pnl_realized (regime_at_close, ts DESC);
CREATE INDEX idx_pnl_unrealized_ts    ON pnl_unrealized (ts DESC);
CREATE INDEX idx_pnl_unrealized_sym   ON pnl_unrealized (symbol, ts DESC);
```

#### 6.2.6 `signal_heartbeats` (Noble Trader upstream provenance)

Every heartbeat received from Noble Trader, persisted **before** any downstream processing. This is the immutable provenance chain — we can always answer "what did Noble Trader tell us and when?"

```sql
CREATE TABLE signal_heartbeats (
    heartbeat_id         VARCHAR PRIMARY KEY,        -- UUID assigned by L0
    ts_received          TIMESTAMPTZ NOT NULL,       -- when L0 received it
    ts_upstream          TIMESTAMPTZ NOT NULL,       -- from heartbeat.ts (unix ms)
    lag_ms               INTEGER NOT NULL,           -- ts_received - ts_upstream
    dedup_hash           VARCHAR NOT NULL,           -- SHA-256(symbol, ts, signal, entry, stop, tp)

    -- Identity
    symbol               VARCHAR NOT NULL,
    strategy_id          VARCHAR NOT NULL,           -- from Redis channel name suffix
    type                 VARCHAR NOT NULL,           -- "heartbeat" (others later)

    -- Upstream signal
    signal               VARCHAR NOT NULL,           -- buy | sell | neutral
    entry_price          DOUBLE,
    stop_loss            DOUBLE,
    take_profit          DOUBLE,
    aggression           VARCHAR,                    -- passive | mid | aggressive

    -- Renko
    brick_size           DOUBLE,
    sl_bricks            DOUBLE,
    tp_bricks            DOUBLE,

    -- Upstream regime (Noble Trader's per-asset 4×4 HMM)
    regime               VARCHAR NOT NULL,           -- e.g., "low_vol_bull"
    regime_conf          DOUBLE NOT NULL,
    regime_shift         BOOLEAN NOT NULL,           -- parsed from "true"/"false"
    prev_regime          VARCHAR,
    shift_at             TIMESTAMPTZ,
    shifts_24h           INTEGER NOT NULL,

    -- Upstream EV engine v4
    ev                   DOUBLE,
    ev_per_dollar        DOUBLE,
    p_win                DOUBLE,
    p_regime             DOUBLE,
    p_imbalance          DOUBLE,
    p_markov             DOUBLE,
    ev_scale             DOUBLE,

    -- TimesFM
    p_timesfm            DOUBLE,                     -- NULL if unavailable
    timesfm_horizon      VARCHAR,

    -- Markov
    markov_current_state VARCHAR,                    -- UP | DOWN | FLAT

    -- Tail risk
    tail_risk_score      DOUBLE,
    tail_risk_action     VARCHAR,                    -- none | reduce_25 | reduce_50 | skip

    -- Kelly (upstream)
    kelly_f              DOUBLE,
    effective_kelly      DOUBLE,

    -- Raw payload (for full audit / replay)
    raw_payload          JSON NOT NULL,              -- full original JSON

    -- L0 processing result
    accepted             BOOLEAN NOT NULL,           -- false if stale/dup/malformed
    reject_reason        VARCHAR,                    -- stale | duplicate | malformed | schema_violation
    reprocessed_at       TIMESTAMPTZ                 -- if replayed later, when
);

CREATE INDEX idx_hb_ts_received   ON signal_heartbeats (ts_received DESC);
CREATE INDEX idx_hb_symbol        ON signal_heartbeats (symbol, ts_received DESC);
CREATE INDEX idx_hb_regime        ON signal_heartbeats (regime, ts_received DESC);
CREATE INDEX idx_hb_regime_shift  ON signal_heartbeats (regime_shift, ts_received DESC) WHERE regime_shift = TRUE;
CREATE INDEX idx_hb_signal        ON signal_heartbeats (signal, ts_received DESC);
CREATE INDEX idx_hb_dedup         ON signal_heartbeats (dedup_hash);

-- Quarantine for malformed heartbeats (separate table, for forensic review)
CREATE TABLE signal_heartbeats_quarantine (
    quarantine_id        VARCHAR PRIMARY KEY,
    ts_received          TIMESTAMPTZ NOT NULL,
    raw_payload          TEXT NOT NULL,              -- raw text as received
    parse_error          TEXT NOT NULL,              -- exception message
    schema_violations    TEXT[],                     -- list of failed fields
    resolved             BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at          TIMESTAMPTZ,
    resolution_note      TEXT
);

CREATE INDEX idx_quar_ts ON signal_heartbeats_quarantine (ts_received DESC);
```

#### 6.2.7 `price_monitor_events` (L2.8 Active Price Monitor)

Every event emitted by the Active Price Monitor (§2.8) is persisted for replay, analysis, and Hermes's self-learning. One row per event.

```sql
CREATE TABLE price_monitor_events (
    event_id             VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    event_type           VARCHAR NOT NULL,           -- anomaly | stop_hit | target_hit | trail_update
                                                       -- pnl_warning | correlation_shift
                                                       -- liquidation_cluster | funding_spike
                                                       -- macro_event_window
    severity             VARCHAR NOT NULL,           -- info | warning | critical

    -- Market context at event time
    last_price           DOUBLE NOT NULL,
    spread_bps           DOUBLE,
    book_imbalance       DOUBLE,                     -- (bid_qty - ask_qty) / (bid_qty + ask_qty)
    realized_vol_1m      DOUBLE,
    realized_vol_1h      DOUBLE,
    atr_14               DOUBLE,

    -- Event-specific payload
    payload              JSON NOT NULL,              -- varies by event_type, see below

    -- Linking
    position_id          VARCHAR,                    -- for stop_hit/target_hit/trail_update/pnl_warning
    related_symbols      TEXT[],                     -- for correlation_shift: the pair that shifted
    meta_regime_at_event VARCHAR                     -- meta-regime state at time of event
);

-- Payload examples by event_type:
-- anomaly:               {"trigger": "5sigma_return", "return_bps": 412, "z_score": 5.3, "window": "60d"}
-- stop_hit:              {"stop_price": 63250.0, "fill_price": 63248.5, "slippage_bps": 0.24, "position_qty": 0.5}
-- target_hit:            {"target_price": 65441.0, "fill_price": 65441.2, "slippage_bps": 0.03, "position_qty": 0.5}
-- trail_update:          {"old_stop": 63000.0, "new_stop": 63500.0, "trail_method": "atr_2x", "atr_at_update": 250.0}
-- pnl_warning:           {"pnl_now_usd": -350, "pnl_5pct_tail_usd": -1200, "prob_breach_stop": 0.42, "r_multiple_now": -0.7}
-- correlation_shift:     {"pair": ["BTC-PERP","ETH-PERP"], "corr_1h": 0.92, "corr_24h_baseline": 0.78, "delta": 0.14}
-- liquidation_cluster:   {"price_min": 63000, "price_max": 64200, "notional_usd": 12500000, "side": "long"}
-- funding_spike:         {"funding_8h": 0.0007, "annualized_pct": 76.65, "predicted_next": 0.0009}
-- macro_event_window:    {"event": "FOMC", "minutes_until": 28, "expected_volatility": "high"}

CREATE INDEX idx_pme_ts        ON price_monitor_events (ts DESC);
CREATE INDEX idx_pme_symbol    ON price_monitor_events (symbol, ts DESC);
CREATE INDEX idx_pme_type      ON price_monitor_events (event_type, ts DESC);
CREATE INDEX idx_pme_severity  ON price_monitor_events (severity, ts DESC);
CREATE INDEX idx_pme_position  ON price_monitor_events (position_id) WHERE position_id IS NOT NULL;
```

#### 6.2.8 `simulation_runs` + `simulation_trades` + `param_optimizations`

The Simulation & Parameter Optimization Engine (§2.9) writes one row per run, one row per simulated trade, and one row per optimization trial.

```sql
CREATE TABLE simulation_runs (
    run_id               VARCHAR PRIMARY KEY,        -- UUID
    ts_started           TIMESTAMPTZ NOT NULL,
    ts_finished          TIMESTAMPTZ,
    duration_sec         INTEGER,

    -- Run type
    mode                 VARCHAR NOT NULL,           -- backtest_replay | walk_forward | parameter_sweep
                                                       -- monte_carlo_reshuffle | stress_test | shadow
                                                       -- counterfactual | regime_slice
    triggered_by         VARCHAR NOT NULL,           -- schedule | hermes | manual | regime_shift
    hermes_hypothesis_id VARCHAR,                    -- FK if triggered by Hermes

    -- Data scope
    start_ts             TIMESTAMPTZ NOT NULL,
    end_ts               TIMESTAMPTZ NOT NULL,
    symbols              TEXT[] NOT NULL,
    venues               TEXT[] NOT NULL,
    regime_filter        VARCHAR,                    -- if regime_slice mode

    -- Config tested
    config_hash          VARCHAR NOT NULL,           -- FK to config_history
    config_json          JSON NOT NULL,              -- full config snapshot

    -- Performance metrics
    n_trades             INTEGER,
    win_rate             DOUBLE,
    avg_r_multiple       DOUBLE,
    sharpe               DOUBLE,
    sortino              DOUBLE,
    calmar               DOUBLE,
    max_drawdown_pct     DOUBLE,
    max_drawdown_usd     DOUBLE,
    profit_factor        DOUBLE,
    ulcer_index          DOUBLE,
    net_pnl_usd          DOUBLE,
    net_pnl_bps          DOUBLE,

    -- Statistical rigor (NULL if not computed)
    deflated_sharpe      DOUBLE,
    walk_forward_oos_sharpe DOUBLE,
    monte_carlo_5pct_sharpe DOUBLE,
    bootstrap_sharpe_lower DOUBLE,
    bootstrap_sharpe_upper DOUBLE,
    whites_reality_check_p DOUBLE,
    capacity_flag        BOOLEAN,

    -- Rigor pass/fail
    rigor_checks_passed  INTEGER,                    -- count of 7 checks passed
    rigor_checks_failed  TEXT[],                     -- list of failed check names
    accepted             BOOLEAN NOT NULL,           -- final verdict

    -- Promotion tracking
    promoted_to_shadow   BOOLEAN NOT NULL DEFAULT FALSE,
    shadow_started_at    TIMESTAMPTZ,
    shadow_ended_at      TIMESTAMPTZ,
    shadow_sharpe        DOUBLE,
    promoted_to_live     BOOLEAN NOT NULL DEFAULT FALSE,
    promotion_decision   VARCHAR,                    -- auto | human_approved | rejected | pending

    -- Audit
    error                TEXT,                       -- if run failed
    notes                TEXT
);

CREATE INDEX idx_sim_ts         ON simulation_runs (ts_started DESC);
CREATE INDEX idx_sim_mode       ON simulation_runs (mode, ts_started DESC);
CREATE INDEX idx_sim_config     ON simulation_runs (config_hash);
CREATE INDEX idx_sim_accepted   ON simulation_runs (accepted, ts_started DESC) WHERE accepted = TRUE;
CREATE INDEX idx_sim_promoted   ON simulation_runs (promoted_to_live, ts_started DESC) WHERE promoted_to_live = TRUE;

-- One row per simulated trade (so Hermes can query "show me all trades from run X")
CREATE TABLE simulation_trades (
    sim_trade_id         VARCHAR PRIMARY KEY,
    run_id               VARCHAR NOT NULL,           -- FK to simulation_runs
    trade_num            INTEGER NOT NULL,           -- sequence within run
    ts_opened            TIMESTAMPTZ NOT NULL,
    ts_closed            TIMESTAMPTZ,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    direction            VARCHAR NOT NULL,
    meta_regime          VARCHAR,
    upstream_regime      VARCHAR,

    -- Sizing (from BEV in this simulation)
    size_usd             DOUBLE NOT NULL,
    kelly_fraction       DOUBLE,
    masaniello_stake     DOUBLE,
    conviction_score     DOUBLE,

    -- Prices
    entry_price          DOUBLE NOT NULL,
    stop_price           DOUBLE NOT NULL,
    target_price         DOUBLE NOT NULL,
    exit_price           DOUBLE,
    exit_reason          VARCHAR,                    -- tp | sl | time | regime_change | trailing | manual

    -- PnL
    gross_pnl            DOUBLE,
    fees                  DOUBLE,
    slippage_cost        DOUBLE,
    funding_pnl          DOUBLE,
    net_pnl              DOUBLE,
    r_multiple           DOUBLE,
    hold_duration_sec    INTEGER,

    -- Attribution
    pnl_attribution      JSON                        -- {directional, timing, sizing, regime}
);

CREATE INDEX idx_simtr_run       ON simulation_trades (run_id, trade_num);
CREATE INDEX idx_simtr_symbol    ON simulation_trades (symbol, ts_opened DESC);
CREATE INDEX idx_simtr_regime    ON simulation_trades (meta_regime, ts_opened DESC);

-- One row per optimization TRIAL (within a parameter_sweep run, many trials)
CREATE TABLE param_optimizations (
    trial_id             VARCHAR PRIMARY KEY,        -- Optuna trial UUID
    run_id               VARCHAR NOT NULL,           -- FK to parent simulation_run (the sweep)
    ts                   TIMESTAMPTZ NOT NULL,
    trial_num            INTEGER NOT NULL,           -- sequence within sweep

    -- Parameters tried
    params               JSON NOT NULL,              -- {param_name: value, ...}

    -- Objective values
    objective_primary    DOUBLE,                     -- deflated_sharpe (or NaN if failed)
    objective_secondary  DOUBLE,                     -- combined score
    sharpe               DOUBLE,
    win_rate             DOUBLE,
    max_drawdown_pct     DOUBLE,
    calmar               DOUBLE,

    -- Rigor checks (per-trial)
    rigor_pass           BOOLEAN NOT NULL,
    rigor_failed_checks  TEXT[],
    reject_reason        VARCHAR,

    -- Status
    status               VARCHAR NOT NULL,           -- pending | running | complete | failed | pruned
    pruned_by            VARCHAR,                    -- hyperband | manual | NULL

    -- Verdict
    promoted_to_shadow   BOOLEAN NOT NULL DEFAULT FALSE,
    promoted_to_live     BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_opt_run         ON param_optimizations (run_id, trial_num);
CREATE INDEX idx_opt_status      ON param_optimizations (status, ts DESC);
CREATE INDEX idx_opt_promoted    ON param_optimizations (promoted_to_live) WHERE promoted_to_live = TRUE;
```

#### 6.2.9 `meta_regime_history` (Hermes's 7-state classifier)

Every time Hermes's 7-state meta-regime classifier changes state (or confidence drops below threshold), one row is written. This lets Hermes analyze "how good was my regime classification?" against subsequent PnL.

```sql
CREATE TABLE meta_regime_history (
    event_id             VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR,                    -- NULL for portfolio-wide regime
    scope                VARCHAR NOT NULL,           -- asset | portfolio

    -- Classification
    prev_state           VARCHAR,                    -- calm_trend | choppy_range | high_vol_breakout
                                                       -- regime_transition | risk_off
                                                       -- funding_stress | liquidity_drained
    new_state            VARCHAR NOT NULL,
    confidence           DOUBLE NOT NULL,            -- 0..1
    posterior_probs      JSON NOT NULL,              -- {state: prob, ...} for all 7 states
    transition_probs     JSON,                       -- 7x7 next-step transition matrix snapshot

    -- Inputs at classification time
    upstream_regime      VARCHAR,                    -- Noble Trader's regime for the symbol
    upstream_regime_conf DOUBLE,
    cross_asset_corr_mean DOUBLE,                    -- mean |rho| across portfolio
    funding_rate_8h      DOUBLE,                     -- if crypto
    book_depth_percentile DOUBLE,
    spread_percentile    DOUBLE,
    posterior_entropy    DOUBLE,                     -- for detecting uncertainty

    -- Why state changed
    trigger              VARCHAR NOT NULL,           -- posterior_change | confidence_drop
                                                       -- upstream_shift | correlation_shift
                                                       -- funding_spike | liquidity_drop | manual
    trigger_detail       JSON,                       -- trigger-specific metadata

    -- Outcome attribution (filled in later, after N minutes)
    pnl_5m_after         DOUBLE,
    pnl_15m_after        DOUBLE,
    pnl_1h_after         DOUBLE,
    correct_call         BOOLEAN                     -- did the new state correctly predict direction?
);

CREATE INDEX idx_mrh_ts       ON meta_regime_history (ts DESC);
CREATE INDEX idx_mrh_symbol   ON meta_regime_history (symbol, ts DESC) WHERE symbol IS NOT NULL;
CREATE INDEX idx_mrh_state    ON meta_regime_history (new_state, ts DESC);
CREATE INDEX idx_mrh_trigger  ON meta_regime_history (trigger, ts DESC);
```

#### 6.2.10 Noble Trader Upstream Tables (Supabase — read-only, NT-owned)

Hermes reads from two Noble Trader Supabase tables for historical analysis and HMM cold-start. These tables are **owned and written by Noble Trader** — Hermes only reads them. The schemas below are the **actual NT schemas** (confirmed from sample data), not Hermes-designed.

##### `nt_sweep_result` (NT-owned — heavy + light sweep results)

Weekly heavy sweeps + periodic light sweeps. Each row = one sweep result for one symbol, containing the optimal `brick_size` / `sl_bricks` / `tp_bricks` combination found by NT's grid search, plus the backtest performance metrics.

| Field | Type | Description |
|---|---|---|
| `id` | int | Primary key (NT-assigned) |
| `symbol` | string | e.g., "BTC", "AAPL" |
| `asset_class` | string | "crypto", "stocks", "commodities", "forex" |
| `brick_size` | float | Optimal renko brick size (e.g., 236.43 for BTC, 0.6459 for AAPL) |
| `sl_bricks` | int | Stop-loss distance in bricks |
| `tp_bricks` | int | Take-profit distance in bricks |
| `sharpe` | float | Backtest Sharpe ratio (⚠️ see data quality notes below) |
| `total_return` | float | Total return (e.g., 8.648 = 864.8%?) |
| `annual_return` | float | Annualized return |
| `max_drawdown_pct` | float | Max drawdown (⚠️ sign convention inconsistent — see notes) |
| `win_rate` | float | Win rate (0–1) |
| `n_trades` | int | Number of trades in backtest |
| `profit_factor` | float | Gross profit / gross loss (⚠️ 0 in some rows — see notes) |
| `regime` | string, nullable | NT's regime label `{vol}_{trend}` (e.g., "high_vol_strong_bull") or "unknown" |
| `regime_conf` | float, nullable | HMM posterior confidence (0–1) |
| `kelly_f` | float, nullable | Kelly fraction (e.g., 0.039–0.1) |
| `markov_p_up` | float | Markov P(up) — often 0.5 (uninformative) |
| `markov_p_dn` | float | Markov P(down) |
| `sweep_window` | string | "90d" (heavy) or "light" |
| `sweep_duration_ms` | int | How long the sweep took |
| `n_combos_tested` | int, nullable | Number of brick/sl/tp combos tested (null for light) |
| `error` | string, nullable | Error message if sweep failed |
| `sweep_timestamp` | timestamptz | When the sweep ran |
| `source` | string | "sweep-light" or "sweep-heavy" |

##### `nt_regime_log` (NT-owned — periodic regime snapshots)

Light sweeps every 5min (crypto/forex) / 15min (stocks/commodities). Each row = one regime classification snapshot per symbol. Same schema as `nt_sweep_result` but `sweep_window="light"` and `n_combos_tested=null`.

**Key difference from `nt_sweep_result`:** `nt_regime_log` is the high-frequency regime heartbeat (current state of the market per NT's HMM). `nt_sweep_result` is the lower-frequency strategy optimization result (best brick/sl/tp combo found).

##### DuckDB Mirror Tables (Hermes-owned — for offline analysis)

Hermes ingests from these Supabase tables into local DuckDB mirrors for fast offline analysis without hitting Supabase repeatedly. The mirrors are append-only and refreshed on a configurable schedule.

```sql
-- Mirror of nt_sweep_result (Hermes-owned copy in DuckDB)
CREATE TABLE nt_sweep_results_local (
    -- Provenance
    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_table         VARCHAR NOT NULL DEFAULT 'nt_sweep_result',

    -- All fields from NT schema (see above)
    nt_id                INTEGER NOT NULL,             -- NT's id field
    symbol               VARCHAR NOT NULL,
    asset_class          VARCHAR NOT NULL,
    brick_size           DOUBLE NOT NULL,
    sl_bricks            INTEGER NOT NULL,
    tp_bricks            INTEGER NOT NULL,
    sharpe               DOUBLE,
    total_return         DOUBLE,
    annual_return        DOUBLE,
    max_drawdown_pct     DOUBLE,
    win_rate             DOUBLE,
    n_trades             INTEGER,
    profit_factor        DOUBLE,
    regime               VARCHAR,
    regime_conf          DOUBLE,
    kelly_f              DOUBLE,
    markov_p_up          DOUBLE,
    markov_p_dn          DOUBLE,
    sweep_window         VARCHAR,
    sweep_duration_ms    INTEGER,
    n_combos_tested      INTEGER,
    error                TEXT,
    sweep_timestamp      TIMESTAMPTZ NOT NULL,
    source               VARCHAR,

    -- Hermes data quality checks (computed on ingest)
    dq_anomalies         TEXT[],                       -- e.g., ['sharpe_too_high', 'max_dd_zero', 'profit_factor_zero']
    dq_trusted           BOOLEAN NOT NULL DEFAULT TRUE -- false if anomalies are severe

    PRIMARY KEY (nt_id)
);

CREATE INDEX idx_ntsrl_symbol   ON nt_sweep_results_local (symbol, sweep_timestamp DESC);
CREATE INDEX idx_ntsrl_regime   ON nt_sweep_results_local (regime, sweep_timestamp DESC);
CREATE INDEX idx_ntsrl_trusted  ON nt_sweep_results_local (dq_trusted, sweep_timestamp DESC) WHERE dq_trusted = FALSE;

-- Mirror of nt_regime_log (Hermes-owned copy in DuckDB)
CREATE TABLE nt_regime_log_local (
    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_table         VARCHAR NOT NULL DEFAULT 'nt_regime_log',

    nt_id                INTEGER NOT NULL,
    symbol               VARCHAR NOT NULL,
    asset_class          VARCHAR NOT NULL,
    brick_size           DOUBLE NOT NULL,
    sl_bricks            INTEGER NOT NULL,
    tp_bricks            INTEGER NOT NULL,
    sharpe               DOUBLE,
    total_return         DOUBLE,
    annual_return        DOUBLE,
    max_drawdown_pct     DOUBLE,
    win_rate             DOUBLE,
    n_trades             INTEGER,
    profit_factor        DOUBLE,
    regime               VARCHAR,
    regime_conf          DOUBLE,
    kelly_f              DOUBLE,
    markov_p_up          DOUBLE,
    markov_p_dn          DOUBLE,
    sweep_window         VARCHAR,
    sweep_duration_ms    INTEGER,
    n_combos_tested      INTEGER,
    error                TEXT,
    sweep_timestamp      TIMESTAMPTZ NOT NULL,
    source               VARCHAR,

    -- Hermes enrichment: time-since-last-sweep per symbol (for §12.18 cadence awareness)
    minutes_since_last_sweep INTEGER,                  -- computed on ingest

    PRIMARY KEY (nt_id)
);

CREATE INDEX idx_ntrll_symbol   ON nt_regime_log_local (symbol, sweep_timestamp DESC);
CREATE INDEX idx_ntrll_regime   ON nt_regime_log_local (regime, sweep_timestamp DESC);
CREATE INDEX idx_ntrll_ts       ON nt_regime_log_local (sweep_timestamp DESC);
```

##### Data Quality Observations (from sample data)

Hermes must apply sanity checks on ingest because NT's sweep data has known anomalies:

| Anomaly | Example from sample | Likely cause | Hermes handling |
|---|---|---|---|
| `sharpe` absurdly high | BTC: 3726.00 | Possible overflow, division error, or different units | Flag `sharpe_too_high` if > 20; exclude from optimization baselines |
| `max_drawdown_pct = 0` | BTC: 0 | Impossible for n_trades > 0; backtest bug | Flag `max_dd_zero`; treat as null |
| `profit_factor = 0` | BTC: 0 | Should be ≥ 0; 0 means no wins or no losses (suspicious) | Flag `profit_factor_zero`; recompute from win_rate + avg_win/avg_loss if possible |
| `max_drawdown_pct` sign inconsistent | BTC: 0 vs AAPL: -0.0836 | Negative = drawdown (AAPL convention), positive = ??? (BTC) | Normalize to always-negative on ingest; flag if positive |
| `regime = "unknown"` | BTC early rows | HMM not yet fitted or cold-start | Acceptable; Hermes's 7-state meta-regime provides portfolio context |
| `regime_conf = 0.9999` | AAPL | Near-1.0 confidence is suspicious (overconfident HMM) | Acceptable but feed into Hermes's calibration analysis |
| `markov_p_up = 0.5` | BTC | Uninformative prior (Markov not contributing) | Acceptable; Hermes trusts NT's blended p_win which downweights Markov |
| `regime` says bull but strategy losing | AAPL: regime="high_vol_strong_bull", sharpe=-1.13 | Regime says bull but brick/sl/tp combo is losing — classic case where Hermes's meta-regime overlay adds value | **This is exactly the signal Hermes's 7-state classifier is designed to catch** — see §2.2.1 |

##### How Hermes Uses These Tables

| Table | Use cases |
|---|---|
| `nt_sweep_result` | (1) HMM cold-start training data; (2) understanding what brick_size/sl/tp NT is currently using per symbol; (3) backtest replay — replay historical NT signals through Hermes's entry/execution logic; (4) calibration analysis (NT's predicted win_rate vs actual) |
| `nt_regime_log` | (1) High-frequency regime history per symbol; (2) regime transition analysis (how often does NT flip regimes?); (3) cross-asset correlation of regime shifts; (4) input to Hermes's 7-state meta-regime classifier |
| `nt_sweep_results_local` (DuckDB mirror) | Fast offline queries without hitting Supabase; optimization engine reads this for walk-forward backtests |
| `nt_regime_log_local` (DuckDB mirror) | Same; plus `minutes_since_last_sweep` field for cadence-awareness (§12.18) |

##### Three NT Data Sources — Clarification

Hermes receives NT data via three distinct channels:

| Source | Channel | Frequency | Content | DuckDB destination |
|---|---|---|---|---|
| **Redis heartbeat** | Redis pub/sub | ~5–15min per symbol | Full trade signal: direction, entry, stop, TP, ev, p_win, kelly, regime, etc. (§5.1) | `signal_heartbeats` (§6.2.6) |
| **`nt_sweep_result`** | Supabase (pull) | Weekly heavy + light | Optimal brick_size/sl/tp per symbol + backtest metrics | `nt_sweep_results_local` (§6.2.10) |
| **`nt_regime_log`** | Supabase (pull) | 5min (crypto/forex) / 15min (stocks/commodities) | Regime classification snapshot per symbol | `nt_regime_log_local` (§6.2.10) |

The Redis heartbeat is the **real-time signal** that triggers trade decisions. The two Supabase tables are **historical context** used for HMM training, backtest replay, and analysis — they do not directly trigger trades.

### 6.3 Auxiliary Tables

```sql
-- Risk decisions (every pre-trade risk gate result, approved or rejected)
CREATE TABLE risk_decisions (
    decision_id          VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    signal_id            VARCHAR NOT NULL,
    approved             BOOLEAN NOT NULL,
    requested_size_usd   DOUBLE NOT NULL,
    approved_size_usd    DOUBLE NOT NULL,
    limits_hit           TEXT[],                     -- e.g., ['max_gross_exposure', 'risk_fraction_cap']
    reason               TEXT,
    circuit_breaker_level INTEGER,                   -- 0 = none, 1-4 from §4.1
    var_pre              DOUBLE,
    var_post             DOUBLE,
    config_hash          VARCHAR NOT NULL
);

-- Circuit breaker events (audit trail of every trip)
CREATE TABLE circuit_breaker_events (
    event_id             VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    breaker_type         VARCHAR NOT NULL,           -- volatility | risk | kill_switch
    level                INTEGER NOT NULL,
    symbol               VARCHAR,                    -- NULL for portfolio-wide
    trigger_value        DOUBLE NOT NULL,
    threshold            DOUBLE NOT NULL,
    action_taken         VARCHAR NOT NULL,           -- reduce | block | tighten | liquidate | halt
    payload              JSON
);

-- Hermes hypotheses (learning loop)
CREATE TABLE hermes_hypotheses (
    hypothesis_id        VARCHAR PRIMARY KEY,
    ts_created           TIMESTAMPTZ NOT NULL,
    hypothesis           TEXT NOT NULL,              -- e.g., "Kelly weight too high in choppy regime on BTC"
    rationale            TEXT,
    proposed_change      JSON,                       -- config delta
    backtest_result      JSON,                       -- sharpe, deflated_sharpe, etc.
    status               VARCHAR NOT NULL,           -- proposed | backtested | shadow | live | rejected | retired
    confidence           DOUBLE,
    promoted_at          TIMESTAMPTZ
);

-- Config history (every config version, for reproducibility)
CREATE TABLE config_history (
    config_hash          VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    config_json          JSON NOT NULL,
    source               VARCHAR NOT NULL,           -- file | hermes | human
    rationale            TEXT
);
```

### 6.4 DuckDB Usage Patterns

**Write path** (single-writer, append-only):
- All writes go through a single `DuckDBWriter` service with a write queue
- Uses batched INSERTs (every 1s or 1000 rows, whichever first)
- WAL enabled for durability on crashes
- DB file: `/home/z/my-project/data/hermes.duckdb` (single file, easy backup)

**Read path** (multi-reader):
- Hermes queries directly via `duckdb.connect('hermes.duckdb', read_only=True)`
- Dashboards connect read-only via Streamlit/Next.js
- Backtester reads historical Parquet via DuckDB's `read_parquet()` — no import needed

**Key analytical queries** Hermes runs:
```sql
-- Sharpe by regime per strategy
SELECT strategy_id, regime_at_close,
       avg(r_multiple) as avg_r,
       count(*) as n_trades,
       std(r_multiple) as std_r
FROM pnl_realized
GROUP BY 1, 2;

-- Worst 10 trades with thesis
SELECT t.symbol, t.entry_thesis, t.exit_reason, p.net_pnl, p.r_multiple
FROM trade_journal t
JOIN pnl_realized p ON t.trade_id = p.trade_id
ORDER BY p.net_pnl ASC LIMIT 10;

-- Slippage by venue
SELECT venue, avg(slippage_bps) as avg_slip, count(*)
FROM fills
GROUP BY venue;

-- Hypothesis win rate
SELECT h.hypothesis, h.status, count(*), avg(p.r_multiple)
FROM hermes_hypotheses h
JOIN trade_journal t ON h.hypothesis_id = ANY(t.hypothesis_ids)
JOIN pnl_realized p ON t.trade_id = p.trade_id
GROUP BY 1, 2;
```

### 6.5 Backup & Compaction
- Daily backup: `cp hermes.duckdb hermes.duckdb.YYYYMMDD.bak`
- Weekly compaction: `CHECKPOINT; VACUUM;` during Sunday maintenance window
- Monthly Parquet export of `pnl_realized` and `account_snapshots` for long-term cold storage
- Retention: live DuckDB keeps 90 days hot; older data lives in Parquet, queryable via DuckDB's `read_parquet()`

### 6.6 Schema Migrations
- Use `yuniql` or a simple Python migration runner with versioned SQL files in `db/migrations/`
- Every migration is forward-only; down-migrations require a restore from backup
- `schema_version` table tracks applied migrations

---

## 7. Self-Learning Loop for Hermes

A closed loop running on schedule (daily EOD + on regime change):

1. **Observe** — pull all signals, fills, PnL, slippage, regime tags from DuckDB
2. **Attribute** — decompose PnL by `{strategy, regime, asset, venue, signal_source}` using `pnl_attribution`
3. **Hypothesize** — Hermes generates hypotheses (e.g., "Kelly weight too high in choppy regime on BTC") → stored in `hermes_hypotheses`
4. **Backtest** — run the proposed parameter change through the backtester on the relevant regime slice
5. **Validate** — Deflated Sharpe, walk-forward, transaction-cost-aware
6. **Shadow trade** — run new config in paper mode for N days; record to DuckDB with `config_hash` tag
7. **Promote to live** — Hermes proposes, human (or rule-based auto-promoter) approves; new config_hash recorded in `config_history`
8. **Post-mortem** — every closed trade gets a `trade_journal` entry from Hermes with `postmortem` and `lessons` fields

This loop is the heart of "teaching Hermes to be a quant PM." Every hypothesis, test, and decision is persisted in DuckDB so Hermes can meta-learn from its own learning history.

---

## 8. Recommended Tech Stack

| Concern | Choice |
|---|---|
| Async runtime | `asyncio` + `uvloop` |
| Redis client | `redis-py` async, with streams for durability |
| WebSockets | `websockets`, `aiohttp` |
| HMM | `hmmlearn`, `pomegranate` |
| Bayesian opt | `Optuna`, `scikit-optimize` |
| Backtest | custom event-driven + `vectorbt` for sweeps |
| Dataframes | `polars` (faster than pandas for ticks) |
| **Analytical DB** | **DuckDB (embedded)** |
| Historical store | Parquet partitioned by `venue/symbol/tf/date` |
| Object store | MinIO / S3 for Parquet cold tier |
| Metrics | `prometheus-client`, Grafana |
| Tracing | OpenTelemetry |
| Config | `pydantic-settings` + Redis hot-reload |
| Math | `numpy`, `scipy`, `numba` for hot paths |
| ML | `scikit-learn`, `xgboost`, optional `torch` |
| Dashboard | Streamlit (internal) or Next.js (operator) |
| Scheduler | `APScheduler` or `Prefect` for pipelines |

---

## 9. Suggested Project Layout

```
trading_platform/
├── config/                  # YAML/TOML configs, regime presets
├── core/                    # enums, schemas, exceptions, clock
├── data/                    # adapters, normalization, Parquet writers
│   ├── alpaca/
│   ├── hyperliquid/
│   └── store/
├── signals/                 # HMM, Kelly, Masaniello, BEV combiner
├── risk/                    # circuit breakers, limits, VaR
├── portfolio/               # state, optimizer, rebalancer
├── execution/               # OMS, SOR, slippage model
├── backtest/                # engine, scenarios, walk-forward
├── learning/                # profiler, registry, hypothesis tracker
├── analytics/               # PnL attribution, tear sheets
├── audit/                   # immutable log, reconciliation
├── transport/               # Redis pubsub, schema registry
├── hermes/                  # agent-specific: decision journal, meta-learner
├── db/                      # DuckDB writer, migrations, queries
│   ├── migrations/
│   ├── writer.py
│   ├── queries.py
│   └── schema.sql
├── ops/                     # alerting, observability, secrets
└── app/                     # entrypoints: live, paper, backtest, replay
```

---

## 10. Implementation Roadmap (Phased)

### Phase 0 — Foundation (Weeks 1–2)
**Goal**: skeleton running, configs loaded, DuckDB schema in place.

- [ ] Project scaffold, linting, CI
- [ ] `pydantic-settings` config schema + hot-reload via Redis `config.update`
- [ ] DuckDB schema v1 (`db/schema.sql`): all tables from §6 (including `signal_heartbeats`, `price_monitor_events`, `simulation_runs`, `simulation_trades`, `param_optimizations`, `meta_regime_history`)
- [ ] Migration runner
- [ ] Redis pubsub envelope schema + schema registry
- [ ] Logging (JSON structured) + OpenTelemetry tracing
- [ ] Daily DuckDB backup cron

**Deliverable**: `platform init` boots, connects to Redis, opens DuckDB, writes a test row.

---

### Phase 1 — Upstream Ingestion (L0) (Weeks 3–4)
**Goal**: subscribe to Noble Trader heartbeats, persist to DuckDB, re-publish internally.

- [ ] Redis subscriber for upstream Noble Trader heartbeat channel (with consumer group)
- [ ] Heartbeat parser + validator (full schema from §5.1)
- [ ] Deduper (SHA-256 hash, 5s window)
- [ ] Staleness checker (configurable `signal_staleness_ms`, default 30s)
- [ ] Regime shift detector (publishes on `regime.shift.{symbol}`)
- [ ] DuckDB writer for `signal_heartbeats` + `signal_heartbeats_quarantine`
- [ ] Internal re-publisher on `signal.raw.hermes.{symbol}`
- [ ] Upstream stale detector (`upstream.stale` alert on heartbeat gap > 60s)
- [ ] Backpressure handler (drop oldest neutral heartbeats first)
- [ ] Replay mode: re-ingest from `signal_heartbeats` for testing

**Deliverable**: Noble Trader heartbeats flow into DuckDB; downstream layers can subscribe to `signal.raw.hermes.{symbol}`.

---

### Phase 2 — Market Data + Active Price Monitor (L2 + L2.8) (Weeks 5–7)
**Goal**: live market data + active price monitoring with anomaly / stop / target detection.

- [ ] Alpaca adapter (stocks + commodities): L1 quotes, trades, 1m bars
- [ ] Hyperliquid adapter: L2 book, trades, funding, 1m bars
- [ ] Normalization layer (unified `Tick`, `Bar`, `OrderBookL2`, `FundingRate`)
- [ ] Parquet writer partitioned by `venue/symbol/tf/date`
- [ ] Redis hot-tier caching (rolling 24h)
- [ ] DuckDB view `market.bars` reading Parquet via `read_parquet()`
- [ ] Backfill utility (historical data pull from both venues)
- [ ] **Active Price Monitor §2.8**:
  - [ ] Tick Aggregator (1s / 5s / 1m / 5m / 15m / 1h bars in memory)
  - [ ] Real-Time Indicator Engine (ATR, EMA, RSI, realized vol, VWAP, Hurst, z-score)
  - [ ] Price Anomaly Detector (5σ return, 99th pct vol, spread ×5, imbalance flip 3σ)
  - [ ] Stop-Loss / Take-Profit Watcher (sub-50ms latency target)
  - [ ] Trailing Stop Engine (ATR-based, %, break-even-after-1R)
  - [ ] Scenario Path Runner (continuous PnL distribution projection)
  - [ ] Cross-Price Monitor (rolling 1h correlation, `correlation.shift` events)
  - [ ] Liquidation Heatmap Watcher (Hyperliquid)
  - [ ] Funding Rate Watcher (Hyperliquid, `funding.spike` events)
  - [ ] Macro Clock (FOMC/CPI/earnings countdown)
- [ ] DuckDB writer for `price_monitor_events`

**Deliverable**: `platform stream --symbols AAPL,BTC-PERP` populates Redis + Parquet + DuckDB view; stop/target watchers fire on every position breach.

---

### Phase 3 — Signal Synthesis (L4) with 7-State Meta-Regime (Weeks 8–9)
**Goal**: consume Noble Trader heartbeats, enrich with Hermes's own 7-state meta-regime + BEV.

- [ ] Hermes's 7-state meta-regime HMM (calm_trend / choppy_range / high_vol_breakout / regime_transition / risk_off / funding_stress / liquidity_drained)
- [ ] Meta-regime inputs: upstream regime label + DCC cross-asset correlation + funding + L2 depth + posterior entropy
- [ ] Meta-regime publisher on `meta_regime.update.{symbol}`
- [ ] DuckDB writer for `meta_regime_history`
- [ ] Hermes's Kelly module (Bayesian prior = upstream `effective_kelly`, posterior from `pnl_realized`)
- [ ] Hermes's Masaniello module (cycle tracker, uses Hermes's own `p_win`)
- [ ] Hermes's P_win via log-odds pooling (`w_regime * meta_regime + w_signal * upstream_p_win + w_micro * microstructure`)
- [ ] BEV combiner (full algorithm from §5.4)
- [ ] DuckDB writer for `trade_signals_raw` + `trade_signals_blended`
- [ ] Publisher on `signal.blended.{symbol}`

**Deliverable**: Noble Trader heartbeat in → BEV out, queryable in DuckDB with full provenance.

---

### Phase 4 — Portfolio & Risk (L5) (Weeks 10–11)
**Goal**: portfolio state service, risk gate, circuit breakers, account snapshots.

- [ ] Portfolio State Service (positions, cash, exposure)
- [ ] Account snapshot writer (1m + on-event) → `account_snapshots`
- [ ] Risk gate (pre-trade, all checks from §4.2) — consumes BEV, never raw signals
- [ ] Volatility circuit breaker (4-level ladder, ATR baseline vs current)
- [ ] Risk circuit breaker (portfolio + per-asset)
- [ ] Global kill switch (Redis `agent.command` + manual)
- [ ] VaR / CVaR calculator (historical + parametric)
- [ ] DuckDB writers for `risk_decisions`, `circuit_breaker_events`

**Deliverable**: every BEV that survives the risk gate produces a `risk_decisions` row; trips produce `circuit_breaker_events` rows.

---

### Phase 5 — Execution (L3) (Weeks 12–13)
**Goal**: paper-trade end-to-end, full order lifecycle in DuckDB.

- [ ] Paper trading mode for both venues (simulated fills)
- [ ] Alpaca live execution adapter
- [ ] Hyperliquid live execution adapter (incl. post-only, reduce-only)
- [ ] Order State Machine + `orders` / `order_events` / `fills` writers
- [ ] Smart order router (TWAP, VWAP, iceberg, post-only)
- [ ] Slippage modeler (arrival price tracking)
- [ ] Reconciliation engine (hourly)
- [ ] Post-trade analyzer → fills out slippage_bps, is_maker, liquidity
- [ ] **Stop/Target Watcher integration** (L2.8 events trigger L3 exit orders automatically)

**Deliverable**: `platform trade paper` runs a strategy end-to-end with full DuckDB trail.

---

### Phase 6 — PnL & Analytics (Weeks 14–15)
**Goal**: PnL attribution, drawdown tracking, dashboards.

- [ ] PnL Service: realized + unrealized writers
- [ ] PnL attribution (directional / timing / sizing / regime)
- [ ] Drawdown tracker (peak, current, time-in-DD, recovery)
- [ ] Funding PnL accrual (perps)
- [ ] Streamlit dashboard: equity curve, DD, exposure, meta-regime heatmap, slippage by venue, R-distribution, upstream vs Hermes signal agreement
- [ ] Daily PnL report (auto-generated, saved to DuckDB + emailed)
- [ ] Tear sheet generator (quantstats-style)

**Deliverable**: `platform report daily` produces a tear sheet + DuckDB-populated analytics.

---

### Phase 7 — Backtesting + Noble Trader Replay (Weeks 16–17)
**Goal**: full event-driven backtester over historical Parquet + stored heartbeats.

- [ ] Event-driven backtest engine (replays Parquet bars through full L2–L5 stack)
- [ ] **Noble Trader replay mode**: replay historical heartbeats from `signal_heartbeats` table through current Hermes stack
- [ ] Vectorized fast path for parameter sweeps
- [ ] Walk-forward optimizer with purged k-fold CV
- [ ] Monte Carlo trade reshuffling
- [ ] Deflated Sharpe Ratio calculator
- [ ] Strategy comparator (multiple strategies × multiple meta-regimes)
- [ ] Backtest results → DuckDB `pnl_realized` with `backtest_run_id` tag

**Deliverable**: `platform backtest --replay-heartbeats --start 2024-01-01 --end 2024-12-31` produces a tear sheet showing what Hermes would have done with last month's signals.

---

### Phase 8 — Renko Simulation & Entry/Execution Optimization Engine (Weeks 18–20)
**Goal**: renko-driven entry/execution optimization, statistical rigor, shadow mode, promotion pipeline. Hermes does NOT replicate NT's strategy sweeps — only optimizes its own entry timing + execution method + position management.

- [ ] Renko bar reconstruction engine (build bricks from historical venue ticks using NT's brick_size from stored heartbeats)
- [ ] Simulation Engine core (9 modes from §2.9.1: renko_replay, walk_forward, entry_timing_sweep, execution_method_sweep, monte_carlo_reshuffle, stress_test, shadow, counterfactual, regime_slice)
- [ ] Supabase historical heartbeat puller (for renko replay + HMM cold-start)
- [ ] Optimizer integrations: Optuna TPESampler, Grid search, CMA-ES, ASHA, NSGA-II (Pareto)
- [ ] 8 statistical rigor checks (§2.9.5) — all vs "blindly execute at market" baseline
- [ ] DuckDB writers for `simulation_runs`, `simulation_trades`, `param_optimizations`
- [ ] Entry alpha calculator (actual entry price vs NT suggested entry, in bps)
- [ ] Shadow mode runner (parallel paper account, configurable N days, scaled-down 10% size)
- [ ] Auto-promotion gate (shadow entry alpha ≥ 80% of backtest entry alpha)
- [ ] Auto-rollback (if promoted config underperforms baseline in live for 14 days)
- [ ] Stress test library (GFC, COVID, LUNA, yen carry unwind, FOMC ±100bps)
- [ ] Counterfactual engine (replay closed trades under alternative entry/execution configs)
- [ ] Self-learning schedule: daily walk-forward renko replay, weekly entry/execution sweep, monthly HMM retrain (from Supabase heartbeats), regime-shift-triggered stress test
- [ ] Hermes-triggered hypothesis runner (`agent.optimize.request` / `agent.optimize.verdict`)

**Deliverable**: `platform optimize --symbols BTC-PERP,AAPL --horizon 90d` runs Bayesian sweep over entry/execution params, returns Pareto front of entry alpha vs drawdown, top 3 candidates enter shadow mode.

---

### Phase 9 — Hermes Agent Integration (Weeks 21–23)
**Goal**: Hermes reads DuckDB, generates hypotheses, writes journal entries, interacts with Sim Engine.

- [ ] Hermes natural-language → DuckDB query layer (read-only)
- [ ] Decision journal writer (every closed trade → `trade_journal` postmortem with `entry_thesis`, `postmortem`, `lessons`)
- [ ] Hypothesis tracker (`hermes_hypotheses` lifecycle: proposed → backtested → shadow → live / rejected / retired)
- [ ] Hermes → Sim Engine bridge (submit hypothesis via `agent.optimize.request`, receive verdict)
- [ ] Counterfactual reasoning (Hermes asks "what if we'd sized 2×?")
- [ ] Daily narrative generator (market commentary auto-drafted from DuckDB)
- [ ] Meta-regime reflection (Hermes reviews `meta_regime_history` and proposes classifier improvements)
- [ ] Human-in-the-loop UI for hypothesis approval (size-threshold-gated)

**Deliverable**: Hermes runs EOD analysis, proposes 1–3 hypotheses with backtest results, auto-promotes those that pass rigor + shadow.

---

### Phase 10 — Hardening & Ops (Weeks 24–26)
**Goal**: production-grade reliability.

- [ ] Multi-region failover (Alpaca East/West, Hyperliquid multi-API)
- [ ] Dead man's switch (heartbeat → auto-flatten)
- [ ] Vault / AWS Secrets Manager integration
- [ ] PagerDuty / Discord / Telegram alerting
- [ ] Replay / forensic mode (replay any session ID through full stack, including Noble Trader heartbeats)
- [ ] Schema migration CI tests
- [ ] Load test: 10k signals/sec, 1k fills/sec sustained, 100k heartbeats/day stored
- [ ] Disaster recovery runbook
- [ ] Upstream Noble Trader version compatibility tests (signal when Noble Trader ships breaking heartbeat schema change)

**Deliverable**: 99.9% uptime target met; full DR drill passes.

---

### Phase 11 — Advanced (Ongoing)
Selected from §11 based on priority:
- [ ] Tail risk hedging module
- [ ] Funding rate arbitrage detection
- [ ] Liquidation heatmap awareness (already in L2.8 — extend to trade decisions)
- [ ] Alternative data ingestion (COT, on-chain, ETF flows)
- [ ] Tax lot tracking (FIFO/LIFO/HIFO)
- [ ] Multi-strategy capital allocation
- [ ] Capacity modeling
- [ ] Beta hedging
- [ ] Bayesian Kelly prior updates (already partially in L4 — extend)
- [ ] CUSUM structural break detection
- [ ] Hermes meta-learning (learn from its own hypothesis history)

---

## 11. Features Missed in Initial Brief

### Execution & Microstructure
1. Order book imbalance features (L2 depth ratios)
2. Funding rate arbitrage detection (cash-and-carry spot vs perp)
3. Liquidation heatmap awareness
4. MEV / frontrunning protection (private mempool, randomized sizes)
5. Smart order routing across venues for same asset (e.g., BTC spot on Alpaca vs Hyperliquid)
6. Maker rebate optimization (prefer post-only when spread allows)
7. Partial fill / requote logic with retry budget

### Risk
8. Tail risk hedging (OTM options where available, inversely-correlated pairing)
9. Stress test library (GFC, COVID, LUNA, yen carry unwind, FOMC ±100bps)
10. Counterparty / venue risk monitor (outages, withdrawal pauses, validator changes)
11. Correlation regime detector (de-risk when 60d corr breaks between "diversifying" assets)
12. Currency / numéraire risk (USDC vs USD tracking)
13. Settlement risk (T+1 Alpaca vs instant Hyperliquid)
14. Drawdown recovery mode (size = (1 - DD/maxDD) until new high-water mark)

### Data & Features
15. Alternative data ingestion (COT, on-chain, ETF flows, sentiment, options skew)
16. Earnings / economic calendar awareness (block entries N min before FOMC/CPI/earnings)
17. Corporate actions handler (splits, dividends, mergers — Alpaca)
18. Token lifecycle events (airdrops, forks, delistings — Hyperliquid)
19. Volatility surface / term structure (where options data available)
20. Cross-asset features (BTC dominance, gold/SPX, yield curve) as regime inputs

### Portfolio Management
21. Tax lot tracking (FIFO/LIFO/HIFO) for real fund accounting
22. Multi-strategy capital allocation (rotate by Sharpe + capacity)
23. Capacity modeling (max AUM before alpha decays)
24. Beta hedging (auto-hedge equity via SPY shorts / ES futures)
25. Factor exposure tracking (value/quality/momentum/carry)
26. Cash drag management (sweep to T-bills / sT-bills / money market)

### Infrastructure & Ops
27. Time-series DB (TimescaleDB/QuestDB) — *DuckDB + Parquet now covers this role*
28. Backpressure / rate-limit handler per venue
29. Multi-region failover
30. Dead man's switch (heartbeat → auto-flatten)
31. Secrets management (Vault / AWS Secrets Manager)
32. Schema registry for Redis messages
33. Observability: structured logs, OpenTelemetry, Prometheus, Grafana
34. Alerting: PagerDuty/Discord/Telegram escalation
35. Replay / forensic mode (session ID → full state reconstruction)

### Quant / Analytics
36. Bayesian parameter updates (Kelly prior updated with posterior)
37. Regime transition probability matrix tracked over time
38. Feature importance drift detection (retrain HMM on shift)
39. PnL attribution: directional / vol / funding / slippage / fees / alpha
40. Risk-adjusted KPIs beyond Sharpe: Sortino, Calmar, Omega, Gain/Pain, MAR, ulcer
41. Trade clustering analysis (losses clustering → regime miss)
42. CUSUM on strategy returns (detect structural break early)

### Compliance & Audit
43. Trade blotter export (CSV/FIX for accountant/auditor)
44. Best execution attestation (venue, price, slippage vs NBBO per fill)
45. Restriction list (blocked symbols: sanctions, insider, halt)
46. Wash-trade detection (across venues)
47. Pre-trade compliance (sector caps, ESG screens, short-sale checks)
48. Immutable journal (hash-chained event log for tamper evidence)

### Hermes Agent Specific
49. Decision journal (every recommendation with inputs, reasoning, decision, outcome) — *now in `trade_journal`*
50. Hypothesis tracker (Hermes's market hypotheses scored over time) — *now in `hermes_hypotheses`*
51. Counterfactual engine (what if we didn't take this trade?)
52. Meta-learning layer (which strategies work in which regimes)
53. Human-in-the-loop approvals (size above threshold, novel strategy types)
54. Narrative generator (daily market commentary auto-drafted)

---

## 12. Open Decisions

1. **HMM regime count** — DECIDED: **7 states** per §2.2.1. The optimizer search space (§2.9.3) includes 5/7/9 as options so the Sim Engine can empirically test alternatives.
2. **Backtest fidelity level** — DECIDED: event-driven from day one (Phase 7), with vectorized fast path only for parameter sweeps inside the Sim Engine (Phase 8).
3. **Hermes autonomy boundary** — DECIDED: **configurable tiered autonomy** per §3.5. Default is size-capped ($5k autonomous, $25k human-approved, structural changes human-only), with shadow-first for config promotions. All thresholds configurable.
4. **DuckDB write strategy** — single-writer batched (current plan) vs. append-only WAL with separate compaction. Need load-test data. With NT publishing every 5min (crypto/forex) to 15min (stocks/commodities) and ~10 symbols, that's ~100k heartbeats/day — well within DuckDB's capability.
5. **Multi-tenant?** — DECIDED: **single instance, multi-strategy within one schema** using `strategy_id` partition. Avoids operational complexity.
6. **Cold storage split** — keep 90 days in DuckDB, older in Parquet (current plan); benchmark typical Hermes queries (1y of `pnl_realized` + `signal_heartbeats` join) once data exists.
7. **Portfolio allocation** — DECIDED: 50% equities / 15% crypto / 20% commodities / 15% forex, configurable in §3.0. Forex 15% reserved but unallocated until a forex venue (OANDA/IBKR) is added. Starting with small asset portfolio (AAPL, BTC-PERP, ETH-PERP, GLD) per §3.0 `initial_symbols`.
8. **Noble Trader upstream Redis connection** — still open. Options: (a) shared Redis instance, (b) Redis-to-Redis bridge, (c) direct cross-network subscription. Need to know: where NT runs, where Hermes will run, whether NT Redis is Upstash/local/self-hosted, and whether you have admin access to NT Redis config.
9. **Heartbeat schema evolution** — still open. NT is at v7.5.0 and actively iterating. Recommend: version field in payload + CI tests against latest NT release. Need your preference on coupling tightness.
10. **Upstream `regime_shift == "true"` semantics** — still open. Need to check NT source/docs: does it mean "regime changed in this heartbeat" or "regime has changed recently and this is the first heartbeat since"? Affects whether we re-evaluate open positions on every such heartbeat or only the first.
11. **Tail risk action override policy** — DECIDED: **`more_conservative`** (configurable in §3.3). When NT and Hermes disagree, take the more conservative of the two.
12. **7-state meta-regime training data** — DECIDED: **use historical NT heartbeats from Supabase** (Option 3 from earlier proposal). Pull 1–2 years of heartbeats, construct portfolio returns from venue-native historical price data, train HMM. Retrain monthly. Supabase access pattern covered in §13. **Supabase table names CONFIRMED from NT schema**: `nt_sweep_result` (heavy + light sweeps with optimal brick/sl/tp) and `nt_regime_log` (periodic regime snapshots). Full schemas in §6.2.10. Still need: Supabase URL + service_role key (paper keys OK) for actual connection testing.
13. **Simulation Engine compute budget** — DECIDED: **separate worker process** communicating via DuckDB + Redis. A runaway optimization can't affect live trading.
14. **Shadow mode sizing** — DECIDED: **scaled-down (10% of live size cap)** with explicit `shadow_run_id` tag on every simulated trade for clean attribution. Configurable.
15. **Forex venue selection** — still open. 15% of portfolio is reserved for forex but no venue yet. Candidates: OANDA (easy API, retail), IBKR (institutional, more asset classes), FXCM. Need your preference and timeline. Alpaca confirmed to have no forex.
16. **Venue-native data enforcement** — DECIDED: **venue-native only, no third-party feeds** (§3.0 `data_sources.policy = "venue_native_only"`). yfinance/alpha_vantage/etc. are prohibited. Historical data must come from each venue's own historical API. Fail-hard on any fallback attempt.
17. **Renko simulation multipliers** — for offline analysis, Hermes tests brick_size at ±0.5×, ±0.75×, ±1.25×, ±1.5× NT's suggested brick_size. These NEVER replace NT's brick_size for live signals — only used to study entry timing sensitivity. Configurable in `renko.simulation_multipliers`.
18. **NT sweep cadence awareness** — NT does weekly full sweeps + 5min (crypto/forex) / 15min (stocks/commodities) light sweeps. Hermes should be aware of this cadence: signals may be "stale-er" right before a sweep and "fresher" right after. Consider: weight signal conviction by time-since-last-sweep. Configurable, default off (trust NT's own `ts` field). The `nt_regime_log_local` table now includes a `minutes_since_last_sweep` field (§6.2.10) to enable this.
19. **NT data quality issues** — DISCOVERED from sample data (§6.2.10): NT's `nt_sweep_result` has known anomalies (BTC: sharpe=3726, max_dd=0, profit_factor=0; AAPL: regime="high_vol_strong_bull" but sharpe=-1.13 and strategy losing). Hermes must apply data quality checks on ingest (`dq_anomalies` + `dq_trusted` fields in `nt_sweep_results_local`). **Insight**: the AAPL case (regime says bull but strategy losing) is exactly the gap Hermes's 7-state meta-regime is designed to fill — when NT's per-asset regime and strategy performance disagree, Hermes's portfolio-level regime overlay can catch the discrepancy and reduce sizing. This is Hermes's value-add made concrete.

---

## 13. Credentials & Secrets Management

**Principle:** One source of truth, never in git, swappable backends. Application code calls `get_secret("alpaca.api_key")` and never knows (or cares) whether that value came from a `.env` file, an environment variable, HashiCorp Vault, or AWS Secrets Manager. The backend is a config-time choice, not a code-time choice.

### 13.1 The SecretResolver Abstraction

```python
# hermes/core/secrets.py
from typing import Protocol

class SecretBackend(Protocol):
    def get(self, key: str) -> str: ...

class EnvFileBackend:
    """Reads from .env file via python-dotenv. Default for local dev."""
    ...

class EnvBackend:
    """Reads from os.environ. For Docker, CI, serverless."""
    ...

class VaultBackend:
    """Fetches from HashiCorp Vault at startup, caches in memory."""
    ...

class AwsSmBackend:
    """Fetches from AWS Secrets Manager. For production on AWS."""
    ...

def get_secret(key: str) -> str:
    """Single entry point. Routes to backend based on SECRETS_BACKEND env var."""
    return _backend.get(key)
```

Switching backends = changing one env var (`SECRETS_BACKEND=vault`). Zero code changes.

### 13.2 Backend Selection Ladder

| Backend | When to use | How it works |
|---|---|---|
| `env_file` | Local dev (default) | Reads `.env` file with `python-dotenv` |
| `env` | Docker, CI, serverless | Reads from `os.environ` directly |
| `vault` | Production, multi-user | Fetches from HashiCorp Vault at startup, caches in memory |
| `aws_sm` | Production on AWS | Fetches from AWS Secrets Manager |

```
Local dev (now)        →  .env file
                          ↓ when you deploy to a server
Single server          →  environment variables in systemd unit
                          ↓ when you have multiple services/users
Multi-service prod     →  HashiCorp Vault (self-hosted or HCP)
                          OR
                          AWS Secrets Manager (if on AWS)
                          ↓ when you have compliance requirements
Compliance (SOC2/etc)  →  Vault + auto-rotation + audit log
```

### 13.3 `.env.example` (committed — template only)

```bash
# === Secrets backend selection ===
SECRETS_BACKEND=env_file                  # env_file | env | vault | aws_sm
SECRETS_ENV_FILE_PATH=./.env

# === Noble Trader upstream (Redis — real-time heartbeats) ===
# Placeholder URLs — replace with actual values in .env
NOBLE_TRADER_REDIS_URL=redis://<nt-redis-host>:<port>
NOBLE_TRADER_REDIS_CHANNEL=signal.raw.noble_trader
NOBLE_TRADER_REDIS_CONSUMER_GROUP=hermes-l0

# === Noble Trader upstream (Supabase — historical sweeps + regime logs) ===
# Placeholder URL — replace with actual Supabase project URL in .env
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_KEY=<service-role-key>                              # service_role, NOT anon
SUPABASE_SWEEP_RESULT_TABLE=nt_sweep_result                  # confirmed from NT schema
SUPABASE_REGIME_LOG_TABLE=nt_regime_log                      # confirmed from NT schema

# === Alpaca (stocks + commodities) — paper trading keys ===
# Get paper keys: https://app.alpaca.markets/paper/dashboard/overview
ALPACA_API_KEY=<paper-api-key>
ALPACA_API_SECRET=<paper-api-secret>
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets

# === Hyperliquid (crypto) — dedicated trading wallet ===
# Generate a NEW wallet for trading; never reuse your main wallet
HYPERLIQUID_WALLET_ADDRESS=<0x-your-dedicated-trading-wallet>
HYPERLIQUID_PRIVATE_KEY=<your-dedicated-wallet-private-key>
HYPERLIQUID_API_URL=https://api.hl.cyber
HYPERLIQUID_VAULT_ADDRESS=                                   # optional, if trading via vault

# === Hermes infrastructure ===
HERMES_DUCKDB_PATH=./data/hermes.duckdb
HERMES_REDIS_URL=redis://localhost:6379/1                     # separate DB number from NT
HERMES_LOG_LEVEL=INFO

# === Notifications (optional) ===
DISCORD_WEBHOOK_URL=
TELEGRAM_BOT_TOKEN=

# === Future: forex venue (uncomment when added) ===
# OANDA_API_KEY=
# OANDA_ACCOUNT_ID=

# === Vault / AWS SM (only when SECRETS_BACKEND is vault or aws_sm) ===
# SECRETS_VAULT_ADDR=
# SECRETS_VAULT_TOKEN=
# SECRETS_AWS_REGION=
```

### 13.4 Config References Use Logical Names

In `config/default.yaml`, secrets are referenced by logical name with the `secret:` prefix. The config loader resolves them at startup:

```yaml
venues:
  alpaca:
    credentials:
      api_key:   "secret:alpaca.api_key"
      api_secret: "secret:alpaca.api_secret"
      base_url:  "secret:alpaca.base_url"
```

Anything without the `secret:` prefix is treated as a literal value. This keeps secrets out of YAML files (which may be committed) while keeping non-sensitive config inline.

### 13.5 `.gitignore` (mandatory)

```gitignore
# Secrets — never commit
.env
.env.local
.env.*.local
secrets/
*.pem
*.key

# Data — contains account state, PnL, trade history
*.duckdb
*.duckdb.wal
data/

# Logs may contain redacted-but-sensitive data
logs/
*.log
```

### 13.6 Pre-Commit Secret Scanning

Add `detect-secrets` (or `trufflehog`) as a pre-commit hook to block accidental commits:

```bash
# Install
pip install detect-secrets
pre-commit install

# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.4.0
    hooks:
      - id: detect-secrets
        args: ['--baseline', '.secrets.baseline']
```

Also enable:
- GitHub secret scanning (free for public repos, built-in)
- GitHub push protection (blocks pushes containing known secret patterns)
- Branch protection on `main` (blocks direct pushes)

### 13.7 Full Credential Inventory

| Phase | Credential | Source | Notes |
|---|---|---|---|
| 0–1 (Foundation + Upstream) | NT Redis URL | NT operator | `rediss://` for TLS; includes password if any |
| 0–1 | NT Redis channel name | NT config | Non-sensitive, but confirm exact name |
| 0–1 | Supabase URL + service_role key | Your Supabase project | service_role, NOT anon — needed for cross-user read |
| 0–1 | Supabase table names | NT schema | Confirm `trading_config` + `ta_backtest_result` |
| 2 (Market Data) | Alpaca API key + secret | https://app.alpaca.markets/paper/dashboard/overview | Paper keys free, instant |
| 2 | HL wallet address + private key | Generate dedicated wallet | Never reuse main wallet; fund with small USDC for paper |
| 2 | HL API URL | HL docs | `https://api.hl.cyber` (testnet: `https://api.hl.cyber-testnet`) |
| 5+ (Live Trading) | Alpaca live API key + secret | Alpaca dashboard | Only when ready to go live |
| 5+ | HL main wallet | Generate or promote paper wallet | Consider multisig for production |
| 10 (Hardening) | Vault address + token (if Vault) | Self-hosted or HCP | Or AWS IAM role (if AWS SM) |
| 10 | Discord webhook URL | Discord channel settings | For alerts |
| 10 | Telegram bot token | @BotFather | For alerts |
| 10 | PagerDuty integration key | PagerDuty service | For on-call escalation |

### 13.8 What's Safe to Share (and What's Not)

**The application code never needs the AI assistant to see real secrets.** The `.env` pattern is specifically structured so that:
- AI assistant writes `.env.example` with placeholder values
- Human fills in `.env` with real values on their own machine, in their own editor
- Code calls `get_secret("alpaca.api_key")` — resolver reads from `.env` at runtime

| Safe to share in chat / commit to repo | NOT safe to share |
|---|---|
| API endpoint URLs (`https://paper-api.alpaca.markets`) | API keys / secrets |
| Redis channel names (`signal.raw.noble_trader`) | Redis passwords |
| Database table names (`trading_config`, `ta_backtest_result`) | Database passwords |
| Schema field names / SQL DDL (`CREATE TABLE ...`) | JWT tokens |
| Docker commands | Private keys |
| Python package versions | Wallet seed phrases |
| Error messages (with keys redacted) | Anything starting with `sk_`, `pk_`, `0x` + 40+ hex chars |
| Screenshots of dashboards (with sensitive columns redacted) | Service role keys |

### 13.9 If Real Credentials Must Be Shared for Debugging

Rare case — should be avoided. If unavoidable:

1. **Paper trading keys only** — Alpaca paper, Hyperliquid testnet. Low stakes if leaked.
2. **Scoped, read-only keys** — if a venue supports it, create a key with read-only permissions and tight IP restrictions.
3. **Rotate immediately after** — treat any key shared in chat as compromised and rotate it the moment the session is done.
4. **Dedicated trading wallet** for Hyperliquid — never your main wallet, never your main vault.
5. **Never share a Bitwarden API token or vault password** — that grants full vault access. Use Bitwarden Send (encrypted, self-destructing link) only for non-critical sharing, and rotate the secret after.

### 13.10 Rotation Policy

| Secret type | Rotation frequency | Trigger |
|---|---|---|
| API keys (Alpaca, OANDA) | Every 90 days | Scheduled |
| Hyperliquid wallet private key | On compromise suspicion | Manual — requires wallet migration |
| Supabase service_role key | Every 90 days | Scheduled |
| Redis password | Every 90 days | Scheduled |
| Discord/Telegram webhooks | On team change | Manual |
| Vault tokens | Dynamic (leased) | Auto-renew |

Rotation is a config change (update `.env` or Vault), not a code change. The `SecretResolver` picks up the new value on next process restart (or hot-reload if backend supports it).

### 13.11 Audit Log

Every secret access is logged (without revealing the value):

```
2025-01-15T10:23:45Z [secret_access] key=alpaca.api_key backend=env_file caller=hermes.adapters.alpaca.AlpacaAdapter result=success
2025-01-15T10:23:45Z [secret_access] key=hyperliquid.private_key backend=env_file caller=hermes.adapters.hyperliquid.HyperliquidAdapter result=success
2025-01-15T10:24:01Z [secret_access] key=supabase.key backend=env_file caller=hermes.transport.supabase.SupabaseBackfiller result=success
```

This goes to the standard structured log + DuckDB `audit_log` table (if added) for forensic review.
