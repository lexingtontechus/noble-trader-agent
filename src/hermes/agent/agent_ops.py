"""
Agent operational tasks — scheduled + on-demand functions for the Hermes agent.

Currently exposes `retrain_meta_regime` (rule-based threshold recalibration
for the meta-regime classifier). Invoked by `platform meta-regime --retrain`.
"""

from __future__ import annotations

from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


# ============================================================
# Meta-regime retrain (rule-based threshold recalibration)
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
