"""
Parquet writer — partitioned by venue/symbol/tf/date.

Stores historical bars and ticks in Parquet format for fast offline
analysis via DuckDB's read_parquet() function.

Partition layout:
    data/parquet/
        bars/
            venue=alpaca/
                symbol=AAPL/
                    tf=1m/
                        date=2026-07-02/
                            part-0.parquet
        ticks/
            venue=hyperliquid/
                symbol=BTC/
                    date=2026-07-02/
                        part-0.parquet
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from hermes.schemas.market import Bar, Tick, Venue

log = structlog.get_logger(__name__)


class ParquetWriter:
    """
    Async batched Parquet writer for market data.

    Buffers bars/ticks in memory and flushes to Parquet when:
    - Batch size reached (default 1000 rows)
    - Flush interval elapsed (default 5s)
    - New date partition starts (forces flush to avoid cross-date files)
    """

    def __init__(
        self,
        base_path: str = "./data/parquet",
        batch_size: int = 1000,
        flush_interval_sec: float = 5.0,
    ) -> None:
        self._base_path = Path(base_path)
        self._batch_size = batch_size
        self._flush_interval = flush_interval_sec

        self._bar_buffer: list[dict[str, Any]] = []
        self._tick_buffer: list[dict[str, Any]] = []
        self._running = False
        self._flush_task: asyncio.Task | None = None
        self._stats = {
            "bars_buffered": 0,
            "bars_written": 0,
            "ticks_buffered": 0,
            "ticks_written": 0,
            "files_written": 0,
            "errors": 0,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._base_path.mkdir(parents=True, exist_ok=True)
        self._flush_task = asyncio.create_task(self._flush_loop())
        log.info(
            "parquet_writer_started",
            base_path=str(self._base_path),
            batch_size=self._batch_size,
        )

    async def stop(self) -> None:
        self._running = False
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Final flush
        await self._flush_all()
        log.info("parquet_writer_stopped", stats=self._stats)

    async def write_bar(self, bar: Bar) -> None:
        """Enqueue a bar for writing."""
        self._bar_buffer.append(self._bar_to_dict(bar))
        self._stats["bars_buffered"] += 1
        if len(self._bar_buffer) >= self._batch_size:
            await self._flush_bars()

    async def write_bars(self, bars: Sequence[Bar]) -> None:
        """Enqueue multiple bars."""
        for bar in bars:
            self._bar_buffer.append(self._bar_to_dict(bar))
        self._stats["bars_buffered"] += len(bars)
        if len(self._bar_buffer) >= self._batch_size:
            await self._flush_bars()

    async def write_tick(self, tick: Tick) -> None:
        """Enqueue a tick for writing."""
        self._tick_buffer.append(self._tick_to_dict(tick))
        self._stats["ticks_buffered"] += 1
        if len(self._tick_buffer) >= self._batch_size:
            await self._flush_ticks()

    async def _flush_loop(self) -> None:
        """Background flush loop."""
        while self._running:
            await asyncio.sleep(self._flush_interval)
            await self._flush_all()

    async def _flush_all(self) -> None:
        await self._flush_bars()
        await self._flush_ticks()

    async def _flush_bars(self) -> None:
        if not self._bar_buffer:
            return
        buffer = self._bar_buffer[:]
        self._bar_buffer.clear()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._write_bars_blocking, buffer
            )
            self._stats["bars_written"] += len(buffer)
        except Exception as e:
            self._stats["errors"] += 1
            log.error("parquet_bars_flush_failed", error=str(e), n_bars=len(buffer))

    async def _flush_ticks(self) -> None:
        if not self._tick_buffer:
            return
        buffer = self._tick_buffer[:]
        self._tick_buffer.clear()
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._write_ticks_blocking, buffer
            )
            self._stats["ticks_written"] += len(buffer)
        except Exception as e:
            self._stats["errors"] += 1
            log.error("parquet_ticks_flush_failed", error=str(e), n_ticks=len(buffer))

    def _write_bars_blocking(self, bars: list[dict[str, Any]]) -> None:
        """Write bars to Parquet, partitioned by venue/symbol/tf/date."""
        import pandas as pd

        df = pd.DataFrame(bars)
        # Group by partition keys
        for (venue, symbol, tf, date_str), group in df.groupby(
            ["venue", "symbol", "timeframe", "date_str"]
        ):
            partition_path = (
                self._base_path / "bars"
                / f"venue={venue}"
                / f"symbol={symbol}"
                / f"tf={tf}"
                / f"date={date_str}"
            )
            partition_path.mkdir(parents=True, exist_ok=True)

            # Drop partition columns before writing
            cols_to_drop = ["venue", "symbol", "timeframe", "date_str"]
            group = group.drop(columns=[c for c in cols_to_drop if c in group.columns])

            file_path = partition_path / f"part-{datetime.now().strftime('%H%M%S%f')}.parquet"
            group.to_parquet(file_path, engine="pyarrow", compression="snappy")
            self._stats["files_written"] += 1
            log.debug("parquet_bars_written", path=str(file_path), n_rows=len(group))

    def _write_ticks_blocking(self, ticks: list[dict[str, Any]]) -> None:
        """Write ticks to Parquet, partitioned by venue/symbol/date."""
        import pandas as pd

        df = pd.DataFrame(ticks)
        for (venue, symbol, date_str), group in df.groupby(
            ["venue", "symbol", "date_str"]
        ):
            partition_path = (
                self._base_path / "ticks"
                / f"venue={venue}"
                / f"symbol={symbol}"
                / f"date={date_str}"
            )
            partition_path.mkdir(parents=True, exist_ok=True)

            cols_to_drop = ["venue", "symbol", "date_str"]
            group = group.drop(columns=[c for c in cols_to_drop if c in group.columns])

            file_path = partition_path / f"part-{datetime.now().strftime('%H%M%S%f')}.parquet"
            group.to_parquet(file_path, engine="pyarrow", compression="snappy")
            self._stats["files_written"] += 1
            log.debug("parquet_ticks_written", path=str(file_path), n_rows=len(group))

    @staticmethod
    def _bar_to_dict(bar: Bar) -> dict[str, Any]:
        return {
            "ts_open": bar.ts_open,
            "ts_close": bar.ts_close,
            "venue": bar.venue.value,
            "symbol": bar.symbol,
            "timeframe": bar.timeframe,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
            "vwap": bar.vwap,
            "n_trades": bar.n_trades,
            "closed": bar.closed,
            "date_str": bar.ts_open.strftime("%Y-%m-%d"),
        }

    @staticmethod
    def _tick_to_dict(tick: Tick) -> dict[str, Any]:
        return {
            "ts": tick.ts,
            "venue": tick.venue.value,
            "symbol": tick.symbol,
            "price": tick.price,
            "size": tick.size,
            "side": tick.side.value if tick.side else None,
            "trade_id": tick.trade_id,
            "date_str": tick.ts.strftime("%Y-%m-%d"),
        }

    def get_stats(self) -> dict[str, Any]:
        return self._stats.copy()


def create_duckdb_parquet_view(duckdb_path: Path | str, parquet_path: Path | str) -> None:
    """
    Create a DuckDB view that reads all Parquet bar files via read_parquet().

    This lets Hermes query historical bars in SQL without importing them:
        SELECT * FROM market.bars WHERE symbol='BTC' AND ts_open >= '2026-01-01'
    """
    import duckdb

    bars_glob = str(Path(parquet_path) / "bars" / "**" / "*.parquet")
    ticks_glob = str(Path(parquet_path) / "ticks" / "**" / "*.parquet")

    with duckdb.connect(str(duckdb_path)) as conn:
        # Create views if the directories exist
        bars_dir = Path(parquet_path) / "bars"
        ticks_dir = Path(parquet_path) / "ticks"

        if bars_dir.exists():
            conn.execute(f"""
                CREATE OR REPLACE VIEW market_bars AS
                SELECT
                    venue,
                    symbol,
                    timeframe,
                    ts_open,
                    ts_close,
                    open,
                    high,
                    low,
                    close,
                    volume,
                    vwap,
                    n_trades,
                    closed
                FROM read_parquet('{bars_glob}', hive_partitioning=true)
            """)
            log.info("duckdb_view_created", view="market_bars", source=bars_glob)

        if ticks_dir.exists():
            conn.execute(f"""
                CREATE OR REPLACE VIEW market_ticks AS
                SELECT
                    venue,
                    symbol,
                    ts,
                    price,
                    size,
                    side,
                    trade_id
                FROM read_parquet('{ticks_glob}', hive_partitioning=true)
            """)
            log.info("duckdb_view_created", view="market_ticks", source=ticks_glob)
