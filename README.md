# Hermes Trading Platform

> Entry/execution optimization layer for Noble Trader signals.
> Hermes consumes Noble Trader's strategy signals and optimizes **when** to enter and **how** to execute — it does NOT replicate Noble Trader's strategy sweeps.

**Status:** ✅ All 11 phases complete — 297 tests passing, 24 CLI commands, 11 dashboard pages, 8 DuckDB migrations, 23 tables, DaisyUI UI with 7 themes. Enhanced with Advanced Circuit Breaker Manager (8 tiered categories, time-decay, rolling windows) and Performance Attribution (decision-branch PnL attribution, A/B testing, signal window optimization).

---

## Project Completion Summary

| Metric | Value |
|---|---|
| **Phases completed** | 11/11 (Phase 0 through Phase 10) + 2 enhancements |
| **Tests** | 297 (all passing) |
| **CLI commands** | 24 |
| **Dashboard pages** | 11 (DaisyUI, 7 switchable themes) |
| **DuckDB migrations** | 8 (schema v8) |
| **DuckDB tables** | 23 |
| **Python source files** | 50 (48 core + 2 enhancements: `cb_manager.py`, `attribution.py`) |
| **Documentation** | roadmap.md (2,457 lines), agent_onboarding.md (845 lines), dr_runbook.md, worklog.md |

---

## All Phases — Details & Status

### Phase 0 — Foundation Scaffold ✅
**Status:** Complete · **Tests:** 12 · **Commit:** `dc3df5e`

Config loader with `secret:` prefix resolution. SecretResolver supporting 4 backends (env_file, env, Vault, AWS SM). DuckDB schema v1 (11 tables). CLI: `platform init`, `health`, `config show`, `version`. Structured JSON logging (structlog). 12 smoke tests. Windows PowerShell setup script. Pre-commit hooks (detect-secrets, ruff).

---

### Phase 0.5 — Web Dashboard ✅
**Status:** Complete · **Tests:** 12 · **Commit:** `71a0302`

FastAPI + Jinja2 web dashboard at `http://127.0.0.1:8080`. Status page with connection badges for 6 subsystems (DuckDB, Hermes Redis, NT Redis, Supabase, Alpaca, Hyperliquid). Heartbeats page with full NT field table. Config page (secrets redacted). Health JSON endpoint for monitoring/CI. Async status checks in parallel. Read-only DuckDB access (safe alongside ingest pipeline).

---

### Phase 1 — Upstream Ingestion (L0) ✅
**Status:** Complete · **Tests:** 19 · **Commit:** `b91571a`

Noble Trader heartbeat subscriber via Redis pub/sub. Pydantic v2 schema validation (28+ NT fields). Dedup (SHA-256, 5s window). Staleness checker (configurable, default 30s). Regime shift detector. Async batched DuckDB writer (single-writer pattern). Supabase historical backfill adapter (REST API, paginated, DQ checks on ingest). DuckDB schema v2 (NT mirror tables). Data quality anomaly detection (sharpe_too_high, max_dd_zero, profit_factor_zero, regime_strategy_disagree). CLI: `platform ingest`, `platform backfill`.

---

### Phase 2 — Market Data + Active Price Monitor (L2 + L2.8) ✅
**Status:** Complete · **Tests:** 29 · **Commit:** `b28ecc7`

Venue adapter interface (abstract base). Alpaca adapter (live WS trades+quotes, historical REST bars). Hyperliquid adapter (live WS trades+L2 book, REST funding+candles). Parquet writer (async batched, partitioned by venue/symbol/tf/date). 7 market data schemas (Tick, Bar, OrderBookL2, FundingRate, LiquidationEvent, Position, PriceMonitorEvent). TickAggregator (6 timeframes, 500-bar window). IndicatorEngine (ATR, EMA, RSI, realized vol, VWAP, Hurst, z-score). AnomalyDetector (5σ returns, spread widening, imbalance flips). StopWatcher (stop/target/trailing/pnl_warning, sub-50ms target). CrossPriceMonitor (correlation matrix + shift detection). FundingWatcher (funding spike detection). DuckDB schema v3 (price_monitor_events). CLI: `platform stream`, `platform monitor`, `platform backfill-market`.

---

### Phase 3 — Signal Synthesis (L4) with 7-State Meta-Regime ✅
**Status:** Complete · **Tests:** 27 · **Commit:** `c9cb3e7`

7-state meta-regime classifier (rule-based waterfall: risk_off, funding_stress, liquidity_drained, regime_transition, calm_trend, choppy_range, high_vol_breakout). Sizing multipliers + entry aggressiveness per state. RenkoConstructor (builds bricks from venue ticks using NT brick_size, handles multi-brick jumps, auto-updates brick_size). BrickPatternAnalyzer (12 patterns: breakout, trend, reversal, double top/bottom, pullback, consolidation). EntryTimingOptimizer (enter_now / wait_for_brick_close / wait_for_pullback / wait_for_retest / block / maker_only). ExecutionMethodOptimizer (market / limit / TWAP / iceberg / post_only). SizingEngine (trust + overlay: baseline = equity × NT effective_kelly × meta-regime multiplier, drawdown adjustment, risk caps). SignalSynthesizer (L4 orchestrator, produces BlendedSignal, writes to DuckDB + publishes to Redis). DuckDB schema v4 (trade_signals_blended, 26 columns). CLI: `platform synthesize`.

---

### Phase 4 — Portfolio & Risk Engine (L5) ✅
**Status:** Complete · **Tests:** 29 · **Commit:** `6e23a31`

PortfolioStateService (positions, cash USD+USDC, exposure, PnL, drawdown, handles long+short). VaRCalculator (historical + parametric, configurable confidence). VolatilityCircuitBreaker (4-level ladder: reduce 50%, block, tighten, liquidate). RiskCircuitBreaker (portfolio DD, daily loss, VaR breach, margin proximity). KillSwitch (global halt with manual + auto triggers). RiskGate (8 pre-trade checks on BlendedSignal, caps size for soft limits). AutonomyGate (5-tier matrix: tier 0 read-only, tier 1 small trades, tier 2 config promo, tier 3 large/novel = human approval, tier 4 structural = hard block). Account snapshot writer (periodic 60s + on-event). DuckDB writes: risk_decisions, circuit_breaker_events, account_snapshots. CLI: `platform risk`.

---

### Phase 5 — Execution Layer (L3) ✅
**Status:** Complete · **Tests:** 25 · **Commit:** `17f808c`

Order schemas + OrderStateMachine (DRAFT→SUBMITTED→PARTIAL→FILLED, transition enforcement). SlippageModeler (square-root impact model, venue-specific fees). PaperTradingEngine (simulated fills for market/limit/post_only/TWAP/iceberg, async callbacks, cancel support). SmartOrderRouter (creates orders from RiskDecision + BlendedSignal, routes to correct order type). DuckDB schema v5 (orders, order_events, fills — 44 columns, 12 indexes). L3 Execution orchestrator (subscribes to risk.decision.*, fetches signal from DuckDB, executes via paper engine, registers positions in PortfolioStateService). CLI: `platform execute --paper`.

---

### Phase 6 — PnL & Analytics ✅
**Status:** Complete · **Tests:** 15 · **Commit:** `4598d6c`

PnLService (realized + unrealized PnL tracking). PnL Attribution (directional, timing, sizing, regime decomposition). DrawdownTracker (peak equity, current/max DD, time-in-DD, underwater %, ulcer index). Funding PnL accrual for perps. DuckDB schema v6 (pnl_realized 22 columns, pnl_unrealized 15 columns). TearSheet generator (30+ metrics: Sharpe, Sortino, Calmar, Omega, VaR, CVaR, max DD, win rate, profit factor, avg R, expectancy, skew, kurtosis, by-regime breakdown). CLI: `platform pnl`.

---

### Phase 7 — Backtesting + Statistical Rigor ✅
**Status:** Complete · **Tests:** 16 · **Commit:** `5f6a855`

Event-driven BacktestEngine (replays historical NT heartbeats through full L4→L5→L3 pipeline, uses temp DuckDB for isolation, generates tear sheet from results). Walk-forward optimizer (purged k-fold CV, López de Prado style, configurable gap, train/test Sharpe comparison, decay threshold). Monte Carlo bootstrap (resamples returns WITH REPLACEMENT, 1000 iterations, Sharpe percentile distribution, p-value). Deflated Sharpe Ratio (Bailey & López de Prado, adjusts for multiple testing, non-normality, sample length). 6-check statistical rigor suite (walk-forward, Deflated Sharpe > 1.0, Monte Carlo 5th pct > 0, bootstrap CI > 0, regime coverage 4+/7, capacity). DuckDB schema v7 (backtest_runs). CLI: `platform backtest`, `platform rigor`.

---

### Phase 8 — Renko Simulation & Entry/Execution Optimization ✅
**Status:** Complete · **Tests:** 15 · **Commit:** `54d7a9f`

RenkoSimulationEngine (Hermes's learning workhorse, does NOT replicate NT sweeps). Entry timing sweep: Bayesian optimization (Optuna TPESampler) over 17 parameters (entry strategies per regime, brick confirmation, pullback depth, execution method, TWAP N, iceberg %, limit offset, trailing stop, exit strategy, sizing multipliers). Baseline comparison (every trial vs "blindly execute at market"). Entry alpha metric (bps better than NT suggested). Shadow mode runner (parallel paper account, 10% of live size, auto-promotion gate: shadow Sharpe ≥ 80% of backtest). Counterfactual engine (replays closed trades under alternative configs). Auto-promotion + auto-rollback. DuckDB schema v8 (simulation_runs 44 cols, simulation_trades 30 cols, param_optimizations 15 cols). CLI: `platform optimize`, `platform shadow`, `platform counterfactual`.

---

### Phase 9 — Hermes Agent (Decision Tree + Self-Learning) ✅
**Status:** Complete · **Tests:** 26 · **Commit:** `2b8d6d6`

Hermes Agent Decision Tree (validated — 3 bugs found + fixed during validation):
- HARD SL: pnl ≤ -1% → close (always fires, risk management override)
- Signal present? YES → Agent manages (native 2.5% TP suspended):
  - Same direction: fading → trail, pnl ≥ 4.5% → early profit, no exit → hold
  - Opposite direction: strong (conviction ≥ 0.7) → flip, not strong → hold with native stops
- Signal present? NO → Native stops manage (pnl ≥ 2.5% → close, otherwise hold)

HypothesisTracker (lifecycle: proposed → backtested → shadow → live / rejected). DecisionJournalWriter (postmortems with entry_thesis + lessons + hypothesis_ids). SelfLearningLoop (EOD analysis: observe → attribute → hypothesize → backtest → promote). CLI: `platform agent` (--eod, --list-hypotheses).

**Decision tree validation tests (7 new):** proved native TP only fires without signal, 4.5% early profit IS reachable with signal, SL is hard override, trail fires before early profit when fading, all 11 branches reachable.

---

### Phase 10 — Hardening & Ops ✅
**Status:** Complete · **Tests:** 14 · **Commit:** `22bad8d`

DeadMansSwitch (background monitor, auto-activates kill switch + flattens if no heartbeat within 60s, auto-deactivates when heartbeat resumes). Alerting system (Discord webhook + Telegram bot, 4 severity levels, rich embeds, graceful no-op when unconfigured). Replay/Forensic mode (replays any historical session from DuckDB, merges 8 event types chronologically, timeline display). Load testing utility (simulates high-frequency heartbeat ingestion, reports actual vs target throughput). Disaster recovery runbook (7 scenarios: process crash, DuckDB corruption, Redis disconnect, NT upstream down, venue API down, daily loss limit, config rollback). Post-incident checklist. CLI: `platform replay`, `platform alert-test`, `platform load-test`.

**Post-Phase-10 enhancements:**
- **Advanced Circuit Breaker Manager** (`src/hermes/portfolio/cb_manager.py`, 42 tests) — 8 tiered categories (portfolio_exposure, position_size, daily_loss, var, drawdown, funding_rate, consecutive_losses, trip_frequency), 7 configurable actions, time-decay via `cooldown_sec`, and `RollingWindowTracker` for consecutive-loss and trip-frequency windows. Config in `config/default.yaml` → `circuit_breakers.manager`.
- **Performance Attribution** (`src/hermes/agent/attribution.py`, 16 tests) — `DecisionBranchTracker` (attributes PnL per `AgentAction` branch + regime × branch matrix + threshold tuning feedback), `ABTestFramework` (Diebold-Mariano + paired t-test for parallel hypothesis comparison), `SignalWindowOptimizer` (sweeps `signal_expiry_minutes` to maximize entry alpha).

---

## What Hermes Does

- **Subscribes** to Noble Trader heartbeats via Redis (real-time trade signals)
- **Pulls** historical Noble Trader data from Supabase (`nt_sweep_result`, `nt_regime_log`) for HMM cold-start and backtest replay
- **Constructs renko bars** from venue-native tick data using Noble Trader's `brick_size`
- **Optimizes entry timing** (when within the signal window to pull the trigger)
- **Optimizes execution method** (market / limit / TWAP / post-only / iceberg)
- **Applies portfolio-level risk overlay** via a 7-state meta-regime classifier
- **Manages positions** post-entry via validated decision tree (SL/TP/trail/flip/hold)
- **Learns** from every trade via simulation engine with 6 statistical rigor checks
- **Self-improves** through hypothesis generation → backtest → shadow → promotion cycle

**What Hermes does NOT do** (Noble Trader owns these):
- Strategy direction (buy/sell/neutral)
- Renko brick_size optimization
- Stop-loss / take-profit brick counts
- Kelly fraction or Masaniello base sizing
- Per-asset HMM regime detection
- EV Engine / p_win blending

---

## Prerequisites (Windows)

1. **Python 3.12+** — https://python.org (check "Add to PATH" during install)
2. **Git** — https://git-scm.com
3. **Redis** (one of):
   - **Memurai** (recommended, native Windows): https://www.memurai.com/get-memurai
   - **Docker Desktop** + `docker run -d -p 6379:6379 --name hermes-redis redis`
   - **WSL2** + `sudo apt install redis-server`
4. **Paper trading credentials** (gather before setup):
   - Alpaca paper keys: https://app.alpaca.markets/paper/dashboard/overview
   - Hyperliquid: generate a **dedicated** trading wallet (never your main)
   - Noble Trader Redis URL + Supabase URL + service_role key

---

## Quick Start (Windows)

### 1. Install

```powershell
# Extract the zip, then in the project folder:
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

### 2. Configure

Edit `.env` with paper credentials:
```powershell
code .env
# Replace all <placeholder> values
```

### 3. Initialize

```powershell
.\.venv\Scripts\Activate.ps1
platform init
platform health
```

### 4. Start Dashboard

```powershell
platform dashboard
```

Open **http://127.0.0.1:8080** — 11 pages, 7 DaisyUI themes.

### 5. Start Trading Pipeline

```powershell
# Terminal 2: L0 — Heartbeat subscriber
platform ingest

# Terminal 3: L2.8 — Active Price Monitor
platform monitor --symbols BTC-PERP,AAPL

# Terminal 4: L4 — Signal Synthesizer
platform synthesize --symbols BTC-PERP,AAPL

# Terminal 5: L5 — Portfolio & Risk Engine
platform risk --equity 100000

# Terminal 6: L3 — Execution Engine (paper)
platform execute --equity 100000 --paper
```

---

## Installing Dependencies

### Option A: Using `uv` (recommended — 10× faster)

```powershell
uv pip install -e ".[dev]"
```

### Option B: Using `pip` with requirements files

```powershell
# Runtime deps only
pip install -r requirements.txt

# Runtime + dev/test/lint tools
pip install -r requirements-dev.txt

# Optional extras (supabase, alpaca-py)
pip install -r requirements-optional.txt
```

### Requirements files

| File | Contents | When to use |
|---|---|---|
| `pyproject.toml` | **Source of truth** — all dependency declarations | Editing deps |
| `requirements.txt` | Runtime deps only (19 packages) | Minimal production install |
| `requirements-dev.txt` | Runtime + dev tools (27 packages) | Local development |
| `requirements-optional.txt` | Optional extras (supabase, alpaca-py) | Specific venue SDKs |

> **Note:** The `requirements*.txt` files are **generated** from `pyproject.toml` by `scripts/sync_requirements.py`.

---

## All CLI Commands (24)

```powershell
# Foundation (Phase 0)
platform init              # Bootstrap: load config, open DuckDB, apply schema
platform health            # Check health of all subsystems
platform config show       # Print loaded config (secrets redacted)
platform version           # Print version

# Dashboard (Phase 0.5)
platform dashboard         # Start web dashboard at http://127.0.0.1:8080

# Upstream Ingestion (Phase 1)
platform ingest            # Start Noble Trader heartbeat subscriber (L0)
platform backfill          # Pull historical heartbeats from Supabase

# Market Data (Phase 2)
platform stream            # Stream live market data from venue WebSockets
platform monitor           # Start Active Price Monitor (L2.8)
platform backfill-market   # Pull historical bars from venue REST API

# Signal Synthesis (Phase 3)
platform synthesize        # Start L4 Signal Synthesizer (BEV combiner)

# Portfolio & Risk (Phase 4)
platform risk              # Start L5 Portfolio & Risk Engine

# Execution (Phase 5)
platform execute           # Start L3 Execution Engine (paper trading)

# PnL & Analytics (Phase 6)
platform pnl               # Generate PnL tear sheet

# Backtesting (Phase 7)
platform backtest          # Run backtest by replaying historical heartbeats
platform rigor             # Run statistical rigor checks

# Optimization (Phase 8)
platform optimize          # Run entry/execution optimization sweep (Optuna)
platform shadow            # Start shadow mode for a new config
platform counterfactual    # Run counterfactual analysis on a closed trade

# Hermes Agent (Phase 9)
platform agent             # Show decision tree / run EOD / list hypotheses

# Ops (Phase 10)
platform replay            # Replay a historical session for forensic analysis
platform alert-test        # Send a test alert to Discord/Telegram
platform load-test         # Run a load test on the DuckDB writer
```

---

## Dashboard Pages (11)

| Page | URL | What it shows |
|---|---|---|
| **Status** | `/` | Connection badges, ingest stats, recent heartbeats (auto-refresh) |
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

**DaisyUI themes** (7): dark (default), retro, cyberpunk, nord, dracula, synthwave, light. Theme switcher in navbar, persists to localStorage.

---

## DuckDB Schema (8 migrations, 23 tables)

| Migration | Tables added | Phase |
|---|---|---|
| v1 (base schema) | schema_version, config_history, signal_heartbeats, signal_heartbeats_quarantine, account_snapshots, trade_journal, risk_decisions, circuit_breaker_events, hermes_hypotheses, meta_regime_history, audit_log | 0 |
| v2 | nt_sweep_results_local, nt_regime_log_local | 1 |
| v3 | price_monitor_events | 2 |
| v4 | trade_signals_blended | 3 |
| v5 | orders, order_events, fills | 5 |
| v6 | pnl_realized, pnl_unrealized | 6 |
| v7 | backtest_runs | 7 |
| v8 | simulation_runs, simulation_trades, param_optimizations | 8 |

---

## Project Structure

```
hermes-trading-platform/
├── .env.example              ← Template — copy to .env, fill in real values
├── .gitignore                ← Blocks .env, .duckdb, data/, etc.
├── .gitattributes            ← Line ending normalization
├── .pre-commit-config.yaml   ← Secret scanning + linting hooks
├── .secrets.baseline         ← detect-secrets baseline
├── pyproject.toml            ← Dependencies (source of truth)
├── requirements.txt          ← Generated runtime deps
├── requirements-dev.txt      ← Generated runtime + dev deps
├── requirements-optional.txt ← Generated optional extras
├── README.md                 ← This file
├── worklog.md                ← Development log by phase
│
├── config/
│   └── default.yaml          ← All configurable parameters (§3 of roadmap)
│
├── docs/
│   ├── roadmap.md            ← Full 2,457-line system design
│   ├── agent_onboarding.md   ← Complete onboarding guide (845 lines)
│   └── dr_runbook.md         ← Disaster recovery runbook (7 scenarios)
│
├── scripts/
│   ├── setup.ps1             ← Windows PowerShell setup
│   ├── init_duckdb.py        ← Standalone DuckDB initializer
│   ├── test_redis.py         ← Redis connectivity test
│   └── sync_requirements.py  ← Regenerate requirements from pyproject.toml
│
├── src/hermes/
│   ├── __init__.py
│   ├── app.py                ← CLI entrypoint (24 commands)
│   ├── core/                 ← secrets.py, config.py, logging.py
│   ├── db/                   ← schema.sql, migrate.py, migrations/
│   ├── schemas/              ← heartbeat.py, market.py
│   ├── transport/            ← redis_subscriber, heartbeat_writer, parquet_writer,
│   │   └── adapters/         ←   base.py, alpaca_adapter, hyperliquid_adapter
│   ├── monitor/              ← tick_aggregator, indicators, anomaly_detector,
│   │   └──                    ←   stop_watcher, cross_price, funding_watcher, orchestrator
│   ├── signals/              ← meta_regime, renko_engine, entry_timing, sizing, synthesizer
│   ├── portfolio/            ← state, var_calculator, circuit_breakers, risk_gate,
│   │   └──                    ←   autonomy_gate, snapshot_writer, orchestrator, **cb_manager**
│   ├── execution/            ← orders, slippage, paper_engine, router, db_writer, orchestrator
│   ├── analytics/            ← pnl_service, tear_sheet
│   ├── backtest/             ← engine, statistics, optimizer
│   ├── agent/                ← decision_tree, learning, **attribution**
│   ├── ops/                  ← dead_mans_switch, alerting, replay
│   └── web/                  ← app.py, status.py, templates/, static/
│
└── tests/
    ├── test_smoke.py         ← Phase 0 (12 tests)
    ├── test_dashboard.py     ← Phase 0.5 (12 tests)
    ├── test_phase1.py        ← Phase 1 (19 tests)
    ├── test_phase2.py        ← Phase 2 (29 tests)
    ├── test_phase3.py        ← Phase 3 (27 tests)
    ├── test_phase4.py        ← Phase 4 (29 tests)
    ├── test_phase5.py        ← Phase 5 (25 tests)
    ├── test_phase6.py        ← Phase 6 (15 tests)
    ├── test_phase7.py        ← Phase 7 (16 tests)
    ├── test_phase8.py        ← Phase 8 (15 tests)
    ├── test_phase9.py        ← Phase 9 (26 tests — includes decision tree validation)
    ├── test_phase10.py       ← Phase 10 (14 tests)
    ├── test_cb_manager.py    ← Advanced Circuit Breaker Manager (42 tests)
    └── test_attribution.py   ← Performance Attribution (16 tests)
```

---

## Documentation

| Document | Lines | Content |
|---|---|---|
| `docs/roadmap.md` | 2,457 | Full system design: architecture, 13 sections, all schemas, 12 open decisions |
| `docs/agent_onboarding.md` | 845 | Complete onboarding: install → config → trading loop (9 steps) → EOD analysis → DR |
| `docs/dr_runbook.md` | ~200 | 7 disaster recovery scenarios with step-by-step procedures |
| `worklog.md` | ~700 | Development log by phase with what was built, bugs fixed, deferred items |
| `README.md` | This file | Project overview, all phases, quick start, CLI reference |

---

## Development

```powershell
# Activate venv
.\.venv\Scripts\Activate.ps1

# Run tests
pytest

# Run tests with coverage
pytest --cov=hermes

# Format code
ruff format .

# Lint
ruff check .

# Install pre-commit hooks (one-time)
pre-commit install

# Regenerate requirements files after editing pyproject.toml
python scripts/sync_requirements.py
```

---

## Troubleshooting (Windows)

See `docs/agent_onboarding.md` §11 for full troubleshooting guide (9 common issues with solutions).

---

## License

Proprietary. All rights reserved.
