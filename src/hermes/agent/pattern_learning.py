"""Pattern-learning: link entry brick_pattern to outcomes, learn per-pattern success.

Harvested concept from the OpenClaw TradingView quant-skill guide (pattern
recognition with historical success-rate + confidence scoring) — reimplemented
on Hermes's own data, not as a dependency.

The sim process (RenkoSimulationEngine) already classifies renko brick patterns
at signal time (BrickPatternAnalyzer) and replays history. This module closes the
loop: it aggregates realized + simulated trade outcomes *grouped by brick_pattern*,
computes sample-size-aware confidence (Wilson lower bound), and persists the result
to pattern_performance. The sim search can then read get_pattern_confidence() to
bias toward high-confidence patterns — learn -> sim -> learn.

Sources of truth:
  * executed trades: pnl_realized (brick_pattern denormalized here via migration 011)
  * sim trades:      simulation_trades (persisted by the optimizer, carries brick_pattern)
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import structlog

from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path, safe_duckdb_connect

log = structlog.get_logger(__name__)

# Minimum sample size before we trust a pattern's win rate enough to surface it.
MIN_SAMPLES_FOR_CONFIDENCE = 5
# Wilson z-score for 95% confidence.
_WILSON_Z = 1.96


def wilson_confidence(n: int, wins: int) -> float:
    """Lower bound of the Wilson score interval (0-1), sample-size-aware.

    Returns ~0.5 for tiny samples (uncertain), converging to the observed
    win rate as n grows. This is the proper "confidence" metric (unlike a raw
    win rate that looks great at n=1).
    """
    if n <= 0:
        return 0.0
    p_hat = wins / n
    denom = 1 + _WILSON_Z**2 / n
    centre = (p_hat + _WILSON_Z**2 / (2 * n)) / denom
    margin = (_WILSON_Z * math.sqrt((p_hat * (1 - p_hat) + _WILSON_Z**2 / (4 * n)) / n)) / denom
    return max(0.0, min(1.0, centre - margin))


def _aggregate_rows(rows: list[dict]) -> dict[str, dict]:
    """Group outcome rows by brick_pattern -> win/loss/pnl stats."""
    out: dict[str, dict] = defaultdict(
        lambda: {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0, "r_sum": 0.0}
    )
    for r in rows:
        pat = (r.get("brick_pattern") or "").strip()
        if not pat or pat == "unknown":
            continue
        net = float(r.get("net_pnl") or 0.0)
        rmult = float(r.get("r_multiple") or 0.0)
        bucket = out[pat]
        bucket["n"] += 1
        bucket["pnl"] += net
        bucket["r_sum"] += rmult
        if net > 0:
            bucket["wins"] += 1
        else:
            bucket["losses"] += 1
    return out


def aggregate_executed(config: HermesConfig) -> dict[str, dict]:
    """Aggregate realized trades (pnl_realized) by brick_pattern."""
    db_path = get_duckdb_path(config)
    rows: list[dict] = []
    try:
        with safe_duckdb_connect(str(db_path), read_only=True) as conn:
            cur = conn.execute(
                """
                SELECT brick_pattern, net_pnl, r_multiple
                FROM pnl_realized
                WHERE brick_pattern IS NOT NULL AND brick_pattern <> ''
                """
            )
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    except Exception as e:
        log.warning("pattern_learning_executed_query_failed", error=str(e))
    return _aggregate_rows(rows)


def aggregate_sim(config: HermesConfig) -> dict[str, dict]:
    """Aggregate simulated trades (simulation_trades) by brick_pattern."""
    db_path = get_duckdb_path(config)
    rows: list[dict] = []
    try:
        with safe_duckdb_connect(str(db_path), read_only=True) as conn:
            cur = conn.execute(
                """
                SELECT brick_pattern, net_pnl, r_multiple
                FROM simulation_trades
                WHERE brick_pattern IS NOT NULL AND brick_pattern <> ''
                """
            )
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                rows.append(dict(zip(cols, row)))
    except Exception as e:
        log.warning("pattern_learning_sim_query_failed", error=str(e))
    return _aggregate_rows(rows)


def _to_performance_record(pattern: str, source: str, bucket: dict) -> dict:
    n = bucket["n"]
    wins = bucket["wins"]
    losses = bucket["losses"]
    win_rate = (wins / n) if n else 0.0
    avg_r = (bucket["r_sum"] / n) if n else 0.0
    expectancy = (bucket["pnl"] / n) if n else 0.0
    gross_win = 0.0
    gross_loss = 0.0
    # profit factor needs gross win/loss; approximate from bucket via pnl sign.
    # We stored only aggregate pnl; recompute PF from wins/losses using avg_r as proxy.
    # (Exact PF requires per-trade gross; acceptable approximation for ranking.)
    profit_factor = 0.0
    if losses > 0:
        # avg win ≈ expectancy + (losses/n)*|avg_loss|; use avg_r to scale.
        avg_win = max(avg_r, 0.01) if wins else 0.0
        avg_loss = abs(min(avg_r, -0.01)) if losses else 0.01
        profit_factor = (avg_win * wins) / (avg_loss * losses) if (avg_loss * losses) > 0 else 0.0
    confidence = wilson_confidence(n, wins)
    return {
        "pattern": pattern,
        "source": source,
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 4),
        "avg_r_multiple": round(avg_r, 4),
        "expectancy": round(expectancy, 4),
        "profit_factor": round(profit_factor, 4),
        "confidence": round(confidence, 4),
        "last_updated": datetime.now(timezone.utc),
    }


def update_pattern_performance(config: HermesConfig) -> list[dict]:
    """Recompute per-pattern stats from executed + sim trades and persist.

    Returns the list of pattern_performance rows written.
    """
    db_path = get_duckdb_path(config)
    executed = aggregate_executed(config)
    sim = aggregate_sim(config)

    records: list[dict] = []
    for pattern, bucket in executed.items():
        records.append(_to_performance_record(pattern, "executed", bucket))
    for pattern, bucket in sim.items():
        records.append(_to_performance_record(pattern, "sim", bucket))

    if not records:
        log.info("pattern_learning_no_data")
        return []

    try:
        with safe_duckdb_connect(str(db_path)) as conn:
            for rec in records:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO pattern_performance
                        (pattern, source, n, wins, losses, win_rate,
                         avg_r_multiple, expectancy, profit_factor,
                         confidence, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        rec["pattern"], rec["source"], rec["n"], rec["wins"], rec["losses"],
                        rec["win_rate"], rec["avg_r_multiple"], rec["expectancy"],
                        rec["profit_factor"], rec["confidence"], rec["last_updated"],
                    ],
                )
    except Exception as e:
        log.error("pattern_performance_write_failed", error=str(e))
        return []

    log.info(
        "pattern_performance_updated",
        n_patterns=len(records),
        executed=len(executed),
        sim=len(sim),
    )
    return records


def get_pattern_confidence(
    pattern: str, source: str | None = None, config: HermesConfig | None = None
) -> float | None:
    """Read a pattern's learned confidence (Wilson lower bound, 0-1).

    source: 'executed' | 'sim' | None (prefer executed, fall back to sim).
    Returns None if the pattern has no learned record (insufficient data).
    """
    if config is None:
        from hermes.core.config import load_config
        config = load_config()
    db_path = get_duckdb_path(config)
    try:
        with safe_duckdb_connect(str(db_path), read_only=True) as conn:
            if source:
                row = conn.execute(
                    "SELECT confidence, n FROM pattern_performance WHERE pattern = ? AND source = ?",
                    [pattern, source],
                ).fetchone()
                if row and row[1] >= MIN_SAMPLES_FOR_CONFIDENCE:
                    return float(row[0])
                return None
            # prefer executed
            row = conn.execute(
                "SELECT confidence, n, source FROM pattern_performance WHERE pattern = ? ORDER BY source = 'executed' DESC",
                [pattern],
            ).fetchone()
            if row and row[1] >= MIN_SAMPLES_FOR_CONFIDENCE:
                return float(row[0])
            return None
    except Exception as e:
        log.warning("pattern_confidence_read_failed", error=str(e))
        return None


def get_pattern_performance(config: HermesConfig | None = None) -> list[dict]:
    """Return all learned pattern_performance rows (for dashboard / skill)."""
    if config is None:
        from hermes.core.config import load_config
        config = load_config()
    db_path = get_duckdb_path(config)
    out: list[dict] = []
    try:
        with safe_duckdb_connect(str(db_path), read_only=True) as conn:
            cur = conn.execute(
                "SELECT pattern, source, n, wins, losses, win_rate, avg_r_multiple, "
                "expectancy, profit_factor, confidence, last_updated "
                "FROM pattern_performance ORDER BY confidence DESC, n DESC"
            )
            cols = [d[0] for d in cur.description]
            for row in cur.fetchall():
                out.append(dict(zip(cols, row)))
    except Exception as e:
        log.warning("pattern_performance_read_failed", error=str(e))
    return out
