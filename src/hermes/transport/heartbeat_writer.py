"""
DuckDB writer for heartbeats.

Single-writer pattern: all writes go through this class, batched for performance.
Other processes connect read-only for analysis.

See roadmap §6.2.6 for signal_heartbeats schema.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import duckdb
import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


class HeartbeatWriter:
    """
    Thread-safe batched writer for signal_heartbeats table.

    Usage:
        writer = HeartbeatWriter(config)
        writer.start()
        writer.enqueue(heartbeat_row_dict)
        # ... later ...
        writer.stop()  # flushes pending writes
    """

    INSERT_COLUMNS = [
        "heartbeat_id", "ts_received", "ts_upstream", "lag_ms", "dedup_hash",
        "symbol", "strategy_id", "type", "signal", "entry_price", "stop_loss",
        "take_profit", "aggression", "brick_size", "sl_bricks", "tp_bricks",
        "regime", "regime_conf", "regime_shift", "prev_regime", "shift_at", "shifts_24h",
        "ev", "ev_per_dollar", "p_win", "p_regime", "p_imbalance", "p_markov", "ev_scale",
        "p_timesfm", "timesfm_horizon", "markov_current_state",
        "tail_risk_score", "tail_risk_action", "kelly_f", "effective_kelly",
        "raw_payload", "accepted", "reject_reason", "reprocessed_at",
    ]

    INSERT_SQL = (
        f"INSERT INTO signal_heartbeats ({', '.join(INSERT_COLUMNS)}) "
        f"VALUES ({', '.join(['?'] * len(INSERT_COLUMNS))})"
    )

    def __init__(
        self,
        config: HermesConfig,
        batch_size: int = 100,
        flush_interval_sec: float = 1.0,
    ) -> None:
        self._db_path = get_duckdb_path(config)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_sec

        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None
        self._running = False
        self._lock = threading.Lock()
        self._stats = {
            "enqueued": 0,
            "written": 0,
            "errors": 0,
            "last_flush_at": None,
        }

    async def start(self) -> None:
        """Start the background writer task."""
        if self._running:
            log.warning("writer_already_running")
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        log.info(
            "heartbeat_writer_started",
            db_path=str(self._db_path),
            batch_size=self._batch_size,
            flush_interval=self._flush_interval,
        )

    async def stop(self) -> None:
        """Stop the writer and flush pending writes."""
        if not self._running:
            return
        self._running = False
        # Signal worker to flush and exit
        await self._queue.put(None)  # sentinel
        if self._worker_task:
            await self._worker_task
        log.info("heartbeat_writer_stopped", stats=self._stats)

    async def enqueue(self, row: dict[str, Any]) -> None:
        """Enqueue a heartbeat row for writing."""
        if not self._running:
            log.warning("writer_not_running_enqueueing_anyway")
        await self._queue.put(row)
        self._stats["enqueued"] += 1

    def enqueue_sync(self, row: dict[str, Any]) -> None:
        """Synchronous enqueue (for non-async callers)."""
        self._queue.put_nowait(row)
        self._stats["enqueued"] += 1

    async def _worker_loop(self) -> None:
        """Background loop: batch writes on size or time threshold."""
        batch: list[dict[str, Any]] = []
        last_flush = asyncio.get_event_loop().time()

        while self._running or not self._queue.empty():
            try:
                timeout = max(
                    0.01,
                    self._flush_interval - (asyncio.get_event_loop().time() - last_flush),
                )
                row = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                row = None  # time-based flush

            if row is not None:
                batch.append(row)

            should_flush = (
                len(batch) >= self._batch_size
                or (row is None and batch)  # timeout with pending
                or (not self._running and batch)  # shutting down with pending
            )

            if should_flush:
                await self._flush(batch)
                batch = []
                last_flush = asyncio.get_event_loop().time()

        # Final flush
        if batch:
            await self._flush(batch)

    async def _flush(self, batch: Sequence[dict[str, Any]]) -> None:
        """Write a batch of rows to DuckDB."""
        if not batch:
            return
        try:
            # DuckDB operations are blocking — run in thread executor
            await asyncio.get_event_loop().run_in_executor(
                None, self._write_batch_blocking, list(batch)
            )
            self._stats["written"] += len(batch)
            self._stats["last_flush_at"] = datetime.now(timezone.utc).isoformat()
            log.debug("flush_complete", n_rows=len(batch), total_written=self._stats["written"])
        except Exception as e:
            self._stats["errors"] += 1
            log.error("flush_failed", error=str(e), n_rows=len(batch))
            # Don't raise — we want the writer to keep going

    def _write_batch_blocking(self, batch: list[dict[str, Any]]) -> None:
        """Synchronous batch insert (called from executor thread)."""
        with self._lock:
            with duckdb.connect(str(self._db_path)) as conn:
                # Build parameter tuples in schema order
                rows = [self._row_to_tuple(row) for row in batch]
                conn.executemany(self.INSERT_SQL, rows)

    @staticmethod
    def _row_to_tuple(row: dict[str, Any]) -> tuple:
        """Convert dict row to tuple in INSERT column order."""
        return (
            row["heartbeat_id"],
            row["ts_received"],
            row["ts_upstream"],
            row["lag_ms"],
            row["dedup_hash"],
            row["symbol"],
            row["strategy_id"],
            row["type"],
            row["signal"],
            row["entry_price"],
            row["stop_loss"],
            row["take_profit"],
            row["aggression"],
            row["brick_size"],
            row["sl_bricks"],
            row["tp_bricks"],
            row["regime"],
            row["regime_conf"],
            row["regime_shift"],
            row["prev_regime"],
            row["shift_at"],
            row["shifts_24h"],
            row["ev"],
            row["ev_per_dollar"],
            row["p_win"],
            row["p_regime"],
            row["p_imbalance"],
            row["p_markov"],
            row["ev_scale"],
            row["p_timesfm"],
            row["timesfm_horizon"],
            row["markov_current_state"],
            row["tail_risk_score"],
            row["tail_risk_action"],
            row["kelly_f"],
            row["effective_kelly"],
            row["raw_payload"],
            row["accepted"],
            row["reject_reason"],
            row.get("reprocessed_at"),
        )

    def get_stats(self) -> dict[str, Any]:
        """Return current writer statistics."""
        return self._stats.copy()

    def write_quarantine(
        self,
        raw_payload: str,
        parse_error: str,
        schema_violations: list[str] | None = None,
    ) -> str:
        """Write a malformed heartbeat to the quarantine table (sync)."""
        quarantine_id = str(uuid4())
        with self._lock:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO signal_heartbeats_quarantine
                        (quarantine_id, ts_received, raw_payload, parse_error, schema_violations)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        quarantine_id,
                        datetime.now(timezone.utc),
                        raw_payload,
                        parse_error,
                        schema_violations or [],
                    ],
                )
        log.warning("heartbeat_quarantined", quarantine_id=quarantine_id, error=parse_error)
        return quarantine_id


def read_heartbeats(
    db_path: Path | str,
    symbol: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """
    Read heartbeats from DuckDB (read-only, for analysis).

    Args:
        db_path: Path to DuckDB file
        symbol: Filter by symbol (None = all)
        since: Filter by ts_received >= since (None = no filter)
        limit: Max rows to return
    """
    query = "SELECT * FROM signal_heartbeats WHERE 1=1"
    params: list[Any] = []

    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)
    if since:
        query += " AND ts_received >= ?"
        params.append(since)

    query += " ORDER BY ts_received DESC LIMIT ?"
    params.append(limit)

    with duckdb.connect(str(db_path), read_only=True) as conn:
        result = conn.execute(query, params).fetchdf()
        return result.to_dict("records") if not result.empty else []
