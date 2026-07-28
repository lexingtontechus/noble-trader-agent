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
- Inline DuckDB write: deterministic v1 postmortem row to trade_journal
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
from hermes.analytics.pnl_service import PnLService
from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.execution.db_writer import ExecutionWriter
from hermes.execution.orders import Fill, Order, OrderEvent, OrderStatus, OrderStateMachine
from hermes.execution.brokers.base import ExecutionBroker
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
    - Writes v1 deterministic postmortem rows to trade_journal on position close

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
        alpha_engine=None,  # BayesianAlpha (optional, Phase C — for Bayesian sizing feedback)
    ) -> None:
        self._config = config
        self._state = portfolio_state
        self._paper_mode = paper_mode
        self._db_path = get_duckdb_path(config)
        self._cb_manager = cb_manager  # optional, wired from PortfolioRiskEngine
        self._alpha_engine = alpha_engine  # optional, Phase C

        # Sub-components
        self._slippage = SlippageModeler()
        self._router = SmartOrderRouter(
            twap_n_bricks=config.execution.get("twap_n_bricks", 3),
            iceberg_child_pct=config.execution.get("iceberg_child_pct", 10),
        )
        self._writer = ExecutionWriter(config)

        # ── Execution broker selection (paper vs live MetaApi) ────────
        # Phase: live trading executes via MetaApiBroker when execution.mode=live
        # and METAAPI_* env vars are set; otherwise falls back to paper (safe).
        exec_mode = (config.execution.get("mode", "paper") or "paper").lower()
        self._exec_mode = exec_mode
        if exec_mode == "live":
            from hermes.execution.brokers.metaapi_broker import MetaApiBroker

            _mk_cfg = config.execution.get("metaapi", {}) or {}
            self._broker: ExecutionBroker = MetaApiBroker(
                demo=_mk_cfg.get("demo", True),
                fill_poll_sec=config.execution.get("metaapi_fill_poll_sec", 5.0),
                symbol_map=_mk_cfg.get("symbol_map"),
            )
            log.info("execution_broker", mode="live", broker="MetaApiBroker")
        else:
            self._broker = PaperTradingEngine(slippage_modeler=self._slippage)
            log.info("execution_broker", mode="paper", broker="PaperTradingEngine")

        # ── Fail-safe for live mode ─────────────────────────────────────
        # If live was requested but the MetaApi broker is misconfigured
        # (missing METAAPI_* env vars), fall back to paper and log CRITICAL
        # (never silently trade paper when live was intended to error loudly;
        # never crash the agent).
        if exec_mode == "live" and (not self._broker or getattr(self._broker, "_token", "") == ""):
            log.critical(
                "execution.live_broker_unavailable",
                note="execution.mode=live but METAAPI_* env vars missing → FALLING BACK TO PAPER",
            )
            self._broker = PaperTradingEngine(slippage_modeler=self._slippage)
            self._exec_mode = "paper"

        # Wired components (attribution + learning)
        self._branch_tracker = DecisionBranchTracker(config)
        # ── HIGH #7 (2026-07-22): all 11 HermesDecisionTree params are
        # now wired from config.position_management.decision_tree (see
        # default.yaml:266-278). Each key maps 1:1 to a HermesDecisionTree
        # __init__ param (decision_tree.py:97-115). Defaults match the
        # class-level defaults so a missing/empty config preserves prior
        # runtime behavior.
        _dt_cfg = (
            config.position_management.get("decision_tree", {})
            if hasattr(config, "position_management")
            else {}
        )
        self._decision_tree = HermesDecisionTree(
            stop_loss_pct=_dt_cfg.get("stop_loss_pct", -0.01),
            take_profit_pct=_dt_cfg.get("take_profit_pct", 0.025),
            early_profit_pct=_dt_cfg.get("early_profit_pct", 0.045),
            fading_brick_count=_dt_cfg.get("fading_brick_count", 2),
            strong_conviction_threshold=_dt_cfg.get("strong_conviction_threshold", 0.7),
            trail_stop_activation_pct=_dt_cfg.get("trail_stop_activation_pct", 0.01),
            markov_persistence_high=_dt_cfg.get("markov_persistence_high", 0.7),
            markov_persistence_low=_dt_cfg.get("markov_persistence_low", 0.55),
            trending_tp_multiplier=_dt_cfg.get("trending_tp_multiplier", 1.5),
            mean_reverting_tp_multiplier=_dt_cfg.get("mean_reverting_tp_multiplier", 0.7),
            trending_fading_bricks_delta=_dt_cfg.get("trending_fading_bricks_delta", 1),
        )
        # ── v5 (Phase C): pass alpha_engine into PnLService so it can
        # feed trade outcomes back to the same BayesianAlpha instance
        # the synthesizer uses for compute_alpha(). ─────────────────
        self._pnl_service = PnLService(config, portfolio_state, alpha_engine=alpha_engine)
        self._journal_stats = {"entries_written": 0}

        # Set callbacks
        self._broker.set_callbacks(
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

        if not (("<" in self._redis_url or self._redis_url.startswith("secret:"))):
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
                await self._redis.ping()
                log.info("l3_redis_connected")
            except Exception as e:
                log.warning("l3_redis_unavailable", error=str(e))
                self._redis = None

        await self._pnl_service.start()

        # Connect the execution broker (MetaApi deploys + synchronizes; paper is no-op).
        try:
            await self._broker.connect()
        except Exception as err:
            if self._exec_mode == "live":
                # Live connect failed — fall back to paper rather than crash the agent.
                log.critical(
                    "execution.broker_connect_failed",
                    error=str(err),
                    note="falling back to paper for this session",
                )
                self._broker = PaperTradingEngine(slippage_modeler=self._slippage)
                self._broker.set_callbacks(
                    event_callback=self._on_order_event, fill_callback=self._on_fill
                )
                self._exec_mode = "paper"
            else:
                log.warning("execution.broker_connect_failed", mode=self._exec_mode, error=str(err))

        log.info("execution_engine_started", paper_mode=(self._exec_mode == "paper"))

    async def stop(self) -> None:
        self._running = False
        if self._exec_mode != "paper":
            try:
                await self._broker.disconnect()
            except Exception as err:
                log.warning("execution.broker_disconnect_failed", error=str(err))
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

            # Submit to execution broker (paper engine or MetaApi live)
            await self._broker.submit_order(
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
        - Postmortem via inline DuckDB write to trade_journal
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

        # Close on the live broker (no-op for paper engine). Portfolio state
        # removal above already happened; this sends the venue close order.
        if self._exec_mode != "paper":
            try:
                await self._broker.close_position(
                    position.position_id, reason=decision.action.value
                )
            except Exception as err:
                log.warning(
                    "execution.broker_close_failed",
                    position_id=position.position_id,
                    error=str(err),
                )

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
            # ── v5 (Phase C): pass BlendedSignal's v5 EV fields so the
            # BayesianAlpha engine has (prediction, outcome) pairs.
            # alpha_at_entry is read from the original signal's sizing
            # result — we stored it on the position when it was opened
            # (see open_position). If we didn't capture it, fall back
            # to None and let BayesianAlpha handle the missing value.
            alpha_at_entry = getattr(position, "alpha_at_entry", None)
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
                # ── Phase 1A cleanup (migration 021): signal_id lets
                # TradeJournal._select_pending JOIN pnl_realized to
                # trade_signals_blended; exit_reason lets the postmortem
                # skill payload read the exit branch without a separate
                # trade_journal JOIN. Both were already in scope at this
                # call site (signal_id from _position_signals map,
                # decision.action.value from the exit decision) — we
                # just weren't passing them through.
                signal_id=signal_id or None,
                exit_reason=decision.action.value,
                # ── v5 EV fields (Phase C) ───────────────────────────
                p_win_agent=getattr(original_signal, "p_win_agent", None),
                p_win_server=getattr(original_signal, "p_win_server", None),
                alpha_at_entry=alpha_at_entry,
                ev_per_dollar=None,  # not on BlendedSignal today; future add
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

        # 3. Write v1 deterministic postmortem row to trade_journal
        regime = original_signal.meta_regime if original_signal else "unknown"
        entry_strategy = original_signal.entry_strategy if original_signal else ""
        self._write_v1_postmortem(
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

    def _write_v1_postmortem(
        self,
        *,
        trade_id: str,
        symbol: str,
        venue: str,
        direction: str,
        entry_thesis: str,
        exit_reason: str,
        exit_pnl: float,
        exit_r_multiple: float,
        hold_duration_sec: int,
        regime_tag: str | None,
        postmortem: str,
        lessons: list[str],
        tags: list[str],
        opened_at: datetime,
        closed_at: datetime,
    ) -> None:
        """Write a v1 deterministic postmortem row to trade_journal.

        This is the simple per-trade journal entry written on every
        position close. The LLM/human postmortem layer lives in the
        separate trade_postmortem table (keyed by signal_id, populated
        by the noble journal CLI / agent runtime).
        """
        import duckdb

        journal_id = str(uuid4())
        now = datetime.now(timezone.utc)
        query = """
            INSERT INTO trade_journal (
                journal_id, trade_id, symbol, venue, strategy_id,
                direction, regime_tag,
                entry_thesis, exit_reason, exit_pnl, exit_r_multiple,
                hold_duration_sec, postmortem, lessons, tags,
                opened_at, closed_at, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = [
            journal_id, trade_id, symbol, venue, "hermes_v1",
            direction, regime_tag,
            entry_thesis, exit_reason, exit_pnl, exit_r_multiple,
            hold_duration_sec, postmortem, lessons, tags,
            opened_at, closed_at, "hermes", now, now,
        ]
        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(query, params)
            self._journal_stats["entries_written"] += 1
        except Exception as e:
            log.error("v1_postmortem_write_failed", trade_id=trade_id, error=str(e))

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
            # ── v5 (Phase C): stamp alpha_at_entry for BayesianAlpha ──
            # We don't have the sizing_result here (it was computed in
            # execute_decision, before _register_position is called).
            # The synthesizer's alpha_value is passed through via the
            # BlendedSignal's sizing_reason field — but a cleaner path
            # is to add an explicit alpha_at_entry field to BlendedSignal
            # in a future iteration. For now, we leave it unset and
            # _on_position_closed reads it via getattr fallback to None.
            # BayesianAlpha.record_outcome handles None gracefully.
        )

        # Phase C: stamp v5 EV fields on the position so they survive
        # to the trade-close path. These are read by
        # _on_position_closed → record_realized_pnl → BayesianAlpha.
        # We use setattr because Position uses extra="allow" rather
        # than declaring these fields explicitly.
        try:
            position.alpha_at_entry = getattr(signal, "alpha_at_entry", None)  # type: ignore[attr-defined]
            position.p_win_agent_at_entry = getattr(signal, "p_win_agent", None)  # type: ignore[attr-defined]
        except Exception:
            pass

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
        order = await self._broker.get_order(order_id)
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



    def get_broker(self) -> ExecutionBroker:
        """Return the active execution broker (paper or live MetaApi)."""
        return self._broker

    def get_paper_engine(self) -> ExecutionBroker:
        """Backward-compatible alias for :meth:`get_broker`."""
        return self._broker

    def get_writer(self) -> ExecutionWriter:
        return self._writer

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["broker"] = self._broker.get_stats()
        stats["broker_mode"] = self._exec_mode
        stats["db_writer"] = self._writer.get_stats()
        stats["pnl_service"] = self._pnl_service.get_stats()
        stats["branch_tracker"] = self._branch_tracker.get_stats()
        stats["journal_writer"] = self._journal_stats.copy()
        return stats
