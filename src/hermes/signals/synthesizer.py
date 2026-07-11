"""
Signal Synthesizer (L4) — the BEV combiner.

Consumes Noble Trader heartbeats (from L0 internal Redis) + meta-regime
(from 7-state classifier) + renko bricks (from renko engine) and produces
a blended entry/execution decision.

See roadmap §5.4 for the full algorithm.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog
from pydantic import BaseModel, Field

from hermes.core.config import HermesConfig, get_config_hash
from hermes.db.migrate import get_duckdb_path
from hermes.schemas.heartbeat import NobleTraderHeartbeat
from hermes.schemas.market import Tick, Venue
from hermes.signals.entry_timing import (
    EntryDecision,
    EntryTimingOptimizer,
    ExecutionMethodOptimizer,
)
from hermes.signals.meta_regime import MetaRegimeClassifier, MetaRegimeResult
from hermes.signals.renko_engine import BrickPatternAnalyzer, RenkoConstructor
from hermes.signals.sizing import SizingEngine, SizingResult

log = structlog.get_logger(__name__)


class BlendedSignal(BaseModel):
    """The final output of the L4 signal synthesizer."""

    signal_id: str
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # From NT (trusted)
    symbol: str
    venue: str
    direction: str  # buy / sell / neutral (from NT)
    nt_entry_price: float
    nt_stop_price: float
    nt_target_price: float
    nt_effective_kelly: float
    nt_brick_size: float

    # From Hermes
    meta_regime: str
    meta_regime_confidence: float
    sizing_multiplier: float

    # Entry/execution decision
    entry_strategy: str
    execution_method: str
    entry_price_target: float | None = None
    limit_price: float | None = None
    final_size_usd: float
    final_size_pct: float
    risk_amount_usd: float

    # Analysis
    brick_pattern: str
    expected_entry_alpha_bps: float
    sizing_limits_hit: list[str] = Field(default_factory=list)
    sizing_reason: str = ""

    # Autonomy
    autonomy_tier: int = 0

    # Config
    config_hash: str = ""


class SignalSynthesizer:
    """
    L4 signal synthesizer — the BEV combiner.

    Consumes NT heartbeats, enriches with meta-regime + renko analysis,
    produces blended entry/execution decisions.

    Lifecycle:
        synthesizer = SignalSynthesizer(config, monitor)
        await synthesizer.start()
        signal = await synthesizer.process_heartbeat(heartbeat)
        # ... later ...
        await synthesizer.stop()
    """

    def __init__(
        self,
        config: HermesConfig,
        price_monitor=None,  # PriceMonitor from Phase 2 (for live market data)
    ) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._monitor = price_monitor

        # Sub-components
        self._meta_regime = MetaRegimeClassifier(
            risk_off_corr_threshold=config.meta_regime.get("thresholds", {}).get(
                "risk_off_corr_threshold", 0.75
            ),
            funding_stress_annualized_pct=config.meta_regime.get("thresholds", {}).get(
                "funding_stress_annualized_pct", 50.0
            ),
            liquidity_depth_percentile=config.meta_regime.get("thresholds", {}).get(
                "liquidity_depth_percentile", 10
            ),
            transition_entropy_threshold=config.meta_regime.get("thresholds", {}).get(
                "transition_entropy_bits", 1.5
            ),
        )

        self._entry_optimizer = EntryTimingOptimizer(
            brick_confirmation_count=config.entry.get("brick_confirmation_count", 2),
            pullback_depth_brick_fraction=config.entry.get(
                "pullback_depth_brick_fraction", 0.5
            ),
        )

        self._execution_optimizer = ExecutionMethodOptimizer(
            large_size_threshold_usd=config.execution.get("large_size_threshold_usd", 10000),
            twap_n_bricks=config.execution.get("twap_n_bricks", 3),
            iceberg_child_pct=config.execution.get("iceberg_child_pct", 10),
            post_only_preference=config.execution.get("post_only_preference", True),
        )

        self._sizing = SizingEngine(
            max_position_size_pct=config.asset.get("max_position_size_pct", 0.05),
            max_position_notional=config.asset.get("max_position_notional", 25000),
            max_gross_exposure_pct=config.account.get("max_gross_exposure_pct", 1.50),
            risk_amount_cap=config.account.get("risk_amount_cap", 1000),
            max_portfolio_drawdown_pct=config.account.get(
                "max_portfolio_drawdown_pct", 0.15
            ),
        )

        # Per-symbol renko constructors (keyed by symbol)
        self._renko_constructors: dict[str, RenkoConstructor] = {}
        # Last bar ts fed to each renko constructor (prevents double-counting volume
        # when synthesize() runs repeatedly on the same monitor window).
        self._renko_last_ts: dict[str, Any] = {}
        self._pattern_analyzer = BrickPatternAnalyzer(lookback=10)

        # Redis for publishing blended signals
        self._redis = None
        self._redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")

        self._running = False
        self._stats = {
            "heartbeats_processed": 0,
            "signals_produced": 0,
            "signals_blocked": 0,
        }

    async def start(self) -> None:
        """Start the synthesizer (connect to Redis)."""
        if self._running:
            return
        self._running = True

        if not ("<" in self._redis_url or self._redis_url.startswith("secret:")):
            try:
                import redis.asyncio as aioredis

                self._redis = aioredis.from_url(
                    self._redis_url, decode_responses=True
                )
                await self._redis.ping()
                log.info("synthesizer_redis_connected")
            except Exception as e:
                log.warning("synthesizer_redis_unavailable", error=str(e))
                self._redis = None

        log.info("signal_synthesizer_started")

    async def stop(self) -> None:
        """Stop the synthesizer."""
        self._running = False
        if self._redis:
            await self._redis.close()
        log.info("signal_synthesizer_stopped", stats=self._stats)

    async def process_heartbeat(
        self,
        heartbeat: NobleTraderHeartbeat,
        equity: float = 100000,
        portfolio_drawdown_pct: float = 0.0,
        current_gross_exposure_usd: float = 0.0,
    ) -> BlendedSignal:
        """
        Process a Noble Trader heartbeat and produce a blended signal.

        Args:
            heartbeat: Validated NT heartbeat
            equity: Current account equity (from portfolio service)
            portfolio_drawdown_pct: Current portfolio DD (0.0 = no DD)
            current_gross_exposure_usd: Current total exposure

        Returns:
            BlendedSignal with entry/execution decision
        """
        self._stats["heartbeats_processed"] += 1
        sym = heartbeat.symbol

        # 1. Get or create renko constructor for this symbol
        if sym not in self._renko_constructors:
            self._renko_constructors[sym] = RenkoConstructor(
                brick_size=heartbeat.brick_size,
                symbol=sym,
                venue=Venue.HYPERLIQUID if sym.endswith("-PERP") else Venue.ALPACA,
            )
        else:
            # Update brick size if NT changed it
            self._renko_constructors[sym].update_brick_size(heartbeat.brick_size)

        renko = self._renko_constructors[sym]

        # 2. Feed recent ticks to renko constructor (if monitor available).
        # CRITICAL: only feed bars NEWER than the last one we already fed. The
        # synthesizer runs on every L4 cycle, so re-feeding the same last 100 bars
        # every pass DOUBLE-COUNTS volume and rebuilds bricks from duplicate ticks.
        if self._monitor:
            last_fed = self._renko_last_ts.get(sym)
            bars = self._monitor.get_bars(sym, "1s", n=500)
            for bar in bars[-100:]:  # candidate window
                bar_ts = bar.ts_close or bar.ts_open
                if last_fed is not None and bar_ts <= last_fed:
                    continue  # already fed this bar in a prior cycle
                renko.on_tick(Tick(
                    ts=bar_ts,
                    venue=bar.venue,
                    symbol=sym,
                    price=bar.close,
                    size=bar.volume,
                ))
                self._renko_last_ts[sym] = bar_ts

        # 3. Classify meta-regime
        # Gather inputs from monitor if available
        cross_asset_corr = None
        funding_pct = None
        book_depth_pct = None

        if self._monitor:
            corr_matrix = self._monitor.get_correlation_matrix()
            if corr_matrix:
                # Compute mean |correlation| across pairs
                all_corrs = []
                for sym_a in corr_matrix:
                    for sym_b in corr_matrix[sym_a]:
                        if sym_a < sym_b and corr_matrix[sym_a][sym_b] is not None:
                            all_corrs.append(abs(corr_matrix[sym_a][sym_b]))
                if all_corrs:
                    cross_asset_corr = sum(all_corrs) / len(all_corrs)

            funding = self._monitor.get_current_funding(sym)
            if funding:
                funding_pct = funding.annualized_pct

        meta_result = self._meta_regime.classify(
            heartbeat=heartbeat,
            symbol=sym,
            cross_asset_corr_mean=cross_asset_corr,
            funding_annualized_pct=funding_pct,
            book_depth_percentile=book_depth_pct,
            upstream_regime_shift=(heartbeat.regime_shift == "true"),
        )

        # 4. Analyze brick pattern
        bricks = renko.get_bricks(n=20)
        brick_pattern = self._pattern_analyzer.classify(bricks)

        # 5. Entry timing decision
        current_price = renko.get_last_price() or heartbeat.entry_price
        entry_decision = self._entry_optimizer.decide(
            meta_regime=meta_result,
            brick_pattern=brick_pattern,
            nt_signal=heartbeat.signal,
            current_price=current_price,
            nt_entry_price=heartbeat.entry_price,
            bricks=bricks,
        )

        # 6. Check if blocked
        if entry_decision.strategy in ("block", "skip_entry"):
            self._stats["signals_blocked"] += 1
            signal = BlendedSignal(
                signal_id=str(uuid4()),
                symbol=sym,
                venue="hyperliquid" if sym.endswith("-PERP") else "alpaca",
                direction=heartbeat.signal,
                nt_entry_price=heartbeat.entry_price,
                nt_stop_price=heartbeat.stop_loss,
                nt_target_price=heartbeat.take_profit,
                nt_effective_kelly=heartbeat.effective_kelly,
                nt_brick_size=heartbeat.brick_size,
                meta_regime=meta_result.state,
                meta_regime_confidence=meta_result.confidence,
                sizing_multiplier=meta_result.sizing_multiplier,
                entry_strategy=entry_decision.strategy,
                execution_method=entry_decision.execution_method,
                final_size_usd=0.0,
                final_size_pct=0.0,
                risk_amount_usd=0.0,
                brick_pattern=brick_pattern.value,
                expected_entry_alpha_bps=0.0,
                sizing_reason=entry_decision.reason,
                config_hash=get_config_hash(self._config),
            )
            await self._write_and_publish(signal)
            return signal

        # 7. Sizing
        stop_distance_pct = abs(heartbeat.entry_price - heartbeat.stop_loss) / heartbeat.entry_price
        sizing_result = self._sizing.compute(
            equity=equity,
            nt_effective_kelly=heartbeat.effective_kelly,
            meta_regime=meta_result,
            portfolio_drawdown_pct=portfolio_drawdown_pct,
            current_gross_exposure_usd=current_gross_exposure_usd,
            stop_distance_pct=stop_distance_pct,
        )

        # 8. Execution method
        venue_supports_post_only = "hyperliquid" in sym or sym.endswith("-PERP")
        execution_method = self._execution_optimizer.select(
            entry_decision=entry_decision,
            position_size_usd=sizing_result.final_size_usd,
            meta_regime_state=meta_result.state,
            venue_supports_post_only=venue_supports_post_only,
        )

        # 9. Build blended signal
        signal = BlendedSignal(
            signal_id=str(uuid4()),
            symbol=sym,
            venue="hyperliquid" if sym.endswith("-PERP") else "alpaca",
            direction=heartbeat.signal,
            nt_entry_price=heartbeat.entry_price,
            nt_stop_price=heartbeat.stop_loss,
            nt_target_price=heartbeat.take_profit,
            nt_effective_kelly=heartbeat.effective_kelly,
            nt_brick_size=heartbeat.brick_size,
            meta_regime=meta_result.state,
            meta_regime_confidence=meta_result.confidence,
            sizing_multiplier=meta_result.sizing_multiplier,
            entry_strategy=entry_decision.strategy,
            execution_method=execution_method,
            entry_price_target=entry_decision.entry_price_target,
            limit_price=entry_decision.limit_price,
            final_size_usd=sizing_result.final_size_usd,
            final_size_pct=sizing_result.final_size_pct_of_equity,
            risk_amount_usd=sizing_result.risk_amount_usd,
            brick_pattern=brick_pattern.value,
            expected_entry_alpha_bps=entry_decision.expected_entry_alpha_bps,
            sizing_limits_hit=sizing_result.limits_hit,
            sizing_reason=sizing_result.reason,
            config_hash=get_config_hash(self._config),
        )

        self._stats["signals_produced"] += 1
        await self._write_and_publish(signal)

        log.info(
            "blended_signal_produced",
            signal_id=signal.signal_id,
            symbol=sym,
            direction=signal.direction,
            meta_regime=signal.meta_regime,
            entry_strategy=signal.entry_strategy,
            execution_method=signal.execution_method,
            final_size_usd=signal.final_size_usd,
            brick_pattern=signal.brick_pattern,
        )

        return signal

    async def _write_and_publish(self, signal: BlendedSignal) -> None:
        """Write to DuckDB + publish to Redis."""
        # Write to DuckDB
        await asyncio.get_event_loop().run_in_executor(
            None, self._write_to_duckdb, signal
        )

        # Publish to Redis
        if self._redis:
            try:
                channel = f"signal.blended.{signal.symbol}"
                payload = signal.model_dump(mode="json")
                await self._redis.publish(channel, json.dumps(payload, default=str))
            except Exception as e:
                log.warning("signal_publish_failed", error=str(e))

    def _write_to_duckdb(self, signal: BlendedSignal) -> None:
        """Write blended signal to DuckDB."""
        import duckdb

        try:
            with duckdb.connect(str(self._db_path)) as conn:
                conn.execute(
                    """
                    INSERT INTO trade_signals_blended (
                        signal_id, ts_emitted, symbol, venue, direction,
                        nt_entry_price, nt_stop_price, nt_target_price,
                        nt_effective_kelly, nt_brick_size,
                        meta_regime, meta_regime_confidence, sizing_multiplier,
                        entry_strategy, execution_method,
                        entry_price_target, limit_price,
                        final_size_usd, final_size_pct, risk_amount_usd,
                        brick_pattern, expected_entry_alpha_bps,
                        sizing_limits_hit, sizing_reason,
                        autonomy_tier, config_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        signal.signal_id,
                        signal.ts,
                        signal.symbol,
                        signal.venue,
                        signal.direction,
                        signal.nt_entry_price,
                        signal.nt_stop_price,
                        signal.nt_target_price,
                        signal.nt_effective_kelly,
                        signal.nt_brick_size,
                        signal.meta_regime,
                        signal.meta_regime_confidence,
                        signal.sizing_multiplier,
                        signal.entry_strategy,
                        signal.execution_method,
                        signal.entry_price_target,
                        signal.limit_price,
                        signal.final_size_usd,
                        signal.final_size_pct,
                        signal.risk_amount_usd,
                        signal.brick_pattern,
                        signal.expected_entry_alpha_bps,
                        signal.sizing_limits_hit,
                        signal.sizing_reason,
                        signal.autonomy_tier,
                        signal.config_hash,
                    ],
                )
        except Exception as e:
            log.error("signal_duckdb_write_failed", error=str(e))

    def get_meta_regime_classifier(self) -> MetaRegimeClassifier:
        return self._meta_regime

    def get_renko_constructor(self, symbol: str) -> RenkoConstructor | None:
        return self._renko_constructors.get(symbol)

    def get_stats(self) -> dict[str, Any]:
        stats = self._stats.copy()
        stats["meta_regime"] = self._meta_regime.get_stats()
        return stats
