"""P0 hardening tests: GA (cold-start), GB (selection), GD (unsupported venue).

Run: env -u PYTHONPATH ./.venv/Scripts/python.exe -m pytest tests/test_hardening_p0.py -q
"""

import asyncio
from datetime import datetime, timezone

import pytest

from hermes.portfolio.autonomy_gate import AutonomyGate
from hermes.portfolio.selection import SelectionLayer
from hermes.signals.synthesizer import BlendedSignal


def _signal(symbol="BTC/USD", venue="hyperliquid", size=100.0, entry=64000.0,
            stop=63000.0, target=66000.0, pat_conf=0.7, alpha=20.0, reg_conf=0.7,
            strategy="wait_for_brick_close"):
    return BlendedSignal(
        signal_id="sig-" + symbol,
        symbol=symbol,
        venue=venue,
        direction="buy",
        nt_entry_price=entry,
        nt_stop_price=stop,
        nt_target_price=target,
        nt_effective_kelly=0.1,
        nt_brick_size=50.0,
        meta_regime="choppy_range",
        meta_regime_confidence=reg_conf,
        sizing_multiplier=1.0,
        entry_strategy=strategy,
        execution_method="limit_at_brick_boundary",
        final_size_usd=size,
        final_size_pct=size / 100000.0,
        risk_amount_usd=size * 0.015,
        brick_pattern="breakout_up",
        pattern_confidence=pat_conf,
        expected_entry_alpha_bps=alpha,
        config_hash="test",
    )


# ---------------------------------------------------------------------------
# GA — Cold-start caps
# ---------------------------------------------------------------------------

def test_ga_cold_start_tight_cap_rejects_3k():
    gate = AutonomyGate(cold_start_enabled=True, cold_start_tier1_notional=100,
                        cold_start_tier1_pct=0.002)
    # $3k trade, equity $100k -> 3% > 0.2% cap, and > $100 notional cap
    d = gate.classify("enter_trade", notional_usd=3000, equity=100000, is_crypto=True)
    assert d.requires_human_approval is True
    assert d.tier == 3
    assert "cold" not in d.reason  # it's "between_tier1_and_tier3" (over $100 normal cap too, but cold caps tighter)
    # Explicitly: within cold caps ($100 / 0.2%) must be tier1
    d2 = gate.classify("enter_trade", notional_usd=90, equity=100000, is_crypto=True)
    assert d2.tier == 1
    assert d2.approved is True
    assert "cold_start" in d2.reason


def test_ga_cold_start_position_budget():
    gate = AutonomyGate(cold_start_enabled=True, cold_start_tier1_notional=100,
                        cold_start_tier1_pct=0.002, cold_start_max_new_positions=3)
    # First 3 small trades OK; 4th hits position budget -> tier3 human
    for i in range(3):
        d = gate.classify("enter_trade", notional_usd=90, equity=100000, is_crypto=True,
                          cs_new_positions=i, cs_new_exposure=90 * i)
        assert d.tier == 1, f"trade {i} should be tier1"
    d4 = gate.classify("enter_trade", notional_usd=90, equity=100000, is_crypto=True,
                       cs_new_positions=3, cs_new_exposure=270)
    assert d4.tier == 3
    assert "cold_start_max_new_positions_reached" in d4.reason


def test_ga_cold_start_exposure_budget():
    gate = AutonomyGate(cold_start_enabled=True, cold_start_tier1_notional=100,
                        cold_start_tier1_pct=0.002, cold_start_max_new_exposure_pct=0.05)
    # exposure 4.95% + new 0.09% = ~5.04% > 5% cap -> tier3
    d = gate.classify("enter_trade", notional_usd=90, equity=100000, is_crypto=True,
                      cs_new_positions=1, cs_new_exposure=4950)
    assert d.tier == 3
    assert "cold_start_max_new_exposure_reached" in d.reason


def test_ga_normal_user_not_cold():
    gate = AutonomyGate(cold_start_enabled=False)  # normal user
    d = gate.classify("enter_trade", notional_usd=1500, equity=100000, is_crypto=True)
    assert d.tier == 1  # within normal $5k/2% cap
    assert "cold_start" not in d.reason


# ---------------------------------------------------------------------------
# GB — Selection layer (top-N, drop excess)
# ---------------------------------------------------------------------------

def test_gb_selection_admits_top_n():
    sel = SelectionLayer(enabled=True, max_new_positions_per_cycle=3, cycle_window_sec=300)
    # 3 candidates within window -> all admitted
    for i in range(3):
        ok, _ = sel.evaluate(_signal(symbol=f"SYM{i}"), equity=100000)
        assert ok is True
    assert sel.pending_count() == 3


def test_gb_selection_drops_excess():
    sel = SelectionLayer(enabled=True, max_new_positions_per_cycle=3, cycle_window_sec=300)
    for i in range(3):
        sel.evaluate(_signal(symbol=f"SYM{i}"), equity=100000)
    # 4th candidate -> dropped (not deferred)
    ok, reason = sel.evaluate(_signal(symbol="SYM9"), equity=100000)
    assert ok is False
    assert "selection_budget_exhausted" in reason
    assert sel.pending_count() == 3  # unchanged


def test_gb_selection_scores_and_ranks():
    sel = SelectionLayer(enabled=True, max_new_positions_per_cycle=1, cycle_window_sec=300)
    # High-score candidate admitted; lower-score one dropped
    high = _signal(symbol="HIGH", pat_conf=0.9, alpha=40.0, reg_conf=0.9)
    low = _signal(symbol="LOW", pat_conf=0.1, alpha=2.0, reg_conf=0.3)
    ok_h, _ = sel.evaluate(high, equity=100000)
    assert ok_h is True
    ok_l, reason_l = sel.evaluate(low, equity=100000)
    assert ok_l is False
    assert "selection_budget_exhausted" in reason_l


def test_gb_selection_disabled_passthrough():
    sel = SelectionLayer(enabled=False)
    ok, reason = sel.evaluate(_signal(), equity=100000)
    assert ok is True
    assert reason == "selection_disabled"


# ---------------------------------------------------------------------------
# GD — Unsupported venue reject (via engine evaluate_signal)
# ---------------------------------------------------------------------------

def test_gd_unsupported_venue_rejected():
    from hermes.portfolio.orchestrator import PortfolioRiskEngine
    from hermes.core.config import load_config

    cfg = load_config()
    engine = PortfolioRiskEngine(cfg, initial_equity=100000)
    # Signal with venue we don't support (e.g. a stock exchange string)
    bad = _signal(symbol="SSE:000001", venue="sse", size=90)
    decision = asyncio.run(engine.evaluate_signal(bad))
    assert decision.approved is False
    assert "unsupported_venue" in decision.limits_hit
    assert "rejected:unsupported_venue" == decision.reason


def test_gd_supported_venue_passes_gd():
    from hermes.portfolio.orchestrator import PortfolioRiskEngine
    from hermes.core.config import load_config

    cfg = load_config()
    engine = PortfolioRiskEngine(cfg, initial_equity=100000)
    # mt4_mt5 is the primary enabled venue (Alpaca/HL deprecated)
    good = _signal(symbol="BTC/USD", venue="mt4_mt5", size=90)
    decision = asyncio.run(engine.evaluate_signal(good))
    # GD passes; it may still be gated by selection/cold-start but not by venue
    assert "unsupported_venue" not in decision.limits_hit


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
