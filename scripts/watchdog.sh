#!/usr/bin/env bash
# =============================================================================
# watchdog.sh — Noble Trader stack auto-recovery (Hermes desktop / git-bash)
#
# Reliability model (hardened 2026-07-10):
#   * This host (git-bash / Windows) has no pgrep/pkill/setsid, and `nohup ... &`
#     children DIE on session-teardown SIGTERM. So loops are launched FULLY
#     DETACHED via PowerShell Start-Process (the only method on this host that
#     survives the cron shell's exit and keeps a single independent process tree).
#   * Liveness is NAME-BASED (any live python running our loop = up), which is
#     robust against the uvicorn reloader parent/child split and stale PIDs.
#     A PID file is still written per loop for diagnostics. Combined with the
#     single-instance lock, this prevents both false relaunches and duplicates.
#   * Every launch is VERIFIED: if the loop did not come up within 4s, the
#     watchdog logs a hard FAIL (visible in /tmp/watchdog.log) instead of
#     silently no-op'ing. The 5-min cron re-runs this script, so a transient
#     launch failure self-heals on the next tick.
#   * Redis is also launched detached + PID-guarded, so it restarts if killed.
# =============================================================================
set -uo pipefail
REPO="C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo"
VENVPY="$REPO/.venv/Scripts/python.exe"

LOGDIR="$REPO/logs"
REDIS="$REPO/tools/redis/redis-server.exe"
REDISCONF="$REPO/tools/redis/redis.windows.conf"
PIDDIR="$REPO/scripts"
export PYTHONPATH=""
mkdir -p "$LOGDIR"

# Single-instance guard: if a prior watchdog run is still alive, exit. This
# prevents double-launching when the cron tick and a manual run overlap, or two
# cron ticks straddle a slow launch. The lock records the live bash PID.
LOCK="$PIDDIR/.watchdog.lock"
if [ -f "$LOCK" ]; then
  oldpid=$(cat "$LOCK" 2>/dev/null | tr -d '
' | head -1)
  if [ -n "$oldpid" ] && powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { \$p=Get-Process -Id $oldpid -ErrorAction Stop; exit 0 } catch { exit 1 }" >/dev/null 2>/dev/null; then
    echo "[$(date -u +%H:%M:%S)] WATCHDOG: another instance (pid $oldpid) is running — skipping"
    exit 0
  fi
fi
echo $$ > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

WLOG="/tmp/watchdog.log"
ts() { date -u +%H:%M:%S; }

# Is the loop named $1 alive? Check whether ANY live python process is running
# our loop (matches by name, not a single PID). This is robust against the
# uvicorn reloader model, where hermes.app <loop> spawns a child worker (the
# actual server) and the parent is just a supervisor — either being alive means
# the loop is up. Pure PowerShell (no psutil / cross-venv dependency) so it
# cannot silently fail. The PID file is still written for diagnostics, but the
# liveness decision is name-based to avoid stale-PID false-negatives.
proc_alive() {
  local name="$1"
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { \$procs=Get-CimInstance Win32_Process -Filter \"Name='python.exe'\"; \$hit=\$procs | Where-Object { \$_.CommandLine -like \"*$name*\" -and \$_.CommandLine -notlike '*-c *' }; if (\$hit) { exit 0 } else { exit 1 } } catch { exit 1 }" >/dev/null 2>/dev/null
}

# Launch a python target FULLY DETACHED, record its PID, and verify it came up.
# $1 = name (used for pid file + liveness match)
# $2 = log file name
# $3+ = python args (after the venv interpreter)
launch_python_detached() {
  local name="$1"; local logfile="$2"; shift 2
  local pidfile="$PIDDIR/_pid_${name}.txt"
  # Build the PowerShell argument array from the python args (VENVPY is already
  # the -FilePath, so it must NOT be repeated here — that would run
  # `python.exe python.exe -m ...` and crash).
  local arr=""
  for a in "$@"; do
    if [ -z "$arr" ]; then arr="'$a'"; else arr="$arr,'$a'"; fi
  done
  # NOTE: Start-Process forbids -RedirectStandardOutput and -RedirectStandardError
  # pointing at the SAME file (throws InvalidOperationException -> no spawn). So
  # stderr goes to a separate .err file (tracebacks land there, JSON stream stays
  # in the main .log). Without -PassThru (which blocks on the stream handle).
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '$VENVPY' -ArgumentList @($arr) -WindowStyle Hidden -RedirectStandardOutput '$LOGDIR/$logfile' -RedirectStandardError '$LOGDIR/${name}.err'" >/dev/null 2>/dev/null
  # Capture the PID of the just-launched process (newest python matching our loop name).
  sleep 1
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "try { \$p=Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { \$_.CommandLine -like \"*$name*\" -and \$_.CommandLine -notlike '*-c *' } | Sort-Object -Property ProcessId | Select-Object -Last 1; if (\$p) { \$p.ProcessId | Out-File -FilePath '$pidfile' -Encoding ascii } } catch {}" >/dev/null 2>/dev/null
  # Verify it actually came up.
  sleep 4
  if proc_alive "$name"; then
    echo "[$(ts)] WATCHDOG: $name launched OK (pid $(cat "$pidfile" 2>/dev/null | tr -d '
' | head -1))"
  else
    echo "[$(ts)] WATCHDOG: FAIL — $name did not come up after launch" | tee -a "$WLOG"
  fi
}

# Redis (long-running server) — detached + PID-guarded.
REDISPID="$PIDDIR/_pid_redis.txt"
if ! "$REPO/tools/redis/redis-cli.exe" -h 127.0.0.1 -p 6379 ping >/dev/null 2>/dev/null; then
  echo "[$(ts)] WATCHDOG: redis down — starting (detached)"
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "\$p=Start-Process -FilePath '$REDIS' -ArgumentList '$REDISCONF' -WindowStyle Hidden -PassThru; \$p.Id | Out-File -FilePath '$REDISPID' -Encoding ascii" >/dev/null 2>/dev/null
  sleep 3
else
  echo "[$(ts)] WATCHDOG: redis already up"
fi

declare -A LOOPS=(
  [dashboard]="--host 127.0.0.1 --port 8080"
  [monitor]=""
  [synthesize]=""
  [risk]="--equity 108000 --sync-brokerage"
  [execute]="--equity 108000 --paper"
  [ingest]=""
)

for name in "${!LOOPS[@]}"; do
  if proc_alive "$name"; then
    :
  else
    echo "[$(ts)] WATCHDOG: $name down — relaunching (detached)"
    launch_python_detached "$name" "$name.log" -m hermes.app "$name" ${LOOPS[$name]}
  fi
done

if ! proc_alive "_watch_optimize"; then
  echo "[$(ts)] WATCHDOG: watcher down — relaunching (detached)"
  launch_python_detached "_watch_optimize" "watch_optimize.log" "$REPO/scripts/_watch_optimize.py"
fi

echo "[$(ts)] WATCHDOG: check complete"
