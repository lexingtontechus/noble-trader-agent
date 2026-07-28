"""Tests for Task 1/2/3: SSE->DuckDB persistence, MetaApi market fallback, SSE watchdog hooks.

Run: PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_sse_metaapi_fallback.py -q
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

# ─── helpers ────────────────────────────────────────────────────────────────

def _apply_schema(db_path: Path) -> None:
    import duckdb

    schema = Path(__file__).parent.parent / "src" / "hermes" / "db" / "schema.sql"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema.read_text(encoding="utf-8"))


def _config_stub(db_path: Path, venues: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        upstream={"noble_trader": {"quote_proxy": {"url": "https://proxy.example"}}},
        duckdb={"path": str(db_path)},
        venues=venues or {},
    )


# ─── Task 1: SSE microstructure -> DuckDB ───────────────────────────────────

async def test_sse_microstructure_persisted_to_duckdb(tmp_path):
    from hermes.transport.sse_consumer import MicrostructureSSEConsumer

    db = tmp_path / "hermes.duckdb"
    _apply_schema(db)
    cfg = _config_stub(db)

    consumer = MicrostructureSSEConsumer(cfg, symbols=["BTCUSD"])
    payload = {
        "symbol": "BTCUSD",
        "ts_ms": 1700000000000,
        "p_microstructure": 0.42,
        "p_micro_l1": 0.3,
        "p_micro_ta": 0.12,
        "direction": "buy",
        "ta_vetoed": False,
    }
    await consumer._handle_microstructure(json.dumps(payload))

    # Verify row written
    import duckdb

    with duckdb.connect(str(db), read_only=True) as conn:
        rows = conn.execute(
            "SELECT symbol, ts_ms, p_microstructure, direction FROM microstructure_events"
        ).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == "BTCUSD"
    assert rows[0][1] == 1700000000000
    assert rows[0][2] == 0.42
    assert rows[0][3] == "buy"
    assert consumer.get_stats()["microstructure_frames"] == 1


async def test_sse_alert_frames_ignored_not_persisted(tmp_path):
    from hermes.transport.sse_consumer import MicrostructureSSEConsumer

    db = tmp_path / "hermes.duckdb"
    _apply_schema(db)
    cfg = _config_stub(db)

    consumer = MicrostructureSSEConsumer(cfg, symbols=["BTCUSD"])
    # alert event should NOT be persisted (duplicate of signal pipeline)
    await consumer._handle_frame("alert", json.dumps({"symbol": "BTCUSD", "signal": "buy"}))
    import duckdb

    with duckdb.connect(str(db), read_only=True) as conn:
        n = conn.execute("SELECT COUNT(*) FROM microstructure_events").fetchone()[0]
    assert n == 0
    assert consumer.get_stats()["alerts_ignored"] >= 1


# ─── Task 2: MetaApi market fallback ────────────────────────────────────────

class _FakeAdapter:
    def __init__(self):
        self._rpc = SimpleNamespace()
        self._configured = True
        self.connected = False
        self.disconnected = False
        self.price_calls = 0
        self.bar_calls = 0
        self._price = 1.1750

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.disconnected = True

    async def get_current_price(self, symbol):
        self.price_calls += 1
        return self._price

    async def fetch_historical_bars(self, symbol, tf, start, end, limit=10000):
        self.bar_calls += 1
        return [("bar",)]

    def get_credit_usage(self):
        return {"used": 10, "total": 100}


async def test_fallback_price_cache_and_lifecycle():
    from hermes.transport.metaapi_market_fallback import MetaApiMarketFallback

    adapter = _FakeAdapter()
    fb = MetaApiMarketFallback(SimpleNamespace(venues={}), adapter=adapter)

    # Not active -> no data, no connect
    assert await fb.get_price("EURUSD") is None
    assert adapter.connected is False

    await fb.activate()
    assert fb.active and adapter.connected

    # First call hits adapter, second within TTL is cached
    p1 = await fb.get_price("EURUSD")
    p2 = await fb.get_price("EURUSD")
    assert p1 == p2 == 1.1750
    assert adapter.price_calls == 1  # cached on 2nd
    assert fb.get_stats()["price_hits"] == 1

    await fb.deactivate()
    assert not fb.active and adapter.disconnected
    assert await fb.get_price("EURUSD") is None  # inactive


async def test_fallback_bars_cached_and_credit_guard():
    from hermes.transport.metaapi_market_fallback import MetaApiMarketFallback

    adapter = _FakeAdapter()
    fb = MetaApiMarketFallback(SimpleNamespace(venues={}), adapter=adapter)
    await fb.activate()

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)
    b1 = await fb.get_bars("EURUSD", "1m", start, end)
    b2 = await fb.get_bars("EURUSD", "1m", start, end)
    assert b1 == b2 == [("bar",)]
    assert adapter.bar_calls == 1  # cached on 2nd
    assert fb.get_stats()["bar_hits"] == 1

    # Low-credit guard: returns [] + increments skipped counter
    adapter._rpc.get_credit_usage = lambda: {"used": 95, "total": 100}
    bars = await fb.get_bars("GBPUSD", "1m", start, end)
    assert bars == []
    assert fb.get_stats()["bar_skipped_low_credit"] == 1


# ─── Task 3: watchdog hooks ─────────────────────────────────────────────────

class _FakeConsumer:
    def __init__(self, last_frame_at):
        self._last = last_frame_at
        self._running = True

    def get_stats(self):
        return {"last_frame_at": self._last}

    def set_last(self, v):
        self._last = v


class _FakeAlertManager:
    def __init__(self):
        self.alerts = []

    async def send_alert(self, alert):
        self.alerts.append(alert)


async def test_watchdog_fires_on_dead_and_on_restored():
    from hermes.transport.pricing_sse_watchdog import PricingSSEWatchdog

    events = []
    consumer = _FakeConsumer(last_frame_at=1000.0)
    am = _FakeAlertManager()

    async def _on_dead():
        events.append("dead")

    async def _on_restored():
        events.append("restored")

    wd = PricingSSEWatchdog(
        _config_stub(Path("/tmp/none.duckdb")),
        consumer,
        am,
        timeout_sec=10.0,
        poll_interval_sec=1.0,
        now=lambda: 2000.0,  # 1000s since last frame > 10s timeout -> dead
        on_dead=_on_dead,
        on_restored=_on_restored,
    )

    await wd._check_once()
    assert "dead" in events
    assert any(a.severity.value == "critical" for a in am.alerts)  # type: ignore[attr-defined]

    # Still dead -> no duplicate hook / alert
    events.clear()
    am.alerts.clear()
    await wd._check_once()
    assert events == []
    assert am.alerts == []

    # Restored: move last_frame_at to within timeout of the new "now"
    consumer.set_last(2595.0)
    wd._now = lambda: 2600.0  # 5s since frame < 10s timeout -> ok
    await wd._check_once()
    assert "restored" in events
    assert any(a.severity.value == "info" for a in am.alerts)  # type: ignore[attr-defined]
