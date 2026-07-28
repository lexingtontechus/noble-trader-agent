"""
Trade journal service — LLM postmortem generation + backfill.

Phase 1A of the Hermes skills strategy. The Hermes agent reads
skills/trade_journal/SKILL.md and updates rows in the
`trade_postmortem` table (1:1 with `trade_signals_blended`).

This module is the in-process service the `noble journal` CLI
calls. It handles:
  * SELECTing signals that need a postmortem (per-day or backfill
    window, filtered by postmortem_status)
  * Building the skill payload from trade_signals_blended + PnL
    attribution
  * Calling the skill invoker (constructor-injected; the agent
    runtime passes its own LLM router's invoke function)
  * UPDATEing trade_postmortem with the result

It does NOT call an LLM directly. The skill_invoker callable is
provided by the caller — when the agent runtime instantiates
TradeJournal, it passes its own inference router's invoke function.
When the CLI is invoked outside the agent runtime, skill_invoker
defaults to None and the generate/backfill methods raise a clear
error.

Skill invoker contract:
    def invoker(skill_md_path: Path, payload: dict) -> dict:
        ...
        return {
            "postmortem_llm": str,
            "hypothesis": str | None,   # only if not already set
            "prompt_tokens": int | None,
            "completion_tokens": int | None,
        }

Returns the skill output as a dict. On failure, raise any
exception; TradeJournal catches it and writes
postmortem_status='llm_unavailable' to the row.
"""

from __future__ import annotations

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


class TradeJournal:
    """Trade journal service — generates LLM postmortems and backfills.

    Usage (from `noble journal generate`):
        tj = TradeJournal(cfg)  # skill_invoker defaults to None
        n = tj.generate_postmortem_for_day(date(2026, 7, 20))

    Usage (from agent runtime):
        tj = TradeJournal(cfg, skill_invoker=agent.infer)
        n = tj.generate_postmortem_for_day(date(2026, 7, 20))
    """

    SKILL_MD_PATH = Path("skills/trade_journal/SKILL.md")

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

    def generate_postmortem_for_day(
        self,
        trade_date: date,
        *,
        force: bool = False,
    ) -> int:
        """Generate LLM postmortems for every signal emitted on `trade_date`.

        Selects rows from trade_postmortem JOIN trade_signals_blended
        WHERE ts_emitted::DATE = trade_date AND (
            postmortem_status IS NULL
            OR postmortem_status = 'llm_unavailable'
            OR (force = True AND postmortem_status NOT IN ('reviewed', 'skipped'))
        ).

        Never overwrites rows where postmortem_status IN ('reviewed',
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
        """Idempotent backfill of missing/failed postmortems.

        Selects rows where postmortem_status IS NULL (always), plus
        postmortem_status='llm_unavailable' iff retry_failed=True.
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
        """Select signals needing postmortem in the date window."""
        import duckdb

        # Build the status filter as an explicit OR of the statuses we DO
        # want. Always include NULL. retry_failed adds 'llm_unavailable'.
        # force adds 'generated' (so --force regenerates prior successes).
        # 'reviewed' / 'skipped' are protected by simple omission.
        #
        # DO NOT add a redundant `AND postmortem_status NOT IN
        # ('reviewed', 'skipped')` sibling clause — that pattern was
        # removed in the Phase 1A cleanup pass because SQL three-valued
        # logic makes `NULL NOT IN (...)` evaluate to NULL (not TRUE),
        # which silently filters out the very NULL rows we want to
        # process. The status_clause enumeration alone is sufficient.
        status_filter: list[str] = []
        status_filter.append("tp.postmortem_status IS NULL")
        if retry_failed or force:
            status_filter.append("tp.postmortem_status = 'llm_unavailable'")
        if force:
            status_filter.append("tp.postmortem_status = 'generated'")
        status_clause = " OR ".join(status_filter)

        query = f"""
            SELECT
                tsb.signal_id,
                tsb.ts_emitted,
                tsb.symbol,
                tsb.venue,
                tsb.direction,
                tsb.nt_entry_price,
                tsb.nt_stop_price,
                tsb.nt_target_price,
                tsb.nt_effective_kelly,
                tsb.nt_brick_size,
                tsb.meta_regime,
                tsb.meta_regime_confidence,
                tsb.sizing_multiplier,
                tsb.entry_strategy,
                tsb.execution_method,
                tsb.final_size_usd,
                tsb.final_size_pct,
                tsb.ts_emitted AS closed_at,
                tp.postmortem_status,
                tp.postmortem_human,
                tp.hypothesis AS existing_hypothesis,
                pr.net_pnl,
                pr.gross_pnl,
                pr.r_multiple,
                pr.hold_duration_sec,
                pr.direction_pnl,
                pr.timing_pnl,
                pr.regime_pnl,
                pr.fees_total,
                pr.funding_pnl,
                pr.slippage_cost,
                pr.exit_reason,
                pr.config_hash
            FROM trade_signals_blended tsb
            LEFT JOIN trade_postmortem tp ON tsb.signal_id = tp.signal_id
            LEFT JOIN pnl_realized pr ON tsb.signal_id = pr.signal_id
            WHERE CAST(tsb.ts_emitted AS DATE) BETWEEN ? AND ?
              AND ({status_clause})
            ORDER BY tsb.ts_emitted
        """

        try:
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                result = conn.execute(query, [start_date, end_date]).fetchall()
                columns = [d[0] for d in conn.description]
        except Exception as e:
            log.error("trade_journal_select_failed", error=str(e))
            return []

        rows = [dict(zip(columns, row)) for row in result]
        self._stats["selected"] += len(rows)
        log.info("trade_journal_selected", count=len(rows), start=str(start_date), end=str(end_date))
        return rows

    def _process_rows(self, rows: list[dict[str, Any]]) -> int:
        """Process each row: invoke skill, update trade_postmortem."""
        if not rows:
            return 0

        if self._skill_invoker is None:
            raise RuntimeError(
                "TradeJournal.skill_invoker is None — cannot generate postmortems. "
                "The noble journal CLI must be invoked from the agent runtime "
                "(which provides its inference router) or skill_invoker must be "
                "passed to the TradeJournal constructor."
            )

        updated = 0
        for row in rows:
            ok = self._process_one(row)
            if ok:
                updated += 1
        return updated

    def _process_one(self, row: dict[str, Any]) -> bool:
        """Process one signal: invoke skill, update row. Returns True on success."""
        signal_id = row["signal_id"]
        payload = self._build_payload(row)

        try:
            result = self._skill_invoker(self.SKILL_MD_PATH, payload)
        except Exception as e:
            log.warning(
                "trade_journal_skill_failed",
                signal_id=signal_id,
                error=str(e),
            )
            self._mark_unavailable(signal_id)
            self._stats["failed"] += 1
            return False

        postmortem_llm = result.get("postmortem_llm")
        if not postmortem_llm:
            log.warning(
                "trade_journal_skill_empty",
                signal_id=signal_id,
            )
            self._mark_unavailable(signal_id)
            self._stats["failed"] += 1
            return False

        hypothesis = result.get("hypothesis")
        prompt_tokens = result.get("prompt_tokens")
        completion_tokens = result.get("completion_tokens")

        self._write_postmortem(
            signal_id=signal_id,
            postmortem_llm=postmortem_llm,
            hypothesis=hypothesis,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            status="generated",
        )
        self._stats["generated"] += 1
        log.info(
            "trade_journal_generated",
            signal_id=signal_id,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        return True

    def _build_payload(self, row: dict[str, Any]) -> dict[str, Any]:
        """Build the skill payload from the signal + PnL row.

        The skill file (skills/trade_journal/SKILL.md) defines what
        fields the skill expects. We pass everything we have; the
        skill picks what it needs.
        """
        payload: dict[str, Any] = {
            "signal_id": row.get("signal_id"),
            "symbol": row.get("symbol"),
            "venue": row.get("venue"),
            "direction": row.get("direction"),
            "ts_emitted": row.get("ts_emitted"),
            # Signal / sizing
            "nt_entry_price": row.get("nt_entry_price"),
            "nt_stop_price": row.get("nt_stop_price"),
            "nt_target_price": row.get("nt_target_price"),
            "nt_effective_kelly": row.get("nt_effective_kelly"),
            "nt_brick_size": row.get("nt_brick_size"),
            "meta_regime": row.get("meta_regime"),
            "meta_regime_confidence": row.get("meta_regime_confidence"),
            "sizing_multiplier": row.get("sizing_multiplier"),
            "entry_strategy": row.get("entry_strategy"),
            "execution_method": row.get("execution_method"),
            "final_size_usd": row.get("final_size_usd"),
            "final_size_pct": row.get("final_size_pct"),
            # PnL attribution
            "net_pnl": row.get("net_pnl"),
            "gross_pnl": row.get("gross_pnl"),
            "r_multiple": row.get("r_multiple"),
            "hold_duration_sec": row.get("hold_duration_sec"),
            "direction_pnl": row.get("direction_pnl"),
            "timing_pnl": row.get("timing_pnl"),
            "regime_pnl": row.get("regime_pnl"),
            "fees_total": row.get("fees_total"),
            "funding_pnl": row.get("funding_pnl"),
            "slippage_cost": row.get("slippage_cost"),
            "exit_reason": row.get("exit_reason"),
            "config_hash": row.get("config_hash"),
            # Existing human note (skill should incorporate if non-NULL)
            "postmortem_human": row.get("postmortem_human"),
            # Existing hypothesis (skill should preserve if non-NULL;
            # only generate a new one if NULL)
            "existing_hypothesis": row.get("existing_hypothesis"),
        }
        return payload

    def _write_postmortem(
        self,
        *,
        signal_id: str,
        postmortem_llm: str,
        hypothesis: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        status: str,
    ) -> None:
        """Upsert trade_postmortem row with the skill result."""
        import duckdb

        now = datetime.now(timezone.utc)

        # hypothesis column: only update if the skill produced a new one
        # AND the row didn't already have one (preserve existing hypothesis)
        if hypothesis is not None:
            hypothesis_clause = "hypothesis = COALESCE(hypothesis, ?),"
            hypothesis_params = [hypothesis]
        else:
            hypothesis_clause = ""
            hypothesis_params = []

        query = f"""
            INSERT INTO trade_postmortem (
                signal_id, hypothesis, postmortem_llm, postmortem_status,
                postmortem_generated_at, prompt_tokens, completion_tokens,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT (signal_id) DO UPDATE SET
                {hypothesis_clause}
                postmortem_llm = excluded.postmortem_llm,
                postmortem_status = excluded.postmortem_status,
                postmortem_generated_at = excluded.postmortem_generated_at,
                prompt_tokens = excluded.prompt_tokens,
                completion_tokens = excluded.completion_tokens,
                updated_at = excluded.updated_at
        """

        params = (
            [signal_id, hypothesis, postmortem_llm, status, now,
             prompt_tokens, completion_tokens, now, now]
            + hypothesis_params
        )

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(query, params)
        except Exception as e:
            log.error(
                "trade_journal_write_failed",
                signal_id=signal_id,
                error=str(e),
            )

    def _mark_unavailable(self, signal_id: str) -> None:
        """Mark a signal's postmortem as failed (retryable)."""
        import duckdb

        now = datetime.now(timezone.utc)
        query = """
            INSERT INTO trade_postmortem (
                signal_id, postmortem_status, created_at, updated_at
            ) VALUES (?, 'llm_unavailable', ?, ?)
            ON CONFLICT (signal_id) DO UPDATE SET
                postmortem_status = 'llm_unavailable',
                updated_at = excluded.updated_at
        """
        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(query, [signal_id, now, now])
        except Exception as e:
            log.error(
                "trade_journal_mark_unavailable_failed",
                signal_id=signal_id,
                error=str(e),
            )
