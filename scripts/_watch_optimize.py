#!/usr/bin/env python3
"""Watcher: wait for real NT heartbeats in DuckDB signal_heartbeats, then run optimize."""
from __future__ import annotations
import os, sys, time, subprocess, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from hermes.core.config import load_config
from hermes.db.migrate import get_duckdb_path
import duckdb

cfg = load_config()
DB = get_duckdb_path(cfg)

MIN_SYMBOLS = 1          # fire as soon as NT pushes anything (crypto + equities)
POLL = 15                # re-check interval
OPTIMIZE_EVERY_SEC = 30 * 60   # re-run at most every 30 min even if set unchanged
N_TRIALS = 30

def get_accepted_symbols():
    try:
        from hermes.db.migrate import safe_duckdb_connect
        with safe_duckdb_connect(str(DB), read_only=True) as c:
            rows = c.execute(
                "SELECT symbol FROM signal_heartbeats "
                "WHERE accepted = TRUE GROUP BY symbol ORDER BY symbol"
            ).fetchall()
        return [r[0] for r in rows]
    except Exception:
        return []

def run_optimize(syms):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    cmd = [
        str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "hermes.app", "optimize",
        "--symbols", ",".join(syms),
        "--days-back", "90",
        "--n-trials", str(N_TRIALS),
    ]
    print("RUN:", " ".join(cmd))
    r = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True)
    print("=== STDOUT ===")
    print(r.stdout)
    print("=== STDERR (tail) ===")
    print(r.stderr[-3000:])
    print("EXIT:", r.returncode)

print("WATCHER_LOOP_START: capturing ALL accepted symbols (crypto + equities) from signal_heartbeats")
last_run_syms = []
last_run_ts = 0.0
while True:
    syms = get_accepted_symbols()
    if len(syms) >= MIN_SYMBOLS:
        now = time.time()
        # Run if we have new symbols OR the cooldown elapsed and the set changed
        if set(syms) != set(last_run_syms) and (now - last_run_ts > OPTIMIZE_EVERY_SEC or not last_run_syms):
            print(f"WATCHER_READY: {len(syms)} symbols -> {syms}")
            run_optimize(syms)
            last_run_syms = syms
            last_run_ts = now
        else:
            print(f"WATCHER_SKIP: {len(syms)} symbols (no new since last run or cooling down)")
    else:
        print(f"WATCHER_WAIT: only {len(syms)} symbol(s) accepted so far")
    time.sleep(POLL)
