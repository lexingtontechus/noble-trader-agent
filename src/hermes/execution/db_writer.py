"""
DuckDB writer for orders, order_events, and fills.

All writes are async (via executor) and batched where possible.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path, safe_duckdb_connect
from hermes.execution.orders import Fill, Order, OrderEvent

log = structlog.get_logger(__name__)


class ExecutionWriter:
    """Writes orders, events, and fills to DuckDB."""

    def __init__(self, config: HermesConfig) -> None:
        self._db_path = get_duckdb_path(config)
        self._stats = {"orders_written": 0, "events_written": 0, "fills_written": 0, "errors": 0}

    def write_order(self, order: Order) -> None:
        """Upsert an order to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO orders (
                        order_id, trade_id, signal_id, risk_decision_id, ts_created,
                        symbol, venue, side, order_type, time_in_force,
                        qty_requested, price_limit, leverage,
                        qty_filled, avg_fill_price, status,
                        algo, venue_order_id,
                        total_fees, total_slippage, maker_rebate,
                        config_hash, position_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        order.order_id,
                        order.trade_id,
                        order.signal_id,
                        order.risk_decision_id,
                        order.ts_created,
                        order.symbol,
                        order.venue,
                        order.side.value,
                        order.order_type.value,
                        order.time_in_force.value,
                        order.qty_requested,
                        order.price_limit,
                        order.leverage,
                        order.qty_filled,
                        order.avg_fill_price,
                        order.status.value,
                        order.algo,
                        order.venue_order_id,
                        order.total_fees,
                        order.total_slippage,
                        order.maker_rebate,
                        order.config_hash,
                        order.position_id,
                    ],
                )
            self._stats["orders_written"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            log.error("order_write_failed", order_id=order.order_id, error=str(e))

    def write_event(self, event: OrderEvent) -> None:
        """Write an order event to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO order_events (event_id, order_id, ts, event_type, payload, seq_num)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        event.event_id,
                        event.order_id,
                        event.ts,
                        event.event_type,
                        json.dumps(event.payload, default=str),
                        event.seq_num,
                    ],
                )
            self._stats["events_written"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            log.error("event_write_failed", error=str(e))

    def write_fill(self, fill: Fill) -> None:
        """Write a fill to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO fills (
                        fill_id, order_id, ts, symbol, venue, side,
                        qty, price, fee, fee_currency,
                        is_maker, liquidity, arrival_price, slippage_bps, venue_fill_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        fill.fill_id,
                        fill.order_id,
                        fill.ts,
                        fill.symbol,
                        fill.venue,
                        fill.side.value,
                        fill.qty,
                        fill.price,
                        fill.fee,
                        fill.fee_currency,
                        fill.is_maker,
                        fill.liquidity,
                        fill.arrival_price,
                        fill.slippage_bps,
                        fill.venue_fill_id,
                    ],
                )
            self._stats["fills_written"] += 1
        except Exception as e:
            self._stats["errors"] += 1
            log.error("fill_write_failed", error=str(e))

    def get_stats(self) -> dict[str, int]:
        return self._stats.copy()
