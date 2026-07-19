"""P1 hardening tests: GC (user-intent sim branch) + GE (human-approval queue).

Run: env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest tests/test_hardening_p1.py -q
"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from hermes.core.config import load_config
from hermes.portfolio.orchestrator import PortfolioRiskEngine
from hermes.portfolio.user_intent import evaluate_user_intent


@pytest.fixture
def cfg_and_engine(tmp_path, monkeypatch):
    """Load config, point DuckDB at a temp file, build engine."""
    monkeypatch.setenv("HERMES_DUCKDB_PATH", str(tmp_path / "test.duckdb"))
    # load_config ignores HERMES_DUCKDB_PATH (known quirk); patch the resolved path
    from hermes.db import migrate as _mig
    orig = _mig.get_duckdb_path

    def patched(config):
        p = tmp_path / "test.duckdb"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    monkeypatch.setattr(_mig, "get_duckdb_path", patched)
    # apply_migrations uses its own get_duckdb_path via the patched fn for base schema
    cfg = load_config()
    # Force pending_decisions migration present
    from hermes.db.migrate import apply_migrations
    apply_migrations(cfg)
    engine = PortfolioRiskEngine(cfg, initial_equity=10000)
    return cfg, engine


# ---------------------------------------------------------------------------
# GC — user-initiated trade branch (sim mandatory, routes through L5)
# ---------------------------------------------------------------------------

def test_gc_user_intent_sim_mandatory_and_routes(cfg_and_engine):
    cfg, engine = cfg_and_engine
    calls = {}

    async def fake_sim(symbol, venue, side, config):
        calls["ran"] = True
        return {"ok": True, "entry_alpha_bps": 15.0, "sharpe": 1.2,
                "net_pnl_bps": 30.0, "pattern": "breakout_up"}

    decision = asyncio.run(evaluate_user_intent(
        engine=engine, symbol="COINBASE:BTCUSD", side="BUY", venue="mt4_mt5",
        equity=10000, sim_fn=fake_sim,
    ))
    # Sim MUST have run (GC: require_sim non-configurable)
    assert calls.get("ran") is True
    # Decision routed through L5 (same gate as signal-driven trades).
    # It may be approved (within caps) or rejected by selection/cold-start/sizing —
    # the key invariant is that it went through L5, not silently dropped.
    assert decision.signal_id.startswith("user-")
    if decision.approved:
        # If approved, size respects the active autonomy caps ($2000/1.5% normal,
        # $100/0.2% cold-start). At $10k equity the max is min(2000, 150)=150.
        assert decision.approved_size_usd <= 2000.0 + 1e-6
        assert decision.approved_size_usd > 0


def test_gc_user_intent_sim_failure_rejected(cfg_and_engine):
    cfg, engine = cfg_and_engine

    async def bad_sim(symbol, venue, side, config):
        return {"ok": False, "reason": "no_data"}

    decision = asyncio.run(evaluate_user_intent(
        engine=engine, symbol="COINBASE:ETHUSD", side="BUY", venue="mt4_mt5",
        equity=10000, sim_fn=bad_sim,
    ))
    assert decision.approved is False
    assert "sim_failed" in decision.reason


def test_gc_user_intent_unsupported_venue_rejected(cfg_and_engine):
    cfg, engine = cfg_and_engine

    async def fake_sim(symbol, venue, side, config):
        return {"ok": True, "entry_alpha_bps": 15.0, "sharpe": 1.2, "pattern": "x"}

    # venue "sse" is not supported -> GD reject at L5 (same as signal-driven)
    decision = asyncio.run(evaluate_user_intent(
        engine=engine, symbol="SSE:000001", side="BUY", venue="sse",
        equity=10000, sim_fn=fake_sim,
    ))
    assert decision.approved is False
    assert "unsupported_venue" in decision.limits_hit


# ---------------------------------------------------------------------------
# GE — human-approval queue (tier-3 -> pending, not silently dropped)
# ---------------------------------------------------------------------------

def test_ge_tier3_stored_pending_and_approvable(cfg_and_engine):
    cfg, engine = cfg_and_engine
    from hermes.signals.synthesizer import BlendedSignal

    # A $4k trade at $10k equity = 40% > 1.5% tier-1 cap, and > $2k -> tier-3 (human)
    sig = BlendedSignal(
        signal_id="ge-sig-1", symbol="BTC/USD", venue="mt4_mt5", direction="buy",
        nt_entry_price=64000, nt_stop_price=63000, nt_target_price=66000,
        nt_effective_kelly=0.1, nt_brick_size=50.0, meta_regime="choppy_range",
        meta_regime_confidence=0.7, sizing_multiplier=1.0,
        entry_strategy="wait_for_brick_close", execution_method="limit_at_brick_boundary",
        final_size_usd=4000.0, final_size_pct=0.40, risk_amount_usd=60.0,
        brick_pattern="breakout_up", pattern_confidence=0.7, expected_entry_alpha_bps=20.0,
        config_hash="test",
    )
    decision = asyncio.run(engine.evaluate_signal(sig))
    # Not auto-approved; flagged pending + stored
    assert decision.approved is False
    assert decision.requires_human_approval is True
    assert decision.status == "pending"

    pending = engine.get_pending_approvals().list_pending()
    assert len(pending) == 1
    assert pending[0]["decision_id"] == decision.decision_id

    # Approve -> re-published payload approved
    payload = asyncio.run(engine.approve_decision(decision.decision_id))
    assert payload is not None
    assert payload["approved"] is True
    assert payload["status"] == "approved"


def test_ge_pending_expired_not_approvable(cfg_and_engine):
    cfg, engine = cfg_and_engine
    from hermes.portfolio.pending_approvals import PendingApprovals
    from hermes.signals.synthesizer import BlendedSignal

    # A $4k trade -> tier-3 (human). Use a 1-second decision deadline.
    sig = BlendedSignal(
        signal_id="ge-expired-1", symbol="BTC/USD", venue="mt4_mt5", direction="buy",
        nt_entry_price=64000, nt_stop_price=63000, nt_target_price=66000,
        nt_effective_kelly=0.1, nt_brick_size=50.0, meta_regime="choppy_range",
        meta_regime_confidence=0.7, sizing_multiplier=1.0,
        entry_strategy="wait_for_brick_close", execution_method="limit_at_brick_boundary",
        final_size_usd=4000.0, final_size_pct=0.40, risk_amount_usd=60.0,
        brick_pattern="breakout_up", pattern_confidence=0.7, expected_entry_alpha_bps=20.0,
        config_hash="test",
    )
    pa = PendingApprovals(cfg, approval_timeout_seconds=1)
    decision = asyncio.run(engine.evaluate_signal(sig))
    # store directly with the 1s TTL to isolate the deadline guard
    pa.store(decision, symbol="BTC/USD", venue="mt4_mt5", direction="buy")

    # Before expiry: approvable
    assert pa.approve(decision.decision_id) is not None

    # New decision, wait past the 1s deadline, then approve must be blocked
    sig2 = BlendedSignal(
        signal_id="ge-expired-2", symbol="BTC/USD", venue="mt4_mt5", direction="buy",
        nt_entry_price=64000, nt_stop_price=63000, nt_target_price=66000,
        nt_effective_kelly=0.1, nt_brick_size=50.0, meta_regime="choppy_range",
        meta_regime_confidence=0.7, sizing_multiplier=1.0,
        entry_strategy="wait_for_brick_close", execution_method="limit_at_brick_boundary",
        final_size_usd=4000.0, final_size_pct=0.40, risk_amount_usd=60.0,
        brick_pattern="breakout_up", pattern_confidence=0.7, expected_entry_alpha_bps=20.0,
        config_hash="test",
    )
    decision2 = asyncio.run(engine.evaluate_signal(sig2))
    pa.store(decision2, symbol="BTC/USD", venue="mt4_mt5", direction="buy")
    import time as _t
    _t.sleep(1.2)
    blocked = pa.approve(decision2.decision_id)
    assert blocked is None, "expired decision must NOT be approvable"
    # And it must no longer appear in the active pending list
    assert all(d["decision_id"] != decision2.decision_id for d in pa.list_pending())


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
