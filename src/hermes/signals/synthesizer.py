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
import math
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


# ── v5 4-source logit-pool P_win blend (Phase C) ───────────────────
# Ported from EV-SYSTEM-REWORK-DESIGN-v2.md §4.1. The agent re-blends
# the four P_win sources locally so it can substitute its own
# pattern_performance (Wilson lower bound) for the server's p_pattern
# (which is always 0.5 — the server has no trade journal).
#
# Logit pooling: logit(P_win) = Σ w_i × logit(p_i)
# - Respects probability bounds (no source can push P_win to 0 or 1
#   single-handedly).
# - Weights are renormalised at blend time over the available sources.
# - Missing sources are NOT substituted with 0.5 — they are dropped
#   and the remaining weights rescaled to sum to 1.0. This prevents
#   a missing source from diluting the signal toward 0.5.

_LOGIT_CLAMP = 1e-15


def _logit(p: float) -> float:
    p = max(_LOGIT_CLAMP, min(1.0 - _LOGIT_CLAMP, p))
    return math.log(p / (1.0 - p))


def _inv_logit(x: float) -> float:
    if x > 0:
        return 1.0 / (1.0 + math.exp(-x))
    ex = math.exp(x)
    return ex / (1.0 + ex)


# Default 4-source weights — overridden by heartbeat.weights_used when present
P_WIN_WEIGHTS_AGENT_DEFAULT: dict[str, float] = {
    "p_pattern": 0.30,
    "p_regime": 0.25,
    "p_markov": 0.20,
    "p_timesfm": 0.25,
}


def _normalise_source_key(k: str) -> str:
    """Normalise source key between backend and agent conventions.

    Backend payload uses both 'p_markov' and 'p_markov_hold_n'
    depending on version. Agent internally uses 'p_markov'.
    """
    if k in ("p_markov_hold_n", "p_markov"):
        return "p_markov"
    return k


# ── HIGH #8 (2026-07-23): single-step Markov T ──────────────────────
def _compute_markov_persistence(
    p_markov_single_step: float | None,
    transition_matrix: dict[str, dict[str, float]] | None,
    current_state: str,
    direction: str,
    p_markov_tn_fallback: float,
) -> float:
    """Resolve the single-step Markov persistence probability.

    Used by the decision tree's adaptive-threshold check
    (``markov_persistence > 0.7`` ⇒ trending regime). See the
    synthesizer's call site for the resolution-order rationale.

    Args:
        p_markov_single_step: backend-sent single-step T[current][target].
            None or 0.5 default for pre-HIGH-#8 heartbeats.
        transition_matrix: 3x3 dict-of-dicts keyed by {UP, DOWN, FLAT}.
            Sent by backend (HIGH #8); None for pre-HIGH-#8 heartbeats.
        current_state: one of "UP", "DOWN", "FLAT".
        direction: "buy" (target=UP), "sell" (target=DOWN), or "neutral".
        p_markov_tn_fallback: T^N multi-step hold probability, used as
            last-resort fallback for pre-HIGH-#8 heartbeats.

    Returns:
        Single-step T[current][target] in [0, 1]. Falls back to
        ``p_markov_tn_fallback`` if neither explicit single-step value
        nor a computable transition matrix is available.
    """
    # Tier 1: explicit single-step value from backend (HIGH #8 path).
    # The backend sends 0.5 (neutral) when the Markov chain build fails,
    # which is a real value — we accept it as-is. We only fall through
    # if the field is missing entirely (None) or non-finite (NaN/inf).
    if p_markov_single_step is not None:
        try:
            import math as _math
            v = float(p_markov_single_step)
            if _math.isfinite(v):
                # Clamp to [0, 1] defensively.
                return max(0.0, min(1.0, v))
            # NaN / inf → fall through to tier 2
        except (TypeError, ValueError):
            pass  # fall through to tier 2

    # Tier 2: compute locally from the transition matrix.
    if transition_matrix and current_state:
        target = "UP" if direction == "buy" else "DOWN" if direction == "sell" else None
        if target:
            try:
                row = transition_matrix.get(current_state, {})
                v = float(row.get(target, 0.5))
                return max(0.0, min(1.0, v))
            except (TypeError, ValueError, AttributeError):
                pass  # fall through to tier 3

    # Tier 3: pre-HIGH-#8 fallback. Use the T^N value as a proxy.
    # Documented as conservative-but-wrong in mean-reverting regimes;
    # preserved for backtest replay of historical heartbeats.
    try:
        return max(0.0, min(1.0, float(p_markov_tn_fallback)))
    except (TypeError, ValueError):
        return 0.5  # ultimate fallback


def compute_blended_p_win(
    p_pattern: float,
    p_regime: float,
    p_markov_hold_n: float,
    p_timesfm: float | None,
    sources_used: list[str] | None = None,
    weights_used: dict[str, float] | None = None,
) -> tuple[float, list[str]]:
    """4-source logit-pooled P_win. Returns (p_win_agent, sources_blended).

    Honours the backend's sources_used + weights_used: if the backend
    dropped a source (e.g. TimesFM unreachable), the agent drops the
    same source and uses the backend's renormalised weights — otherwise
    re-applying the default weights double-counts the gap.

    The agent's local p_pattern (from pattern_performance via Wilson
    lower bound) ALWAYS overrides the backend's p_pattern (which is
    always 0.5 because the server has no trade journal).

    Args:
        p_pattern: Agent's local Wilson-confident pattern win-rate.
        p_regime: Backend's HMM-regime posterior P_win component.
        p_markov_hold_n: Backend's T^N multi-step hold probability.
        p_timesfm: Backend's TimesFM directional forecast, or None.
        sources_used: Backend's reported available-source list.
        weights_used: Backend's reported renormalised weights.

    Returns:
        Tuple of (p_win_agent in [0,1], sources_blended list).
    """
    # Agent-side source values (after override of p_pattern)
    agent_values: dict[str, float] = {
        "p_pattern": p_pattern,
        "p_regime": p_regime,
        "p_markov": p_markov_hold_n,
    }
    if p_timesfm is not None:
        agent_values["p_timesfm"] = p_timesfm

    if sources_used and weights_used:
        # Backend sent source info — honour its available-source set.
        # Re-normalise the backend's weights over the intersection of
        # (backend's available sources) and (agent's available sources).
        normalised_backend_sources = {
            _normalise_source_key(k) for k in sources_used
        }
        available_keys = [
            k for k in agent_values
            if k in normalised_backend_sources
        ]
        if not available_keys:
            # No overlap — fall back to agent defaults
            weights = {
                k: P_WIN_WEIGHTS_AGENT_DEFAULT.get(k, 0.0)
                for k in agent_values
            }
            total_w = sum(weights.values())
            if total_w > 0:
                weights = {k: v / total_w for k, v in weights.items()}
            else:
                weights = {k: 1.0 / len(agent_values) for k in agent_values}
            available_keys = list(agent_values.keys())
        else:
            # Map backend weight keys to agent-internal keys before lookup
            mapped_weights = {
                _normalise_source_key(k): v for k, v in weights_used.items()
            }
            weights = {k: mapped_weights.get(k, 0.0) for k in available_keys}
            total_w = sum(weights.values())
            if total_w > 0:
                weights = {k: v / total_w for k, v in weights.items()}
            else:
                weights = {k: 1.0 / len(available_keys) for k in available_keys}
    else:
        # Backend didn't send source info — use agent defaults
        weights = {
            k: P_WIN_WEIGHTS_AGENT_DEFAULT.get(k, 0.0)
            for k in agent_values
        }
        total_w = sum(weights.values())
        if total_w > 0:
            weights = {k: v / total_w for k, v in weights.items()}
        else:
            weights = {k: 1.0 / len(agent_values) for k in agent_values}
        available_keys = list(agent_values.keys())

    if not available_keys:
        return 0.5, []

    logit_sum = sum(
        weights[k] * _logit(agent_values[k]) for k in available_keys
    )
    p_win = _inv_logit(logit_sum)
    return p_win, available_keys


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
    pattern_confidence: float = 0.0  # learned Wilson confidence for brick_pattern (0-1)
    expected_entry_alpha_bps: float
    sizing_limits_hit: list[str] = Field(default_factory=list)
    sizing_reason: str = ""

    # Autonomy
    autonomy_tier: int = 0

    # Config
    config_hash: str = ""

    # ── v5: 4-source blended EV (Phase C) ──────────────────────────
    # p_win_agent is the agent's locally re-blended P_win via
    # compute_blended_p_win. It uses the backend's per-source
    # breakdown (p_regime, p_markov, p_timesfm) but OVERRIDES
    # p_pattern with the agent's own Wilson-confident
    # pattern_performance value (the one source the server cannot
    # know). Downstream (decision tree, sizing, risk gate) should
    # prefer p_win_agent over heartbeat.p_win for conviction checks.
    p_win_agent: float = 0.5
    # Single-step Markov persistence T[current][target]. Used by the
    # decision tree to adapt TP/SL thresholds: strong persistence
    # (>0.7) widens trailing stops; weak persistence (<0.55) takes
    # early profit. Approximated by heartbeat.p_markov (which is
    # actually T^N, not single-step) until backend sends a separate
    # single-step field.
    markov_persistence: float = 0.5
    # T^N multi-step hold probability (N=tp_bricks). Same value as
    # heartbeat.p_markov; renamed at this layer for clarity.
    markov_hold_n: float = 0.5
    # Backend's pre-blended P_win (heartbeat.p_win). Preserved for
    # audit / calibration comparison vs p_win_agent and vs the
    # realized trade outcome.
    p_win_server: float = 0.5
    # The Wilson lower-bound pattern win-rate the agent actually
    # used in its local blend. 0.5 when pattern_performance has no
    # data for this brick_pattern.
    p_pattern_local: float = 0.5
    # Which of the 4 sources were available for the local blend
    # (after agent override of p_pattern + backend's sources_used
    # intersection).
    sources_blended: list[str] = Field(default_factory=list)


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
        microstructure_source=None,  # MicrostructureSSEConsumer (HIGH #9, audit 2026-07-22)
    ) -> None:
        self._config = config
        self._db_path = get_duckdb_path(config)
        self._monitor = price_monitor
        # HIGH #9 (2026-07-22): optional SSE consumer for p_microstructure.
        # When None, p_microstructure is None in classify() and the
        # MetaRegimeClassifier runs without microstructure input (degraded
        # but functional — preserves pre-HIGH-#9 behavior).
        self._microstructure_source = microstructure_source

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

        # ── Bayesian alpha engine (v5) ─────────────────────────────
        # Rolling trade-outcome posterior → position-sizing modulator.
        # NEVER a gate — alpha in [0.10, 1.0] scales the baseline size.
        # Computed from local DuckDB pnl_realized (agent source of truth).
        # Lazy-imported to avoid hard dependency at module load time.
        try:
            from hermes.agent.bayesian_alpha import BayesianAlpha
            _alpha_cfg = getattr(config, "bayesian_alpha", None) or {}
            self._alpha_engine = BayesianAlpha(
                db_path=self._db_path,
                alpha_floor=_alpha_cfg.get("alpha_floor", 0.10),
                alpha_ceiling=_alpha_cfg.get("alpha_ceiling", 1.0),
                lookback_trades=_alpha_cfg.get("lookback_trades", 30),
                decay=_alpha_cfg.get("decay", 0.95),
                cache_ttl_s=_alpha_cfg.get("cache_ttl_s", 30.0),
            )
            log.info("bayesian_alpha_engine_initialised", db_path=str(self._db_path))
        except Exception as exc:
            log.warning("bayesian_alpha_init_failed", error=str(exc))
            self._alpha_engine = None

        # Per-symbol renko constructors (keyed by symbol)
        self._renko_constructors: dict[str, RenkoConstructor] = {}
        # Last bar ts fed to each renko constructor (prevents double-counting volume
        # when synthesize() runs repeatedly on the same monitor window).
        self._renko_last_ts: dict[str, Any] = {}
        self._pattern_analyzer = BrickPatternAnalyzer(lookback=10)

        # Per-symbol last seen meta-regime state — used by the
        # meta_regime_history writer to detect state transitions
        # (inserts a row only when state actually changes).
        self._meta_regime_last_state: dict[str, str] = {}

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
        # [SOFT-DEPRECATED P3.5] book_depth_pct is intentionally hardcoded to
        # None here. L2 order-book depth was deprecated in P3.5 (the upstream
        # WebSocket feed was decommissioned), so there is no live source for
        # this field anymore. The MetaRegimeClassifier.classify() signature
        # still accepts it for backward compat with backtest replay, but in
        # production it is dead weight. The replacement microstructure input
        # is `p_microstructure` (see ~10 lines below + meta_regime.py:128-148
        # + HIGH #9 which wired the SSE consumer as the live source). Do NOT
        # "fix" this by re-wiring L2 — that feed no longer exists.
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

        # HIGH #9 (2026-07-22): pull p_microstructure from the SSE consumer
        # if wired. Returns None when not configured or stale — the
        # MetaRegimeClassifier treats None as "no microstructure input"
        # and skips the confidence adjustment.
        p_microstructure: float | None = None
        if self._microstructure_source is not None:
            try:
                p_microstructure = self._microstructure_source.get_p_microstructure(sym)
            except Exception:
                p_microstructure = None

        meta_result = self._meta_regime.classify(
            heartbeat=heartbeat,
            symbol=sym,
            cross_asset_corr_mean=cross_asset_corr,
            funding_annualized_pct=funding_pct,
            book_depth_percentile=book_depth_pct,
            upstream_regime_shift=(heartbeat.regime_shift == "true"),
            p_microstructure=p_microstructure,
        )

        # ── M1-M10 open-issue fix: persist meta-regime state transitions ──
        # Fire-and-forget write to meta_regime_history table. Only inserts a
        # row when the new state differs from the previously-seen state for
        # this symbol (or first observation for this symbol). Failures are
        # swallowed inside the writer so they never break signal processing.
        try:
            new_state = str(meta_result.state)
            prev_state = self._meta_regime_last_state.get(sym)
            if prev_state != new_state:
                from hermes.signals.meta_regime_history_writer import (
                    record_meta_regime_transition,
                )
                trigger = "initial" if prev_state is None else "shift"
                record_meta_regime_transition(
                    db_path=self._db_path,
                    symbol=sym,
                    prev_state=prev_state,
                    new_state=new_state,
                    confidence=float(meta_result.confidence),
                    posterior_probs=dict(meta_result.posterior_probs or {}),
                    upstream_regime=meta_result.upstream_regime,
                    upstream_regime_conf=meta_result.upstream_regime_conf,
                    trigger=trigger,
                    trigger_detail=dict(meta_result.trigger_detail or {}),
                    extra_cols={
                        "cross_asset_corr_mean": cross_asset_corr,
                        "funding_rate_8h": funding_pct,
                        "book_depth_percentile": book_depth_pct,
                    },
                )
                self._meta_regime_last_state[sym] = new_state
        except Exception as _mrh_exc:  # noqa: BLE001
            log.debug(
                "meta_regime_history_dispatch_failed",
                symbol=sym,
                error=str(_mrh_exc)[:120],
            )

        # 4. Analyze brick pattern
        bricks = renko.get_bricks(n=20)
        brick_pattern = self._pattern_analyzer.classify(bricks)

        # 4b. Learn->live loop: read the *learned* confidence for this pattern
        # (aggregated from executed + sim outcomes by pattern_learning). High
        # confidence reinforces the live decision; low/unknown leaves it neutral.
        pattern_conf = 0.0
        try:
            from hermes.agent.pattern_learning import get_pattern_confidence

            pc = get_pattern_confidence(brick_pattern.value, config=self._config)
            if pc is not None:
                pattern_conf = float(pc)
        except Exception:
            pass
        # Bounded conviction boost: scale meta_regime_confidence toward the
        # learned pattern confidence (max +0.15), so a battle-tested pattern
        # nudges the live entry without overriding the primary signals.
        regime_conf = meta_result.confidence
        if pattern_conf >= 0.6 and meta_result.state not in ("unknown",):
            regime_conf = min(1.0, regime_conf + (pattern_conf - 0.5) * 0.3)
            regime_conf = min(regime_conf, meta_result.confidence + 0.15)

        # ── HIGH #6 (2026-07-22): Apply calibration_bias shrink to live
        # regime_conf. Schema docstring (heartbeat.py:135-138) says:
        # "Positive calibration_bias = overconfident model; agent should
        # shrink p_regime toward 0.5." Backtest applies the same field
        # as a linear subtraction on raw p_win (engine.py:271); the live
        # path uses the documented multiplicative shrink on regime_conf
        # — this avoids double-correcting the 4-source blend, which
        # would distort the relative weights of markov/regime/pattern/
        # timesfm in compute_blended_p_win(). Only positive bias
        # (overconfident) triggers the shrink; negative bias
        # (underconfident) is left to the backtest's subtractive path.
        if heartbeat.calibration_bias is not None and heartbeat.calibration_bias > 0:
            regime_conf = 0.5 + (regime_conf - 0.5) * max(0.0, 1.0 - heartbeat.calibration_bias)

        # ── v5: 4-source logit-pool P_win re-blend (Phase C) ────────
        # The agent re-blends the four P_win sources locally, overriding
        # the server's p_pattern (always 0.5 — no trade journal on
        # server) with the local Wilson-confident pattern_performance
        # value. The result, p_win_agent, is the canonical P_win used
        # by downstream consumers (decision tree conviction check,
        # sizing, risk gate). The backend's pre-blended p_win is
        # preserved on BlendedSignal.p_win_server for audit.
        p_pattern_local = pattern_conf if pattern_conf > 0 else 0.5
        try:
            p_win_agent, sources_blended = compute_blended_p_win(
                p_pattern=p_pattern_local,
                p_regime=heartbeat.p_regime,
                p_markov_hold_n=heartbeat.p_markov,
                p_timesfm=heartbeat.p_timesfm,
                sources_used=heartbeat.sources_used,
                weights_used=heartbeat.weights_used,
            )
        except Exception as exc:
            log.warning(
                "compute_blended_p_win_failed",
                symbol=sym, error=str(exc),
            )
            # Fall back to server's pre-blended p_win
            p_win_agent = heartbeat.p_win
            sources_blended = []
        # ── HIGH #8 (2026-07-23): single-step Markov T ──────────────
        # The decision tree's adaptive thresholds check
        # `markov_persistence > 0.7` to detect trending regimes and
        # switch to the "let winners run" branch. Previously the
        # synthesizer used heartbeat.p_markov (the T^N multi-step hold
        # probability) as a proxy for single-step T[current][target].
        # That approximation was conservative-but-wrong in mean-reverting
        # regimes where T^N can be high while single-step T is low.
        #
        # Resolution order (highest precedence first):
        #   1. heartbeat.p_markov_single_step — sent by backend (HIGH #8)
        #   2. locally computed from heartbeat.markov_transition_matrix +
        #      heartbeat.markov_current_state + direction
        #   3. heartbeat.p_markov (T^N value) — pre-HIGH-#8 fallback,
        #      preserved for backtest replay of historical heartbeats
        #      that don't have the new fields.
        #
        # markov_hold_n is ALWAYS heartbeat.p_markov (T^N value) — that
        # field's semantics didn't change, only `markov_persistence` did.
        markov_hold_n = heartbeat.p_markov
        markov_persistence = _compute_markov_persistence(
            p_markov_single_step=heartbeat.p_markov_single_step,
            transition_matrix=heartbeat.markov_transition_matrix,
            current_state=heartbeat.markov_current_state,
            direction=heartbeat.signal,
            p_markov_tn_fallback=heartbeat.p_markov,
        )

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
                meta_regime_confidence=regime_conf,
                sizing_multiplier=meta_result.sizing_multiplier,
                entry_strategy=entry_decision.strategy,
                execution_method=entry_decision.execution_method,
                final_size_usd=0.0,
                final_size_pct=0.0,
                risk_amount_usd=0.0,
                brick_pattern=brick_pattern.value,
                pattern_confidence=pattern_conf,
                expected_entry_alpha_bps=0.0,
                sizing_reason=entry_decision.reason,
                config_hash=get_config_hash(self._config),
                # ── v5 EV fields (Phase C) ───────────────────────────
                p_win_agent=p_win_agent,
                markov_persistence=markov_persistence,
                markov_hold_n=markov_hold_n,
                p_win_server=heartbeat.p_win,
                p_pattern_local=p_pattern_local,
                sources_blended=sources_blended,
            )
            await self._write_and_publish(signal)
            return signal

        # 7. Sizing
        stop_distance_pct = abs(heartbeat.entry_price - heartbeat.stop_loss) / heartbeat.entry_price

        # ── Bayesian alpha (v5) ────────────────────────────────────
        # Compute rolling alpha from local DuckDB trade outcomes.
        # Alpha is a MODULATOR in [0.10, 1.0] — never blocks trading.
        # When alpha_engine is unavailable (init failure) or the symbol
        # has no trade history, alpha defaults to 1.0 (no modulation).
        alpha_value = 1.0
        if self._alpha_engine is not None:
            try:
                alpha_result = self._alpha_engine.compute_alpha(sym)
                alpha_value = alpha_result.alpha
                log.debug(
                    "bayesian_alpha_computed",
                    symbol=sym,
                    alpha=alpha_result.alpha,
                    posterior_mean=alpha_result.posterior_mean,
                    n_trades=alpha_result.n_trades_used,
                    wins=alpha_result.wins,
                    losses=alpha_result.losses,
                    reason=alpha_result.reason,
                )
            except Exception as exc:
                log.warning("bayesian_alpha_compute_failed", symbol=sym, error=str(exc))
                alpha_value = 1.0

        sizing_result = self._sizing.compute(
            equity=equity,
            nt_effective_kelly=heartbeat.effective_kelly,
            meta_regime=meta_result,
            portfolio_drawdown_pct=portfolio_drawdown_pct,
            current_gross_exposure_usd=current_gross_exposure_usd,
            stop_distance_pct=stop_distance_pct,
            alpha=alpha_value,
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
            meta_regime_confidence=regime_conf,
            sizing_multiplier=meta_result.sizing_multiplier,
            entry_strategy=entry_decision.strategy,
            execution_method=execution_method,
            entry_price_target=entry_decision.entry_price_target,
            limit_price=entry_decision.limit_price,
            final_size_usd=sizing_result.final_size_usd,
            final_size_pct=sizing_result.final_size_pct_of_equity,
            risk_amount_usd=sizing_result.risk_amount_usd,
            brick_pattern=brick_pattern.value,
            pattern_confidence=pattern_conf,
            expected_entry_alpha_bps=entry_decision.expected_entry_alpha_bps,
            sizing_limits_hit=sizing_result.limits_hit,
            sizing_reason=sizing_result.reason,
            config_hash=get_config_hash(self._config),
            # ── v5 EV fields (Phase C) ───────────────────────────────
            p_win_agent=p_win_agent,
            markov_persistence=markov_persistence,
            markov_hold_n=markov_hold_n,
            p_win_server=heartbeat.p_win,
            p_pattern_local=p_pattern_local,
            sources_blended=sources_blended,
        )

        self._stats["signals_produced"] += 1
        await self._write_and_publish(signal)

        # Signal-driven urgency + simulation hook (Fix B). When an actionable
        # heartbeat arrives we:
        #   - publish monitor.control.{symbol} so the live monitor can escalate
        #     its tick cadence for that symbol (ACTIVE=5s burst, WATCH=15s);
        #   - publish sim.request.{symbol} so the optimization watcher can run an
        #     on-demand simulation for that symbol immediately (instead of waiting
        #     for the 30-min watcher cadence).
        await self._publish_signal_control(heartbeat)

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

    def _get_trade_flag(self, heartbeat: NobleTraderHeartbeat) -> bool | None:
        """Resolve the `trade` intent from the heartbeat.

        The schema has no explicit `trade` field, but `extra=\"allow\"` lets the
        EA/NT embed `trade: true|false`. If absent, infer from signal direction
        (buy/sell => actionable, neutral => analysis-only).
        """
        extra = getattr(heartbeat, "__pydantic_extra__", None) or {}
        tf = extra.get("trade")
        if isinstance(tf, bool):
            return tf
        if isinstance(tf, str):
            return tf.strip().lower() in ("1", "true", "yes")
        return None  # unknown -> infer below

    async def _publish_signal_control(self, heartbeat: NobleTraderHeartbeat) -> None:
        """Publish urgency + on-demand-sim control messages for this symbol."""
        if not self._redis:
            return
        sym = heartbeat.symbol
        trade_flag = self._get_trade_flag(heartbeat)
        actionable = trade_flag is True or (trade_flag is None and heartbeat.signal in ("buy", "sell"))
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            if actionable:
                # Escalate monitor cadence (ACTIVE = 5s burst) + request a sim.
                await self._redis.publish(
                    f"monitor.control.{sym}",
                    json.dumps({"tier": "ACTIVE", "ttl": 300, "ts": now_ms, "source": "synthesize"}),
                )
                await self._redis.publish(
                    f"sim.request.{sym}",
                    json.dumps({"urgent": True, "ts": now_ms, "source": "synthesize"}),
                )
                log.info("signal_control_active", symbol=sym)
            else:
                # Analysis-only: WATCH tier (15s), no sim request.
                await self._redis.publish(
                    f"monitor.control.{sym}",
                    json.dumps({"tier": "WATCH", "ttl": 300, "ts": now_ms, "source": "synthesize"}),
                )
                log.info("signal_control_watch", symbol=sym)
        except Exception as e:
            log.warning("signal_control_publish_failed", symbol=sym, error=str(e))

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
                        brick_pattern, pattern_confidence, expected_entry_alpha_bps,
                        sizing_limits_hit, sizing_reason,
                        autonomy_tier, config_hash,
                        p_win_agent, markov_persistence, markov_hold_n,
                        p_win_server, p_pattern_local
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        signal.pattern_confidence,
                        signal.expected_entry_alpha_bps,
                        signal.sizing_limits_hit,
                        signal.sizing_reason,
                        signal.autonomy_tier,
                        signal.config_hash,
                        # ── v5 EV fields (Phase C, migration 017) ────────
                        signal.p_win_agent,
                        signal.markov_persistence,
                        signal.markov_hold_n,
                        signal.p_win_server,
                        signal.p_pattern_local,
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
