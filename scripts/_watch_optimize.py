#!/usr/bin/env python3
"""Watcher: trigger Noble Trader simulation optimizations.

Two triggers (Fix B):
  1. DuckDB poll: when new accepted symbols appear in signal_heartbeats
     (the original 30-min-cadence watcher path).
  2. Redis sim.request.{symbol}: published by the L4 synthesizer the instant an
     actionable heartbeat (trade=true / buy|sell) arrives — fires an on-demand
     optimization immediately instead of waiting for the 30-min watcher.

Both paths funnel into run_optimize() with a per-symbol cooldown so a burst of
signals doesn't spawn a storm of Optuna runs.
"""
from __future__ import annotations
import os, sys, time, subprocess, json, asyncio
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from hermes.core.config import load_config
from hermes.db.migrate import get_duckdb_path
import duckdb

cfg = load_config()
DB = get_duckdb_path(cfg)
HERMES_REDIS_URL = cfg.hermes_redis.get("url", "redis://localhost:6379/1")

MIN_SYMBOLS = 1          # fire as soon as NT pushes anything (crypto + equities)
POLL = 15                # DuckDB re-check interval (seconds)
OPTIMIZE_EVERY_SEC = 30 * 60   # re-run at most every 30 min even if set unchanged
URGENT_COOLDOWN = 5 * 60       # on-demand sim request cooldown per symbol
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


async def redis_sim_requests(last_urgent: dict):
    """Subscribe to sim.request.{symbol}; return symbols needing on-demand sim."""
    import redis.asyncio as aioredis
    client = aioredis.from_url(HERMES_REDIS_URL, decode_responses=True)
    pubsub = client.pubsub()
    await pubsub.psubscribe("sim.request.*")
    pending = []
    try:
        while True:
            msg = await pubsub.get_message(timeout=1.0, ignore_subscribe_messages=True)
            if msg and msg.get("type") == "pmessage":
                try:
                    sym = msg["channel"].rsplit(".", 1)[-1]
                    pending.append(sym)
                except Exception:
                    pass
            else:
                if pending:
                    break
            await asyncio.sleep(0.05)
    finally:
        await pubsub.punsubscribe("sim.request.*")
        await client.close()
    return pending


def main():
    print("WATCHER_LOOP_START: DuckDB poll (30m cadence) + Redis sim.request.* (on-demand)")
    last_run_syms = []
    last_run_ts = 0.0
    last_urgent = {}  # symbol -> epoch sec of last on-demand run

    # Run the Redis subscriber in a background thread-free asyncio loop.
    async def loop():
        nonlocal last_run_syms, last_run_ts
        while True:
            # 1. On-demand sim requests from Redis.
            try:
                requested = await redis_sim_requests(last_urgent)
            except Exception as e:
                print(f"REDIS_SIM_SUB_ERROR: {e}")
                requested = []
            now = time.time()
            urgent_syms = [
                s for s in requested
                if now - last_urgent.get(s, 0) > URGENT_COOLDOWN
            ]
            if urgent_syms:
                print(f"WATCHER_URGENT_SIM: {urgent_syms}")
                run_optimize(urgent_syms)
                for s in urgent_syms:
                    last_urgent[s] = now

            # 2. DuckDB accepted-symbol poll (original cadence).
            syms = get_accepted_symbols()
            if len(syms) >= MIN_SYMBOLS:
                if set(syms) != set(last_run_syms) and (
                    now - last_run_ts > OPTIMIZE_EVERY_SEC or not last_run_syms
                ):
                    print(f"WATCHER_READY: {len(syms)} symbols -> {syms}")
                    run_optimize(syms)
                    last_run_syms = syms
                    last_run_ts = now
                else:
                    print(f"WATCHER_SKIP: {len(syms)} symbols (no new / cooling down)")
            else:
                print(f"WATCHER_WAIT: only {len(syms)} symbol(s) accepted so far")
            await asyncio.sleep(POLL)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        print("WATCHER_STOPPED")


if __name__ == "__main__":
    main()
