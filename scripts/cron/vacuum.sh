#!/usr/bin/env bash
# =============================================================================
# vacuum.sh — weekly DuckDB maintenance (agent_onboarding.md §7.2 Saturday 04:00 PT).
# Safe no-op if the database isn't initialized yet.
# =============================================================================
set -uo pipefail
REPO="C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo"
VENVPY="$REPO/.venv/Scripts/python.exe"
cd "$REPO" || exit 1
unset PYTHONPATH
"$VENVPY" -c "import duckdb; duckdb.connect('data/hermes.duckdb').execute('VACUUM')" \
  && echo "VACUUM ok $(date -u)" \
  || echo "VACUUM skipped $(date -u): db not ready"
