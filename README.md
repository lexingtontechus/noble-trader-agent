# Hermes Trading Platform

> Entry/execution optimization layer for Noble Trader signals.
> Hermes consumes Noble Trader's strategy signals and optimizes **when** to enter and **how** to execute — it does NOT replicate Noble Trader's strategy sweeps.

**Status:** Phase 0 (Foundation) — skeleton scaffolded, not yet functional for trading.

## What Hermes Does

- **Subscribes** to Noble Trader heartbeats via Redis (real-time trade signals)
- **Pulls** historical Noble Trader data from Supabase (`nt_sweep_result`, `nt_regime_log`) for HMM cold-start and backtest replay
- **Constructs renko bars** from venue-native tick data using Noble Trader's `brick_size`
- **Optimizes entry timing** (when within the signal window to pull the trigger)
- **Optimizes execution method** (market / limit / TWAP / post-only / iceberg)
- **Applies portfolio-level risk overlay** via a 7-state meta-regime classifier
- **Manages positions** post-entry (trailing stops, scenario projections, regime-change exits)
- **Learns** from every trade via a simulation engine with statistical rigor

**What Hermes does NOT do** (Noble Trader owns these):
- Strategy direction (buy/sell/neutral)
- Renko brick_size optimization
- Stop-loss / take-profit brick counts
- Kelly fraction or Masaniello base sizing
- Per-asset HMM regime detection
- EV Engine / p_win blending

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

## Installing Dependencies

There are **two ways** to install dependencies — pick whichever you prefer.

### Option A: Using `uv` (recommended — 10× faster)

The `setup.ps1` script uses this approach:

```powershell
uv pip install -e ".[dev]"
```

This reads from `pyproject.toml` (the source of truth) and installs the project in editable mode with dev extras.

### Option B: Using `pip` with requirements files

If you prefer plain `pip` (or don't want to install `uv`):

```powershell
# Runtime deps only (minimal install)
pip install -r requirements.txt

# Runtime + dev/test/lint tools (recommended for development)
pip install -r requirements-dev.txt

# Optional extras (install only what you need)
pip install -r requirements-optional.txt
```

### Requirements files

| File | Contents | When to use |
|---|---|---|
| `pyproject.toml` | **Source of truth** — all dependency declarations | Editing deps; `uv`/`pip install -e .` reads from here |
| `requirements.txt` | Runtime deps only (11 packages) | Minimal production install |
| `requirements-dev.txt` | Runtime + dev/test/lint tools (17 packages) | Local development |
| `requirements-optional.txt` | Optional extras (supabase, alpaca-py) | When you need specific venue SDKs |

> **Note:** The `requirements*.txt` files are **generated** from `pyproject.toml` by `scripts/sync_requirements.py`. Never edit them directly — edit `pyproject.toml` and re-run the sync script.

```powershell
# After editing pyproject.toml, regenerate requirements files:
python scripts/sync_requirements.py
```

## Quick Start (Windows)

### 1. Install

```powershell
# Extract the zip, then in the project folder:
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

This will:
- Verify Python 3.12+ is installed
- Create a `.venv` virtual environment
- Install all dependencies (via `uv` if available, else `pip`)
- Copy `.env.example` → `.env` (if not exists)
- Initialize DuckDB with schema

**Manual install alternative** (if `setup.ps1` fails):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pip install -e . --no-deps
copy .env.example .env
python scripts\init_duckdb.py
```

### 2. Configure

Open `.env` in your editor and fill in real values (paper credentials only):

```powershell
code .env
# or: notepad .env
```

Replace all `<placeholder>` values:
- `NOBLE_TRADER_REDIS_URL` — from Noble Trader operator
- `SUPABASE_URL` + `SUPABASE_KEY` — from your Supabase project
- `ALPACA_API_KEY` + `ALPACA_API_SECRET` — from https://app.alpaca.markets/paper/dashboard/overview
- `HYPERLIQUID_WALLET_ADDRESS` + `HYPERLIQUID_PRIVATE_KEY` — generate a dedicated wallet

### 3. Initialize

```powershell
# Activate venv (do this in every new terminal)
.\.venv\Scripts\Activate.ps1

# Initialize Hermes (applies DuckDB schema, writes test row)
platform init

# Check health of all subsystems
platform health
```

### 4. Start the Dashboard

```powershell
# Start the web dashboard (visual monitoring)
platform dashboard

# Or with custom host/port:
platform dashboard --host 0.0.0.0 --port 8080
```

Then open **http://127.0.0.1:8080** in your browser. You'll see:
- **Status page** — connection state for DuckDB, Redis (Hermes + Noble Trader), Supabase, Alpaca, Hyperliquid
- **Heartbeats page** — recent Noble Trader heartbeats (after you run `platform ingest`)
- **Config page** — loaded config with secrets redacted
- **Health JSON** — `/health` endpoint for monitoring/CI
- **API** — `/api/status` and `/api/heartbeats` for programmatic access

The status page auto-refreshes every 10 seconds.

### 5. Start Ingesting Heartbeats

In a **separate terminal** (keep the dashboard running):

```powershell
.\.venv\Scripts\Activate.ps1

# Dry run first — validates config without subscribing
platform ingest --dry-run

# Start subscribing to Noble Trader heartbeats (runs forever)
platform ingest
```

Watch the dashboard's "Heartbeats" page fill up as signals arrive.

### 6. Backfill Historical Data (optional)

```powershell
# Pull last 365 days of Noble Trader sweep + regime data from Supabase
platform backfill --days-back 365

# Or just specific symbols:
platform backfill --days-back 90 --symbols BTC,AAPL
```

### All CLI Commands

```powershell
platform init           # Bootstrap: load config, open DuckDB, apply schema
platform health         # Check health of all subsystems
platform config show    # Print loaded config (secrets redacted)
platform version        # Print version
platform dashboard      # Start web dashboard at http://127.0.0.1:8080
platform ingest         # Start Noble Trader heartbeat subscriber (L0)
platform ingest --dry-run  # Validate config without subscribing
platform backfill       # Pull historical data from Supabase
platform backfill --days-back 90 --symbols BTC,AAPL
```

## Project Structure

```
hermes-trading-platform/
├── .env.example              ← Template — copy to .env, fill in real values
├── .gitignore                ← Blocks .env, .duckdb, etc. from git
├── .gitattributes            ← Line ending normalization (LF for code)
├── .pre-commit-config.yaml   ← Secret scanning + linting hooks
├── .secrets.baseline         ← detect-secrets baseline
├── pyproject.toml            ← Dependencies (uv / pip)
├── README.md                 ← This file
│
├── config/
│   └── default.yaml          ← All configurable parameters (see roadmap §3)
│
├── src/hermes/               ← Application code
│   ├── __init__.py
│   ├── app.py                ← CLI entrypoint (`platform init`, `health`, etc.)
│   ├── core/                 ← Core utilities
│   │   ├── secrets.py        ← SecretResolver (.env / env / Vault / AWS SM)
│   │   ├── config.py         ← YAML config loader with secret: prefix resolution
│   │   └── logging.py        ← Structured JSON logging (structlog)
│   └── db/                   ← DuckDB schema + migrations
│       ├── schema.sql        ← Phase 0 schema (11 tables)
│       └── migrate.py        ← Migration runner
│
├── scripts/
│   ├── setup.ps1             ← Windows PowerShell setup script
│   ├── init_duckdb.py        ← Standalone DuckDB initializer
│   └── test_redis.py         ← Redis connectivity test
│
├── tests/
│   └── test_smoke.py         ← Phase 0 smoke tests (12 tests, all passing)
│
└── docs/                     ← Documentation (placeholder for now)
```

## Phase 0 Status

**Working:**
- ✓ Config loads from `config/default.yaml` with `secret:` prefix resolution
- ✓ SecretResolver supports 4 backends (env_file, env, vault, aws_sm)
- ✓ DuckDB schema applies (10 tables: config_history, signal_heartbeats, account_snapshots, trade_journal, risk_decisions, circuit_breaker_events, hermes_hypotheses, meta_regime_history, audit_log, schema_version)
- ✓ CLI works: `platform init`, `platform health`, `platform config show`, `platform version`
- ✓ Structured JSON logging
- ✓ Smoke tests pass
- ✓ Pre-commit hooks configured (secret scanning, ruff, etc.)

**Not yet implemented (Phase 1+):**
- Noble Trader Redis heartbeat subscriber
- Supabase historical backfill adapter
- Market data adapters (Alpaca, Hyperliquid)
- 7-state meta-regime HMM
- Renko bar constructor
- Entry timing engine
- Execution layer
- Simulation engine
- Hermes agent integration

See `roadmap.md` for the full 11-phase plan.

## Configuration

All configuration is in `config/default.yaml`. Every parameter has a sensible default and can be overridden via:
1. Editing `default.yaml` directly
2. Setting environment variables (uppercase key names)
3. Setting values in `.env`

Secrets are referenced with the `secret:` prefix and resolved by `SecretResolver`:

```yaml
venues:
  alpaca:
    credentials:
      api_key: "secret:alpaca.api_key"   # resolved from ALPACA_API_KEY in .env
```

## Secrets Management

See roadmap §13 for full documentation. Summary:

- **Never** paste real secrets into chat, commit them to git, or share via Bitwarden token
- The `.env` pattern: I write `.env.example` with placeholders, you fill in `.env` locally
- Switch backends via `SECRETS_BACKEND` env var (`env_file` | `env` | `vault` | `aws_sm`)
- Pre-commit hook scans for accidental secret commits

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

# Run pre-commit on all files
pre-commit run --all-files
```

## Troubleshooting (Windows)

### "python not found"
- Reinstall Python from https://python.org and check "Add to PATH"
- Or use `py` launcher: `py --version`

### "uv not found" after setup
- Close and reopen PowerShell (PATH refresh)
- Or manually: `$env:Path += ";$env:USERPROFILE\.local\bin"`

### "Redis unreachable"
- Start Memurai service: `Start-Service Memurai`
- Or start Docker Redis: `docker start hermes-redis`
- Test: `redis-cli ping` should return `PONG`

### "DuckDB init failed"
- Check `.env` has `HERMES_DUCKDB_PATH=./data/hermes.duckdb`
- Ensure `./data/` directory is writable
- Delete `./data/hermes.duckdb` and re-run `platform init` to start fresh

### "ExecutionPolicy" errors
- Run: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`
- Or bypass for one script: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`

### "pre-commit not found"
- Install: `uv pip install pre-commit`
- Then: `pre-commit install`

## Roadmap

See `roadmap.md` in the project root (or `../roadmap.md` if you downloaded it separately) for:
- Full architecture (§1–2)
- All configurable parameters (§3)
- Circuit breakers (§4)
- Signal pipeline + Noble Trader heartbeat schema (§5)
- DuckDB schema (§6)
- Self-learning loop (§7)
- Tech stack (§8)
- 11-phase implementation plan (§10)
- Open decisions (§12)
- Credentials & secrets management (§13)

## License

Proprietary. All rights reserved.
