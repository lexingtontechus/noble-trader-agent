"""
L5 Portfolio & Risk Engine orchestrator.

Coordinates all L5 components:
- PortfolioStateService (positions, cash, exposure)
- VaRCalculator (historical + parametric)
- VolatilityCircuitBreaker (per-asset, 4-level ladder)
- CircuitBreakerManager (8-category tiered CBs with time-decay + rolling windows)
- RiskCircuitBreaker (legacy portfolio-level, still used by RiskGate)
- KillSwitch (global halt)
- RiskGate (pre-trade checks on BlendedSignal)
- AutonomyGate (tier-based approval)
- SnapshotWriter (periodic + on-event)
- DeadMansSwitch (auto-flatten if heartbeat missed)
- AlertManager (Discord/Telegram notifications)

Consumes BlendedSignal from L4, produces RiskDecision for L3.

See roadmap §2.3.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path, safe_duckdb_connect
from hermes.ops.alerting import Alert, AlertManager, AlertSeverity
from hermes.ops.dead_mans_switch import DeadMansSwitch
from hermes.portfolio.autonomy_gate import AutonomyDecision, AutonomyGate
from hermes.portfolio.selection import SelectionLayer
from hermes.portfolio.pending_approvals import PendingApprovals
from hermes.portfolio.cb_manager import CircuitBreakerManager
from hermes.portfolio.circuit_breakers import (
    CircuitBreakerEvent,
    KillSwitch,
    RiskCircuitBreaker,
    VolatilityCircuitBreaker,
)
from hermes.portfolio.risk_gate import RiskDecision, RiskGate
from hermes.portfolio.snapshot_writer import SnapshotWriter
from hermes.portfolio.state import PortfolioStateService
from hermes.portfolio.var_calculator import VaRCalculator
from hermes.signals.synthesizer import BlendedSignal

log = structlog.get_logger(__name__)


class PortfolioRiskEngine:
    """
    L5 orchestrator — portfolio state + risk gate + circuit breakers.

    Usage:
        engine = PortfolioRiskEngine(config)
        await engine.start()
        decision = await engine.evaluate_signal(signal)
        # ... later ...
        await engine.stop()
    """

    def __init__(
        self,
        config: HermesConfig,
        initial_equity: float = 100000,
    ) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._config_hash = get_config_hash(config)

        # Sub-components
        self._state = PortfolioStateService(
            initial_equity=initial_equity,
            config_hash=self._config_hash,
        )
        self._var_calc = VaRCalculator()
        self._vol_breaker = VolatilityCircuitBreaker(
            vol_mult_threshold=config.circuit_breakers.get("volatility", {}).get(
                "vol_mult_threshold", 2.5
            ),
        )
        self._risk_breaker = RiskCircuitBreaker(
            max_portfolio_drawdown_pct=config.account.get("max_portfolio_drawdown_pct", 0.15),
            max_asset_drawdown_pct=config.asset.get("max_asset_drawdown_pct", 0.08),
            daily_loss_limit_pct=config.account.get("daily_loss_limit_pct", 0.03),
        )

        # Advanced Circuit Breaker Manager (8 categories, time-decay, rolling windows)
        cb_config = config.circuit_breakers.get("manager", {})
        self._cb_manager = CircuitBreakerManager.from_config(cb_config if cb_config else None)

        self._kill_switch = KillSwitch()
        self._risk_gate = RiskGate(
            portfolio_state=self._state,
            vol_breaker=self._vol_breaker,
            risk_breaker=self._risk_breaker,
            kill_switch=self._kill_switch,
            var_calculator=self._var_calc,
            max_gross_exposure_pct=config.account.get("max_gross_exposure_pct", 1.50),
            risk_amount_cap=config.account.get("risk_amount_cap", 1000),
            reward_risk_min=config.signal.get("reward_risk_min", 1.5),
            config_hash=self._config_hash,
        )
        autonomy_cfg = config.autonomy
        cold_cfg = autonomy_cfg.get("cold_start", {}) or {}
        self._autonomy_gate = AutonomyGate(
            tier1_max_notional=autonomy_cfg.get("tier_1", {}).get("max_notional_usd", 5000),
            tier1_max_position_pct=autonomy_cfg.get("tier_1", {}).get(
                "max_position_pct_of_equity", 0.02
            ),
            tier3_max_notional=autonomy_cfg.get("tier_3", {}).get("max_notional_usd", 25000),
            active_hours_start=autonomy_cfg.get("active_hours", {}).get("start", "09:30"),
            active_hours_end=autonomy_cfg.get("active_hours", {}).get("end", "16:00"),
            crypto_24_7=autonomy_cfg.get("active_hours", {}).get("crypto_24_7", True),
            degrade_outside_hours=autonomy_cfg.get("active_hours", {}).get(
                "degrade_outside_hours", True
            ),
            timezone_name=autonomy_cfg.get("active_hours", {}).get(
                "timezone", "America/Los_Angeles"
            ),
            cold_start_enabled=cold_cfg.get("enabled", False),
            cold_start_tier1_notional=cold_cfg.get("tier1_max_notional", 100),
            cold_start_tier1_pct=cold_cfg.get("tier1_max_position_pct", 0.002),
            cold_start_max_new_positions=cold_cfg.get("max_new_positions", 3),
            cold_start_max_new_exposure_pct=cold_cfg.get("max_new_exposure_pct", 0.05),
        )
        # L4.5 Selection layer (GB)
        sel_cfg = getattr(config.portfolio, "selection", {}) or {}
        self._selection = SelectionLayer(
            enabled=sel_cfg.get("enabled", True),
            max_new_positions_per_cycle=sel_cfg.get("max_new_positions_per_cycle", 3),
            cycle_window_sec=sel_cfg.get("cycle_window_sec", 300),
            policy=sel_cfg.get("policy", "top_n"),
            score_threshold=sel_cfg.get("score_threshold", 0.0),
            score_weights=sel_cfg.get("score_weights"),
            max_correlated_exposure=sel_cfg.get("max_correlated_exposure", 0.20),
        )
        # Cold-start exit tracking
        self._cold_exit_n = cold_cfg.get("exit_after_n_trades", 20)
        self._cold_exit_exp_bps = cold_cfg.get("exit_min_expectancy_bps", 0)
        # GE — pending human-approval queue (DuckDB-backed).
        # Decision deadline = autonomy.tier_3.approval_decision_ttl_sec (default 300s / 5 min).
        approve_ttl_sec = autonomy_cfg.get("tier_3", {}).get("approval_decision_ttl_sec", 300)
        self._pending = PendingApprovals(config, approval_timeout_seconds=approve_ttl_sec)
        self._snapshot_writer = SnapshotWriter(config, self._state)

        # Ops components
        self._dms = DeadMansSwitch(
            timeout_sec=60.0,
            auto_flatten=True,
            on_activate=self._on_dms_activate,
        )
        self._alert_manager = AlertManager(config)

        self._running = False
        self._redis = None
        self._redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")

        self._stats = {
            "signals_evaluated": 0,
            "signals_approved": 0,
            "signals_rejected": 0,
            "cb_manager_trips": 0,
            "dms_activations": 0,
            "alerts_sent": 0,
        }

    async def start(self) -> None:
        """Start the L5 engine."""
        if self._running:
            return
        self._running = True

        # Connect to Redis for publishing risk decisions
        if not ("<" in self._redis_url or self._redis_url.startswith("secret:")):
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
                await self._redis.ping()
                log.info("l5_redis_connected")
            except Exception as e:
                log.warning("l5_redis_unavailable", error=str(e))
                self._redis = None

        # Start snapshot writer
        await self._snapshot_writer.start()

        # Start dead man's switch
        await self._dms.start()

        # Start alert manager
        await self._alert_manager.start()

        log.info("portfolio_risk_engine_started", equity=self._state._initial_equity)

    async def stop(self) -> None:
        """Stop the L5 engine."""
        self._running = False
        await self._snapshot_writer.stop()
        await self._dms.stop()
        await self._alert_manager.stop()
        if self._redis:
            await self._redis.close()
        log.info("portfolio_risk_engine_stopped", stats=self._stats)

    def heartbeat(self, source: str = "l5") -> None:
        """Signal that the engine is alive (feeds Dead Man's Switch)."""
        self._dms.heartbeat(source)

    def sync_external_equity(self, total: float) -> None:
        """Re-anchor equity/drawdown baseline to live brokerage equity."""
        self._state.set_external_equity(total)

    def _is_supported_venue(self, signal: BlendedSignal) -> bool:
        """GD: a signal is tradeable only if its venue is an enabled, supported one.

        Uses the same Venue enum that the execution adapters implement. A symbol
        that resolves to no Venue (e.g. an exchange we don't support) cannot be
        priced or executed, so it is rejected here — before autonomy/risk.
        """
        from hermes.schemas.market import Venue

        venue = getattr(signal, "venue", None)
        if not venue:
            return False
        venue_str = str(venue).lower()
        # tradingview is a data source, not an executable venue
        if venue_str == "tradingview":
            return False
        # Supported execution venues per our adapters
        supported = {str(v.value).lower() for v in Venue.__members__.values()}
        if venue_str not in supported:
            return False
        # Respect explicitly enabled venues from config (dict[str, VenueConfig])
        venues_cfg = getattr(self._config, "venues", {}) or {}
        if venues_cfg:
            enabled_names = {str(k).lower() for k, v in venues_cfg.items() if getattr(v, "enabled", False)}
            return venue_str in enabled_names
        return True

    def _check_cold_start_exit(self) -> None:
        """GA exit: leave cold-start when count + positive expectancy both met."""
        if not self._autonomy_gate.is_cold_start():
            return
        closed = self._state._stats.get("positions_closed", 0)
        expectancy = self._state._realized_pnl  # cumulative realized PnL
        if closed >= self._cold_exit_n and expectancy > (self._cold_exit_exp_bps / 10000.0):
            self._autonomy_gate.set_cold_start_state(False)
            log.info(
                "cold_start_exited",
                closed_trades=closed,
                realized_pnl=round(expectancy, 2),
                exit_min_expectancy_bps=self._cold_exit_exp_bps,
            )

    async def evaluate_signal(
        self,
        signal: BlendedSignal,
        atr_baseline: float | None = None,
        atr_current: float | None = None,
    ) -> RiskDecision:
        """
        Evaluate a blended signal through the full risk gate.

        Also:
        - Checks CircuitBreakerManager (8 categories) for size multiplier
        - Applies CB multiplier to approved size
        - Sends heartbeat to Dead Man's Switch
        """
        self._stats["signals_evaluated"] += 1
        self.heartbeat("evaluate_signal")

        # Cheap state snapshot (needed by GD + GB + autonomy + cold-start)
        metrics = self._state.get_metrics()
        # GA exit check (count + positive expectancy) — flips cold-start off when met
        self._check_cold_start_exit()

        # GD — Unsupported venue: reject at L5 before autonomy/risk (single chokepoint).
        # A signal whose symbol has no supported Venue cannot be priced/executed.
        if not self._is_supported_venue(signal):
            self._stats["signals_rejected"] += 1
            return RiskDecision(
                signal_id=signal.signal_id,
                approved=False,
                requested_size_usd=signal.final_size_usd,
                approved_size_usd=0.0,
                limits_hit=["unsupported_venue"],
                reason="rejected:unsupported_venue",
                autonomy_tier=0,
                config_hash=self._config_hash,
            )

        # GB — L4.5 selection layer: rank candidate, admit top-N per cycle.
        # Excess candidates are DROPPED (not re-queued).
        if signal.entry_strategy not in ("block", "skip_entry"):
            admit, sel_reason = self._selection.evaluate(signal, equity=metrics.equity_total)
            if not admit:
                self._stats["signals_rejected"] += 1
                return RiskDecision(
                    signal_id=signal.signal_id,
                    approved=False,
                    requested_size_usd=signal.final_size_usd,
                    approved_size_usd=0.0,
                    limits_hit=[sel_reason],
                    reason=f"rejected:{sel_reason}",
                    autonomy_tier=0,
                    config_hash=self._config_hash,
                )

        # 1. Autonomy gate classification (cold-start budget supplied)
        is_crypto = signal.venue == "hyperliquid"
        autonomy_decision = self._autonomy_gate.classify(
            action_type="enter_trade",
            notional_usd=signal.final_size_usd,
            equity=metrics.equity_total,
            is_crypto=is_crypto,
            cs_new_positions=self._state._stats.get("positions_opened", 0),
            cs_new_exposure=metrics.gross_exposure_usd,
        )

        # 2. Risk gate evaluation (legacy: 8 checks)
        decision = self._risk_gate.evaluate(
            signal=signal,
            atr_baseline=atr_baseline,
            atr_current=atr_current,
            autonomy_tier=autonomy_decision.tier,
        )

        # 3. Advanced Circuit Breaker Manager checks
        if decision.approved and decision.approved_size_usd > 0:
            # Check all 8 CB categories
            cb_trips: list = []

            # Portfolio exposure
            gross_exposure = metrics.gross_exposure_usd + decision.approved_size_usd
            cb_trips.extend(self._cb_manager.check_portfolio_exposure(
                gross_exposure_usd=gross_exposure,
                equity=metrics.equity_total,
            ))

            # Position size
            cb_trips.extend(self._cb_manager.check_position_size(
                position_notional_usd=decision.approved_size_usd,
                symbol=signal.symbol,
            ))

            # Daily loss — use TODAY's realized PnL, not all-time cumulative.
            # metrics.realized_pnl is cumulative; the daily-loss CB must compare
            # against today's loss or it never trips correctly.
            cb_trips.extend(self._cb_manager.check_daily_loss(
                daily_loss_usd=self._state.get_realized_pnl_today(),
            ))

            # Daily-WINS cooloff: win-count ratio vs rolling baseline.
            cb_trips.extend(self._cb_manager.check_daily_wins(
                wins_today=self._state.get_wins_today(),
                avg_daily_wins=self._state.get_avg_daily_wins(),
            ))

            # Daily-PROFIT cooloff: realized profit for the session (ride-and-exit
            # halt before a likely regime shift / mean-reversion).
            cb_trips.extend(self._cb_manager.check_daily_profit(
                daily_profit_usd=self._state.get_realized_pnl_today(),
            ))

            # VaR
            if metrics.var_1d_99 is not None:
                cb_trips.extend(self._cb_manager.check_var(
                    var_1d_99_usd=metrics.var_1d_99,
                ))

            # Drawdown
            cb_trips.extend(self._cb_manager.check_drawdown(
                drawdown_pct=metrics.drawdown_pct,
                drawdown_usd=metrics.drawdown_usd,
            ))

            # Apply CB size multiplier
            if self._cb_manager.is_any_tripped():
                cb_multiplier = self._cb_manager.get_size_multiplier()
                if cb_multiplier < 1.0:
                    decision.approved_size_usd = round(
                        decision.approved_size_usd * cb_multiplier, 2
                    )
                    decision.limits_hit.append(f"cb_manager_multiplier:{cb_multiplier:.2f}")
                    self._stats["cb_manager_trips"] += len(cb_trips)

                    # If multiplier is 0, block entirely
                    if cb_multiplier == 0.0:
                        decision.approved = False
                        decision.reason = "blocked_by_circuit_breaker_manager"
                        self._stats["signals_rejected"] += 1
                        await self._write_and_publish(decision)
                        return decision

                # Record trips for frequency tracking
                for _ in cb_trips:
                    self._cb_manager.record_trip()

                # Send alerts for CB trips
                for trip in cb_trips:
                    await self._send_alert(
                        title=f"Circuit Breaker: {trip.tier_label}",
                        message=f"{trip.breaker_name} tripped. Action: {trip.action.value}. "
                                f"Value: {trip.trigger_value:.2f}, Threshold: {trip.threshold:.2f}",
                        severity=AlertSeverity.WARNING if trip.action.value.startswith("reduce")
                                  else AlertSeverity.CRITICAL,
                        data={
                            "category": trip.category,
                            "action": trip.action.value,
                            "trigger_value": trip.trigger_value,
                            "threshold": trip.threshold,
                        },
                    )

        # 4. Override if autonomy requires human approval (GE — queue, don't drop)
        if autonomy_decision.requires_human_approval and decision.approved:
            decision.approved = False
            decision.requires_human_approval = True
            decision.status = "pending"
            decision.limits_hit.append(f"autonomy_tier_{autonomy_decision.tier}_requires_human")
            decision.reason = f"autonomy_tier_{autonomy_decision.tier}:human_approval_required"
            decision.approved_size_usd = 0.0
            # Persist to the pending-approval queue + alert the human via msg channel
            try:
                self._pending.store(
                    decision,
                    symbol=signal.symbol,
                    venue=str(getattr(signal, "venue", "")),
                    direction=signal.direction,
                )
                # Publish a tenant-scoped approval event so the platform (or any
                # subscriber) can deliver it to the user without the agent needing
                # third-party creds (Discord server ownership / Telegram bot+chat_id).
                # The agent's in-app queue (DuckDB pending_decisions) is the default
                # path; this event lets the *platform* relay it via the user's
                # already-known contact (email/push from subscription).
                if self._redis:
                    import os

                    tenant_id = os.environ.get("HERMES_TENANT_ID", "default")
                    approval_event = {
                        "decision_id": decision.decision_id,
                        "signal_id": signal.signal_id,
                        "symbol": signal.symbol,
                        "venue": str(getattr(signal, "venue", "")),
                        "direction": signal.direction,
                        "requested_size_usd": decision.requested_size_usd,
                        "autonomy_tier": autonomy_decision.tier,
                        "status": "pending",
                        "approve_action": f"noble approve {decision.decision_id}",
                    }
                    await self._redis.publish(
                        f"risk.approval.{tenant_id}",
                        json.dumps(approval_event, default=str),
                    )
                await self._send_alert(
                    title=f"Approval required: {signal.direction.upper()} {signal.symbol}",
                    message=(
                        f"Decision {decision.decision_id} needs human approval "
                        f"(tier {autonomy_decision.tier}).\n"
                        f"Requested ${decision.requested_size_usd:,.0f} on {signal.symbol}.\n"
                        f"Approve: `platform approve {decision.decision_id}`"
                    ),
                    severity=AlertSeverity.WARNING,
                    data={
                        "decision_id": decision.decision_id,
                        "symbol": signal.symbol,
                        "direction": signal.direction,
                        "requested_size_usd": decision.requested_size_usd,
                        "autonomy_tier": autonomy_decision.tier,
                        "action": "approve",
                    },
                )
            except Exception as e:
                log.warning("pending_queue_write_failed", error=str(e))

        # 5. Update stats
        if decision.approved:
            self._stats["signals_approved"] += 1
        else:
            self._stats["signals_rejected"] += 1

        # 6. Write to DuckDB + publish
        await self._write_and_publish(decision)

        log.info(
            "risk_decision",
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            approved=decision.approved,
            requested=decision.requested_size_usd,
            approved_size=decision.approved_size_usd,
            limits_hit=decision.limits_hit,
            autonomy_tier=decision.autonomy_tier,
        )

        return decision

    async def check_risk_breakers(self) -> list[CircuitBreakerEvent]:
        """Check portfolio-level risk breakers (call periodically)."""
        self.heartbeat("check_risk_breakers")
        metrics = self._state.get_metrics()
        daily_pnl_pct = metrics.realized_pnl / metrics.equity_total if metrics.equity_total > 0 else 0
        margin_used_pct = metrics.margin_used / metrics.equity_total if metrics.equity_total > 0 else 0

        # Legacy risk breaker
        events = self._risk_breaker.check_portfolio(
            drawdown_pct=metrics.drawdown_pct,
            daily_pnl_pct=daily_pnl_pct,
            var_1d_99=metrics.var_1d_99,
            equity=metrics.equity_total,
            margin_used_pct=margin_used_pct,
        )

        for event in events:
            await self._write_breaker_event(event)
            # Send alert for legacy breaker trips
            await self._send_alert(
                title=f"Risk Breaker: {event.payload.get('check', 'unknown')}",
                message=f"Level {event.level} breaker tripped. Action: {event.action_taken}.",
                severity=AlertSeverity.CRITICAL,
                data={
                    "breaker_type": event.breaker_type,
                    "level": event.level,
                    "trigger_value": event.trigger_value,
                    "threshold": event.threshold,
                },
            )

            # If critical, activate kill switch
            if event.level >= 3:
                self._kill_switch.activate(
                    reason=f"risk_breaker:{event.payload.get('check', 'unknown')}",
                    flatten=event.level >= 4,
                )
                await self._send_alert(
                    title="KILL SWITCH ACTIVATED",
                    message=f"Kill switch activated: {event.payload.get('check', 'unknown')}. "
                            f"Flatten: {event.level >= 4}",
                    severity=AlertSeverity.EMERGENCY,
                )

        return events

    async def _on_dms_activate(self, reason: str, flatten: bool) -> None:
        """Callback when Dead Man's Switch activates."""
        self._stats["dms_activations"] += 1
        log.critical("dms_activated_in_l5", reason=reason, flatten=flatten)

        # Activate kill switch
        self._kill_switch.activate(reason=f"dms:{reason}", flatten=flatten)

        # Send emergency alert
        await self._send_alert(
            title="DEAD MAN'S SWITCH ACTIVATED",
            message=f"Hermes stopped responding. Reason: {reason}. "
                    f"Kill switch activated. Flatten: {flatten}.",
            severity=AlertSeverity.EMERGENCY,
            data={"reason": reason, "flatten": flatten},
        )

    async def _send_alert(
        self,
        title: str,
        message: str,
        severity: AlertSeverity = AlertSeverity.INFO,
        data: dict | None = None,
    ) -> None:
        """Send an alert via AlertManager."""
        self._stats["alerts_sent"] += 1
        try:
            await self._alert_manager.send_alert(Alert(
                title=title,
                message=message,
                severity=severity,
                source="l5_risk_engine",
                data=data or {},
            ))
        except Exception as e:
            log.warning("alert_send_failed", error=str(e))

    async def _write_and_publish(self, decision: RiskDecision) -> None:
        """Write risk decision to DuckDB + publish to Redis."""
        await asyncio.get_event_loop().run_in_executor(
            None, self._write_decision_to_duckdb, decision
        )

        if self._redis:
            try:
                channel = f"risk.decision.{decision.signal_id}"
                payload = decision.model_dump(mode="json")
                await self._redis.publish(channel, json.dumps(payload, default=str))
            except Exception as e:
                log.warning("risk_decision_publish_failed", error=str(e))

    def _write_decision_to_duckdb(self, decision: RiskDecision) -> None:
        """Write risk decision to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO risk_decisions (
                        decision_id, ts, signal_id, approved,
                        requested_size_usd, approved_size_usd,
                        limits_hit, reason, circuit_breaker_level,
                        var_pre, var_post, config_hash, autonomy_tier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        decision.decision_id,
                        decision.ts,
                        decision.signal_id,
                        decision.approved,
                        decision.requested_size_usd,
                        decision.approved_size_usd,
                        decision.limits_hit,
                        decision.reason,
                        decision.circuit_breaker_level,
                        decision.var_pre,
                        decision.var_post,
                        decision.config_hash,
                        decision.autonomy_tier,
                    ],
                )
        except Exception as e:
            log.error("risk_decision_duckdb_write_failed", error=str(e))

    async def _write_breaker_event(self, event: CircuitBreakerEvent) -> None:
        """Write circuit breaker event to DuckDB."""
        await asyncio.get_event_loop().run_in_executor(
            None, self._write_breaker_to_duckdb, event
        )

    def _write_breaker_to_duckdb(self, event: CircuitBreakerEvent) -> None:
        """Write circuit breaker event to DuckDB."""
        import duckdb

        try:
            with safe_duckdb_connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO circuit_breaker_events (
                        event_id, ts, breaker_type, level, symbol,
                        trigger_value, threshold, action_taken, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        event.event_id,
                        event.ts,
                        event.breaker_type,
                        event.level,
                        event.symbol,
                        event.trigger_value,
                        event.threshold,
                        event.action_taken,
                        json.dumps(event.payload, default=str),
                    ],
                )
        except Exception as e:
            log.error("breaker_event_duckdb_write_failed", error=str(e))

    def activate_kill_switch(self, reason: str, flatten: bool = False) -> None:
        """Activate the global kill switch."""
        self._kill_switch.activate(reason, flatten)

    def deactivate_kill_switch(self) -> None:
        """Deactivate the kill switch."""
        self._kill_switch.deactivate()

    def get_cb_manager(self) -> CircuitBreakerManager:
        return self._cb_manager

    def get_dms(self) -> DeadMansSwitch:
        return self._dms

    def get_alert_manager(self) -> AlertManager:
        return self._alert_manager

    def get_pending_approvals(self) -> "PendingApprovals":
        return self._pending

    async def approve_decision(self, decision_id: str) -> dict | None:
        """GE — human approves a pending decision; re-publish to risk.decision.*.

        Returns the approved payload (for L3 to pick up) or None if not pending.
        """
        payload = self._pending.approve(decision_id)
        if payload is None:
            return None
        if self._redis:
            channel = f"risk.decision.{payload['signal_id']}"
            await self._redis.publish(channel, json.dumps(payload, default=str))
            log.info("pending_decision_republished", decision_id=decision_id, channel=channel)
        return payload

    def get_portfolio_state(self) -> PortfolioStateService:
        return self._state

    def get_metrics(self):
        return self._state.get_metrics()

    def get_kill_switch(self) -> KillSwitch:
        return self._kill_switch

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["portfolio"] = self._state.get_stats()
        stats["risk_gate"] = self._risk_gate.get_stats()
        stats["vol_breaker"] = self._vol_breaker.get_stats()
        stats["risk_breaker"] = self._risk_breaker.get_stats()
        stats["cb_manager"] = self._cb_manager.get_stats()
        stats["kill_switch"] = self._kill_switch.get_stats()
        stats["autonomy"] = self._autonomy_gate.get_stats()
        stats["snapshots"] = self._snapshot_writer.get_stats()
        stats["var"] = self._var_calc.get_stats()
        stats["dms"] = self._dms.get_stats()
        stats["alert_manager"] = self._alert_manager.get_stats()
        return stats
