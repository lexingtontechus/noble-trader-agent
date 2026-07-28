"""
Phase C tests — agent-side 4-source EV blend, Markov adaptive thresholds,
BayesianAlpha record_outcome wiring, and v5 source-breakdown persistence.

Run with:
    pytest tests/test_phase_c.py -v
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

import pytest


# ─── compute_blended_p_win unit tests ─────────────────────────────────


from hermes.signals.synthesizer import (
    P_WIN_WEIGHTS_AGENT_DEFAULT,
    _inv_logit,
    _logit,
    _normalise_source_key,
    compute_blended_p_win,
)


class TestComputeBlendedPWin:
    """4-source logit-pool P_win blend — ported from EV-REWORK-DESIGN-v2 §4.1."""

    def test_all_four_sources_uses_default_weights(self):
        """When all 4 sources are available and backend sends no source info,
        default weights are used and sum to 1.0."""
        p_win, sources = compute_blended_p_win(
            p_pattern=0.6, p_regime=0.7, p_markov_hold_n=0.55, p_timesfm=0.65,
        )
        assert 0.5 < p_win < 0.7
        assert set(sources) == {"p_pattern", "p_regime", "p_markov", "p_timesfm"}

    def test_three_sources_drops_timesfm(self):
        """When p_timesfm is None, the remaining 3 weights are renormalised
        to sum to 1.0 — TimesFM is dropped, not substituted with 0.5."""
        p_win, sources = compute_blended_p_win(
            p_pattern=0.6, p_regime=0.7, p_markov_hold_n=0.55, p_timesfm=None,
        )
        assert set(sources) == {"p_pattern", "p_regime", "p_markov"}
        assert "p_timesfm" not in sources

    def test_backend_sources_used_overrides_defaults(self):
        """When backend reports sources_used + weights_used, agent uses
        those (renormalised over the intersection with agent's available
        sources)."""
        p_win, sources = compute_blended_p_win(
            p_pattern=0.6, p_regime=0.7, p_markov_hold_n=0.55,
            p_timesfm=0.65,  # available, but backend says it was unreachable
            sources_used=["p_regime", "p_pattern", "p_markov"],
            weights_used={
                "p_regime": 0.4,
                "p_pattern": 0.35,
                "p_markov": 0.25,
            },
        )
        # TimesFM should be dropped because backend didn't have it
        assert "p_timesfm" not in sources
        assert set(sources) == {"p_regime", "p_pattern", "p_markov"}

    def test_normalise_source_key_handles_both_naming_conventions(self):
        """Backend may send 'p_markov' or 'p_markov_hold_n' — both map to
        agent-internal 'p_markov'."""
        assert _normalise_source_key("p_markov") == "p_markov"
        assert _normalise_source_key("p_markov_hold_n") == "p_markov"
        assert _normalise_source_key("p_pattern") == "p_pattern"
        assert _normalise_source_key("p_regime") == "p_regime"
        assert _normalise_source_key("p_timesfm") == "p_timesfm"

    def test_logit_inv_logit_round_trip(self):
        """logit and inv_logit are inverses."""
        for p in [0.1, 0.3, 0.5, 0.7, 0.9]:
            assert abs(_inv_logit(_logit(p)) - p) < 1e-9

    def test_extreme_inputs_are_clamped(self):
        """Probabilities at 0 or 1 are clamped to avoid log(0) or log(inf)."""
        # Should not raise
        p_win, _ = compute_blended_p_win(
            p_pattern=0.0, p_regime=1.0, p_markov_hold_n=0.0, p_timesfm=1.0,
        )
        assert 0.0 < p_win < 1.0  # not exactly 0 or 1 due to clamp

    def test_uniform_sources_returns_average_in_logit_space(self):
        """When all 4 sources are equal, p_win_agent equals that value
        (because logit-average of identical values is the value itself)."""
        p_win, _ = compute_blended_p_win(
            p_pattern=0.7, p_regime=0.7, p_markov_hold_n=0.7, p_timesfm=0.7,
        )
        assert abs(p_win - 0.7) < 1e-9

    def test_no_backend_source_info_uses_agent_defaults(self):
        """When backend sends no sources_used/weights_used, agent uses
        its own default weights (renormalised over available sources)."""
        p_win, sources = compute_blended_p_win(
            p_pattern=0.6, p_regime=0.7, p_markov_hold_n=0.55, p_timesfm=None,
            sources_used=None, weights_used=None,
        )
        assert set(sources) == {"p_pattern", "p_regime", "p_markov"}


# ─── NobleTraderHeartbeat v5 schema tests ─────────────────────────────


from hermes.schemas.heartbeat import NobleTraderHeartbeat, parse_heartbeat


def _make_valid_payload(**overrides: Any) -> dict:
    """Build a valid heartbeat payload with v5 fields, applying overrides."""
    payload = {
        "type": "heartbeat",
        "symbol": "BTC",
        "ts": 1735900800000,
        "regime": "low_vol_bull",
        "regime_conf": 0.85,
        "signal": "buy",
        "entry_price": 64441.0,
        "stop_loss": 63941.0,
        "take_profit": 65441.0,
        "aggression": "mid",
        "brick_size": 250.0,
        "sl_bricks": 3,
        "tp_bricks": 6,
        "kelly_f": 0.12,
        "effective_kelly": 0.10,
        "ev": 1250.0,
        "ev_per_dollar": 0.42,
        "p_win": 0.62,
        "p_regime": 0.65,
        "p_imbalance": 0.5,
        "p_markov": 0.58,
        "ev_scale": 0.84,
        "markov_current_state": "UP",
        "regime_shift": "false",
        "shift_at": 0,
        "shifts_24h": 0,
        # v5 fields
        "p_pattern": 0.5,  # server always sends 0.5
        "sources_used": ["p_regime", "p_pattern", "p_markov", "p_timesfm"],
        "weights_used": {
            "p_pattern": 0.30,
            "p_regime": 0.25,
            "p_markov": 0.20,
            "p_timesfm": 0.25,
        },
        "calibration_bias": 0.03,
        "calibration_status": "CALIBRATED",
        "p_win_kelly_shrink": 1.0,
        "p_timesfm": 0.62,
    }
    payload.update(overrides)
    return payload


class TestHeartbeatV5Schema:
    """v5 EV source-breakdown fields on NobleTraderHeartbeat."""

    def test_v5_fields_are_parsed_as_typed_values(self):
        """All v5 fields are accepted and coerced to the right type."""
        payload = _make_valid_payload()
        hb = parse_heartbeat(json.dumps(payload))
        assert hb.p_pattern == 0.5
        assert hb.sources_used == ["p_regime", "p_pattern", "p_markov", "p_timesfm"]
        assert hb.weights_used == {
            "p_pattern": 0.30, "p_regime": 0.25,
            "p_markov": 0.20, "p_timesfm": 0.25,
        }
        assert hb.calibration_bias == 0.03
        assert hb.calibration_status == "CALIBRATED"
        assert hb.p_win_kelly_shrink == 1.0

    def test_v5_fields_are_optional(self):
        """Pre-Phase C heartbeats (no v5 fields) still parse."""
        payload = _make_valid_payload()
        for k in ("p_pattern", "sources_used", "weights_used",
                  "calibration_bias", "calibration_status", "p_win_kelly_shrink"):
            payload.pop(k, None)
        hb = parse_heartbeat(json.dumps(payload))
        assert hb.p_pattern is None
        assert hb.sources_used is None
        assert hb.weights_used is None
        assert hb.calibration_bias is None
        assert hb.calibration_status is None
        assert hb.p_win_kelly_shrink is None

    def test_sources_used_accepts_json_string(self):
        """Redis may send sources_used as a JSON-encoded string."""
        payload = _make_valid_payload(
            sources_used='["p_regime", "p_pattern"]',
        )
        hb = parse_heartbeat(json.dumps(payload))
        assert hb.sources_used == ["p_regime", "p_pattern"]

    def test_weights_used_accepts_json_string(self):
        """Redis may send weights_used as a JSON-encoded string."""
        payload = _make_valid_payload(
            weights_used='{"p_regime": 0.5, "p_pattern": 0.5}',
        )
        hb = parse_heartbeat(json.dumps(payload))
        assert hb.weights_used == {"p_regime": 0.5, "p_pattern": 0.5}

    def test_to_duckdb_row_includes_v5_fields(self):
        """to_duckdb_row() persists v5 fields for backtest replay."""
        payload = _make_valid_payload()
        hb = parse_heartbeat(json.dumps(payload))
        row = hb.to_duckdb_row(
            ts_received=datetime.now(timezone.utc),
            dedup_hash="abc",
            accepted=True,
        )
        assert row["p_pattern"] == 0.5
        assert row["calibration_bias"] == 0.03
        assert row["calibration_status"] == "CALIBRATED"
        assert row["p_win_kelly_shrink"] == 1.0
        # sources_used / weights_used are JSON-encoded for VARCHAR storage
        assert isinstance(row["sources_used"], str)
        assert isinstance(row["weights_used"], str)
        decoded_sources = json.loads(row["sources_used"])
        assert "p_regime" in decoded_sources


# ─── BlendedSignal v5 fields tests ────────────────────────────────────


from hermes.signals.synthesizer import BlendedSignal


class TestBlendedSignalV5Fields:
    """BlendedSignal model carries v5 EV fields for downstream consumers."""

    def test_blended_signal_has_v5_fields_with_defaults(self):
        """All v5 fields have safe defaults so pre-Phase C code paths
        that don't populate them still work."""
        signal = BlendedSignal(
            signal_id="test-1",
            symbol="BTC",
            venue="hyperliquid",
            direction="buy",
            nt_entry_price=64000.0,
            nt_stop_price=63500.0,
            nt_target_price=65000.0,
            nt_effective_kelly=0.10,
            nt_brick_size=250.0,
            meta_regime="trending",
            meta_regime_confidence=0.75,
            sizing_multiplier=1.0,
            entry_strategy="enter_market",
            execution_method="market",
            final_size_usd=1000.0,
            final_size_pct=0.01,
            risk_amount_usd=100.0,
            brick_pattern="double_top",
            expected_entry_alpha_bps=0.0,
        )
        assert signal.p_win_agent == 0.5
        assert signal.markov_persistence == 0.5
        assert signal.markov_hold_n == 0.5
        assert signal.p_win_server == 0.5
        assert signal.p_pattern_local == 0.5
        assert signal.sources_blended == []


# ─── Decision tree v5 conviction + adaptive thresholds tests ─────────


from hermes.agent.decision_tree import AgentAction, HermesDecisionTree
from hermes.portfolio.state import PortfolioPosition


def _make_position(direction: str = "long") -> PortfolioPosition:
    return PortfolioPosition(
        position_id="pos-1",
        symbol="BTC",
        venue="hyperliquid",
        direction=direction,
        qty=0.1,
        entry_price=64000.0,
        current_price=64000.0,
        stop_price=63500.0,
        target_price=65000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=50.0,
    )


def _make_signal(
    direction: str = "sell",  # opposite of position to trigger flip-check branch
    p_win_agent: float = 0.5,
    markov_persistence: float = 0.5,
) -> BlendedSignal:
    return BlendedSignal(
        signal_id="sig-1",
        symbol="BTC",
        venue="hyperliquid",
        direction=direction,
        nt_entry_price=64000.0,
        nt_stop_price=63500.0,
        nt_target_price=65000.0,
        nt_effective_kelly=0.10,
        nt_brick_size=250.0,
        meta_regime="trending",
        meta_regime_confidence=0.85,
        sizing_multiplier=1.0,
        entry_strategy="enter_market",
        execution_method="market",
        final_size_usd=1000.0,
        final_size_pct=0.01,
        risk_amount_usd=100.0,
        brick_pattern="double_top",
        expected_entry_alpha_bps=0.0,
        p_win_agent=p_win_agent,
        markov_persistence=markov_persistence,
    )


class TestDecisionTreeV5:

    def test_conviction_uses_p_win_agent_when_set(self):
        """Flip-check conviction reads p_win_agent (not meta_regime_confidence)
        when p_win_agent is non-default."""
        tree = HermesDecisionTree()
        position = _make_position(direction="long")
        # p_win_agent=0.85 should trigger flip (>= 0.7 threshold)
        signal = _make_signal(direction="sell", p_win_agent=0.85)
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64000.0, adverse_brick_count=0,
        )
        assert decision.action == AgentAction.CLOSE_FLIP
        assert decision.detail["conviction"] == 0.85

    def test_conviction_falls_back_to_meta_regime_when_p_win_agent_default(self):
        """When p_win_agent is 0.5 (default — e.g. pre-Phase C signal),
        conviction falls back to meta_regime_confidence."""
        tree = HermesDecisionTree()
        position = _make_position(direction="long")
        # p_win_agent=0.5 (default) → fall back to meta_regime_confidence=0.85
        signal = _make_signal(direction="sell", p_win_agent=0.5)
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64000.0, adverse_brick_count=0,
        )
        assert decision.action == AgentAction.CLOSE_FLIP
        assert decision.detail["conviction"] == 0.85  # fell back

    def test_low_conviction_holds_with_native_stops(self):
        """When conviction < 0.7 threshold, hold with native stops."""
        tree = HermesDecisionTree()
        position = _make_position(direction="long")
        signal = _make_signal(direction="sell", p_win_agent=0.55)  # below 0.7
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64000.0, adverse_brick_count=0,
        )
        assert decision.action == AgentAction.HOLD_NATIVE_STOPS

    def test_adaptive_thresholds_widen_tp_in_trending_regime(self):
        """When markov_persistence > 0.7, early TP threshold is widened
        (× 1.5 default), so a +4.5% gain that would normally close
        does NOT close in a strong-trending regime."""
        tree = HermesDecisionTree(early_profit_pct=0.045)  # 4.5%
        position = _make_position(direction="long")
        # Same-direction signal (buy), +4.5% pnl, strong trending persistence
        signal = _make_signal(direction="buy", p_win_agent=0.5, markov_persistence=0.85)
        # 4.5% gain — would close with default threshold (0.045)
        # but adaptive threshold is 0.045 × 1.5 = 0.0675, so should HOLD
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64280.0,  # ~+0.44% — well below 6.75%
            adverse_brick_count=0,
        )
        assert decision.action == AgentAction.HOLD

    def test_adaptive_thresholds_tighten_tp_in_mean_reverting_regime(self):
        """When markov_persistence < 0.55, early TP threshold is tightened
        (× 0.7 default), so a +3.2% gain closes in a mean-reverting
        regime even though default threshold is 4.5%."""
        tree = HermesDecisionTree(early_profit_pct=0.045)  # 4.5%
        position = _make_position(direction="long")
        signal = _make_signal(direction="buy", p_win_agent=0.5, markov_persistence=0.45)
        # adaptive threshold = 0.045 × 0.7 = 0.0315 (3.15%)
        # +3.2% > 3.15% → CLOSE_EARLY_PROFIT
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64000.0 * 1.032,  # +3.2%
            adverse_brick_count=0,
        )
        assert decision.action == AgentAction.CLOSE_EARLY_PROFIT

    def test_adaptive_thresholds_default_in_neutral_regime(self):
        """When 0.55 <= markov_persistence <= 0.7, default thresholds apply."""
        tree = HermesDecisionTree(early_profit_pct=0.045)
        position = _make_position(direction="long")
        signal = _make_signal(direction="buy", p_win_agent=0.5, markov_persistence=0.6)
        # default threshold = 0.045
        # +4.5% should close
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64000.0 * 1.045,
            adverse_brick_count=0,
        )
        assert decision.action == AgentAction.CLOSE_EARLY_PROFIT

    def test_adaptive_thresholds_add_fading_tolerance_in_trending(self):
        """In trending regime, fading tolerance is +1 brick — so 2 adverse
        bricks (default threshold) does NOT trigger trail, only 3+ does."""
        tree = HermesDecisionTree(fading_brick_count=2)
        position = _make_position(direction="long")
        signal = _make_signal(direction="buy", p_win_agent=0.5, markov_persistence=0.85)
        # 2 adverse bricks + pnl > 0 — default would trigger TRAIL_STOP,
        # but trending adds +1 tolerance so threshold is 3.
        decision = tree.evaluate_existing_position(
            position=position, signal=signal,
            current_price=64100.0,  # small positive pnl
            adverse_brick_count=2,
        )
        # With trending tolerance = 3, 2 bricks is below threshold → HOLD (not trail)
        assert decision.action == AgentAction.HOLD


# ─── RealizedPnL v5 fields tests ──────────────────────────────────────


from hermes.analytics.pnl_service import RealizedPnL


class TestRealizedPnLV5Fields:

    def test_realized_pnl_accepts_v5_fields(self):
        """RealizedPnL can carry p_win_agent + alpha_at_entry for the
        BayesianAlpha feedback loop."""
        realized = RealizedPnL(
            trade_id="t-1",
            symbol="BTC",
            venue="hyperliquid",
            gross_pnl=100.0,
            fees_total=2.0,
            funding_pnl=0.0,
            slippage_cost=1.0,
            net_pnl=97.0,
            net_pnl_bps=15.1,
            risk_amount=50.0,
            r_multiple=1.94,
            hold_duration_sec=3600,
            n_fills=1,
            p_win_agent=0.65,
            p_win_server=0.62,
            alpha_at_entry=0.85,
            ev_per_dollar=0.42,
        )
        assert realized.p_win_agent == 0.65
        assert realized.p_win_server == 0.62
        assert realized.alpha_at_entry == 0.85
        assert realized.ev_per_dollar == 0.42

    def test_realized_pnl_v5_fields_default_none(self):
        """Pre-Phase C callers still work — v5 fields default to None."""
        realized = RealizedPnL(
            trade_id="t-1",
            symbol="BTC",
            venue="hyperliquid",
            gross_pnl=100.0,
            fees_total=2.0,
            funding_pnl=0.0,
            slippage_cost=1.0,
            net_pnl=97.0,
            net_pnl_bps=15.1,
            risk_amount=50.0,
            r_multiple=1.94,
            hold_duration_sec=3600,
            n_fills=1,
        )
        assert realized.p_win_agent is None
        assert realized.p_win_server is None
        assert realized.alpha_at_entry is None
        assert realized.ev_per_dollar is None
