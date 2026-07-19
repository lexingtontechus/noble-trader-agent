"""GC — user-initiated trade branch (analysis/sim -> reject/approve at L3).

Usecase 2: a user asks Hermes to BUY/SELL an asset that has NO Redis signal and
may not even be in the portfolio. Previously the codebase had NO code path for this
(L3 `execute` silently skipped any decision with no matching trade_signals_blended
row). This module closes that gap:

  1. ALWAYS run a simulation (require_sim=True is HARD-CODED, non-configurable — a
     configurable skip would break the pricing/monitor/backtest processes that depend
     on the sim output). The sim is bounded by `max_sim_latency_sec` (default 60s;
     not HFT).
  2. Build a BlendedSignal from the sim outcome (entry/stop/target, expected alpha,
     dominant pattern), sized to the current autonomy caps (normal $2000/1.5% or
     cold-start $100/0.2%).
  3. Route the signal through the SAME L5 gate (PortfolioRiskEngine.evaluate_signal)
     — so GD (unsupported venue), GB (selection), GA (cold-start), and the autonomy
     tier all apply identically to user-initiated and signal-driven trades.
  4. Return the RiskDecision. If approved, the caller publishes it to risk.decision.*
     (L3 executes). If rejected, the reason explains why (sim failed, risk gate,
     autonomy tier-3 pending, etc.).

The sim call is injectable so tests can use a fast fake instead of the real
historical backtest.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Callable

import structlog

from hermes.signals.synthesizer import BlendedSignal

log = structlog.get_logger(__name__)

# Hard-coded: user-initiated trades MUST be simulated. Never configurable.
REQUIRE_SIM = True
DEFAULT_MAX_SIM_LATENCY_SEC = 60.0


async def _default_sim(symbol: str, venue: str, side: str, config: Any) -> dict:
    """Real sim: bounded entry-timing sweep via RenkoSimulationEngine.

    Bounded to `days_back`/`n_trials` small enough to finish within the latency
    budget. Returns a dict the builder consumes.
    """
    from hermes.backtest.optimizer import RenkoSimulationEngine

    engine = RenkoSimulationEngine(config)
    # Bounded sweep: short window + few trials so it fits the latency budget.
    results = await asyncio.wait_for(
        engine.run_entry_timing_sweep(
            symbols=[symbol],
            days_back=30,
            n_trials=20,
        ),
        timeout=DEFAULT_MAX_SIM_LATENCY_SEC,
    )
    best = max((r for r in results if not r.error), key=lambda r: r.sharpe, default=None)
    if best is None:
        return {"ok": False, "reason": "sim_produced_no_tradeable_result"}
    return {
        "ok": True,
        "entry_alpha_bps": float(best.entry_alpha_bps or 0.0),
        "sharpe": float(best.sharpe or 0.0),
        "net_pnl_bps": float((best.net_pnl_usd or 0.0) / 100000.0 * 10000),
        "pattern": "",  # filled by caller from blended signal history if available
    }


def _build_signal(
    symbol: str,
    venue: str,
    side: str,
    sim: dict,
    equity: float,
    size_cap_usd: float,
    size_cap_pct: float,
    entry_price: float = 0.0,
    config_hash: str = "",
) -> BlendedSignal:
    """Construct a BlendedSignal from the sim outcome, sized to autonomy caps."""
    direction = "buy" if side.lower() in ("buy", "long") else "sell"
    # Default entry/stop/target if sim didn't provide price levels: derive a
    # simple 1% stop / 2% target around entry so the risk gate has something to chew.
    entry = entry_price or 100.0
    stop = entry * (0.99 if direction == "buy" else 1.01)
    target = entry * (1.02 if direction == "buy" else 0.98)

    size = min(size_cap_usd, equity * size_cap_pct)
    size = max(size, 1.0)

    return BlendedSignal(
        signal_id=f"user-{symbol}-{int(datetime.now(timezone.utc).timestamp())}",
        symbol=symbol,
        venue=venue,
        direction=direction,
        nt_entry_price=entry,
        nt_stop_price=stop,
        nt_target_price=target,
        nt_effective_kelly=0.1,
        nt_brick_size=max(entry * 0.001, 0.01),
        meta_regime="user_intent",
        meta_regime_confidence=0.5,
        sizing_multiplier=1.0,
        entry_strategy="wait_for_brick_close",
        execution_method="limit_at_brick_boundary",
        final_size_usd=size,
        final_size_pct=size / equity if equity > 0 else 0.0,
        risk_amount_usd=size * 0.01,
        brick_pattern=sim.get("pattern", "") or "user_intent",
        pattern_confidence=0.0,
        expected_entry_alpha_bps=sim.get("entry_alpha_bps", 0.0),
        config_hash=config_hash,
    )


async def evaluate_user_intent(
    engine: Any,
    symbol: str,
    side: str,
    venue: str = "hyperliquid",
    equity: float = 10000.0,
    entry_price: float = 0.0,
    sim_fn: Callable[..., Any] | None = None,
    sim_latency_sec: float = DEFAULT_MAX_SIM_LATENCY_SEC,
) -> Any:
    """Run the GC branch: sim -> build signal -> L5 gate -> RiskDecision.

    Args:
        engine: PortfolioRiskEngine (provides evaluate_signal + autonomy caps).
        symbol/side/venue: the user's intent.
        equity: current account equity (for sizing + pct caps).
        entry_price: optional reference price; if 0, a default 100.0 is used.
        sim_fn: injectable sim (defaults to the real bounded RenkoSimulationEngine sweep).
        sim_latency_sec: budget for the sim (default 60s).

    Returns:
        RiskDecision (approved or rejected + reason). On sim failure, returns a
        rejected decision with reason "rejected:sim_failed".
    """
    if not REQUIRE_SIM:
        # Defensive: this branch must never be reachable — see module docstring.
        raise RuntimeError("user-intent sim is mandatory and non-configurable")

    config = getattr(engine, "_config", None)
    sim = sim_fn or _default_sim
    try:
        sim_result = await asyncio.wait_for(
            sim(symbol, venue, side, config),
            timeout=sim_latency_sec,
        )
    except asyncio.TimeoutError:
        log.warning("user_intent_sim_timeout", symbol=symbol, budget=sim_latency_sec)
        return _rejected_decision(symbol, "sim_timeout", engine)
    except Exception as e:
        log.warning("user_intent_sim_error", symbol=symbol, error=str(e)[:160])
        return _rejected_decision(symbol, f"sim_error:{e}", engine)

    if not sim_result.get("ok"):
        return _rejected_decision(symbol, f"sim_failed:{sim_result.get('reason', 'unknown')}", engine)

    # Size to the active autonomy caps (normal $2000/1.5% or cold-start $100/0.2%)
    gate = engine._autonomy_gate
    if gate.is_cold_start():
        cap_usd, cap_pct = gate._cs_tier1_max, gate._cs_tier1_pct
    else:
        cap_usd, cap_pct = gate._tier1_max, gate._tier1_pct

    signal = _build_signal(
        symbol=symbol,
        venue=venue,
        side=side,
        sim=sim_result,
        equity=equity,
        size_cap_usd=cap_usd,
        size_cap_pct=cap_pct,
        entry_price=entry_price,
        config_hash=getattr(engine, "_config_hash", "") or "",
    )

    decision = await engine.evaluate_signal(signal)
    decision.signal_id = signal.signal_id  # ensure linkage for L3 lookup
    log.info(
        "user_intent_evaluated",
        symbol=symbol,
        side=side,
        approved=decision.approved,
        reason=decision.reason,
    )
    return decision


def _rejected_decision(symbol: str, reason: str, engine: Any) -> Any:
    from hermes.portfolio.risk_gate import RiskDecision

    return RiskDecision(
        signal_id=f"user-{symbol}-rejected",
        approved=False,
        requested_size_usd=0.0,
        approved_size_usd=0.0,
        limits_hit=["user_intent_" + reason],
        reason=f"rejected:{reason}",
        autonomy_tier=0,
        config_hash=getattr(engine, "_config_hash", "") or "",
    )
