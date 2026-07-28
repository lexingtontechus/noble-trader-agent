"""
Signal explainer service — LLM rationale + explanation generation
+ backfill.

Phase 1B of the Hermes skills strategy. The Hermes agent reads
skills/signal-explainer/SKILL.md and updates rows in the
`signal_explanations` table (1:1 with `signal_heartbeats`, keyed
by `heartbeat_id`).

This module is the in-process service the `noble explanation` CLI
calls. It handles:
  * SELECTing heartbeats that need an explanation (per-day or
    backfill window, filtered by explanation_status)
  * Building the skill payload from signal_heartbeats + the latest
    meta_regime_history snapshot for the symbol
  * Calling the skill invoker (constructor-injected; the agent
    runtime passes its own LLM router's invoke function)
  * INSERT/UPDATEing signal_explanations with the result

It does NOT call an LLM directly. The skill_invoker callable is
provided by the caller — when the agent runtime instantiates
SignalExplainer, it passes its own inference router's invoke
function. When the CLI is invoked outside the agent runtime,
skill_invoker defaults to None and the generate/backfill methods
raise a clear error.

Mirrors the Phase 1A v10 contract of `TradeJournal`
(`src/hermes/ops/trade_journal.py`). Same shape:
  * skill_invoker constructor kwarg (Protocol-typed)
  * SKILL_MD_PATH class constant pointing at the skill file
  * generate_for_day() + backfill() public API
  * _select_pending() builds the SELECT + status filter
  * _process_rows() iterates + handles success/failure
  * _process_one() builds payload, invokes skill, writes row
  * _write_explanation() upserts the row
  * _mark_unavailable() marks the row for retry
  * _stats dict for CLI echo

Skill invoker contract:
    def invoker(skill_md_path: Path, payload: dict) -> dict:
        ...
        return {
            "rationale":         str,   # 1-2 sentences
            "explanation":       str,   # 4-6 sentences
            "source_breakdown":  dict,  # per-source P_win + ev + kelly
            "prompt_tokens":     int | None,
            "completion_tokens": int | None,
        }

Returns the skill output as a dict. On failure, raise any
exception; SignalExplainer catches it and writes
explanation_status='llm_unavailable' to the row.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


class SkillInvoker(Protocol):
    """Callable contract for the agent's skill invoker."""

    def __call__(self, skill_md_path: Path, payload: dict) -> dict:
        ...


class SignalExplainer:
    """Signal explainer service — generates LLM rationales and backfills.

    Usage (from `noble explanation generate`):
        se = SignalExplainer(cfg)  # skill_invoker defaults to None
        n = se.generate_explanations_for_day(date(2026, 7, 22))

    Usage (from agent runtime):
        se = SignalExplainer(cfg, skill_invoker=agent.infer)
        n = se.generate_explanations_for_day(date(2026, 7, 22))
    """

    SKILL_MD_PATH = Path("skills/signal-explainer/SKILL.md")

    def __init__(
        self,
        config: HermesConfig,
        *,
        skill_invoker: SkillInvoker | None = None,
    ) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._skill_invoker = skill_invoker
        self._stats: dict[str, int] = {
            "selected": 0,
            "generated": 0,
            "failed": 0,
            "skipped": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_explanations_for_day(
        self,
        trade_date: date,
        *,
        force: bool = False,
    ) -> int:
        """Generate LLM explanations for every signal emitted on `trade_date`.

        Selects rows from signal_heartbeats LEFT JOIN signal_explanations
        WHERE ts_received::DATE = trade_date AND (
            explanation_status IS NULL
            OR explanation_status = 'llm_unavailable'
            OR (force = True AND explanation_status NOT IN ('reviewed', 'skipped'))
        ).

        Never overwrites rows where explanation_status IN ('reviewed',
        'skipped') — those are human-acked / human-dismissed.

        Returns the count of rows updated (generated + failed).
        """
        rows = self._select_pending(trade_date, trade_date, retry_failed=True, force=force)
        return self._process_rows(rows)

    def backfill(
        self,
        start_date: date,
        end_date: date,
        *,
        retry_failed: bool = False,
    ) -> int:
        """Idempotent backfill of missing/failed explanations.

        Selects rows where explanation_status IS NULL (always), plus
        explanation_status='llm_unavailable' iff retry_failed=True.
        Never overwrites 'generated' or 'reviewed' rows.

        Returns the count of rows updated (generated + failed).
        """
        rows = self._select_pending(start_date, end_date, retry_failed=retry_failed, force=False)
        return self._process_rows(rows)

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_pending(
        self,
        start_date: date,
        end_date: date,
        *,
        retry_failed: bool,
        force: bool,
    ) -> list[dict[str, Any]]:
        """Select heartbeats needing explanations in the date window."""
        import duckdb

        # Same status filter logic as TradeJournal — every NULL row
        # always gets an attempt; 'llm_unavailable' rows get retried
        # if retry_failed or force; 'generated' rows get re-attempted
        # only if force. 'reviewed' and 'skipped' are always protected.
        status_filter: list[str] = []
        status_filter.append("se.explanation_status IS NULL")
        if retry_failed or force:
            status_filter.append("se.explanation_status = 'llm_unavailable'")
        if force:
            status_filter.append("se.explanation_status = 'generated'")
        status_clause = " OR ".join(status_filter)

        # We select the heartbeat row + the latest meta_regime_history
        # snapshot for the symbol (taken at or before ts_received) so
        # the skill can ground the explanation in the regime context
        # the signal was emitted under.
        query = f"""
            SELECT
                hb.heartbeat_id,
                hb.ts_received,
                hb.symbol,
                hb.strategy_id,
                hb.signal,
                hb.entry_price,
                hb.stop_loss,
                hb.take_profit,
                hb.aggression,
                hb.brick_size,
                hb.sl_bricks,
                hb.tp_bricks,
                hb.regime,
                hb.regime_conf,
                hb.regime_shift,
                hb.prev_regime,
                hb.shift_at,
                hb.shifts_24h,
                hb.ev,
                hb.ev_per_dollar,
                hb.p_win,
                hb.p_regime,
                hb.p_imbalance,
                hb.p_markov,
                hb.p_pattern,
                hb.p_timesfm,
                hb.ev_scale,
                hb.timesfm_horizon,
                hb.markov_current_state,
                hb.tail_risk_score,
                hb.tail_risk_action,
                hb.kelly_f,
                hb.effective_kelly,
                hb.sources_used,
                hb.weights_used,
                hb.p_win_kelly_shrink,
                hb.calibration_bias,
                hb.calibration_status,
                se.explanation_status,
                mrh.new_state            AS mrh_new_state,
                mrh.prev_state           AS mrh_prev_state,
                mrh.confidence           AS mrh_confidence,
                mrh.posterior_probs      AS mrh_posterior_probs,
                mrh.trigger              AS mrh_trigger,
                mrh.funding_rate_8h      AS mrh_funding_rate_8h,
                mrh.book_depth_percentile AS mrh_book_depth_percentile,
                mrh.spread_percentile    AS mrh_spread_percentile,
                mrh.posterior_entropy    AS mrh_posterior_entropy,
                mrh.ts                   AS mrh_ts
            FROM signal_heartbeats hb
            LEFT JOIN signal_explanations se
                ON hb.heartbeat_id = se.heartbeat_id
            LEFT JOIN LATERAL (
                SELECT *
                FROM meta_regime_history
                WHERE symbol IS NOT DISTINCT FROM hb.symbol
                  AND ts <= hb.ts_received
                ORDER BY ts DESC
                LIMIT 1
            ) mrh ON TRUE
            WHERE CAST(hb.ts_received AS DATE) BETWEEN ? AND ?
              AND ({status_clause})
              AND hb.accepted = TRUE
            ORDER BY hb.ts_received
        """

        try:
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                result = conn.execute(query, [start_date, end_date]).fetchall()
                columns = [d[0] for d in conn.description]
        except Exception as e:
            log.error("signal_explainer_select_failed", error=str(e))
            return []

        rows = [dict(zip(columns, row)) for row in result]
        self._stats["selected"] += len(rows)
        log.info(
            "signal_explainer_selected",
            count=len(rows),
            start=str(start_date),
            end=str(end_date),
        )
        return rows

    def _process_rows(self, rows: list[dict[str, Any]]) -> int:
        """Process each row: invoke skill, upsert signal_explanations."""
        if not rows:
            return 0

        if self._skill_invoker is None:
            raise RuntimeError(
                "SignalExplainer.skill_invoker is None — cannot generate explanations. "
                "The noble explanation CLI must be invoked from the agent runtime "
                "(which provides its inference router) or skill_invoker must be "
                "passed to the SignalExplainer constructor."
            )

        updated = 0
        for row in rows:
            ok = self._process_one(row)
            if ok:
                updated += 1
        return updated

    def _process_one(self, row: dict[str, Any]) -> bool:
        """Process one heartbeat: invoke skill, upsert row. Returns True on success."""
        heartbeat_id = row["heartbeat_id"]
        payload = self._build_payload(row)

        try:
            result = self._skill_invoker(self.SKILL_MD_PATH, payload)
        except Exception as e:
            log.warning(
                "signal_explainer_skill_failed",
                heartbeat_id=heartbeat_id,
                error=str(e),
            )
            self._mark_unavailable(heartbeat_id)
            self._stats["failed"] += 1
            return False

        rationale = result.get("rationale")
        explanation = result.get("explanation")
        if not rationale or not explanation:
            log.warning(
                "signal_explainer_skill_empty",
                heartbeat_id=heartbeat_id,
            )
            self._mark_unavailable(heartbeat_id)
            self._stats["failed"] += 1
            return False

        source_breakdown = result.get("source_breakdown")
        source_breakdown_json: str | None = None
        if source_breakdown is not None:
            try:
                source_breakdown_json = json.dumps(source_breakdown)
            except (TypeError, ValueError) as e:
                log.warning(
                    "signal_explainer_breakdown_serialize_failed",
                    heartbeat_id=heartbeat_id,
                    error=str(e),
                )

        prompt_tokens = result.get("prompt_tokens")
        completion_tokens = result.get("completion_tokens")

        self._write_explanation(
            heartbeat_id=heartbeat_id,
            rationale=rationale,
            explanation=explanation,
            source_breakdown=source_breakdown_json,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            status="generated",
        )
        self._stats["generated"] += 1
        log.info(
            "signal_explainer_generated",
            heartbeat_id=heartbeat_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return True

    def _build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        """Build the skill payload from the heartbeat + regime snapshot.

        The skill file (skills/signal-explainer/SKILL.md) defines what
        fields the skill expects. We pass everything we have; the
        skill picks what it needs.
        """
        # The heartbeat row is the source-of-truth signal snapshot.
        # The skill receives it under `signal` (mirroring the
        # trade_journal exemplar's payload shape) plus a separate
        # `regime_context` object with the meta_regime_history snapshot.
        signal_obj: dict[str, Any] = {
            "heartbeat_id": row.get("heartbeat_id"),
            "symbol": row.get("symbol"),
            "strategy_id": row.get("strategy_id"),
            "signal": row.get("signal"),
            "ts_received": row.get("ts_received"),
            # Entry / exit
            "entry_price": row.get("entry_price"),
            "stop_loss": row.get("stop_loss"),
            "take_profit": row.get("take_profit"),
            "aggression": row.get("aggression"),
            # Renko
            "brick_size": row.get("brick_size"),
            "sl_bricks": row.get("sl_bricks"),
            "tp_bricks": row.get("tp_bricks"),
            # Upstream regime
            "regime": row.get("regime"),
            "regime_conf": row.get("regime_conf"),
            "regime_shift": row.get("regime_shift"),
            "prev_regime": row.get("prev_regime"),
            "shift_at": row.get("shift_at"),
            "shifts_24h": row.get("shifts_24h"),
            # Upstream EV engine
            "ev": row.get("ev"),
            "ev_per_dollar": row.get("ev_per_dollar"),
            "p_win": row.get("p_win"),
            "p_regime": row.get("p_regime"),
            "p_imbalance": row.get("p_imbalance"),
            "p_markov": row.get("p_markov"),
            "p_pattern": row.get("p_pattern"),
            "p_timesfm": row.get("p_timesfm"),
            "ev_scale": row.get("ev_scale"),
            # TimesFM / Markov
            "timesfm_horizon": row.get("timesfm_horizon"),
            "markov_current_state": row.get("markov_current_state"),
            # Tail risk
            "tail_risk_score": row.get("tail_risk_score"),
            "tail_risk_action": row.get("tail_risk_action"),
            # Kelly
            "kelly_f": row.get("kelly_f"),
            "effective_kelly": row.get("effective_kelly"),
            # v5 source breakdown (migration 016)
            "sources_used": row.get("sources_used"),
            "weights_used": row.get("weights_used"),
            "p_win_kelly_shrink": row.get("p_win_kelly_shrink"),
            # Calibration (migration 015)
            "calibration_bias": row.get("calibration_bias"),
            "calibration_status": row.get("calibration_status"),
        }

        # The regime_context object is the latest meta_regime_history
        # snapshot at or before ts_received. May be entirely NULL if
        # no regime history exists for the symbol yet — the skill
        # must handle that case (omit regime_context from the
        # explanation rather than fabricate).
        regime_context: dict[str, Any] | None = None
        if row.get("mrh_new_state") is not None:
            regime_context = {
                "ts": row.get("mrh_ts"),
                "prev_state": row.get("mrh_prev_state"),
                "new_state": row.get("mrh_new_state"),
                "confidence": row.get("mrh_confidence"),
                "posterior_probs": row.get("mrh_posterior_probs"),
                "trigger": row.get("mrh_trigger"),
                "funding_rate_8h": row.get("mrh_funding_rate_8h"),
                "book_depth_percentile": row.get("mrh_book_depth_percentile"),
                "spread_percentile": row.get("mrh_spread_percentile"),
                "posterior_entropy": row.get("mrh_posterior_entropy"),
            }

        return {
            "heartbeat_id": row.get("heartbeat_id"),
            "symbol": row.get("symbol"),
            "signal": signal_obj,
            "regime_context": regime_context,
        }

    def _write_explanation(
        self,
        *,
        heartbeat_id: str,
        rationale: str,
        explanation: str,
        source_breakdown: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        status: str,
    ) -> None:
        """Upsert signal_explanations row with the skill result."""
        import duckdb

        now = datetime.now(timezone.utc)

        query = """
            INSERT INTO signal_explanations (
                heartbeat_id, rationale, explanation, source_breakdown,
                explanation_status, explanation_generated_at,
                prompt_tokens, completion_tokens,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT (heartbeat_id) DO UPDATE SET
                rationale = excluded.rationale,
                explanation = excluded.explanation,
                source_breakdown = excluded.source_breakdown,
                explanation_status = excluded.explanation_status,
                explanation_generated_at = excluded.explanation_generated_at,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                updated_at = excluded.updated_at
        """

        params = [
            heartbeat_id, rationale, explanation, source_breakdown,
            status, now,
            prompt_tokens, completion_tokens,
            now, now,
        ]

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(query, params)
        except Exception as e:
            log.error(
                "signal_explainer_write_failed",
                heartbeat_id=heartbeat_id,
                error=str(e),
            )

    def _mark_unavailable(self, heartbeat_id: str) -> None:
        """Mark a heartbeat's explanation as failed (retryable)."""
        import duckdb

        now = datetime.now(timezone.utc)
        query = """
            INSERT INTO signal_explanations (
                heartbeat_id, explanation_status, created_at, updated_at
            ) VALUES (?, 'llm_unavailable', ?, ?)
            ON CONFLICT (heartbeat_id) DO UPDATE SET
                explanation_status = 'llm_unavailable',
                updated_at = excluded.updated_at
        """
        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(query, [heartbeat_id, now, now])
        except Exception as e:
            log.error(
                "signal_explainer_mark_unavailable_failed",
                heartbeat_id=heartbeat_id,
                error=str(e),
            )
