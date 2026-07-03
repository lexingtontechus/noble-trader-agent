"""
L5 Portfolio & Risk Engine orchestrator.

Coordinates all L5 components:
- PortfolioStateService (positions, cash, exposure)
- VaRCalculator (historical + parametric)
- VolatilityCircuitBreaker (per-asset, 4-level ladder)
- RiskCircuitBreaker (portfolio-level, continuous)
- KillSwitch (global halt)
- RiskGate (pre-trade checks on BlendedSignal)
- AutonomyGate (tier-based approval)
- SnapshotWriter (periodic + on-event)

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
from hermes.db.migrate import get_duckdb_path
from hermes.portfolio.autonomy_gate import AutonomyDecision, AutonomyGate
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
        )
        self._snapshot_writer = SnapshotWriter(config, self._state)

        self._running = False
        self._redis = None
        self._redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")

        self._stats = {
            "signals_evaluated": 0,
            "signals_approved": 0,
            "signals_rejected": 0,
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

        log.info("portfolio_risk_engine_started", equity=self._state._initial_equity)

    async def stop(self) -> None:
        """Stop the L5 engine."""
        self._running = False
        await self._snapshot_writer.stop()
        if self._redis:
            await self._redis.close()
        log.info("portfolio_risk_engine_stopped", stats=self._stats)

    async def evaluate_signal(
        self,
        signal: BlendedSignal,
        atr_baseline: float | None = None,
        atr_current: float | None = None,
    ) -> RiskDecision:
        """
        Evaluate a blended signal through the full risk gate.

        Args:
            signal: BlendedSignal from L4
            atr_baseline: Baseline ATR (from IndicatorEngine)
            atr_current: Current ATR

        Returns:
            RiskDecision (approved/rejected + final size + limits hit)
        """
        self._stats["signals_evaluated"] += 1

        # 1. Autonomy gate classification
        metrics = self._state.get_metrics()
        is_crypto = signal.venue == "hyperliquid"
        autonomy_decision = self._autonomy_gate.classify(
            action_type="enter_trade",
            notional_usd=signal.final_size_usd,
            equity=metrics.equity_total,
            is_crypto=is_crypto,
        )

        # 2. Risk gate evaluation
        decision = self._risk_gate.evaluate(
            signal=signal,
            atr_baseline=atr_baseline,
            atr_current=atr_current,
            autonomy_tier=autonomy_decision.tier,
        )

        # 3. Override if autonomy requires human approval
        if autonomy_decision.requires_human_approval and decision.approved:
            decision.approved = False
            decision.limits_hit.append(f"autonomy_tier_{autonomy_decision.tier}_requires_human")
            decision.reason = f"autonomy_tier_{autonomy_decision.tier}:human_approval_required"
            decision.approved_size_usd = 0.0

        # 4. Update stats
        if decision.approved:
            self._stats["signals_approved"] += 1
        else:
            self._stats["signals_rejected"] += 1

        # 5. Write to DuckDB + publish
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
        metrics = self._state.get_metrics()
        daily_pnl_pct = metrics.realized_pnl / metrics.equity_total if metrics.equity_total > 0 else 0
        margin_used_pct = metrics.margin_used / metrics.equity_total if metrics.equity_total > 0 else 0

        events = self._risk_breaker.check_portfolio(
            drawdown_pct=metrics.drawdown_pct,
            daily_pnl_pct=daily_pnl_pct,
            var_1d_99=metrics.var_1d_99,
            equity=metrics.equity_total,
            margin_used_pct=margin_used_pct,
        )

        for event in events:
            await self._write_breaker_event(event)

        return events

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
            with duckdb.connect(str(self._db_path)) as conn:
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
            with duckdb.connect(str(self._db_path)) as conn:
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
        stats["kill_switch"] = self._kill_switch.get_stats()
        stats["autonomy"] = self._autonomy_gate.get_stats()
        stats["snapshots"] = self._snapshot_writer.get_stats()
        stats["var"] = self._var_calc.get_stats()
        return stats
