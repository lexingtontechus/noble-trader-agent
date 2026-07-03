"""
L3 Execution Orchestrator — consumes RiskDecisions, creates orders,
executes via paper or live engine, writes to DuckDB.

Subscribes to risk.decision.{signal_id} Redis channel (from L5),
creates orders via SmartOrderRouter, executes via PaperTradingEngine
(or live venue adapters in production), writes results to DuckDB.

See roadmap §2.4.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.execution.db_writer import ExecutionWriter
from hermes.execution.orders import Fill, Order, OrderEvent, OrderStatus, OrderStateMachine
from hermes.execution.paper_engine import PaperTradingEngine
from hermes.execution.router import SmartOrderRouter
from hermes.execution.slippage import SlippageModeler
from hermes.portfolio.risk_gate import RiskDecision
from hermes.portfolio.state import PortfolioStateService
from hermes.schemas.market import Position, Venue
from hermes.signals.synthesizer import BlendedSignal

log = structlog.get_logger(__name__)


class ExecutionEngine:
    """
    L3 execution orchestrator.

    Consumes RiskDecisions from L5, creates orders, executes them,
    and writes results to DuckDB.

    In paper mode (default): uses PaperTradingEngine for simulated fills.
    In live mode (future): uses venue adapters for real orders.

    Usage:
        engine = ExecutionEngine(config, portfolio_state)
        await engine.start()
        # Subscribes to risk.decision.* on Redis
        # ... orders execute automatically ...
        await engine.stop()
    """

    def __init__(
        self,
        config: HermesConfig,
        portfolio_state: PortfolioStateService,
        paper_mode: bool = True,
    ) -> None:
        self._config = config
        self._state = portfolio_state
        self._paper_mode = paper_mode
        self._db_path = get_duckdb_path(config)

        # Sub-components
        self._slippage = SlippageModeler()
        self._paper_engine = PaperTradingEngine(slippage_modeler=self._slippage)
        self._router = SmartOrderRouter(
            twap_n_bricks=config.execution.get("twap_n_bricks", 3),
            iceberg_child_pct=config.execution.get("iceberg_child_pct", 10),
        )
        self._writer = ExecutionWriter(config)

        # Set callbacks
        self._paper_engine.set_callbacks(
            event_callback=self._on_order_event,
            fill_callback=self._on_fill,
        )

        # Redis
        self._redis = None
        self._redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")

        self._running = False
        self._seq_counters: dict[str, int] = {}  # order_id → next seq_num

        self._stats = {
            "decisions_received": 0,
            "orders_created": 0,
            "orders_filled": 0,
            "orders_rejected": 0,
            "total_fees": 0.0,
            "total_slippage_bps": 0.0,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        if not ("<" in self._redis_url or self._redis_url.startswith("secret:")):
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
                await self._redis.ping()
                log.info("l3_redis_connected")
            except Exception as e:
                log.warning("l3_redis_unavailable", error=str(e))
                self._redis = None

        log.info("execution_engine_started", paper_mode=self._paper_mode)

    async def stop(self) -> None:
        self._running = False
        if self._redis:
            await self._redis.close()
        log.info("execution_engine_stopped", stats=self._stats)

    async def execute_decision(
        self,
        decision: RiskDecision,
        signal: BlendedSignal,
        current_price: float | None = None,
    ) -> list[Order]:
        """
        Execute a risk decision: create orders + submit to engine.

        Args:
            decision: Approved RiskDecision from L5
            signal: BlendedSignal that triggered this decision
            current_price: Current market price (for paper fills)

        Returns:
            List of created Order objects
        """
        self._stats["decisions_received"] += 1

        if not decision.approved:
            self._stats["orders_rejected"] += 1
            log.info(
                "decision_not_approved_skipping",
                signal_id=signal.signal_id,
                reason=decision.reason,
            )
            return []

        # 1. Create orders via smart order router
        orders = self._router.create_orders(decision, signal)
        if not orders:
            log.warning("no_orders_created", signal_id=signal.signal_id)
            return []

        self._stats["orders_created"] += len(orders)

        # 2. Get current price for paper execution
        price = current_price or signal.entry_price_target or signal.nt_entry_price

        # 3. Execute each order
        for order in orders:
            # Write order to DuckDB
            self._writer.write_order(order)

            # Write draft event
            draft_event = OrderEvent(
                order_id=order.order_id,
                event_type="draft",
                payload={"order": order.model_dump(mode="json")},
                seq_num=self._next_seq(order.order_id),
            )
            self._writer.write_event(draft_event)

            # Submit to paper engine
            await self._paper_engine.submit_order(
                order=order,
                current_price=price,
                annualized_vol=0.60,  # TODO: from IndicatorEngine
            )

            # Update order in DuckDB after execution
            self._writer.write_order(order)

            # If filled, register position in portfolio state
            if order.status == OrderStatus.FILLED and order.avg_fill_price:
                await self._register_position(order, signal, decision)

            log.info(
                "order_executed",
                order_id=order.order_id,
                symbol=order.symbol,
                side=order.side.value,
                qty=order.qty_requested,
                status=order.status.value,
                avg_fill=order.avg_fill_price,
                fees=order.total_fees,
                slippage=order.total_slippage,
            )

        return orders

    async def _register_position(
        self,
        order: Order,
        signal: BlendedSignal,
        decision: RiskDecision,
    ) -> None:
        """Register a filled order as a position in portfolio state."""
        position_id = str(uuid4())
        order.position_id = position_id

        direction = "long" if order.side.value == "buy" else "short"
        entry_price = order.avg_fill_price or signal.nt_entry_price
        stop_price = signal.nt_stop_price
        target_price = signal.nt_target_price
        risk_amount = abs(entry_price - stop_price) * order.qty_filled

        position = Position(
            position_id=position_id,
            symbol=order.symbol,
            venue=Venue.HYPERLIQUID if order.venue == "hyperliquid" else Venue.ALPACA,
            direction=direction,
            qty=order.qty_filled,
            entry_price=entry_price,
            stop_price=stop_price,
            target_price=target_price,
            opened_at=datetime.now(timezone.utc),
            risk_amount=risk_amount,
        )

        self._state.add_position(
            position=position,
            signal_id=signal.signal_id,
            strategy_id="hermes_v1",
        )

        # Update order with position_id in DuckDB
        self._writer.write_order(order)

        log.info(
            "position_registered",
            position_id=position_id,
            symbol=order.symbol,
            direction=direction,
            qty=order.qty_filled,
            entry_price=entry_price,
        )

    async def _on_order_event(self, order_id: str, event: OrderEvent) -> None:
        """Callback for order events from paper engine."""
        event.seq_num = self._next_seq(order_id)
        self._writer.write_event(event)

        # Update order in DuckDB on status change
        order = self._paper_engine.get_order(order_id)
        if order:
            self._writer.write_order(order)
            if order.status == OrderStatus.FILLED:
                self._stats["orders_filled"] += 1

    async def _on_fill(self, fill: Fill) -> None:
        """Callback for fills from paper engine."""
        self._writer.write_fill(fill)
        self._stats["total_fees"] += fill.fee
        self._stats["total_slippage_bps"] += fill.slippage_bps

    def _next_seq(self, order_id: str) -> int:
        """Get next sequence number for an order's events."""
        self._seq_counters[order_id] = self._seq_counters.get(order_id, 0) + 1
        return self._seq_counters[order_id]

    def get_paper_engine(self) -> PaperTradingEngine:
        return self._paper_engine

    def get_writer(self) -> ExecutionWriter:
        return self._writer

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["paper_engine"] = self._paper_engine.get_stats()
        stats["db_writer"] = self._writer.get_stats()
        return stats
