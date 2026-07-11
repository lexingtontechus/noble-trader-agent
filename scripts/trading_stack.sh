#!/usr/bin/env bash
# =============================================================================
# trading_stack.sh — Noble Trader / Hermes OPERATIONAL live-loop orchestrator
# Implements docs/agent_onboarding.md §2.7 (six continuous processes) and §5.2
# (the trading loop), plus the §7.1 supervisor rule (loops run forever; restart
# only after config change / reboot / deploy).
#
# Usage:
#   ./trading_stack.sh start      # launch all 6 + supervise (foreground)
#   ./trading_stack.sh stop       # kill all loop processes
#   ./trading_stack.sh restart    # stop, then start + supervise
#   ./trading_stack.sh status     # show per-process + platform health
#
# Env overrides: EQUITY (default 100000), HEALTH_INTERVAL (default 60s).
# Prereqs: venv built, Redis up, .env filled, `platform init` run once.
#
# >>> HOST-ENVIRONMENT CAVEAT (Hermes desktop / Windows / git-bash) <<<
#   `launch()` uses `setsid` so children detach into their own session and keep
#   running after this script exits — this works on a normal Linux host. On the
#   Hermes desktop (git-bash) a self-spawning supervisor is unreliable; there the
#   AGENT launches each process with the `terminal(background=true)` primitive
#   (see the noble-trader-quant-hf-manager skill). `stop` and `status` use
#   pgrep/pkill and work in BOTH environments regardless of how processes started.
#
#   The Hermes host also exports a GLOBAL PYTHONPATH pointing at a different
#   (broken) venv. We `unset PYTHONPATH` and invoke the repo venv's python module
#   directly so the correct interpreter + deps are always used.
# =============================================================================
set -uo pipefail

REPO="C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo"
VENVPY="$REPO/.venv/Scripts/python.exe"
LOGDIR="$REPO/logs"
EQUITY="${EQUITY:-100000}"
HEALTH_INTERVAL="${HEALTH_INTERVAL:-60}"
PROCS=(dashboard ingest monitor synthesize risk execute)

cd "$REPO" || { echo "REPO not found: $REPO"; exit 1; }
unset PYTHONPATH
mkdir -p "$LOGDIR"

# launch <name> [extra args...]
# The platform subcommand is ALWAYS the process name (dashboard/ingest/monitor/
# synthesize/risk/execute), so we prepend $name to the args. This was the bug
# that produced "No such option '--equity'" — the subcommand was being dropped.
launch() {
  local name="$1"; shift
  echo "[$(date -u +%H:%M:%S)] launch $name"
  setsid "$VENVPY" -m hermes.app "$name" "$@" >> "$LOGDIR/$name.log" 2>&1 < /dev/null &
}

start_all() {
  launch dashboard  --host 127.0.0.1 --port 8080   # FastAPI + SPA
  launch ingest                                               # L0  heartbeat -> DuckDB
  launch monitor                                              # L2.8 live ticks/indicators
  launch synthesize                                           # L4  blended signal + meta-regime
  launch risk --equity "$EQUITY"                             # L5  gate + VaR + circuit breakers
  launch execute --equity "$EQUITY" --paper                  # L3  paper fills + decision tree
}

stop_all() {
  echo "[$(date -u +%H:%M:%S)] stopping all loop processes"
  pkill -f "hermes.app \(dashboard\|ingest\|monitor\|synthesize\|risk\|execute\)" 2>/dev/null \
    && echo "  killed matched processes" || echo "  none matched"
}

status() {
  for name in "${PROCS[@]}"; do
    if pgrep -f "hermes.app $name" >/dev/null 2>&1; then
      echo "  $name: UP"
    else
      echo "  $name: DOWN"
    fi
  done
  echo "--- platform health ---"
  "$VENVPY" -m hermes.app health || true
}

supervise() {
  # §7.1: loops run continuously. Restart only on death (crash), not on a timer.
  while true; do
    sleep "$HEALTH_INTERVAL"
    for name in "${PROCS[@]}"; do
      if ! pgrep -f "hermes.app $name" >/dev/null 2>&1; then
        echo "[$(date -u +%H:%M:%S)] SUPERVISOR: $name died — restarting"
        case "$name" in
          dashboard)  launch dashboard  --host 127.0.0.1 --port 8080 ;;
          ingest)     launch ingest ;;
          monitor)    launch monitor ;;
          synthesize) launch synthesize ;;
          risk)       launch risk --equity "$EQUITY" ;;
          execute)    launch execute --equity "$EQUITY" --paper ;;
        esac
      fi
    done
    "$VENVPY" -m hermes.app health >/dev/null 2>&1 || echo "[$(date -u +%H:%M:%S)] WARNING: platform health non-zero"
  done
}

case "${1:-start}" in
  start)   start_all; supervise ;;
  stop)    stop_all ;;
  restart) stop_all; sleep 2; start_all; supervise ;;
  status)  status ;;
  *) echo "usage: $0 {start|stop|restart|status}"; exit 1 ;;
esac
