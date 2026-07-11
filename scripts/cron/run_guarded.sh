#!/usr/bin/env bash
# =============================================================================
# run_guarded.sh — infra-safe wrapper for the STRATEGIC (agent-owned) cron jobs.
# Implements docs/agent_onboarding.md §6–§7 schedule. Before running the
# requested `platform` command it checks `platform health`; if Redis/DuckDB
# are down the job prints SKIPPED and exits 0 (no error spam) so the schedule
# can be installed pre-infra and auto-activates once `trading_stack.sh start`
# brings the stack up.
#
# Usage (from Hermes cronjob `script`):
#   scripts/cron/run_guarded.sh agent --eod
#   scripts/cron/run_guarded.sh optimize --days-back 90 --n-trials 200
# =============================================================================
set -uo pipefail
REPO="C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo"
VENVPY="$REPO/.venv/Scripts/python.exe"
cd "$REPO" || exit 1
unset PYTHONPATH   # host exports a broken global PYTHONPATH — clear it

out=$("$VENVPY" -m hermes.app health 2>&1); rc=$?
if [ $rc -ne 0 ] || echo "$out" | grep -qi 'error'; then
  echo "SKIPPED $(date -u): infra not ready (redis/duckdb down) — run trading_stack.sh start first"
  exit 0
fi
"$VENVPY" -m hermes.app "$@"
