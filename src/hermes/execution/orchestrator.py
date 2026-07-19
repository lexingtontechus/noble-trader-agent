"""
L3 Execution Orchestrator — consumes RiskDecisions, creates orders,
executes via paper or live engine, writes to DuckDB.

Subscribes to risk.decision.{signal_id} Redis channel (from L5),
creates orders via SmartOrderRouter, executes via PaperTradingEngine
(or live venue adapters in production), writes results to DuckDB.

Wired components (on trade entry):
- DecisionBranchTracker: records which AgentAction was taken at entry
- HermesDecisionTree: evaluates existing positions on each new signal

Wired components (on position close):
- PnLService: records realized PnL with attribution
- DecisionJournalWriter: writes postmortem with lessons
- DecisionBranchTracker: records exit action + computes branch stats

See roadmap §2.4.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

from hermes.agent.attribution import DecisionBranchTracker
from hermes.agent.decision_tree import AgentAction, HermesDecisionTree
from hermes.agent.learning import DecisionJournalWriter
from hermes.analytics.pnl_service import PnLService
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
    and writes results to DuckDB. Also:
    - Tracks decision branches (entry + exit) via DecisionBranchTracker
    - Evaluates existing positions via HermesDecisionTree on each signal
    - Records realized PnL via PnLService on position close
    - Writes postmortems via DecisionJournalWriter on position close

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
        cb_manager=None,  # CircuitBreakerManager (optional, for consecutive loss tracking)
    ) -> None:
        self._config = config
        self._state = portfolio_state
        self._paper_mode = paper_mode
        self._db_path = get_duckdb_path(config)
        self._cb_manager = cb_manager  # optional, wired from PortfolioRiskEngine

        # Sub-components
        self._slippage = SlippageModeler()
        self._paper_engine = PaperTradingEngine(slippage_modeler=self._slippage)
        self._router = SmartOrderRouter(
            twap_n_bricks=config.execution.get("twap_n_bricks", 3),
            iceberg_child_pct=config.execution.get("iceberg_child_pct", 10),
        )
        self._writer = ExecutionWriter(config)

        # Wired components (attribution + learning)
        self._branch_tracker = DecisionBranchTracker(config)
        self._decision_tree = HermesDecisionTree(
            stop_loss_pct=config.position_management.get("trailing", {}).get("stop_loss_pct", -0.01) if hasattr(config, 'position_management') else -0.01,
        )
        self._pnl_service = PnLService(config, portfolio_state)
        self._journal_writer = DecisionJournalWriter(config)

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

        # Track signal → order → position mapping for attribution
        self._signal_map: dict[str, BlendedSignal] = {}  # signal_id → signal
        self._position_signals: dict[str, str] = {}  # position_id → signal_id

        self._stats = {
            "decisions_received": 0,
            "decisions_duplicated": 0,
            "orders_created": 0,
            "orders_filled": 0,
            "orders_rejected": 0,
            "positions_closed": 0,
            "total_fees": 0.0,
            "total_slippage_bps": 0.0,
            "branch_attributions": 0,
            "postmortems_written": 0,
            "pnl_records": 0,
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

        await self._pnl_service.start()

        log.info("execution_engine_started", paper_mode=self._paper_mode)

    async def stop(self) -> None:
        self._running = False
        if self._redis:
            await self._redis.close()
        await self._pnl_service.stop()
        log.info("execution_engine_stopped", stats=self._stats)

    async def execute_decision(
        self,
        decision: RiskDecision,
        signal: BlendedSignal,
        current_price: float | None = None,
    ) -> list[Order]:
        """
        Execute a risk decision: create orders + submit to engine.

        Also:
        - Records entry decision branch via DecisionBranchTracker
        - Evaluates existing positions via HermesDecisionTree
        """
        self._stats["decisions_received"] += 1

        # GF — idempotency: skip if this decision_id was already executed.
        # Protects against duplicate Redis delivery (at-least-once) and an
        # `approve` re-publish re-sending the same decision_id.
        if self._decision_already_executed(decision.decision_id):
            self._stats["decisions_duplicated"] = self._stats.get("decisions_duplicated", 0) + 1
            log.info(
                "decision_already_executed_skipping",
                decision_id=decision.decision_id,
                signal_id=signal.signal_id,
            )
            return []

        # Store signal for later attribution
        self._signal_map[signal.signal_id] = signal

        if not decision.approved:
            self._stats["orders_rejected"] += 1
            log.info(
                "decision_not_approved_skipping",
                signal_id=signal.signal_id,
                reason=decision.reason,
            )
            return []

        # === Evaluate existing positions via decision tree ===
        await self._evaluate_existing_positions(signal, current_price or signal.nt_entry_price)

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

            # If filled, register position + record entry branch
            if order.status == OrderStatus.FILLED and order.avg_fill_price:
                position_id = await self._register_position(order, signal, decision)

                # Record entry decision branch — use position_id so it links to the
                # exit branch (record_exit uses position.position_id). order.trade_id !=
                # position_id, which broke entry/exit attribution in the self-learning loop.
                self._branch_tracker.record_entry(
                    trade_id=position_id,
                    symbol=order.symbol,
                    venue=order.venue,
                    entry_action=AgentAction.ENTER_NEW,
                    entry_strategy=signal.entry_strategy,
                    execution_method=signal.execution_method,
                    meta_regime=signal.meta_regime,
                    brick_pattern=signal.brick_pattern,
                    conviction_score=signal.meta_regime_confidence,
                    sizing_multiplier=signal.sizing_multiplier,
                    ts_opened=datetime.now(timezone.utc),
                )
                self._stats["branch_attributions"] += 1

                # Map position → signal for later attribution
                self._position_signals[position_id] = signal.signal_id

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

    async def _evaluate_existing_positions(
        self,
        signal: BlendedSignal,
        current_price: float,
    ) -> None:
        """
        Evaluate existing positions for this symbol via the decision tree.

        If the decision tree says to close, close the position and record:
        - PnL via PnLService
        - Postmortem via DecisionJournalWriter
        - Exit branch via DecisionBranchTracker
        """
        positions = self._state.get_positions_by_symbol(signal.symbol)
        if not positions:
            return

        for pos in positions:
            decision = self._decision_tree.evaluate_existing_position(
                position=pos,
                signal=signal if signal.direction != "neutral" else None,
                current_price=current_price,
            )

            # If decision tree says to close, execute the close
            if decision.action in (
                AgentAction.CLOSE_STOP_LOSS,
                AgentAction.CLOSE_TAKE_PROFIT,
                AgentAction.CLOSE_EARLY_PROFIT,
                AgentAction.CLOSE_FLIP,
            ):
                await self._close_position(pos, current_price, decision)

    async def _close_position(
        self,
        position: Any,  # PortfolioPosition
        exit_price: float,
        decision: Any,  # AgentDecision
    ) -> None:
        """Close a position and record all attribution."""
        # Close in portfolio state
        result = self._state.remove_position(
            position_id=position.position_id,
            exit_price=exit_price,
            exit_reason=decision.action.value,
        )
        self._stats["positions_closed"] += 1

        if not result:
            return

        net_pnl = result.get("realized_pnl", 0)
        r_multiple = result.get("r_multiple", 0)
        hold_duration = int(result.get("hold_duration_sec", 0))

        # Get the original signal for this position
        signal_id = self._position_signals.get(position.position_id, "")
        original_signal = self._signal_map.get(signal_id)

        # Compute entry alpha
        entry_alpha_bps = 0.0
        if original_signal and original_signal.nt_entry_price > 0:
            actual_entry = position.entry_price
            nt_entry = original_signal.nt_entry_price
            if position.direction == "long":
                entry_alpha_bps = (nt_entry - actual_entry) / nt_entry * 10000
            else:
                entry_alpha_bps = (actual_entry - nt_entry) / nt_entry * 10000

        # 1. Record realized PnL via PnLService
        if original_signal:
            self._pnl_service.record_realized_pnl(
                trade_id=result.get("position_id", position.position_id),
                symbol=position.symbol,
                venue=position.venue,
                direction=position.direction,
                entry_price=position.entry_price,
                exit_price=exit_price,
                qty=position.qty,
                fees=0,  # TODO: from fills
                slippage=0,  # TODO: from fills
                funding=0,  # TODO: from funding accrual
                risk_amount=position.risk_amount,
                hold_duration_sec=hold_duration,
                n_fills=1,
                nt_entry_price=original_signal.nt_entry_price,
                regime_at_close=original_signal.meta_regime,
                config_hash=original_signal.config_hash,
            )
            self._stats["pnl_records"] += 1

        # 2. Record exit branch via DecisionBranchTracker
        self._branch_tracker.record_exit(
            trade_id=position.position_id,
            exit_action=decision.action,
            exit_reason=decision.reason,
            net_pnl=net_pnl,
            r_multiple=r_multiple,
            hold_duration_sec=hold_duration,
            meta_regime_at_exit=original_signal.meta_regime if original_signal else "",
            entry_alpha_bps=entry_alpha_bps,
        )
        self._stats["branch_attributions"] += 1

        # 3. Write postmortem via DecisionJournalWriter
        regime = original_signal.meta_regime if original_signal else "unknown"
        entry_strategy = original_signal.entry_strategy if original_signal else ""
        self._journal_writer.write_postmortem(
            trade_id=position.position_id,
            symbol=position.symbol,
            venue=position.venue,
            direction=position.direction,
            entry_thesis=f"Signal: {regime} regime, {entry_strategy} strategy, {decision.action.value} exit",
            exit_reason=decision.action.value,
            exit_pnl=net_pnl,
            exit_r_multiple=r_multiple,
            hold_duration_sec=hold_duration,
            regime_tag=regime,
            postmortem=f"Exited via {decision.action.value}: {decision.reason}. "
                      f"Net PnL: ${net_pnl:.2f}, R: {r_multiple:.2f}, "
                      f"Entry alpha: {entry_alpha_bps:.1f} bps.",
            lessons=self._extract_lessons(decision.action, net_pnl, r_multiple, regime),
            tags=[decision.action.value, regime],
            opened_at=position.opened_at,
            closed_at=datetime.now(timezone.utc),
        )
        self._stats["postmortems_written"] += 1

        # 4. Record trade result for consecutive loss tracking
        if self._cb_manager:
            self._cb_manager.record_trade_result(won=net_pnl > 0)

        log.info(
            "position_closed",
            position_id=position.position_id,
            symbol=position.symbol,
            exit_action=decision.action.value,
            exit_price=exit_price,
            net_pnl=net_pnl,
            r_multiple=r_multiple,
            entry_alpha_bps=entry_alpha_bps,
        )

    @staticmethod
    def _extract_lessons(action: AgentAction, pnl: float, r: float, regime: str) -> list[str]:
        """Extract actionable lessons from a closed trade."""
        lessons = []
        if pnl < 0 and action == AgentAction.CLOSE_STOP_LOSS:
            lessons.append(f"Stop-loss hit in {regime} — review entry timing for this regime")
        if pnl > 0 and action == AgentAction.CLOSE_EARLY_PROFIT:
            lessons.append(f"Early profit take worked in {regime} — trend was fading at +4.5%")
        if pnl < 0 and action == AgentAction.CLOSE_FLIP:
            lessons.append(f"Flip failed in {regime} — conviction threshold may need raising")
        if r < -1:
            lessons.append(f"Large loss ({r:.1f}R) in {regime} — consider reducing size")
        return lessons

    async def _register_position(
        self,
        order: Order,
        signal: BlendedSignal,
        decision: RiskDecision,
    ) -> str:
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

        return position_id

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

    def _decision_already_executed(self, decision_id: str) -> bool:
        """GF — idempotency check: has this decision_id already produced an order?

        Reads the `orders` table via the shared DuckDB path (read-only). A decision
        that was already executed must not be re-executed on duplicate delivery.
        """
        if not decision_id:
            return False
        try:
            from hermes.db.migrate import safe_duckdb_connect as _safe

            with _safe(str(self._db_path), read_only=True) as conn:
                row = conn.execute(
                    "SELECT 1 FROM orders WHERE risk_decision_id = ? LIMIT 1",
                    [decision_id],
                ).fetchone()
                return row is not None
        except Exception as e:
            # If the table/DB is unavailable, err toward executing (don't silently
            # drop a live decision) — but log so it's visible.
            log.warning("idempotency_check_failed", decision_id=decision_id, error=str(e)[:120])
            return False

    def get_branch_tracker(self) -> DecisionBranchTracker:
        return self._branch_tracker

    def get_decision_tree(self) -> HermesDecisionTree:
        return self._decision_tree

    def get_pnl_service(self) -> PnLService:
        return self._pnl_service

    def get_journal_writer(self) -> DecisionJournalWriter:
        return self._journal_writer

    def get_paper_engine(self) -> PaperTradingEngine:
        return self._paper_engine

    def get_writer(self) -> ExecutionWriter:
        return self._writer

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["paper_engine"] = self._paper_engine.get_stats()
        stats["db_writer"] = self._writer.get_stats()
        stats["pnl_service"] = self._pnl_service.get_stats()
        stats["branch_tracker"] = self._branch_tracker.get_stats()
        stats["journal_writer"] = self._journal_writer.get_stats()
        return stats
