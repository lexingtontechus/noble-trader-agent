"""
Account Snapshot Writer — periodic + on-event snapshots to DuckDB.

Writes portfolio metrics to account_snapshots table at configurable
intervals (default 1m) and on significant events (position open/close,
circuit breaker trip, kill switch activation).

See roadmap §6.2.2.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.portfolio.state import PortfolioMetrics, PortfolioStateService

log = structlog.get_logger(__name__)


class SnapshotWriter:
    """
    Writes account snapshots to DuckDB periodically + on events.

    Usage:
        writer = SnapshotWriter(config, portfolio_state)
        await writer.start()
        # ... later ...
        await writer.stop()
    """

    def __init__(
        self,
        config: HermesConfig,
        portfolio_state: PortfolioStateService,
        snapshot_interval_sec: float = 60.0,
    ) -> None:
        self._db_path = get_duckdb_path(config)
        self._state = portfolio_state
        self._interval = snapshot_interval_sec
        self._running = False
        self._task: asyncio.Task | None = None
        self._stats = {"snapshots_written": 0, "errors": 0}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        log.info("snapshot_writer_started", interval_sec=self._interval)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Final snapshot
        await self.write_snapshot("shutdown")
        log.info("snapshot_writer_stopped", stats=self._stats)

    async def _loop(self) -> None:
        """Background loop: write snapshots at interval."""
        while self._running:
            await asyncio.sleep(self._interval)
            await self.write_snapshot("periodic")

    async def write_snapshot(self, snapshot_type: str = "periodic") -> None:
        """Write a single snapshot to DuckDB."""
        try:
            metrics = self._state.get_metrics()
            snapshot_id = str(uuid4())

            await asyncio.get_event_loop().run_in_executor(
                None, self._write_blocking, snapshot_id, snapshot_type, metrics
            )
            self._stats["snapshots_written"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            log.error("snapshot_write_failed", error=str(e))

    def _write_blocking(
        self,
        snapshot_id: str,
        snapshot_type: str,
        metrics: PortfolioMetrics,
    ) -> None:
        """Synchronous DuckDB write (called from executor)."""
        import duckdb

        with duckdb.connect(str(self._db_path)) as conn:
            conn.execute(
                """
                INSERT INTO account_snapshots (
                    snapshot_id, ts, snapshot_type,
                    equity_total, cash_usd, cash_usdc,
                    margin_used, margin_available,
                    leverage_gross, leverage_net,
                    realized_pnl, unrealized_pnl, funding_pnl, fees_paid,
                    gross_exposure_usd, net_exposure_usd,
                    long_exposure_usd, short_exposure_usd,
                    n_open_positions, n_venues,
                    peak_equity, drawdown_pct, drawdown_usd, time_in_dd_sec,
                    var_1d_99, cvar_1d_99, beta_to_spy,
                    config_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    snapshot_id,
                    metrics.ts,
                    snapshot_type,
                    metrics.equity_total,
                    metrics.cash_usd,
                    metrics.cash_usdc,
                    metrics.margin_used,
                    metrics.margin_available,
                    metrics.leverage_gross,
                    metrics.leverage_net,
                    metrics.realized_pnl,
                    metrics.unrealized_pnl,
                    metrics.funding_pnl,
                    metrics.fees_paid,
                    metrics.gross_exposure_usd,
                    metrics.net_exposure_usd,
                    metrics.long_exposure_usd,
                    metrics.short_exposure_usd,
                    metrics.n_open_positions,
                    metrics.n_venues,
                    metrics.peak_equity,
                    metrics.drawdown_pct,
                    metrics.drawdown_usd,
                    metrics.time_in_dd_sec,
                    metrics.var_1d_99,
                    metrics.cvar_1d_99,
                    metrics.beta_to_spy,
                    metrics.config_hash,
                ],
            )

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
