"""
Agent operational tasks — scheduled + on-demand functions for the Hermes agent.

These functions are invoked by the CLI (`platform agent --check-shadow-promotions`,
`--monthly-maintenance`, `--check-underperformance`) and by the meta-regime
retrain command. They implement the prescriptive runbook from
`docs/agent_onboarding.md`.

All functions are side-effecting: they read from DuckDB, may write to DuckDB
(hypothesis status updates, config_history rollbacks), and may invoke the
config management layer (`platform config promote/rollback`).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


# ============================================================
# 1. Check shadow promotions (daily, after EOD)
# ============================================================


def check_shadow_promotions(config: HermesConfig) -> dict[str, Any]:
    """Check for shadow hypotheses that are ready to promote to live.

    A hypothesis is ready if:
      - status = 'shadow'
      - shadow_started_at < now() - 7 days (configurable via autonomy.tier_2.requires_shadow_days)
      - shadow Sharpe ≥ 80% of backtest Sharpe (configurable via autonomy.tier_2.requires_shadow_sharpe_pct)

    For each ready hypothesis, this function:
      1. Calls promote_config() to apply the proposed changes
      2. Updates the hypothesis status to 'live'
      3. Returns a summary of what was promoted

    If the AutonomyGate blocks a key (tier 3/4), the hypothesis is marked
    'awaiting_human_approval' and a notification is logged.

    Returns:
        {checked, ready, promoted, blocked, details: [...]}
    """
    from hermes.agent.learning import HypothesisTracker
    from hermes.db.config_history import promote_config

    tracker = HypothesisTracker(config)
    shadow_hyps = tracker.get_hypotheses(status="shadow")

    # Read shadow config
    autonomy_cfg = config.autonomy if hasattr(config, "autonomy") else {}
    if not isinstance(autonomy_cfg, dict):
        autonomy_cfg = {}
    tier2_cfg = autonomy_cfg.get("tier_2", {})
    required_days = tier2_cfg.get("requires_shadow_days", 7)
    # Config stores this as 80 (meaning 80%); normalize to 0.80 fraction
    required_sharpe_pct_raw = tier2_cfg.get("requires_shadow_sharpe_pct", 80)
    if required_sharpe_pct_raw > 1:
        required_sharpe_pct = required_sharpe_pct_raw / 100.0
    else:
        required_sharpe_pct = required_sharpe_pct_raw

    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "checked": len(shadow_hyps),
        "ready": 0,
        "promoted": 0,
        "blocked": 0,
        "details": [],
    }

    for hyp in shadow_hyps:
        # Parse shadow_started_at from backtest_result or rationale
        # (the HypothesisTracker doesn't have a dedicated field for this,
        # so we store it in backtest_result when promote_to_shadow is called)
        backtest_result = hyp.backtest_result or {}
        shadow_started_str = backtest_result.get("shadow_started_at")
        if not shadow_started_str:
            log.debug("shadow_no_start_date", hypothesis_id=hyp.hypothesis_id)
            continue

        try:
            shadow_started = datetime.fromisoformat(shadow_started_str.replace("Z", "+00:00"))
        except Exception:
            continue

        days_in_shadow = (now - shadow_started).days
        if days_in_shadow < required_days:
            log.debug(
                "shadow_not_ready",
                hypothesis_id=hyp.hypothesis_id,
                days_in_shadow=days_in_shadow,
                required=required_days,
            )
            continue

        result["ready"] += 1

        # Check shadow Sharpe vs backtest Sharpe
        backtest_sharpe = backtest_result.get("sharpe", 0) or 0
        shadow_sharpe = backtest_result.get("shadow_sharpe", 0) or 0
        threshold = backtest_sharpe * required_sharpe_pct

        detail = {
            "hypothesis_id": hyp.hypothesis_id,
            "hypothesis": hyp.hypothesis[:80],
            "days_in_shadow": days_in_shadow,
            "backtest_sharpe": backtest_sharpe,
            "shadow_sharpe": shadow_sharpe,
            "threshold": threshold,
            "action": None,
        }

        if shadow_sharpe < threshold:
            detail["action"] = "shadow_underperformed"
            # Mark as rejected — shadow didn't validate the backtest
            tracker.reject(hyp.hypothesis_id, reason=f"shadow Sharpe {shadow_sharpe:.2f} < {threshold:.2f} (80% of backtest {backtest_sharpe:.2f})")
            log.info("shadow_rejected", hypothesis_id=hyp.hypothesis_id, shadow_sharpe=shadow_sharpe, threshold=threshold)
        else:
            # Ready to promote — extract the proposed changes
            proposed_change = hyp.proposed_change or {}
            if not proposed_change:
                detail["action"] = "no_proposed_change"
                log.warning("shadow_no_proposed_change", hypothesis_id=hyp.hypothesis_id)
                result["details"].append(detail)
                continue

            try:
                promote_result = promote_config(
                    config,
                    changes=proposed_change,
                    rationale=f"shadow Sharpe {shadow_sharpe:.2f} ≥ {threshold:.2f} (80% of backtest {backtest_sharpe:.2f})",
                    author="hermes",
                    hypothesis_id=hyp.hypothesis_id,
                )
                tracker.promote_to_live(hyp.hypothesis_id)
                detail["action"] = "promoted"
                detail["config_hash"] = promote_result["config_hash"]
                result["promoted"] += 1
                log.info(
                    "shadow_promoted",
                    hypothesis_id=hyp.hypothesis_id,
                    config_hash=promote_result["config_hash"],
                )
            except Exception as e:
                # Likely blocked by AutonomyGate (tier 3/4 key)
                detail["action"] = "blocked"
                detail["error"] = str(e)
                result["blocked"] += 1
                # Mark as awaiting human approval
                _mark_awaiting_human(tracker, hyp.hypothesis_id, str(e))
                log.warning(
                    "shadow_promotion_blocked",
                    hypothesis_id=hyp.hypothesis_id,
                    error=str(e),
                )

        result["details"].append(detail)

    log.info("shadow_promotion_check_complete", **result)
    return result


def _mark_awaiting_human(tracker, hypothesis_id: str, reason: str) -> None:
    """Mark a hypothesis as awaiting human approval (status stays 'shadow'
    but rationale is updated so the operator knows to act)."""
    try:
        import duckdb
        from hermes.db.migrate import get_duckdb_path

        # Direct SQL update — HypothesisTracker doesn't have an "awaiting" status
        db_path = get_duckdb_path(tracker._config)
        with duckdb.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE hermes_hypotheses SET rationale = ? WHERE hypothesis_id = ?",
                [f"AWAITING_HUMAN_APPROVAL: {reason}", hypothesis_id],
            )
    except Exception as e:
        log.warning("mark_awaiting_failed", hypothesis_id=hypothesis_id, error=str(e))


# ============================================================
# 2. Check underperformance → auto-rollback
# ============================================================


def check_underperformance(config: HermesConfig) -> dict[str, Any]:
    """Check if any recently-promoted config is underperforming in live trading.

    A promoted config is underperforming if:
      - It was promoted from config_history with source='hermes'
      - It's been live for ≥ 14 days
      - Live Sharpe < 50% of backtest Sharpe over the same period

    For each underperforming config:
      1. Calls rollback_config() to restore the previous config
      2. Logs the rollback in config_history
      3. Returns a summary

    Returns:
        {checked, underperforming, rolled_back, details: [...]}
    """
    from hermes.db.config_history import get_config_history, rollback_config, get_config_by_hash

    history = get_config_history(config, limit=50)
    hermes_promotions = [h for h in history if h.get("source") == "hermes"]

    now = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "checked": len(hermes_promotions),
        "underperforming": 0,
        "rolled_back": 0,
        "details": [],
    }

    for entry in hermes_promotions:
        ts = entry.get("ts")
        if isinstance(ts, str):
            try:
                promoted_at = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        elif isinstance(ts, datetime):
            promoted_at = ts
        else:
            continue

        days_live = (now - promoted_at).days
        if days_live < 14:
            continue  # too soon to judge

        # Get the previous config hash (the one before this promotion)
        # History is newest-first, so find the entry just before this one
        try:
            idx = history.index(entry)
            if idx + 1 >= len(history):
                continue  # no previous entry
            previous_hash = history[idx + 1].get("config_hash")
        except (ValueError, IndexError):
            continue

        # Compute live Sharpe since promotion
        live_sharpe = _compute_live_sharpe(config, promoted_at, now)

        # Get backtest Sharpe from the diff/rationale
        config_hash = entry.get("config_hash")
        detail_entry = get_config_by_hash(config, config_hash) or {}
        diff = detail_entry.get("diff", {}) or {}
        rationale = entry.get("rationale", "")

        # Try to extract backtest Sharpe from rationale (format: "shadow Sharpe X ≥ Y")
        backtest_sharpe = _extract_sharpe_from_rationale(rationale)

        detail = {
            "config_hash": config_hash,
            "promoted_at": str(promoted_at),
            "days_live": days_live,
            "live_sharpe": live_sharpe,
            "backtest_sharpe": backtest_sharpe,
            "action": None,
        }

        if backtest_sharpe and live_sharpe is not None:
            threshold = backtest_sharpe * 0.5
            if live_sharpe < threshold:
                detail["action"] = "underperforming"
                result["underperforming"] += 1

                # Auto-rollback
                try:
                    rollback_result = rollback_config(
                        config, previous_hash,
                        author="hermes",
                        rationale=f"auto-rollback: live Sharpe {live_sharpe:.2f} < 50% of backtest {backtest_sharpe:.2f} over {days_live} days",
                    )
                    detail["action"] = "rolled_back"
                    detail["rolled_back_to"] = previous_hash
                    result["rolled_back"] += 1
                    log.info(
                        "auto_rollback_executed",
                        config_hash=config_hash,
                        rolled_back_to=previous_hash,
                        live_sharpe=live_sharpe,
                        backtest_sharpe=backtest_sharpe,
                    )
                except Exception as e:
                    detail["action"] = "rollback_failed"
                    detail["error"] = str(e)
                    log.error("auto_rollback_failed", config_hash=config_hash, error=str(e))

        result["details"].append(detail)

    log.info("underperformance_check_complete", **result)
    return result


def _compute_live_sharpe(
    config: HermesConfig, since: datetime, until: datetime,
) -> float | None:
    """Compute annualized Sharpe ratio from pnl_realized since the given date.

    Returns None if insufficient data (< 10 trades).
    """
    try:
        import duckdb
        import numpy as np

        db_path = get_duckdb_path(config)
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM pnl_realized LIMIT 1")
            except Exception:
                return None

            result = conn.execute(
                """
                SELECT net_pnl
                FROM pnl_realized
                WHERE ts_closed >= ? AND ts_closed <= ?
                ORDER BY ts_closed ASC
                """,
                [since, until],
            ).fetchdf()

        if result.empty or len(result) < 10:
            return None

        returns = result["net_pnl"].astype(float).values
        if returns.std() == 0:
            return 0.0
        # Annualize: assume ~252 trading days, ~5 trades/day → 1260 trades/year
        # Sharpe = mean / std * sqrt(N_per_year)
        n_per_year = 1260
        sharpe = (returns.mean() / returns.std()) * (n_per_year ** 0.5)
        return float(sharpe)
    except Exception as e:
        log.warning("compute_live_sharpe_failed", error=str(e))
        return None


def _extract_sharpe_from_rationale(rationale: str) -> float | None:
    """Extract the backtest Sharpe from a rationale string.

    Looks for patterns like "shadow Sharpe 1.6 ≥ 80% of backtest 1.8" → returns 1.8.
    """
    import re
    # Match "backtest X.Y" or "of backtest X.Y"
    match = re.search(r"backtest\s+(\d+\.?\d*)", rationale, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return None


# ============================================================
# 3. Monthly maintenance
# ============================================================


def monthly_maintenance(config: HermesConfig) -> dict[str, Any]:
    """Run monthly maintenance tasks.

    Tasks:
      1. Archive old Parquet data (>90 days) to archives/parquet/
      2. DuckDB VACUUM
      3. Review hypothesis tracker (count by status, flag stuck)
      4. Check API key rotation schedule (log reminders)
      5. Test DR (verify backup is restorable)
      6. Log reminder for NT HMM retraining (upstream task)

    Returns:
        {archived_files, vacuumed, hypothesis_summary, rotation_reminders, dr_test}
    """
    from pathlib import Path
    from hermes.agent.learning import HypothesisTracker

    result: dict[str, Any] = {
        "archived_files": 0,
        "vacuumed": False,
        "hypothesis_summary": {},
        "rotation_reminders": [],
        "dr_test": "skipped",
        "hmm_retrain_reminder": False,
    }

    # 1. Archive old Parquet data
    try:
        parquet_base = Path("./data/parquet")
        archive_base = Path("./archives/parquet")
        if parquet_base.exists():
            archive_base.mkdir(parents=True, exist_ok=True)
            import shutil
            cutoff = datetime.now(timezone.utc).timestamp() - (90 * 86400)
            for pq_file in parquet_base.rglob("*.parquet"):
                if pq_file.stat().st_mtime < cutoff:
                    dest = archive_base / pq_file.relative_to(parquet_base)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(pq_file), str(dest))
                    result["archived_files"] += 1
            log.info("parquet_archived", files=result["archived_files"])
    except Exception as e:
        log.warning("parquet_archive_failed", error=str(e))

    # 2. DuckDB VACUUM
    try:
        import duckdb
        db_path = get_duckdb_path(config)
        with duckdb.connect(str(db_path)) as conn:
            conn.execute("VACUUM")
        result["vacuumed"] = True
        log.info("duckdb_vacuumed")
    except Exception as e:
        log.warning("duckdb_vacuum_failed", error=str(e))

    # 3. Hypothesis tracker summary
    try:
        tracker = HypothesisTracker(config)
        all_hyps = tracker.get_hypotheses()
        summary: dict[str, int] = {}
        stuck: list[str] = []
        now = datetime.now(timezone.utc)
        for h in all_hyps:
            summary[h.status] = summary.get(h.status, 0) + 1
            if h.status == "shadow":
                # Check if stuck (>14 days in shadow)
                br = h.backtest_result or {}
                shadow_started = br.get("shadow_started_at")
                if shadow_started:
                    try:
                        started = datetime.fromisoformat(shadow_started.replace("Z", "+00:00"))
                        if (now - started).days > 14:
                            stuck.append(h.hypothesis_id)
                    except Exception:
                        pass
        result["hypothesis_summary"] = summary
        result["stuck_hypotheses"] = stuck
        log.info("hypothesis_review_complete", summary=summary, stuck=len(stuck))
    except Exception as e:
        log.warning("hypothesis_review_failed", error=str(e))

    # 4. API key rotation reminders (every 90 days)
    reminders = [
        "Alpaca API keys — generate new paper keys, update .env, restart",
        "Hyperliquid wallet — consider generating new dedicated wallet",
        "HERMES_AGENT_TOKEN — rotate via .env + restart",
        "HERMES_SESSION_SECRET — rotate via .env + restart (invalidates all browser sessions)",
    ]
    result["rotation_reminders"] = reminders
    log.info("rotation_reminders_logged", count=len(reminders))

    # 5. DR test — verify backup exists and is restorable
    try:
        backup_dir = Path("./backups")
        backups = sorted(backup_dir.glob("hermes_*.duckdb")) if backup_dir.exists() else []
        if backups:
            latest = backups[-1]
            # Verify the backup opens
            import duckdb
            with duckdb.connect(str(latest), read_only=True) as conn:
                tables = conn.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='main'"
                ).fetchone()[0]
            result["dr_test"] = f"ok (latest backup: {latest.name}, {tables} tables)"
            log.info("dr_test_passed", backup=latest.name, tables=tables)
        else:
            result["dr_test"] = "no backups found — run: cp data/hermes.duckdb backups/hermes_$(date +%Y%m%d).duckdb"
            log.warning("dr_test_no_backups")
    except Exception as e:
        result["dr_test"] = f"failed: {e}"
        log.error("dr_test_failed", error=str(e))

    # 6. NT HMM retraining reminder (upstream task)
    result["hmm_retrain_reminder"] = True
    log.info("hmm_retrain_reminder_logged", note="NT HMM retraining is an upstream task — notify Noble Trader operator")

    log.info("monthly_maintenance_complete", **result)
    return result


# ============================================================
# 4. Meta-regime retrain (rule-based threshold recalibration)
# ============================================================


def retrain_meta_regime(config: HermesConfig) -> dict[str, Any]:
    """Retrain the meta-regime classifier.

    Hermes's meta-regime classifier is RULE-BASED (not an HMM — the HMM lives
    upstream in Noble Trader). Retraining means:

      1. Pull the last 30 days of meta_regime_history from DuckDB
      2. Compute the actual distribution of regime states
      3. Recalibrate thresholds (correlation, funding, liquidity, entropy) based
         on recent data percentiles
      4. Write the new thresholds to config_history (tier 3 — requires human approval)
      5. Log a reminder that the NT HMM (upstream) should also be retrained

    Returns:
        {samples, distribution, recalibrated_thresholds, config_change_proposed, nt_reminder}
    """
    result: dict[str, Any] = {
        "samples": 0,
        "distribution": {},
        "recalibrated_thresholds": {},
        "config_change_proposed": False,
        "nt_reminder": True,
    }

    try:
        import duckdb

        db_path = get_duckdb_path(config)
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM meta_regime_history LIMIT 1")
            except Exception:
                result["error"] = "meta_regime_history table not found — run platform monitor first"
                return result

            rows = conn.execute(
                """
                SELECT state, COUNT(*) as n
                FROM meta_regime_history
                WHERE ts >= now() - INTERVAL '30 days'
                GROUP BY state
                ORDER BY n DESC
                """
            ).fetchall()

        distribution = {state: count for state, count in rows}
        total = sum(distribution.values())
        result["samples"] = total
        result["distribution"] = distribution

        if total < 100:
            result["error"] = f"insufficient data ({total} samples, need ≥100)"
            log.warning("retrain_insufficient_data", samples=total)
            return result

        # Recalibrate thresholds based on distribution
        # If risk_off > 20% of time → tighten correlation threshold (more sensitive)
        # If calm_trend > 60% → loosen (less sensitive, fewer false alarms)
        risk_off_pct = distribution.get("risk_off", 0) / total
        calm_trend_pct = distribution.get("calm_trend", 0) / total

        current_thresholds = (
            config.meta_regime.get("thresholds", {}) if hasattr(config, "meta_regime") else {}
        )
        if not isinstance(current_thresholds, dict):
            current_thresholds = {}

        recalibrated = dict(current_thresholds)
        changes_proposed = False

        if risk_off_pct > 0.20:
            # Too much risk_off → tighten correlation threshold (lower = more sensitive)
            old = recalibrated.get("risk_off_corr_threshold", 0.75)
            new = max(0.60, old - 0.05)
            if new != old:
                recalibrated["risk_off_corr_threshold"] = new
                changes_proposed = True
                log.info("threshold_recalibrated", key="risk_off_corr_threshold", old=old, new=new, reason=f"risk_off={risk_off_pct:.1%}")

        if calm_trend_pct > 0.60 and risk_off_pct < 0.05:
            # Very calm → loosen correlation threshold (less sensitive)
            old = recalibrated.get("risk_off_corr_threshold", 0.75)
            new = min(0.85, old + 0.05)
            if new != old:
                recalibrated["risk_off_corr_threshold"] = new
                changes_proposed = True
                log.info("threshold_recalibrated", key="risk_off_corr_threshold", old=old, new=new, reason=f"calm_trend={calm_trend_pct:.1%}")

        result["recalibrated_thresholds"] = recalibrated
        result["config_change_proposed"] = changes_proposed

        if changes_proposed:
            # The thresholds are tier 3 keys — agent cannot auto-promote.
            # Log a proposal for human approval.
            result["proposed_changes"] = {
                "meta_regime.thresholds.risk_off_corr_threshold": recalibrated["risk_off_corr_threshold"],
            }
            result["action"] = "human_approval_required"
            result["next_step"] = (
                "Thresholds recalibrated based on 30-day regime distribution. "
                "To apply: platform config set meta_regime.thresholds.risk_off_corr_threshold "
                f"{recalibrated['risk_off_corr_threshold']} --rationale \"monthly retrain: "
                f"risk_off={risk_off_pct:.1%}, calm_trend={calm_trend_pct:.1%}\""
            )
            log.info("retrain_proposed", changes=result["proposed_changes"])
        else:
            result["action"] = "no_changes_needed"
            log.info("retrain_no_changes", distribution=distribution)

    except Exception as e:
        result["error"] = str(e)
        log.error("retrain_failed", error=str(e))

    result["nt_reminder"] = True
    result["nt_reminder_note"] = (
        "Noble Trader's HMM (4-state vol × 4-state trend) should also be retrained "
        "on a 2-year rolling window. This is an UPSTREAM task — notify the NT operator."
    )

    log.info("meta_regime_retrain_complete", **result)
    return result
