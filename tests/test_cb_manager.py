"""
Circuit Breaker Manager tests — all categories, time-decay, rolling windows.

Run with:
    pytest tests/test_cb_manager.py -v
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest


# === Manager Initialization ===


def test_manager_initializes_with_defaults():
    """CircuitBreakerManager initializes with default config."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    assert manager is not None
    assert not manager.is_any_tripped()
    assert manager.get_size_multiplier() == 1.0


def test_manager_initializes_with_custom_config():
    """CircuitBreakerManager accepts custom config."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    custom_config = {
        "drawdown": {
            "name": "Custom DD",
            "enabled": True,
            "tiers": [
                {"threshold": 0.10, "action": "reduce_50pct", "label": "10%", "cooldown_sec": 0},
            ],
        }
    }
    manager = CircuitBreakerManager.from_config(custom_config)
    configs = manager.get_config()
    assert "drawdown" in configs
    assert configs["drawdown"].tiers[0].threshold == 0.10


# === Portfolio Exposure ===


def test_portfolio_exposure_no_trip_below_80():
    """No trip when exposure < 80%."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_portfolio_exposure(gross_exposure_usd=70000, equity=100000)
    assert len(trips) == 0
    assert not manager.is_any_tripped()


def test_portfolio_exposure_reduce_at_80():
    """REDUCE_25 at 80% exposure."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_portfolio_exposure(gross_exposure_usd=80000, equity=100000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_25
    assert manager.get_size_multiplier() == 0.75


def test_portfolio_exposure_reduce_50_at_90():
    """REDUCE_50 at 90% exposure."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_portfolio_exposure(gross_exposure_usd=90000, equity=100000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_50
    assert manager.get_size_multiplier() == 0.50


def test_portfolio_exposure_block_at_100():
    """BLOCK at 100% exposure."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_portfolio_exposure(gross_exposure_usd=100000, equity=100000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.BLOCK_ENTRIES
    assert manager.get_size_multiplier() == 0.0


def test_portfolio_exposure_halt_at_150():
    """HALT_ALL at 150% exposure."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_portfolio_exposure(gross_exposure_usd=150000, equity=100000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.HALT_ALL
    assert manager.get_size_multiplier() == 0.0


def test_portfolio_exposure_clears_when_below_threshold():
    """Trip clears when exposure drops below threshold."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    manager.check_portfolio_exposure(gross_exposure_usd=85000, equity=100000)
    assert manager.is_any_tripped()

    manager.check_portfolio_exposure(gross_exposure_usd=50000, equity=100000)
    assert not manager.is_any_tripped()
    assert manager.get_size_multiplier() == 1.0


# === Position Size ===


def test_position_size_reduce_at_50k():
    """REDUCE_25 at $50k position."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_position_size(position_notional_usd=50000, symbol="BTC")
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_25


def test_position_size_block_at_100k():
    """BLOCK at $100k position."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_position_size(position_notional_usd=100000, symbol="BTC")
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.BLOCK_ENTRIES


def test_position_size_no_trip_below_50k():
    """No trip below $50k."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_position_size(position_notional_usd=40000, symbol="BTC")
    assert len(trips) == 0


# === Daily Loss Limit ===


def test_daily_loss_no_trip_on_profit():
    """No trip when daily PnL is positive."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_daily_loss(daily_loss_usd=5000)  # profit
    assert len(trips) == 0


def test_daily_loss_reduce_at_5k():
    """REDUCE_50 at $5k daily loss."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_daily_loss(daily_loss_usd=-5000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_50


def test_daily_loss_block_at_10k():
    """BLOCK at $10k daily loss."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_daily_loss(daily_loss_usd=-10000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.BLOCK_ENTRIES


def test_daily_loss_halt_at_15k():
    """HALT_ALL at $15k daily loss."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_daily_loss(daily_loss_usd=-15000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.HALT_ALL


# === VaR ===


def test_var_reduce_at_50k():
    """REDUCE_50 at $50k VaR."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_var(var_1d_99_usd=-50000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_50


def test_var_block_at_100k():
    """BLOCK at $100k VaR."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_var(var_1d_99_usd=-100000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.BLOCK_ENTRIES


def test_var_no_trip_below_50k():
    """No trip below $50k VaR."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_var(var_1d_99_usd=-30000)
    assert len(trips) == 0


# === Drawdown ===


def test_drawdown_reduce_at_15():
    """REDUCE_50 at 15% drawdown."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_drawdown(drawdown_pct=0.15, drawdown_usd=15000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_50


def test_drawdown_block_at_20():
    """BLOCK at 20% drawdown."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_drawdown(drawdown_pct=0.20, drawdown_usd=20000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.BLOCK_ENTRIES


def test_drawdown_liquidate_at_25():
    """LIQUIDATE at 25% drawdown."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_drawdown(drawdown_pct=0.25, drawdown_usd=25000)
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.LIQUIDATE


def test_drawdown_no_trip_below_15():
    """No trip below 15% drawdown."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_drawdown(drawdown_pct=0.10)
    assert len(trips) == 0


# === Funding Rate ===


def test_funding_no_trip_on_income():
    """No trip when funding income (negative cost)."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_funding_rate(daily_funding_cost_usd=-50, symbol="BTC")
    assert len(trips) == 0


def test_funding_temp_block_at_50():
    """TEMP_BLOCK at $50/day funding cost."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_funding_rate(daily_funding_cost_usd=50, symbol="BTC")
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.TEMP_BLOCK


def test_funding_block_at_200():
    """BLOCK at $200/day funding cost."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    trips = manager.check_funding_rate(daily_funding_cost_usd=200, symbol="BTC")
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.BLOCK_ENTRIES


# === Consecutive Losses (Rolling Window) ===


def test_consecutive_losses_reduce_at_3():
    """REDUCE_50 after 3 consecutive losses."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    manager.record_trade_result(won=False)  # 1
    assert not manager.is_any_tripped()

    manager.record_trade_result(won=False)  # 2
    assert not manager.is_any_tripped()

    trips = manager.record_trade_result(won=False)  # 3
    assert len(trips) == 1
    assert trips[0].action == BreakerAction.REDUCE_50


def test_consecutive_losses_block_at_5():
    """BLOCK after 5 consecutive losses."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    for _ in range(5):
        manager.record_trade_result(won=False)

    assert manager.is_category_tripped("consecutive_losses")
    trips = manager.get_active_trips()
    assert any(t.action == BreakerAction.BLOCK_ENTRIES for t in trips)


def test_consecutive_losses_clears_on_win():
    """Consecutive loss counter resets on a win."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    for _ in range(3):
        manager.record_trade_result(won=False)
    assert manager.is_category_tripped("consecutive_losses")

    manager.record_trade_result(won=True)  # win resets counter
    assert not manager.is_category_tripped("consecutive_losses")


# === Time-Decay ===


def test_time_decay_auto_clears():
    """Circuit breaker auto-clears after cooldown period."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    # Use very short cooldown for testing
    custom_config = {
        "drawdown": {
            "name": "DD",
            "enabled": True,
            "tiers": [
                {"threshold": 0.15, "action": "block_entries", "label": "15%", "cooldown_sec": 0.1},
            ],
        }
    }
    manager = CircuitBreakerManager.from_config(custom_config)

    # Trip the breaker
    manager.check_drawdown(drawdown_pct=0.16)
    assert manager.is_category_tripped("drawdown")

    # Wait for cooldown to expire
    time.sleep(0.15)

    # Should auto-clear on next check
    assert not manager.is_category_tripped("drawdown")


def test_time_decay_not_expired_still_active():
    """Circuit breaker stays active if cooldown hasn't expired."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    custom_config = {
        "drawdown": {
            "name": "DD",
            "enabled": True,
            "tiers": [
                {"threshold": 0.15, "action": "block_entries", "label": "15%", "cooldown_sec": 3600},
            ],
        }
    }
    manager = CircuitBreakerManager.from_config(custom_config)

    manager.check_drawdown(drawdown_pct=0.16)
    assert manager.is_category_tripped("drawdown")

    # Wait a tiny bit (not enough for 1h cooldown)
    time.sleep(0.05)

    assert manager.is_category_tripped("drawdown")  # still active


# === Rolling Window: Trip Frequency ===


def test_trip_frequency_reduce_at_5():
    """REDUCE_50 after 5 trips in 24h."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    for _ in range(5):
        manager.record_trip()

    trips = manager.get_active_trips()
    assert any(t.category == "trip_frequency" for t in trips)


def test_trip_frequency_halt_at_10():
    """HALT_ALL after 10 trips in 24h."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    for _ in range(10):
        manager.record_trip()

    action = manager.get_blocking_action()
    assert action == BreakerAction.HALT_ALL


# === Combined / Multi-Category ===


def test_multiple_categories_trip_simultaneously():
    """Multiple categories can trip simultaneously."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    manager.check_portfolio_exposure(gross_exposure_usd=95000, equity=100000)  # 95% → reduce_50
    manager.check_drawdown(drawdown_pct=0.18)  # 18% → reduce_50

    trips = manager.get_active_trips()
    assert len(trips) >= 2
    assert manager.is_any_tripped()


def test_most_severe_action_wins():
    """When multiple trips active, most severe action is returned."""
    from hermes.portfolio.cb_manager import BreakerAction, CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    manager.check_portfolio_exposure(gross_exposure_usd=85000, equity=100000)  # reduce_25
    manager.check_drawdown(drawdown_pct=0.25)  # liquidate

    action = manager.get_blocking_action()
    assert action == BreakerAction.LIQUIDATE  # most severe


def test_size_multiplier_takes_minimum():
    """Size multiplier takes the minimum across all active trips."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()

    manager.check_portfolio_exposure(gross_exposure_usd=82000, equity=100000)  # reduce_25 → 0.75
    manager.check_daily_loss(daily_loss_usd=-6000)  # reduce_50 → 0.50

    assert manager.get_size_multiplier() == 0.50  # minimum


def test_clear_all():
    """Clear all trips manually."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    manager.check_drawdown(drawdown_pct=0.16)
    manager.check_portfolio_exposure(gross_exposure_usd=85000, equity=100000)
    assert manager.is_any_tripped()

    manager.clear()
    assert not manager.is_any_tripped()
    assert manager.get_size_multiplier() == 1.0


def test_clear_specific_category():
    """Clear a specific category."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    manager.check_drawdown(drawdown_pct=0.16)
    manager.check_portfolio_exposure(gross_exposure_usd=85000, equity=100000)

    manager.clear("drawdown")
    assert not manager.is_category_tripped("drawdown")
    assert manager.is_category_tripped("portfolio_exposure")


# === Status & Stats ===


def test_get_status():
    """get_status returns full status for dashboard."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    manager.check_drawdown(drawdown_pct=0.16)

    status = manager.get_status()
    assert status["any_tripped"] is True
    assert len(status["active_trips"]) == 1
    assert status["active_trips"][0]["category"] == "drawdown"
    assert "configs" in status
    assert "drawdown" in status["configs"]


def test_get_stats():
    """get_stats returns statistics."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    manager = CircuitBreakerManager.from_config()
    manager.check_drawdown(drawdown_pct=0.16)
    manager.check_portfolio_exposure(gross_exposure_usd=85000, equity=100000)

    stats = manager.get_stats()
    assert stats["total_trips"] >= 2
    assert stats["active_trips"] >= 2
    assert stats["size_multiplier"] < 1.0
    assert "rolling" in stats


# === RollingWindowTracker ===


def test_rolling_window_tracker():
    """RollingWindowTracker tracks events in a time window."""
    from hermes.portfolio.cb_manager import RollingWindowTracker

    tracker = RollingWindowTracker(window_sec=0.2)  # 200ms window

    tracker.add(1)
    tracker.add(2)
    tracker.add(3)

    assert tracker.count() == 3
    assert tracker.sum() == 6

    # Wait for window to expire
    time.sleep(0.25)

    assert tracker.count() == 0
    assert tracker.sum() == 0


def test_rolling_window_tracker_clear():
    """RollingWindowTracker can be cleared."""
    from hermes.portfolio.cb_manager import RollingWindowTracker

    tracker = RollingWindowTracker(window_sec=60)
    tracker.add(1)
    tracker.add(1)
    assert tracker.count() == 2

    tracker.clear()
    assert tracker.count() == 0


# === Disabled Breaker ===


def test_disabled_breaker_does_not_trip():
    """Disabled breaker category never trips."""
    from hermes.portfolio.cb_manager import CircuitBreakerManager

    custom_config = {
        "drawdown": {
            "name": "DD",
            "enabled": False,
            "tiers": [
                {"threshold": 0.15, "action": "block_entries", "label": "15%", "cooldown_sec": 0},
            ],
        }
    }
    manager = CircuitBreakerManager.from_config(custom_config)
    trips = manager.check_drawdown(drawdown_pct=0.20)
    assert len(trips) == 0
    assert not manager.is_any_tripped()
