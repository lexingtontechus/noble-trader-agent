"""
Performance Attribution tests — branch tracking, regime matrix, A/B testing,
signal window optimization, threshold feedback.

Run with:
    pytest tests/test_attribution.py -v
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest


# === Decision Branch Tracker ===


def _make_tracker():
    """Create a DecisionBranchTracker with mock config."""
    from hermes.agent.attribution import DecisionBranchTracker
    from hermes.core.config import load_config

    config = load_config()
    return DecisionBranchTracker(config)


def test_branch_tracker_record_entry():
    """Tracker records entry decisions."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()
    tracker.record_entry(
        trade_id="test-1",
        symbol="BTC",
        venue="hyperliquid",
        entry_action=AgentAction.ENTER_NEW,
        entry_strategy="enter_now",
        execution_method="market",
        meta_regime="calm_trend",
        brick_pattern="trend_up",
        conviction_score=0.8,
        sizing_multiplier=1.0,
    )

    assert "test-1" in tracker._records
    assert tracker._records["test-1"].entry_action == AgentAction.ENTER_NEW


def test_branch_tracker_record_exit():
    """Tracker records exit decisions."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()
    tracker.record_entry(
        trade_id="test-2",
        symbol="BTC",
        venue="hyperliquid",
        entry_action=AgentAction.ENTER_NEW,
        meta_regime="calm_trend",
    )
    tracker.record_exit(
        trade_id="test-2",
        exit_action=AgentAction.CLOSE_EARLY_PROFIT,
        exit_reason="early_profit",
        net_pnl=500,
        r_multiple=0.8,
        hold_duration_sec=3600,
        meta_regime_at_exit="calm_trend",
        entry_alpha_bps=5.2,
    )

    record = tracker._records["test-2"]
    assert record.exit_action == AgentAction.CLOSE_EARLY_PROFIT
    assert record.net_pnl == 500
    assert record.r_multiple == 0.8


def test_branch_tracker_analyze_branch_performance():
    """Tracker analyzes PnL by exit branch."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    # Add trades with different exit branches
    trades = [
        ("t1", AgentAction.ENTER_NEW, AgentAction.CLOSE_STOP_LOSS, -200, -1.0, "calm_trend"),
        ("t2", AgentAction.ENTER_NEW, AgentAction.CLOSE_STOP_LOSS, -250, -1.2, "choppy_range"),
        ("t3", AgentAction.ENTER_NEW, AgentAction.CLOSE_TAKE_PROFIT, 300, 0.5, "calm_trend"),
        ("t4", AgentAction.ENTER_NEW, AgentAction.CLOSE_EARLY_PROFIT, 500, 0.8, "calm_trend"),
        ("t5", AgentAction.ENTER_NEW, AgentAction.CLOSE_EARLY_PROFIT, 600, 0.9, "high_vol_breakout"),
        ("t6", AgentAction.ENTER_NEW, AgentAction.TRAIL_STOP, 200, 0.3, "calm_trend"),
    ]

    for trade_id, entry_action, exit_action, pnl, r, regime in trades:
        tracker.record_entry(
            trade_id=trade_id, symbol="BTC", venue="hyperliquid",
            entry_action=entry_action, meta_regime=regime,
        )
        tracker.record_exit(
            trade_id=trade_id, exit_action=exit_action, net_pnl=pnl,
            r_multiple=r, meta_regime_at_exit=regime,
        )

    stats = tracker.analyze_branch_performance()

    assert "close_stop_loss" in stats
    assert stats["close_stop_loss"].n_trades == 2
    assert stats["close_stop_loss"].win_rate == 0.0
    assert stats["close_stop_loss"].avg_r_multiple == -1.1  # (-1.0 + -1.2) / 2

    assert "close_early_profit" in stats
    assert stats["close_early_profit"].n_trades == 2
    assert stats["close_early_profit"].win_rate == 1.0

    assert "close_take_profit" in stats
    assert stats["close_take_profit"].n_trades == 1


def test_branch_tracker_regime_matrix():
    """Tracker produces branch × regime matrix."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    trades = [
        ("t1", AgentAction.CLOSE_STOP_LOSS, -200, -1.0, "calm_trend"),
        ("t2", AgentAction.CLOSE_EARLY_PROFIT, 500, 0.8, "calm_trend"),
        ("t3", AgentAction.CLOSE_EARLY_PROFIT, 600, 0.9, "high_vol_breakout"),
        ("t4", AgentAction.CLOSE_TAKE_PROFIT, 300, 0.5, "choppy_range"),
    ]

    for trade_id, exit_action, pnl, r, regime in trades:
        tracker.record_entry(
            trade_id=trade_id, symbol="BTC", venue="hyperliquid",
            entry_action=AgentAction.ENTER_NEW, meta_regime=regime,
        )
        tracker.record_exit(
            trade_id=trade_id, exit_action=exit_action, net_pnl=pnl,
            r_multiple=r, meta_regime_at_exit=regime,
        )

    matrix = tracker.analyze_regime_branch_matrix()

    assert "close_early_profit" in matrix.matrix
    assert "calm_trend" in matrix.matrix["close_early_profit"]
    assert "high_vol_breakout" in matrix.matrix["close_early_profit"]
    assert matrix.matrix["close_early_profit"]["calm_trend"].n_trades == 1


def test_branch_tracker_hypothesis_attribution():
    """Tracker attributes PnL to hypotheses."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    tracker.record_entry(
        trade_id="t1", symbol="BTC", venue="hyperliquid",
        entry_action=AgentAction.ENTER_NEW, meta_regime="calm_trend",
        hypothesis_ids=["hyp-1", "hyp-2"],
    )
    tracker.record_exit(
        trade_id="t1", exit_action=AgentAction.CLOSE_EARLY_PROFIT,
        net_pnl=500, r_multiple=0.8, meta_regime_at_exit="calm_trend",
    )

    hyp_stats = tracker.analyze_hypothesis_performance()
    assert "hyp-1" in hyp_stats
    assert "hyp-2" in hyp_stats
    assert hyp_stats["hyp-1"].n_trades == 1
    assert hyp_stats["hyp-1"].total_pnl == 500


def test_threshold_feedback_stop_loss_too_loose():
    """Feedback detects SL threshold is too loose."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    # 5 SL trades with avg R worse than -1.2
    for i in range(5):
        tracker.record_entry(
            trade_id=f"sl-{i}", symbol="BTC", venue="hyperliquid",
            entry_action=AgentAction.ENTER_NEW, meta_regime="calm_trend",
        )
        tracker.record_exit(
            trade_id=f"sl-{i}", exit_action=AgentAction.CLOSE_STOP_LOSS,
            net_pnl=-300, r_multiple=-1.5, meta_regime_at_exit="calm_trend",
        )

    feedback = tracker.get_threshold_feedback()
    assert "stop_loss_pct" in feedback
    assert "too loose" in feedback["stop_loss_pct"]["issue"]


def test_threshold_feedback_early_profit_too_low():
    """Feedback detects early profit threshold is too low."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    # 5 early profit trades with low avg R
    for i in range(5):
        tracker.record_entry(
            trade_id=f"ep-{i}", symbol="BTC", venue="hyperliquid",
            entry_action=AgentAction.ENTER_NEW, meta_regime="calm_trend",
        )
        tracker.record_exit(
            trade_id=f"ep-{i}", exit_action=AgentAction.CLOSE_EARLY_PROFIT,
            net_pnl=100, r_multiple=0.2, meta_regime_at_exit="calm_trend",
        )

    feedback = tracker.get_threshold_feedback()
    assert "early_profit_pct" in feedback
    assert "too low" in feedback["early_profit_pct"]["issue"]


def test_threshold_feedback_flip_not_working():
    """Feedback detects flip threshold is too low."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    for i in range(3):
        tracker.record_entry(
            trade_id=f"flip-{i}", symbol="BTC", venue="hyperliquid",
            entry_action=AgentAction.ENTER_NEW, meta_regime="calm_trend",
        )
        tracker.record_exit(
            trade_id=f"flip-{i}", exit_action=AgentAction.CLOSE_FLIP,
            net_pnl=-100, r_multiple=-0.3, meta_regime_at_exit="calm_trend",
        )

    feedback = tracker.get_threshold_feedback()
    assert "strong_conviction_threshold" in feedback


def test_decision_quality_report():
    """Full decision quality report generates correctly."""
    from hermes.agent.decision_tree import AgentAction

    tracker = _make_tracker()

    tracker.record_entry("t1", "BTC", "hyperliquid", AgentAction.ENTER_NEW, meta_regime="calm_trend")
    tracker.record_exit("t1", AgentAction.CLOSE_EARLY_PROFIT, net_pnl=500, r_multiple=0.8, meta_regime_at_exit="calm_trend")

    tracker.record_entry("t2", "BTC", "hyperliquid", AgentAction.ENTER_NEW, meta_regime="choppy_range")
    tracker.record_exit("t2", AgentAction.CLOSE_STOP_LOSS, net_pnl=-200, r_multiple=-1.0, meta_regime_at_exit="choppy_range")

    report = tracker.get_decision_quality_report()

    assert report["total_trades_analyzed"] == 2
    assert "exit_branch_performance" in report
    assert "regime_branch_matrix" in report
    assert "threshold_feedback" in report
    assert report["best_branch"] is not None
    assert report["worst_branch"] is not None


# === A/B Testing Framework ===


def test_ab_test_significant_difference():
    """A/B test detects significant difference between configs."""
    from hermes.agent.attribution import ABTestFramework
    import numpy as np

    np.random.seed(42)
    # Config A: mean 0.001, std 0.01
    returns_a = np.random.normal(0.001, 0.01, 100).tolist()
    # Config B: mean 0.003, std 0.01 (clearly better)
    returns_b = np.random.normal(0.003, 0.01, 100).tolist()

    result = ABTestFramework.compare(
        config_a_name="current",
        config_a_returns=returns_a,
        config_b_name="hypothesis_1",
        config_b_returns=returns_b,
    )

    assert result.config_b_sharpe > result.config_a_sharpe
    assert result.significant
    assert result.winner == "hypothesis_1"


def test_ab_test_inconclusive():
    """A/B test returns inconclusive when no significant difference."""
    from hermes.agent.attribution import ABTestFramework
    import numpy as np

    np.random.seed(42)
    returns_a = np.random.normal(0.001, 0.01, 100).tolist()
    returns_b = np.random.normal(0.001, 0.01, 100).tolist()  # same distribution

    result = ABTestFramework.compare(
        config_a_name="current",
        config_a_returns=returns_a,
        config_b_name="hypothesis_1",
        config_b_returns=returns_b,
    )

    assert not result.significant
    assert result.winner == "inconclusive"


def test_ab_test_insufficient_data():
    """A/B test handles insufficient data gracefully."""
    from hermes.agent.attribution import ABTestFramework

    result = ABTestFramework.compare(
        config_a_name="a",
        config_a_returns=[0.01, 0.02],
        config_b_name="b",
        config_b_returns=[0.01, 0.03],
    )

    assert result.winner == "inconclusive"


def test_ab_test_sharpe_computation():
    """Sharpe ratio is computed correctly."""
    from hermes.agent.attribution import ABTestFramework

    returns = [0.001] * 252  # constant positive returns
    sharpe = ABTestFramework._sharpe(returns)
    # std = 0 → Sharpe = 0 (division by zero guard)
    assert sharpe == 0.0

    # Normal returns should produce a positive Sharpe
    import numpy as np
    np.random.seed(42)
    returns_varied = np.random.normal(0.001, 0.01, 252).tolist()
    sharpe_varied = ABTestFramework._sharpe(returns_varied)
    assert sharpe_varied > 0


# === Signal Window Optimizer ===


def test_signal_window_optimizer_finds_best():
    """Signal window optimizer finds the best window."""
    from hermes.agent.attribution import SignalWindowOptimizer

    now = datetime.now(timezone.utc)

    # Create a signal
    signals = [{
        "ts": now,
        "symbol": "BTC",
        "direction": "buy",
        "entry_price": 64000,
        "stop_loss": 63000,
        "take_profit": 65000,
    }]

    # Create price data: price drops to 63900 at minute 5, then rises to 65000 at minute 30
    prices = []
    for i in range(60):
        ts = now + timedelta(minutes=i)
        if i < 5:
            price = 64000 - i * 20  # dropping
        elif i < 10:
            price = 63900 + (i - 5) * 5  # bouncing
        else:
            price = 63925 + (i - 10) * 12  # rising toward target
        prices.append((ts, price))

    price_data = {"BTC": prices}

    result = SignalWindowOptimizer.optimize_window(
        signals=signals,
        price_data=price_data,
        windows=[1, 5, 10, 30, 60],
    )

    assert "by_window" in result
    assert "best_window" in result
    assert result["best_window"] in [1, 5, 10, 30, 60]


def test_signal_window_optimizer_empty_signals():
    """Signal window optimizer handles empty signals."""
    from hermes.agent.attribution import SignalWindowOptimizer

    result = SignalWindowOptimizer.optimize_window(
        signals=[],
        price_data={},
        windows=[10, 30],
    )

    assert result["best_window"] == 10  # default to first


# === Integration: Tracker + Decision Tree ===


def test_tracker_integrates_with_decision_tree():
    """Tracker can record decisions from the actual decision tree."""
    from hermes.agent.attribution import DecisionBranchTracker
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree
    from hermes.portfolio.state import PortfolioPosition

    tracker = _make_tracker()
    tree = HermesDecisionTree()

    # Simulate a position that hits early profit
    position = PortfolioPosition(
        position_id="pos-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="long",
        qty=1.0,
        entry_price=64000,
        current_price=64000 * 1.05,  # +5%
        stop_price=63360,
        target_price=65600,
        opened_at=datetime.now(timezone.utc),
        risk_amount=640,
    )

    from hermes.signals.synthesizer import BlendedSignal
    signal = BlendedSignal(
        signal_id="sig-1",
        symbol="BTC", venue="hyperliquid", direction="buy",
        nt_entry_price=64000, nt_stop_price=63360, nt_target_price=65600,
        nt_effective_kelly=0.12, nt_brick_size=50,
        meta_regime="calm_trend", meta_regime_confidence=0.8,
        sizing_multiplier=1.0, entry_strategy="enter_now",
        execution_method="market", final_size_usd=2000,
        final_size_pct=0.02, risk_amount_usd=640,
        brick_pattern="trend_up", expected_entry_alpha_bps=0,
        config_hash="test",
    )

    # Record entry
    tracker.record_entry(
        trade_id="trade-1",
        symbol="BTC",
        venue="hyperliquid",
        entry_action=AgentAction.ENTER_NEW,
        meta_regime="calm_trend",
        brick_pattern="trend_up",
    )

    # Decision tree evaluates position → should return CLOSE_EARLY_PROFIT
    decision = tree.evaluate_existing_position(
        position=position,
        signal=signal,
        current_price=64000 * 1.05,
        adverse_brick_count=0,
    )

    # Record exit with the decision tree's action
    tracker.record_exit(
        trade_id="trade-1",
        exit_action=decision.action,
        exit_reason=decision.reason,
        net_pnl=320,  # (67200 - 64000) * 1.0
        r_multiple=0.5,
        meta_regime_at_exit="calm_trend",
    )

    # Verify attribution
    stats = tracker.analyze_branch_performance()
    assert "close_early_profit" in stats
    assert stats["close_early_profit"].n_trades == 1
    assert stats["close_early_profit"].win_rate == 1.0
