"""
Hypothesis Tracker + Decision Journal + Self-Learning Loop.

Hypothesis lifecycle: proposed → backtested → shadow → live / rejected / retired

Decision journal: every closed trade gets a postmortem with:
- entry_thesis (pre-trade reasoning)
- postmortem (post-trade analysis)
- lessons (list of takeaways)
- hypothesis_ids (link to hypotheses that informed this trade)

Self-learning loop (runs on schedule):
1. Observe — pull all signals, fills, PnL from DuckDB
2. Attribute — decompose PnL by strategy, regime, asset
3. Hypothesize — generate hypotheses for improvement
4. Backtest — run hypothesis through simulation engine
5. Validate — 6 rigor checks
6. Shadow — paper-trade for N days
7. Promote — auto or human approval

See roadmap §7 + §2.9.7.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


class Hypothesis(BaseModel):
    """A Hermes hypothesis about how to improve trading."""

    hypothesis_id: str = Field(default_factory=lambda: str(uuid4()))
    ts_created: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    hypothesis: str
    rationale: str = ""
    proposed_change: dict = Field(default_factory=dict)
    backtest_result: dict | None = None
    status: str = "proposed"  # proposed | backtested | shadow | live | rejected | retired
    confidence: float = 0.0
    promoted_at: datetime | None = None


class TradeJournalEntry(BaseModel):
    """A trade journal entry with narrative + postmortem."""

    journal_id: str = Field(default_factory=lambda: str(uuid4()))
    trade_id: str
    symbol: str
    venue: str
    strategy_id: str = "hermes_v1"
    direction: str
    regime_tag: str | None = None

    # Narrative
    entry_thesis: str = ""
    entry_conviction: float = 0.0
    entry_edge_estimate: float = 0.0
    entry_atr: float | None = None
    entry_stop_distance: float | None = None
    entry_target: float | None = None

    # Outcome
    exit_reason: str = ""
    exit_pnl: float = 0.0
    exit_r_multiple: float = 0.0
    hold_duration_sec: int = 0
    max_favorable_exc: float | None = None  # MAE
    max_adverse_exc: float | None = None    # MFE

    # Learning
    postmortem: str = ""
    lessons: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    opened_at: datetime
    closed_at: datetime | None = None
    created_by: str = "hermes"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HypothesisTracker:
    """
    Manages the lifecycle of Hermes's trading improvement hypotheses.

    Usage:
        tracker = HypothesisTracker(config)
        hyp = tracker.propose(
            hypothesis="Kelly weight too high in choppy_range for BTC",
            rationale="BTC win rate in choppy_range is 40% but we're sizing at 0.8x",
            proposed_change={"sizing_multiplier.choppy_range": 0.5},
        )
        tracker.backtest(hyp.hypothesis_id, backtest_result={"sharpe": 1.2})
        tracker.promote_to_shadow(hyp.hypothesis_id)
        tracker.promote_to_live(hyp.hypothesis_id)
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._config_hash = get_config_hash(config)
        self._stats = {"proposed": 0, "backtested": 0, "shadow": 0, "live": 0, "rejected": 0}

    def propose(
        self,
        hypothesis: str,
        rationale: str = "",
        proposed_change: dict | None = None,
    ) -> Hypothesis:
        """Propose a new hypothesis."""
        hyp = Hypothesis(
            hypothesis=hypothesis,
            rationale=rationale,
            proposed_change=proposed_change or {},
        )
        self._write_hypothesis(hyp)
        self._stats["proposed"] += 1
        log.info("hypothesis_proposed", hypothesis_id=hyp.hypothesis_id, hypothesis=hypothesis[:80])
        return hyp

    def backtest(self, hypothesis_id: str, backtest_result: dict) -> None:
        """Update hypothesis with backtest result."""
        hyp = self._load_hypothesis(hypothesis_id)
        if not hyp:
            return
        hyp.backtest_result = backtest_result
        hyp.status = "backtested"
        hyp.confidence = backtest_result.get("confidence", 0.5)
        self._update_hypothesis(hyp)
        self._stats["backtested"] += 1
        log.info("hypothesis_backtested", hypothesis_id=hypothesis_id, status=hyp.status)

    def promote_to_shadow(self, hypothesis_id: str) -> None:
        """Promote hypothesis to shadow testing."""
        hyp = self._load_hypothesis(hypothesis_id)
        if not hyp:
            return
        hyp.status = "shadow"
        self._update_hypothesis(hyp)
        self._stats["shadow"] += 1
        log.info("hypothesis_promoted_to_shadow", hypothesis_id=hypothesis_id)

    def promote_to_live(self, hypothesis_id: str) -> None:
        """Promote hypothesis to live (after shadow passes)."""
        hyp = self._load_hypothesis(hypothesis_id)
        if not hyp:
            return
        hyp.status = "live"
        hyp.promoted_at = datetime.now(timezone.utc)
        self._update_hypothesis(hyp)
        self._stats["live"] += 1
        log.info("hypothesis_promoted_to_live", hypothesis_id=hypothesis_id)

    def reject(self, hypothesis_id: str, reason: str = "") -> None:
        """Reject a hypothesis."""
        hyp = self._load_hypothesis(hypothesis_id)
        if not hyp:
            return
        hyp.status = "rejected"
        hyp.rationale = f"REJECTED: {reason}" if reason else "REJECTED"
        self._update_hypothesis(hyp)
        self._stats["rejected"] += 1
        log.info("hypothesis_rejected", hypothesis_id=hypothesis_id, reason=reason)

    def get_hypotheses(self, status: str | None = None) -> list[Hypothesis]:
        """Load hypotheses from DuckDB, optionally filtered by status."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                if status:
                    result = conn.execute(
                        "SELECT * FROM hermes_hypotheses WHERE status = ? ORDER BY ts_created DESC",
                        [status],
                    ).fetchdf()
                else:
                    result = conn.execute(
                        "SELECT * FROM hermes_hypotheses ORDER BY ts_created DESC"
                    ).fetchdf()

                if result.empty:
                    return []

                hypotheses = []
                for _, row in result.iterrows():
                    hypotheses.append(Hypothesis(
                        hypothesis_id=row["hypothesis_id"],
                        ts_created=row["ts_created"],
                        hypothesis=row["hypothesis"],
                        rationale=row.get("rationale", ""),
                        proposed_change=json.loads(row["proposed_change"]) if row.get("proposed_change") else {},
                        backtest_result=json.loads(row["backtest_result"]) if row.get("backtest_result") else None,
                        status=row["status"],
                        confidence=row.get("confidence", 0),
                        promoted_at=row.get("promoted_at"),
                    ))
                return hypotheses
        except Exception as e:
            log.warning("get_hypotheses_failed", error=str(e))
            return []

    def _write_hypothesis(self, hyp: Hypothesis) -> None:
        """Write a new hypothesis to DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO hermes_hypotheses (
                        hypothesis_id, ts_created, hypothesis, rationale,
                        proposed_change, backtest_result, status, confidence, promoted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        hyp.hypothesis_id, hyp.ts_created, hyp.hypothesis, hyp.rationale,
                        json.dumps(hyp.proposed_change, default=str),
                        json.dumps(hyp.backtest_result, default=str) if hyp.backtest_result else None,
                        hyp.status, hyp.confidence, hyp.promoted_at,
                    ],
                )
        except Exception as e:
            log.error("hypothesis_write_failed", error=str(e))

    def _update_hypothesis(self, hyp: Hypothesis) -> None:
        """Update an existing hypothesis in DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    UPDATE hermes_hypotheses SET
                        rationale = ?,
                        proposed_change = ?,
                        backtest_result = ?,
                        status = ?,
                        confidence = ?,
                        promoted_at = ?
                    WHERE hypothesis_id = ?
                    """,
                    [
                        hyp.rationale,
                        json.dumps(hyp.proposed_change, default=str),
                        json.dumps(hyp.backtest_result, default=str) if hyp.backtest_result else None,
                        hyp.status, hyp.confidence, hyp.promoted_at,
                        hyp.hypothesis_id,
                    ],
                )
        except Exception as e:
            log.error("hypothesis_update_failed", error=str(e))

    def _load_hypothesis(self, hypothesis_id: str) -> Hypothesis | None:
        """Load a single hypothesis from DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                result = conn.execute(
                    "SELECT * FROM hermes_hypotheses WHERE hypothesis_id = ?",
                    [hypothesis_id],
                ).fetchdf()
                if result.empty:
                    return None
                row = result.iloc[0]
                return Hypothesis(
                    hypothesis_id=row["hypothesis_id"],
                    ts_created=row["ts_created"],
                    hypothesis=row["hypothesis"],
                    rationale=row.get("rationale", ""),
                    proposed_change=json.loads(row["proposed_change"]) if row.get("proposed_change") else {},
                    backtest_result=json.loads(row["backtest_result"]) if row.get("backtest_result") else None,
                    status=row["status"],
                    confidence=row.get("confidence", 0),
                    promoted_at=row.get("promoted_at"),
                )
        except Exception:
            return None

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()


class DecisionJournalWriter:
    """
    Writes trade journal entries with postmortems for closed trades.

    Every closed trade gets:
    - entry_thesis (what Hermes thought before the trade)
    - postmortem (what happened and why)
    - lessons (list of actionable takeaways)
    - hypothesis_ids (which hypotheses informed this trade)
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._stats = {"entries_written": 0}

    def write_postmortem(
        self,
        trade_id: str,
        symbol: str,
        venue: str,
        direction: str,
        entry_thesis: str,
        exit_reason: str,
        exit_pnl: float,
        exit_r_multiple: float,
        hold_duration_sec: int,
        regime_tag: str | None = None,
        entry_conviction: float = 0.0,
        entry_edge_estimate: float = 0.0,
        max_favorable_exc: float | None = None,
        max_adverse_exc: float | None = None,
        postmortem: str = "",
        lessons: list[str] | None = None,
        hypothesis_ids: list[str] | None = None,
        tags: list[str] | None = None,
        opened_at: datetime | None = None,
        closed_at: datetime | None = None,
    ) -> TradeJournalEntry:
        """Write a trade journal postmortem."""
        entry = TradeJournalEntry(
            trade_id=trade_id,
            symbol=symbol,
            venue=venue,
            direction=direction,
            regime_tag=regime_tag,
            entry_thesis=entry_thesis,
            entry_conviction=entry_conviction,
            entry_edge_estimate=entry_edge_estimate,
            exit_reason=exit_reason,
            exit_pnl=exit_pnl,
            exit_r_multiple=exit_r_multiple,
            hold_duration_sec=hold_duration_sec,
            max_favorable_exc=max_favorable_exc,
            max_adverse_exc=max_adverse_exc,
            postmortem=postmortem,
            lessons=lessons or [],
            hypothesis_ids=hypothesis_ids or [],
            tags=tags or [],
            opened_at=opened_at or datetime.now(timezone.utc),
            closed_at=closed_at or datetime.now(timezone.utc),
        )

        self._write_to_duckdb(entry)
        self._stats["entries_written"] += 1

        log.info(
            "postmortem_written",
            trade_id=trade_id,
            symbol=symbol,
            exit_reason=exit_reason,
            r_multiple=exit_r_multiple,
            pnl=exit_pnl,
        )

        return entry

    def _write_to_duckdb(self, entry: TradeJournalEntry) -> None:
        """Write journal entry to DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO trade_journal (
                        journal_id, trade_id, symbol, venue, strategy_id,
                        direction, regime_tag,
                        entry_thesis, entry_conviction, entry_edge_estimate,
                        entry_atr, entry_stop_distance, entry_target,
                        exit_reason, exit_pnl, exit_r_multiple,
                        hold_duration_sec, max_favorable_exc, max_adverse_exc,
                        postmortem, lessons, hypothesis_ids, tags,
                        opened_at, closed_at, created_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        entry.journal_id, entry.trade_id, entry.symbol, entry.venue,
                        entry.strategy_id, entry.direction, entry.regime_tag,
                        entry.entry_thesis, entry.entry_conviction, entry.entry_edge_estimate,
                        entry.entry_atr, entry.entry_stop_distance, entry.entry_target,
                        entry.exit_reason, entry.exit_pnl, entry.exit_r_multiple,
                        entry.hold_duration_sec, entry.max_favorable_exc, entry.max_adverse_exc,
                        entry.postmortem, entry.lessons, entry.hypothesis_ids, entry.tags,
                        entry.opened_at, entry.closed_at, entry.created_by, entry.created_at, entry.updated_at,
                    ],
                )
        except Exception as e:
            log.error("journal_write_failed", error=str(e))

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()


class SelfLearningLoop:
    """
    Hermes's self-learning loop.

    Runs on schedule (daily EOD, weekly, monthly):
    1. Observe — pull PnL + signals from DuckDB
    2. Attribute — decompose by regime, symbol, strategy
    3. Hypothesize — generate improvement hypotheses
    4. Backtest — run through simulation engine
    5. Validate — 6 rigor checks
    6. Shadow — paper-trade for N days
    7. Promote — auto or human approval

    Usage:
        loop = SelfLearningLoop(config)
        await loop.run_eod_analysis()
    """

    def __init__(self, config: HermesConfig) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._hypothesis_tracker = HypothesisTracker(config)
        self._journal_writer = DecisionJournalWriter(config)
        self._stats = {
            "eod_runs": 0,
            "hypotheses_generated": 0,
            "hypotheses_promoted": 0,
            "postmortems_written": 0,
        }

    async def run_eod_analysis(self) -> dict[str, Any]:
        """
        Run end-of-day analysis.

        Returns summary of what was done.
        """
        self._stats["eod_runs"] += 1
        log.info("eod_analysis_starting")

        summary: dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trades_analyzed": 0,
            "hypotheses_generated": 0,
            "postmortems_written": 0,
            "regime_performance": {},
        }

        # 1. Load today's closed trades from DuckDB
        trades = self._load_today_trades()
        summary["trades_analyzed"] = len(trades)

        if not trades:
            log.info("eod_analysis_no_trades")
            return summary

        # 2. Attribute PnL by regime
        regime_pnls: dict[str, list[float]] = defaultdict(list)
        for trade in trades:
            regime = trade.get("regime_at_close", "unknown")
            regime_pnls[regime].append(trade.get("net_pnl", 0))

        for regime, pnls in regime_pnls.items():
            summary["regime_performance"][regime] = {
                "n_trades": len(pnls),
                "total_pnl": sum(pnls),
                "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
                "win_rate": sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0,
            }

        # 3. Write postmortems for trades without them
        for trade in trades:
            postmortem = self._generate_postmortem(trade, summary["regime_performance"])
            self._journal_writer.write_postmortem(
                trade_id=trade.get("trade_id", ""),
                symbol=trade.get("symbol", ""),
                venue=trade.get("venue", ""),
                direction="long",  # simplified
                entry_thesis=f"Signal: {trade.get('regime_at_close', 'unknown')} regime",
                exit_reason=trade.get("exit_reason", "unknown"),
                exit_pnl=trade.get("net_pnl", 0),
                exit_r_multiple=trade.get("r_multiple", 0),
                hold_duration_sec=trade.get("hold_duration_sec", 0),
                regime_tag=trade.get("regime_at_close"),
                postmortem=postmortem,
                lessons=self._extract_lessons(trade, summary["regime_performance"]),
                tags=["eod_auto"],
            )
            self._stats["postmortems_written"] += 1
            summary["postmortems_written"] += 1

        # 4. Generate hypotheses
        hypotheses = self._generate_hypotheses(summary["regime_performance"])
        for hyp in hypotheses:
            self._hypothesis_tracker.propose(
                hypothesis=hyp["hypothesis"],
                rationale=hyp["rationale"],
                proposed_change=hyp["proposed_change"],
            )
            self._stats["hypotheses_generated"] += 1
            summary["hypotheses_generated"] += 1

        log.info("eod_analysis_complete", **summary)
        return summary

    def _load_today_trades(self) -> list[dict]:
        """Load today's closed trades from DuckDB."""
        import duckdb

        try:
            today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            with duckdb.connect(str(self._db_path), read_only=True) as conn:
                result = conn.execute(
                    """
                    SELECT trade_id, symbol, venue, regime_at_close,
                           net_pnl, r_multiple, hold_duration_sec,
                           gross_pnl, fees_total, funding_pnl, slippage_cost,
                           direction_pnl, timing_pnl, regime_pnl
                    FROM pnl_realized
                    WHERE ts >= ?
                    ORDER BY ts ASC
                    """,
                    [today],
                ).fetchdf()
                if result.empty:
                    return []
                return result.to_dict("records")
        except Exception:
            return []

    @staticmethod
    def _generate_postmortem(trade: dict, regime_perf: dict) -> str:
        """Generate an automated postmortem for a trade."""
        pnl = trade.get("net_pnl", 0)
        r = trade.get("r_multiple", 0)
        regime = trade.get("regime_at_close", "unknown")
        timing_pnl = trade.get("timing_pnl", 0)
        direction_pnl = trade.get("direction_pnl", 0)

        if pnl > 0:
            outcome = "PROFITABLE"
        else:
            outcome = "UNPROFITABLE"

        postmortem = (
            f"Trade {trade.get('trade_id', '?')[:8]}: {outcome}. "
            f"Net PnL: ${pnl:.2f}, R-multiple: {r:.2f}. "
            f"Regime at close: {regime}. "
            f"Direction PnL: ${direction_pnl:.2f}, Timing PnL: ${timing_pnl:.2f}. "
        )

        if regime in regime_perf:
            rp = regime_perf[regime]
            postmortem += (
                f"Regime '{regime}' had {rp['n_trades']} trades today "
                f"with {rp['win_rate']:.0%} win rate and avg PnL ${rp['avg_pnl']:.2f}. "
            )

        if timing_pnl > 0:
            postmortem += "Entry timing added positive alpha. "
        elif timing_pnl < 0:
            postmortem += "Entry timing was negative — consider waiting for brick confirmation. "

        return postmortem

    @staticmethod
    def _extract_lessons(trade: dict, regime_perf: dict) -> list[str]:
        """Extract actionable lessons from a trade."""
        lessons = []
        pnl = trade.get("net_pnl", 0)
        r = trade.get("r_multiple", 0)
        timing_pnl = trade.get("timing_pnl", 0)
        regime = trade.get("regime_at_close", "unknown")

        if pnl < 0 and timing_pnl < 0:
            lessons.append("Entry timing was negative — review entry strategy for this regime")

        if r < -1:
            lessons.append(f"Large loss ({r:.1f}R) in {regime} — consider reducing size in this regime")

        if pnl > 0 and timing_pnl > 0:
            lessons.append(f"Good entry timing in {regime} — current strategy works well here")

        if regime in regime_perf:
            rp = regime_perf[regime]
            if rp["win_rate"] < 0.4:
                lessons.append(f"Low win rate ({rp['win_rate']:.0%}) in {regime} — consider skipping signals in this regime")

        return lessons

    @staticmethod
    def _generate_hypotheses(regime_perf: dict) -> list[dict]:
        """Generate improvement hypotheses from regime performance."""
        hypotheses = []

        for regime, stats in regime_perf.items():
            # Hypothesis: reduce size in low win-rate regimes
            if stats["win_rate"] < 0.4 and stats["n_trades"] >= 3:
                hypotheses.append({
                    "hypothesis": f"Reduce sizing in {regime} (win rate {stats['win_rate']:.0%})",
                    "rationale": f"{regime} has {stats['win_rate']:.0%} win rate over {stats['n_trades']} trades with avg PnL ${stats['avg_pnl']:.2f}",
                    "proposed_change": {f"sizing_multiplier.{regime}": 0.3},
                })

            # Hypothesis: increase size in high win-rate regimes
            if stats["win_rate"] > 0.65 and stats["n_trades"] >= 3 and stats["avg_pnl"] > 0:
                hypotheses.append({
                    "hypothesis": f"Increase sizing in {regime} (win rate {stats['win_rate']:.0%})",
                    "rationale": f"{regime} has {stats['win_rate']:.0%} win rate with positive avg PnL ${stats['avg_pnl']:.2f}",
                    "proposed_change": {f"sizing_multiplier.{regime}": 1.2},
                })

        return hypotheses

    def get_hypothesis_tracker(self) -> HypothesisTracker:
        return self._hypothesis_tracker

    def get_journal_writer(self) -> DecisionJournalWriter:
        return self._journal_writer

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["hypothesis_tracker"] = self._hypothesis_tracker.get_stats()
        stats["journal_writer"] = self._journal_writer.get_stats()
        return stats
