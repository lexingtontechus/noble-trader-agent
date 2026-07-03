"""
Phase 2 tests — market data schemas, venue adapters, tick aggregator,
indicators, anomaly detector, stop watcher, cross-price monitor,
funding watcher, Parquet writer.

Run with:
    pytest tests/test_phase2.py -v
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone

import pytest


# === Market data schemas ===


def test_tick_schema_valid():
    """Tick validates with required fields."""
    from hermes.schemas.market import Tick, Venue

    tick = Tick(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        price=64441.0,
        size=0.5,
    )
    assert tick.symbol == "BTC"
    assert tick.price == 64441.0


def test_tick_schema_coerces_ts_from_ms():
    """Tick coerces int timestamp to datetime."""
    from hermes.schemas.market import Tick, Venue

    tick = Tick(
        ts=1735900800000,  # ms epoch
        venue=Venue.ALPACA,
        symbol="AAPL",
        price=200.0,
    )
    assert isinstance(tick.ts, datetime)
    assert tick.ts.year == 2025


def test_tick_rejects_negative_price():
    """Tick rejects negative price."""
    from hermes.schemas.market import Tick, Venue
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Tick(
            ts=datetime.now(timezone.utc),
            venue=Venue.ALPACA,
            symbol="AAPL",
            price=-100.0,
        )


def test_bar_schema_valid():
    """Bar validates with OHLCV fields."""
    from hermes.schemas.market import Bar, Venue

    bar = Bar(
        ts_open=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        timeframe="1m",
        open=64441.0,
        high=64500.0,
        low=64400.0,
        close=64480.0,
        volume=10.5,
    )
    assert bar.close == 64480.0
    assert not bar.closed


def test_bar_rejects_high_below_open():
    """Bar rejects high < open."""
    from hermes.schemas.market import Bar, Venue
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Bar(
            ts_open=datetime.now(timezone.utc),
            venue=Venue.ALPACA,
            symbol="AAPL",
            timeframe="1m",
            open=200.0,
            high=199.0,  # < open — invalid
            low=198.0,
            close=199.5,
        )


def test_order_book_properties():
    """OrderBookL2 computes mid_price, spread, imbalance."""
    from hermes.schemas.market import OrderBookL2, OrderBookLevel, Venue

    book = OrderBookL2(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        bids=[
            OrderBookLevel(price=64440.0, size=1.5),
            OrderBookLevel(price=64435.0, size=2.0),
        ],
        asks=[
            OrderBookLevel(price=64442.0, size=1.0),
            OrderBookLevel(price=64445.0, size=0.5),
        ],
    )
    assert book.best_bid.price == 64440.0
    assert book.best_ask.price == 64442.0
    assert book.mid_price == 64441.0
    assert book.spread == 2.0
    assert book.spread_bps is not None
    assert abs(book.imbalance - (3.5 - 1.5) / 5.0) < 0.01  # (bid - ask) / total


def test_funding_rate_is_extreme():
    """FundingRate.is_extreme detects blowout."""
    from hermes.schemas.market import FundingRate, Venue

    normal = FundingRate(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        funding_rate=0.0001,  # 0.01% per 8h — normal
        annualized_pct=10.95,
    )
    extreme = FundingRate(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        funding_rate=0.0006,  # 0.06% per 8h — ~65% annualized
        annualized_pct=65.7,
    )
    assert not normal.is_extreme
    assert extreme.is_extreme


# === Tick Aggregator ===


def test_tick_aggregator_builds_bars():
    """TickAggregator builds 1s bars from ticks."""
    from hermes.monitor.tick_aggregator import TickAggregator
    from hermes.schemas.market import Tick, Venue

    agg = TickAggregator(timeframes=["1s"], window_size=100)
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Send 3 ticks in same second
    for i, price in enumerate([100.0, 101.0, 99.0]):
        tick = Tick(
            ts=base_ts + timedelta(milliseconds=i * 100),
            venue=Venue.ALPACA,
            symbol="AAPL",
            price=price,
            size=10.0,
        )
        agg.on_tick(tick)

    current = agg.get_current_bar("AAPL", "1s")
    assert current is not None
    assert current.open == 100.0
    assert current.high == 101.0
    assert current.low == 99.0
    assert current.close == 99.0
    assert current.volume == 30.0


def test_tick_aggregator_closes_bar_on_new_window():
    """TickAggregator closes bar when time moves to next window."""
    from hermes.monitor.tick_aggregator import TickAggregator
    from hermes.schemas.market import Tick, Venue

    agg = TickAggregator(timeframes=["1s"], window_size=100)
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Tick in second 0
    agg.on_tick(Tick(ts=base_ts, venue=Venue.ALPACA, symbol="AAPL", price=100.0, size=10.0))
    # Tick in second 1 — should close the first bar
    closed = agg.on_tick(
        Tick(ts=base_ts + timedelta(seconds=1), venue=Venue.ALPACA, symbol="AAPL", price=101.0, size=5.0)
    )

    assert len(closed) == 1
    assert closed[0].closed
    assert closed[0].open == 100.0
    assert closed[0].close == 100.0

    # New bar started
    current = agg.get_current_bar("AAPL", "1s")
    assert current is not None
    assert current.open == 101.0


def test_tick_aggregator_last_price():
    """TickAggregator tracks last price."""
    from hermes.monitor.tick_aggregator import TickAggregator
    from hermes.schemas.market import Tick, Venue

    agg = TickAggregator(timeframes=["1s"])
    ts = datetime.now(timezone.utc)

    agg.on_tick(Tick(ts=ts, venue=Venue.ALPACA, symbol="AAPL", price=100.0))
    agg.on_tick(Tick(ts=ts, venue=Venue.ALPACA, symbol="AAPL", price=105.0))

    assert agg.get_last_price("AAPL") == 105.0


# === Indicators ===


def test_indicator_engine_atr():
    """IndicatorEngine computes ATR correctly."""
    from hermes.monitor.indicators import IndicatorEngine
    from hermes.schemas.market import Bar, Venue

    engine = IndicatorEngine()
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Create 20 bars with known ranges
    for i in range(20):
        bar = Bar(
            ts_open=base_ts + timedelta(minutes=i),
            venue=Venue.ALPACA,
            symbol="AAPL",
            timeframe="1m",
            open=100.0 + i,
            high=102.0 + i,
            low=99.0 + i,
            close=101.0 + i,
            volume=1000.0,
            closed=True,
        )
        engine.on_bar(bar)

    atr = engine.get_atr("AAPL", "1m", 14)
    assert atr is not None
    assert atr > 0


def test_indicator_engine_ema():
    """IndicatorEngine computes EMA."""
    from hermes.monitor.indicators import IndicatorEngine
    from hermes.schemas.market import Bar, Venue

    engine = IndicatorEngine()
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Create 30 bars with rising prices
    for i in range(30):
        bar = Bar(
            ts_open=base_ts + timedelta(minutes=i),
            venue=Venue.ALPACA,
            symbol="AAPL",
            timeframe="1m",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0,
            closed=True,
        )
        engine.on_bar(bar)

    ema = engine.get_ema("AAPL", "1m", 20)
    assert ema is not None
    assert 100.0 < ema <= 120.0


def test_indicator_engine_rsi():
    """IndicatorEngine computes RSI."""
    from hermes.monitor.indicators import IndicatorEngine
    from hermes.schemas.market import Bar, Venue

    engine = IndicatorEngine()
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Create 20 bars with rising prices (should give high RSI)
    for i in range(20):
        bar = Bar(
            ts_open=base_ts + timedelta(minutes=i),
            venue=Venue.ALPACA,
            symbol="AAPL",
            timeframe="1m",
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1000.0,
            closed=True,
        )
        engine.on_bar(bar)

    rsi = engine.get_rsi("AAPL", "1m", 14)
    assert rsi is not None
    assert 50 < rsi <= 100  # rising prices → RSI > 50


def test_indicator_engine_returns_none_with_insufficient_data():
    """Indicators return None with insufficient data."""
    from hermes.monitor.indicators import IndicatorEngine
    from hermes.schemas.market import Bar, Venue

    engine = IndicatorEngine()
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Only 5 bars (need 15+ for ATR)
    for i in range(5):
        engine.on_bar(Bar(
            ts_open=base_ts + timedelta(minutes=i),
            venue=Venue.ALPACA,
            symbol="AAPL",
            timeframe="1m",
            open=100.0, high=101.0, low=99.0, close=100.5,
            volume=1000.0, closed=True,
        ))

    assert engine.get_atr("AAPL", "1m", 14) is None
    assert engine.get_ema("AAPL", "1m", 20) is None
    assert engine.get_rsi("AAPL", "1m", 14) is None


# === Anomaly Detector ===


def test_anomaly_detector_no_anomaly_with_normal_ticks():
    """AnomalyDetector doesn't flag normal price action."""
    from hermes.monitor.anomaly_detector import AnomalyDetector
    from hermes.schemas.market import Tick, Venue

    detector = AnomalyDetector()
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Send 200 normal ticks (small random walk)
    price = 100.0
    import random
    random.seed(42)
    for i in range(200):
        price += random.gauss(0, 0.01)
        tick = Tick(
            ts=base_ts + timedelta(milliseconds=i * 100),
            venue=Venue.ALPACA,
            symbol="AAPL",
            price=price,
        )
        event = detector.on_tick(tick)
        assert event is None  # no anomaly for normal movement


def test_anomaly_detector_flags_huge_jump():
    """AnomalyDetector flags a 5-sigma price jump."""
    from hermes.monitor.anomaly_detector import AnomalyDetector
    from hermes.schemas.market import Tick, Venue

    detector = AnomalyDetector(return_sigma_threshold=3.0)
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Send 200 normal ticks
    price = 100.0
    for i in range(200):
        price += 0.001  # tiny moves
        detector.on_tick(Tick(
            ts=base_ts + timedelta(milliseconds=i * 100),
            venue=Venue.ALPACA,
            symbol="AAPL",
            price=price,
        ))

    # Now a huge jump
    event = detector.on_tick(Tick(
        ts=base_ts + timedelta(seconds=20),
        venue=Venue.ALPACA,
        symbol="AAPL",
        price=price * 1.5,  # 50% jump
    ))

    assert event is not None
    assert event.event_type == "anomaly"
    assert event.severity in ("warning", "critical")


# === Stop Watcher ===


def test_stop_watcher_detects_stop_hit():
    """StopWatcher detects when price hits stop-loss."""
    from hermes.monitor.stop_watcher import StopWatcher
    from hermes.schemas.market import Position, Tick, Venue

    watcher = StopWatcher()
    position = Position(
        position_id="test-1",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=64000.0,
        stop_price=63000.0,
        target_price=66000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000.0,
    )
    watcher.add_position(position)

    # Price drops to stop
    events = watcher.on_tick(Tick(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        price=62900.0,
    ))

    stop_events = [e for e in events if e.event_type == "stop_hit"]
    assert len(stop_events) == 1
    assert stop_events[0].severity == "critical"


def test_stop_watcher_detects_target_hit():
    """StopWatcher detects when price hits take-profit."""
    from hermes.monitor.stop_watcher import StopWatcher
    from hermes.schemas.market import Position, Tick, Venue

    watcher = StopWatcher()
    position = Position(
        position_id="test-2",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=64000.0,
        stop_price=63000.0,
        target_price=66000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000.0,
    )
    watcher.add_position(position)

    # Price rises to target
    events = watcher.on_tick(Tick(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        price=66100.0,
    ))

    target_events = [e for e in events if e.event_type == "target_hit"]
    assert len(target_events) == 1


def test_stop_watcher_short_position():
    """StopWatcher handles short positions correctly."""
    from hermes.monitor.stop_watcher import StopWatcher
    from hermes.schemas.market import Position, Tick, Venue

    watcher = StopWatcher()
    position = Position(
        position_id="test-short",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="short",
        qty=1.0,
        entry_price=64000.0,
        stop_price=65000.0,
        target_price=62000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000.0,
    )
    watcher.add_position(position)

    # Price rises to stop (bad for short)
    events = watcher.on_tick(Tick(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        price=65100.0,
    ))

    assert any(e.event_type == "stop_hit" for e in events)

    # Price drops to target (good for short)
    watcher.add_position(Position(
        position_id="test-short-2",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="short",
        qty=1.0,
        entry_price=64000.0,
        stop_price=65000.0,
        target_price=62000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000.0,
    ))
    events = watcher.on_tick(Tick(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        price=61900.0,
    ))
    assert any(e.event_type == "target_hit" for e in events)


def test_stop_watcher_trailing_stop_atr():
    """StopWatcher updates trailing stop using ATR method."""
    from hermes.monitor.stop_watcher import StopWatcher
    from hermes.schemas.market import Position, Tick, Venue

    watcher = StopWatcher(trailing_atr_mult=2.0)
    position = Position(
        position_id="test-trail",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=64000.0,
        stop_price=63000.0,
        target_price=66000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000.0,
        trailing_method="atr",
    )
    watcher.add_position(position)

    # Price moves up, ATR = 200
    events = watcher.on_tick(
        Tick(ts=datetime.now(timezone.utc), venue=Venue.HYPERLIQUID, symbol="BTC", price=64500.0),
        current_atr=200.0,
    )

    trail_events = [e for e in events if e.event_type == "trail_update"]
    assert len(trail_events) == 1
    # Trailing stop = 64500 - 2 * 200 = 64100
    assert position.trailing_stop == 64100.0


def test_stop_watcher_pnl_warning():
    """StopWatcher emits pnl_warning when loss exceeds threshold."""
    from hermes.monitor.stop_watcher import StopWatcher
    from hermes.schemas.market import Position, Tick, Venue

    watcher = StopWatcher(pnl_warning_threshold_r=-0.5)
    position = Position(
        position_id="test-pnl",
        symbol="BTC",
        venue=Venue.HYPERLIQUID,
        direction="long",
        qty=1.0,
        entry_price=64000.0,
        stop_price=63000.0,  # risk = $1000
        target_price=66000.0,
        opened_at=datetime.now(timezone.utc),
        risk_amount=1000.0,
    )
    watcher.add_position(position)

    # Price drops 600 (R = -0.6)
    events = watcher.on_tick(Tick(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        price=63400.0,
    ))

    pnl_warnings = [e for e in events if e.event_type == "pnl_warning"]
    assert len(pnl_warnings) == 1


# === Funding Watcher ===


def test_funding_watcher_detects_spike():
    """FundingWatcher detects extreme funding rates."""
    from hermes.monitor.funding_watcher import FundingWatcher
    from hermes.schemas.market import FundingRate, Venue

    watcher = FundingWatcher(extreme_annualized_pct=50.0)

    # Normal funding
    normal = FundingRate(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        funding_rate=0.0001,
        annualized_pct=10.95,
    )
    assert watcher.on_funding(normal) is None

    # Extreme funding (65% annualized)
    extreme = FundingRate(
        ts=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        funding_rate=0.0006,
        annualized_pct=65.7,
    )
    event = watcher.on_funding(extreme)
    assert event is not None
    assert event.event_type == "funding_spike"


# === Cross-Price Monitor ===


def test_cross_price_monitor_correlation():
    """CrossPriceMonitor computes correlation between symbols."""
    from hermes.monitor.cross_price import CrossPriceMonitor
    from hermes.schemas.market import Tick, Venue

    monitor = CrossPriceMonitor(correlation_window=50, baseline_window=100)
    base_ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Send 60 correlated ticks for BTC and ETH
    for i in range(60):
        btc_price = 64000 + i * 10
        eth_price = 3000 + i * 1  # correlated with BTC
        monitor.on_tick(Tick(ts=base_ts + timedelta(seconds=i), venue=Venue.HYPERLIQUID, symbol="BTC", price=btc_price))
        monitor.on_tick(Tick(ts=base_ts + timedelta(seconds=i), venue=Venue.HYPERLIQUID, symbol="ETH", price=eth_price))

    matrix = monitor.get_correlation_matrix(["BTC", "ETH"])
    assert "BTC" in matrix
    assert "ETH" in matrix
    assert matrix["BTC"]["ETH"] is not None
    assert matrix["BTC"]["ETH"] > 0.5  # should be positively correlated


# === Parquet Writer ===


@pytest.mark.asyncio
async def test_parquet_writer_writes_bars(tmp_path):
    """ParquetWriter writes bars to partitioned Parquet files."""
    from hermes.transport.parquet_writer import ParquetWriter
    from hermes.schemas.market import Bar, Venue

    writer = ParquetWriter(base_path=str(tmp_path / "parquet"), batch_size=1)
    await writer.start()

    bar = Bar(
        ts_open=datetime.now(timezone.utc),
        venue=Venue.HYPERLIQUID,
        symbol="BTC",
        timeframe="1m",
        open=64441.0,
        high=64500.0,
        low=64400.0,
        close=64480.0,
        volume=10.5,
        closed=True,
    )
    await writer.write_bar(bar)

    # Wait for flush
    await asyncio.sleep(0.5)
    await writer.stop()

    # Check Parquet file was created
    parquet_files = list((tmp_path / "parquet" / "bars").glob("**/*.parquet"))
    assert len(parquet_files) > 0

    # Verify we can read it back — venue/symbol/timeframe are in partition path
    import pandas as pd

    df = pd.read_parquet(parquet_files[0])
    assert len(df) == 1
    assert df.iloc[0]["close"] == 64480.0
    assert df.iloc[0]["open"] == 64441.0
    assert df.iloc[0]["volume"] == 10.5

    # Verify partition structure (symbol/venue/timeframe are in the path)
    file_path = str(parquet_files[0])
    assert "symbol=BTC" in file_path
    assert "venue=hyperliquid" in file_path
    assert "tf=1m" in file_path


# === Venue Adapter Interface ===


def test_alpaca_adapter_normalizes_symbol():
    """AlpacaAdapter normalizes symbols correctly."""
    from hermes.transport.adapters.alpaca_adapter import AlpacaAdapter
    from hermes.core.config import load_config

    config = load_config()
    adapter = AlpacaAdapter(config)

    assert adapter.normalize_symbol("AAPL") == "AAPL"
    assert adapter.normalize_symbol("GLD") == "GLD"


def test_hyperliquid_adapter_normalizes_symbol():
    """HyperliquidAdapter strips -PERP suffix for API calls."""
    from hermes.transport.adapters.hyperliquid_adapter import HyperliquidAdapter
    from hermes.core.config import load_config

    config = load_config()
    adapter = HyperliquidAdapter(config)

    assert adapter.normalize_symbol("BTC-PERP") == "BTC"
    assert adapter.normalize_symbol("ETH-PERP") == "ETH"
    assert adapter.normalize_symbol("BTC") == "BTC"


# === CLI ===


def test_cli_stream_help():
    """`platform stream --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["stream", "--help"])
    assert result.exit_code == 0
    assert "--symbols" in result.output
    assert "--venues" in result.output


def test_cli_monitor_help():
    """`platform monitor --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["monitor", "--help"])
    assert result.exit_code == 0
    assert "--symbols" in result.output


def test_cli_backfill_market_help():
    """`platform backfill-market --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["backfill-market", "--help"])
    assert result.exit_code == 0
    assert "--symbol" in result.output
    assert "--venue" in result.output
