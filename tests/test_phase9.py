"""
Phase 9 tests — agent decision tree, hypothesis tracker, decision journal,
self-learning loop.

Run with:
    pytest tests/test_phase9.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest


# === Decision Tree ===


def _make_position(pnl_pct: float = 0.0, direction: str = "long"):
    """Create a test PortfolioPosition."""
    from hermes.portfolio.state import PortfolioPosition

    entry = 64000
    if direction == "long":
        current = entry * (1 + pnl_pct)
    else:
        current = entry * (1 - pnl_pct)

    return PortfolioPosition(
        position_id="test-pos-1",
        symbol="BTC",
        venue="hyperliquid",
        direction=direction,
        qty=1.0,
        entry_price=entry,
        current_price=current,
        stop_price=entry * 0.99,  # -1% stop
        target_price=entry * 1.025,  # +2.5% target
        opened_at=datetime.now(timezone.utc),
        risk_amount=640,  # 1% of 64000
    )


def _make_signal(direction: str = "buy", regime: str = "calm_trend", confidence: float = 0.7):
    """Create a test BlendedSignal."""
    from hermes.signals.synthesizer import BlendedSignal

    return BlendedSignal(
        signal_id="test-sig-1",
        symbol="BTC",
        venue="hyperliquid",
        direction=direction,
        nt_entry_price=64000,
        nt_stop_price=63360,
        nt_target_price=65600,
        nt_effective_kelly=0.12,
        nt_brick_size=50,
        meta_regime=regime,
        meta_regime_confidence=confidence,
        sizing_multiplier=1.0,
        entry_strategy="enter_now",
        execution_method="market",
        final_size_usd=2000,
        final_size_pct=0.02,
        risk_amount_usd=200,
        brick_pattern="trend_up",
        expected_entry_alpha_bps=-2.0,
        config_hash="test",
    )


def test_decision_tree_closes_on_stop_loss():
    """Decision tree closes position when pnl <= -1%."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(stop_loss_pct=-0.01)
    position = _make_position(pnl_pct=-0.015, direction="long")  # -1.5% pnl

    decision = tree.evaluate_existing_position(position, signal=None, current_price=64000 * 0.985)

    assert decision.action == AgentAction.CLOSE_STOP_LOSS
    assert decision.current_pnl_pct < -0.01


def test_decision_tree_closes_on_take_profit():
    """Decision tree closes position when pnl >= +2.5%."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(take_profit_pct=0.025)
    position = _make_position(pnl_pct=0.03, direction="long")  # +3% pnl

    decision = tree.evaluate_existing_position(position, signal=None, current_price=64000 * 1.03)

    assert decision.action == AgentAction.CLOSE_TAKE_PROFIT


def test_decision_tree_early_profit_with_same_direction():
    """Decision tree takes early profit at +4.5% with same direction signal."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(early_profit_pct=0.045)
    position = _make_position(pnl_pct=0.05, direction="long")  # +5% pnl
    signal = _make_signal(direction="buy")  # same direction

    decision = tree.evaluate_existing_position(position, signal=signal, current_price=64000 * 1.05)

    assert decision.action == AgentAction.CLOSE_EARLY_PROFIT


def test_decision_tree_trails_on_fading():
    """Decision tree trails stop when pnl > 0 + fading bricks."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(fading_brick_count=2)
    position = _make_position(pnl_pct=0.02, direction="long")  # +2% pnl
    signal = _make_signal(direction="buy")  # same direction

    decision = tree.evaluate_existing_position(
        position, signal=signal, current_price=64000 * 1.02, adverse_brick_count=3
    )

    assert decision.action == AgentAction.TRAIL_STOP


def test_decision_tree_holds_on_same_direction_no_exit():
    """Decision tree holds when same direction + no exit condition."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=0.01, direction="long")  # +1% pnl (below early TP)
    signal = _make_signal(direction="buy")  # same direction

    decision = tree.evaluate_existing_position(
        position, signal=signal, current_price=64000 * 1.01, adverse_brick_count=0
    )

    assert decision.action == AgentAction.HOLD


def test_decision_tree_flips_on_opposite_strong_signal():
    """Decision tree flips position on opposite strong signal."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(strong_conviction_threshold=0.7)
    position = _make_position(pnl_pct=0.01, direction="long")  # long position
    signal = _make_signal(direction="sell", confidence=0.85)  # opposite + strong

    decision = tree.evaluate_existing_position(
        position, signal=signal, current_price=64000 * 1.01, adverse_brick_count=0
    )

    assert decision.action == AgentAction.CLOSE_FLIP


def test_decision_tree_holds_on_opposite_weak_signal():
    """Decision tree holds when opposite signal is weak."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(strong_conviction_threshold=0.7)
    position = _make_position(pnl_pct=0.01, direction="long")
    signal = _make_signal(direction="sell", confidence=0.5)  # opposite but weak

    decision = tree.evaluate_existing_position(
        position, signal=signal, current_price=64000 * 1.01, adverse_brick_count=0
    )

    assert decision.action == AgentAction.HOLD_NATIVE_STOPS


def test_decision_tree_holds_native_stops_on_no_signal():
    """Decision tree holds with native stops when no signal."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=0.01, direction="long")

    decision = tree.evaluate_existing_position(position, signal=None, current_price=64000 * 1.01)

    assert decision.action == AgentAction.HOLD_NATIVE_STOPS


def test_decision_tree_enters_new_on_signal():
    """Decision tree enters new position when signal present."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    signal = _make_signal(direction="buy")

    decision = tree.evaluate_new_signal(signal, has_existing_position=False)

    assert decision.action == AgentAction.ENTER_NEW


def test_decision_tree_skips_on_no_signal():
    """Decision tree skips when no signal."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()

    decision = tree.evaluate_new_signal(None, has_existing_position=False)

    assert decision.action == AgentAction.SKIP_NO_SIGNAL


def test_decision_tree_skips_on_blocked_entry():
    """Decision tree skips when entry strategy is blocked."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    signal = _make_signal(direction="buy")
    signal.entry_strategy = "block"

    decision = tree.evaluate_new_signal(signal, has_existing_position=False)

    assert decision.action == AgentAction.SKIP_NO_SIGNAL


def test_decision_tree_short_position_stop_loss():
    """Decision tree handles short position stop-loss correctly."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree(stop_loss_pct=-0.01)
    position = _make_position(pnl_pct=-0.015, direction="short")  # -1.5% pnl for short

    decision = tree.evaluate_existing_position(position, signal=None, current_price=64000 * 1.015)

    assert decision.action == AgentAction.CLOSE_STOP_LOSS


# === Decision tree validation: TP vs early-profit interaction ===


def test_native_tp_25pct_fires_with_no_signal():
    """Native TP (2.5%) fires when NO signal present, even if pnl < 4.5%."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=0.03, direction="long")  # +3% (above 2.5% TP, below 4.5% early)

    decision = tree.evaluate_existing_position(position, signal=None, current_price=64000 * 1.03)

    assert decision.action == AgentAction.CLOSE_TAKE_PROFIT
    assert "native TP" in decision.reason


def test_native_tp_does_NOT_fire_with_signal_present():
    """Native TP (2.5%) does NOT fire when a same-direction signal is present — agent manages instead."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=0.03, direction="long")  # +3% (above 2.5%, below 4.5%)
    signal = _make_signal(direction="buy")  # same direction

    decision = tree.evaluate_existing_position(position, signal=signal, current_price=64000 * 1.03)

    # Should HOLD, not close — agent lets it run toward 4.5% early profit
    assert decision.action == AgentAction.HOLD
    assert decision.action != AgentAction.CLOSE_TAKE_PROFIT


def test_early_profit_45pct_reachable_with_signal():
    """Early profit (4.5%) IS reachable when same-direction signal present — this was the bug."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=0.05, direction="long")  # +5% (above 4.5%)
    signal = _make_signal(direction="buy")  # same direction

    decision = tree.evaluate_existing_position(position, signal=signal, current_price=64000 * 1.05)

    assert decision.action == AgentAction.CLOSE_EARLY_PROFIT
    assert decision.action != AgentAction.CLOSE_TAKE_PROFIT  # native TP didn't fire


def test_sl_always_fires_regardless_of_signal():
    """Stop-loss (-1%) fires even when a same-direction signal is present — it's a hard override."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=-0.015, direction="long")  # -1.5%
    signal = _make_signal(direction="buy")  # same direction

    decision = tree.evaluate_existing_position(position, signal=signal, current_price=64000 * 0.985)

    assert decision.action == AgentAction.CLOSE_STOP_LOSS


def test_trail_fires_before_early_profit_when_fading():
    """When fading (adverse bricks), trail fires before early-profit check."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    position = _make_position(pnl_pct=0.05, direction="long")  # +5% (above 4.5%)
    signal = _make_signal(direction="buy")  # same direction
    # But fading with 3 adverse bricks

    decision = tree.evaluate_existing_position(
        position, signal=signal, current_price=64000 * 1.05, adverse_brick_count=3
    )

    # Trail fires first (pnl > 0 + fading) before early profit check
    assert decision.action == AgentAction.TRAIL_STOP


def test_full_decision_tree_coverage():
    """Exhaustive test: every branch of the decision tree is reachable."""
    from hermes.agent.decision_tree import AgentAction, HermesDecisionTree

    tree = HermesDecisionTree()
    actions_seen = set()

    # 1. SL (no signal)
    pos = _make_position(pnl_pct=-0.02, direction="long")
    d = tree.evaluate_existing_position(pos, signal=None, current_price=64000 * 0.98)
    actions_seen.add(d.action)

    # 2. SL (with signal — hard override)
    pos = _make_position(pnl_pct=-0.02, direction="long")
    sig = _make_signal(direction="buy")
    d = tree.evaluate_existing_position(pos, signal=sig, current_price=64000 * 0.98)
    actions_seen.add(d.action)

    # 3. Native TP (no signal, pnl >= 2.5%)
    pos = _make_position(pnl_pct=0.03, direction="long")
    d = tree.evaluate_existing_position(pos, signal=None, current_price=64000 * 1.03)
    actions_seen.add(d.action)

    # 4. Hold native (no signal, pnl between -1% and 2.5%)
    pos = _make_position(pnl_pct=0.01, direction="long")
    d = tree.evaluate_existing_position(pos, signal=None, current_price=64000 * 1.01)
    actions_seen.add(d.action)

    # 5. Trail (same direction + pnl > 0 + fading)
    pos = _make_position(pnl_pct=0.02, direction="long")
    sig = _make_signal(direction="buy")
    d = tree.evaluate_existing_position(pos, signal=sig, current_price=64000 * 1.02, adverse_brick_count=3)
    actions_seen.add(d.action)

    # 6. Early profit (same direction + pnl >= 4.5%)
    pos = _make_position(pnl_pct=0.05, direction="long")
    sig = _make_signal(direction="buy")
    d = tree.evaluate_existing_position(pos, signal=sig, current_price=64000 * 1.05, adverse_brick_count=0)
    actions_seen.add(d.action)

    # 7. Hold (same direction + no exit condition)
    pos = _make_position(pnl_pct=0.01, direction="long")
    sig = _make_signal(direction="buy")
    d = tree.evaluate_existing_position(pos, signal=sig, current_price=64000 * 1.01, adverse_brick_count=0)
    actions_seen.add(d.action)

    # 8. Flip (opposite + strong)
    pos = _make_position(pnl_pct=0.01, direction="long")
    sig = _make_signal(direction="sell", confidence=0.85)
    d = tree.evaluate_existing_position(pos, signal=sig, current_price=64000 * 1.01, adverse_brick_count=0)
    actions_seen.add(d.action)

    # 9. Hold native stops (opposite + weak)
    pos = _make_position(pnl_pct=0.01, direction="long")
    sig = _make_signal(direction="sell", confidence=0.5)
    d = tree.evaluate_existing_position(pos, signal=sig, current_price=64000 * 1.01, adverse_brick_count=0)
    actions_seen.add(d.action)

    # 10. Enter new (no position + signal)
    sig = _make_signal(direction="buy")
    d = tree.evaluate_new_signal(sig, has_existing_position=False)
    actions_seen.add(d.action)

    # 11. Skip (no position + no signal)
    d = tree.evaluate_new_signal(None, has_existing_position=False)
    actions_seen.add(d.action)

    # Verify all actions were seen
    expected_actions = {
        AgentAction.CLOSE_STOP_LOSS,      # 1, 2
        AgentAction.CLOSE_TAKE_PROFIT,    # 3
        AgentAction.HOLD_NATIVE_STOPS,    # 4, 9
        AgentAction.TRAIL_STOP,           # 5
        AgentAction.CLOSE_EARLY_PROFIT,   # 6
        AgentAction.HOLD,                 # 7
        AgentAction.CLOSE_FLIP,           # 8
        AgentAction.ENTER_NEW,            # 10
        AgentAction.SKIP_NO_SIGNAL,       # 11
    }
    missing = expected_actions - actions_seen
    assert not missing, f"Missing actions: {missing}"


# === Hypothesis Tracker ===


@pytest.mark.asyncio
async def test_hypothesis_tracker_propose(tmp_path):
    """HypothesisTracker creates and stores hypotheses."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.agent.learning import HypothesisTracker

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    tracker = HypothesisTracker(config)
    tracker._db_path = db_path  # override

    hyp = tracker.propose(
        hypothesis="Reduce sizing in choppy_range for BTC",
        rationale="Win rate is 40% but sizing at 0.8x",
        proposed_change={"sizing_multiplier.choppy_range": 0.3},
    )

    assert hyp.status == "proposed"
    assert hyp.hypothesis == "Reduce sizing in choppy_range for BTC"

    # Verify written to DuckDB
    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM hermes_hypotheses").fetchone()[0]
        assert count == 1

    # Test status transition
    tracker.backtest(hyp.hypothesis_id, {"sharpe": 1.2, "confidence": 0.8})
    tracker.promote_to_shadow(hyp.hypothesis_id)
    tracker.promote_to_live(hyp.hypothesis_id)

    hyps = tracker.get_hypotheses()
    assert len(hyps) == 1
    assert hyps[0].status == "live"


def test_hypothesis_tracker_reject():
    """HypothesisTracker can reject hypotheses."""
    from hermes.core.config import load_config
    from hermes.agent.learning import HypothesisTracker

    config = load_config()
    tracker = HypothesisTracker(config)
    # Non-existent ID — should not error
    tracker.reject("nonexistent", "test reason")


# === Decision Journal ===


@pytest.mark.asyncio
async def test_decision_journal_writes_postmortem(tmp_path):
    """DecisionJournalWriter writes postmortems to DuckDB."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.agent.learning import DecisionJournalWriter

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    writer = DecisionJournalWriter(config)
    writer._db_path = db_path  # override

    entry = writer.write_postmortem(
        trade_id="test-trade-1",
        symbol="BTC",
        venue="hyperliquid",
        direction="long",
        entry_thesis="calm_trend regime + breakout pattern",
        exit_reason="take_profit",
        exit_pnl=500.0,
        exit_r_multiple=0.8,
        hold_duration_sec=3600,
        regime_tag="calm_trend",
        postmortem="Good entry timing, TP hit within 1 hour.",
        lessons=["calm_trend works well for breakout entries"],
        tags=["eod_auto"],
    )

    assert entry.trade_id == "test-trade-1"
    assert entry.exit_pnl == 500.0

    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM trade_journal").fetchone()[0]
        assert count == 1


# === Self-Learning Loop ===


@pytest.mark.asyncio
async def test_self_learning_loop_eod_no_trades(tmp_path):
    """SelfLearningLoop handles no trades gracefully."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.agent.learning import SelfLearningLoop

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    import hermes.db.migrate as migrate_mod
    original = migrate_mod.get_duckdb_path
    migrate_mod.get_duckdb_path = lambda c: db_path

    loop = SelfLearningLoop(config)
    loop._db_path = db_path
    loop._hypothesis_tracker._db_path = db_path
    loop._journal_writer._db_path = db_path

    summary = await loop.run_eod_analysis()

    assert summary["trades_analyzed"] == 0
    assert summary["hypotheses_generated"] == 0

    migrate_mod.get_duckdb_path = original


# === CLI ===


def test_cli_agent_help():
    """`platform agent --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["agent", "--help"])
    assert result.exit_code == 0
    assert "--eod" in result.output
    assert "--list-hypotheses" in result.output


def test_cli_agent_default():
    """`platform agent` without flags shows decision tree."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["agent"])
    assert result.exit_code == 0
    assert "Decision Tree" in result.output
    assert "Stop-loss" in result.output


# === Dashboard ===


def test_dashboard_agent_page():
    """GET /agent returns 200."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/agent")
    assert response.status_code == 200
    assert "Decision Tree" in response.text
    assert "Hypotheses" in response.text
    assert "Trade Journal" in response.text


def test_dashboard_api_hypotheses():
    """GET /api/hypotheses returns JSON."""
    from fastapi.testclient import TestClient

    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    client = TestClient(app)

    response = client.get("/api/hypotheses")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "hypotheses" in data
