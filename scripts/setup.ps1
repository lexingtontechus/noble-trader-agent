# ============================================================
# Hermes Trading Platform — Windows PowerShell Setup Script
# ============================================================
# Run from project root:
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
# ============================================================

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot/..

Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Hermes Trading Platform — Windows Setup" -ForegroundColor Cyan
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""

# === 1. Check Python ===
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  OK: $pythonVersion"
    if ($pythonVersion -notmatch "3\.(1[2-9]|[2-9]\d)") {
        Write-Host "  WARNING: Python 3.12+ recommended. You have: $pythonVersion" -ForegroundColor Red
        $continue = Read-Host "  Continue anyway? (y/N)"
        if ($continue -ne "y") { exit 1 }
    }
} catch {
    Write-Host "  ERROR: Python not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# === 2. Check uv (package manager — optional, falls back to pip) ===
Write-Host ""
Write-Host "[2/6] Checking uv (optional, speeds up installs)..." -ForegroundColor Yellow
try {
    $uvVersion = uv --version 2>&1
    Write-Host "  OK: $uvVersion"
} catch {
    Write-Host "  uv not found. Will use pip instead (slower but works fine)." -ForegroundColor Yellow
    Write-Host "  To install uv for faster installs: https://docs.astral.sh/uv/"
    Write-Host "  Continuing with pip..."
}

# === 3. Create virtual environment ===
Write-Host ""
Write-Host "[3/6] Creating virtual environment..." -ForegroundColor Yellow
if (Test-Path ".venv") {
    Write-Host "  .venv already exists, skipping create"
} else {
    uv venv --python 3.12 .venv
    Write-Host "  OK: .venv created"
}
# Activate for this session
& .\.venv\Scripts\Activate.ps1

# === 4. Install dependencies ===
Write-Host ""
Write-Host "[4/6] Installing dependencies..." -ForegroundColor Yellow

# Try uv first (faster), fall back to pip
$useUv = $false
try {
    $uvCheck = uv --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $useUv = $true
    }
} catch {}

if ($useUv) {
    Write-Host "  Using uv (fast installer)..."
    uv pip install -e ".[dev]"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  uv install failed, falling back to pip..." -ForegroundColor Yellow
        $useUv = $false
    }
}

if (-not $useUv) {
    Write-Host "  Using pip..."
    pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  ERROR: dependency install failed" -ForegroundColor Red
        exit 1
    }
    # Also install the package in editable mode so `platform` CLI works
    pip install -e . --no-deps
}
Write-Host "  OK: dependencies installed"

# === 5. Create .env from template ===
Write-Host ""
Write-Host "[5/6] Setting up .env..." -ForegroundColor Yellow
if (Test-Path ".env") {
    Write-Host "  .env already exists — skipping (do not overwrite your secrets!)"
} else {
    Copy-Item ".env.example" ".env"
    Write-Host "  OK: .env created from .env.example"
    Write-Host "  IMPORTANT: Open .env in your editor and fill in real values" -ForegroundColor Yellow
}

# === 6. Initialize DuckDB ===
Write-Host ""
Write-Host "[6/6] Initializing DuckDB..." -ForegroundColor Yellow
python scripts/init_duckdb.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARNING: DuckDB init failed — check .env values" -ForegroundColor Yellow
} else {
    Write-Host "  OK: DuckDB initialized"
}

# === Redis check (informational only) ===
Write-Host ""
Write-Host "Checking Redis (optional)..." -ForegroundColor Yellow
$redisCheck = Get-Command redis-cli -ErrorAction SilentlyContinue
$dockerCheck = Get-Command docker -ErrorAction SilentlyContinue
if ($redisCheck) {
    Write-Host "  redis-cli found — testing connection..."
    try {
        $pong = redis-cli ping 2>&1
        if ($pong -eq "PONG") {
            Write-Host "  OK: Redis is running locally"
        } else {
            Write-Host "  Redis not responding — start it with: redis-server"
        }
    } catch {
        Write-Host "  Redis not running — start it with: redis-server"
    }
} elseif ($dockerCheck) {
    Write-Host "  Docker found. To start Redis:"
    Write-Host "    docker run -d -p 6379:6379 --name hermes-redis redis"
    Write-Host "  Then test: docker exec -it hermes-redis redis-cli ping"
} else {
    Write-Host "  Neither redis-cli nor Docker found. Options:"
    Write-Host "    A) Install Memurai (Redis for Windows): https://www.memurai.com/get-memurai"
    Write-Host "    B) Install Docker Desktop: https://www.docker.com/products/docker-desktop"
    Write-Host "    C) Use WSL2 + apt install redis-server"
}

# === Done ===
Write-Host ""
Write-Host "================================================" -ForegroundColor Cyan
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env and fill in real values (paper keys for now)"
Write-Host "  2. Activate venv:  .\.venv\Scripts\Activate.ps1"
Write-Host "  3. Run init:       platform init"
Write-Host "  4. Check health:   platform health"
Write-Host "  5. View config:    platform config show"
Write-Host ""
Write-Host "For development:"
Write-Host "  Run tests:         pytest"
Write-Host "  Format code:       ruff format ."
Write-Host "  Lint:              ruff check ."
Write-Host ""
