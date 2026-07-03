"""
Phase 10 tests — dead man's switch, alerting, replay engine.

Run with:
    pytest tests/test_phase10.py -v
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# === Dead Man's Switch ===


def test_dms_starts_alive():
    """Dead man's switch starts in alive state."""
    from hermes.ops.dead_mans_switch import DeadMansSwitch

    dms = DeadMansSwitch(timeout_sec=60)
    assert dms.is_alive
    assert not dms.is_activated


def test_dms_heartbeat_resets_timer():
    """Calling heartbeat() resets the timeout timer."""
    from hermes.ops.dead_mans_switch import DeadMansSwitch

    dms = DeadMansSwitch(timeout_sec=0.1)  # 100ms timeout

    # Without heartbeat, should activate quickly
    time.sleep(0.2)
    assert not dms.is_alive  # timeout exceeded

    # Reset with heartbeat
    dms.heartbeat()
    assert dms.is_alive  # alive again


@pytest.mark.asyncio
async def test_dms_activates_on_timeout():
    """Dead man's switch activates when heartbeat is missed."""
    from hermes.ops.dead_mans_switch import DeadMansSwitch

    activated = False

    async def on_activate(reason, flatten):
        nonlocal activated
        activated = True

    dms = DeadMansSwitch(
        timeout_sec=0.2,  # 200ms timeout
        check_interval_sec=0.05,  # check every 50ms
        auto_flatten=True,
        on_activate=on_activate,
    )

    await dms.start()

    # Wait for activation (no heartbeat sent)
    await asyncio.sleep(0.5)

    assert dms.is_activated
    assert activated

    await dms.stop()


@pytest.mark.asyncio
async def test_dms_deactivates_on_heartbeat():
    """Dead man's switch deactivates when heartbeat is received after activation."""
    from hermes.ops.dead_mans_switch import DeadMansSwitch

    dms = DeadMansSwitch(timeout_sec=0.1, check_interval_sec=0.05)
    await dms.start()

    # Wait for activation
    await asyncio.sleep(0.3)
    assert dms.is_activated

    # Send heartbeat → should deactivate
    dms.heartbeat("test")
    assert not dms.is_activated
    assert dms.is_alive

    await dms.stop()


def test_dms_get_state():
    """get_state returns correct state."""
    from hermes.ops.dead_mans_switch import DeadMansSwitch

    dms = DeadMansSwitch(timeout_sec=30, auto_flatten=False)
    state = dms.get_state()

    assert state.is_alive
    assert state.timeout_sec == 30
    assert state.auto_flatten is False
    assert not state.activated


# === Alerting ===


def test_alert_formats_discord():
    """Alert formats correctly for Discord."""
    from hermes.ops.alerting import Alert, AlertSeverity

    alert = Alert(
        title="Test Alert",
        message="This is a test",
        severity=AlertSeverity.WARNING,
        data={"key": "value"},
    )

    payload = alert.to_discord()
    assert "embeds" in payload
    assert payload["embeds"][0]["title"] == "[WARNING] Test Alert"
    assert payload["embeds"][0]["description"] == "This is a test"


def test_alert_formats_telegram():
    """Alert formats correctly for Telegram."""
    from hermes.ops.alerting import Alert, AlertSeverity

    alert = Alert(
        title="Test Alert",
        message="This is a test",
        severity=AlertSeverity.CRITICAL,
        data={"drawdown": "15.5%"},
    )

    text = alert.to_telegram()
    assert "Test Alert" in text
    assert "CRITICAL" in text
    assert "drawdown" in text


def test_alert_manager_initializes():
    """AlertManager initializes without error."""
    from hermes.core.config import load_config
    from hermes.ops.alerting import AlertManager

    config = load_config()
    manager = AlertManager(config)

    # Without configured webhooks, both should be disabled
    assert isinstance(manager.is_discord_enabled(), bool)
    assert isinstance(manager.is_telegram_enabled(), bool)


@pytest.mark.asyncio
async def test_alert_manager_sends_no_op_when_disabled():
    """AlertManager handles disabled channels gracefully."""
    from hermes.core.config import load_config
    from hermes.ops.alerting import Alert, AlertManager, AlertSeverity

    config = load_config()
    manager = AlertManager(config)
    await manager.start()

    alert = Alert(
        title="Test",
        message="Test message",
        severity=AlertSeverity.INFO,
    )
    await manager.send_alert(alert)

    stats = manager.get_stats()
    # Alert was "sent" (attempted) but no channels delivered
    assert stats["alerts_sent"] == 1
    assert stats["discord_sent"] == 0  # disabled
    assert stats["telegram_sent"] == 0  # disabled

    await manager.stop()


# === Replay Engine ===


@pytest.mark.asyncio
async def test_replay_engine_no_data(tmp_path):
    """ReplayEngine handles missing DuckDB gracefully."""
    from hermes.core.config import load_config
    from hermes.ops.replay import ReplayEngine

    config = load_config()
    engine = ReplayEngine(config)

    # Use a non-existent DB path
    import hermes.db.migrate as migrate_mod
    original = migrate_mod.get_duckdb_path
    migrate_mod.get_duckdb_path = lambda c: tmp_path / "nonexistent.duckdb"
    engine._db_path = tmp_path / "nonexistent.duckdb"

    result = await engine.replay(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert len(result.errors) > 0
    assert "not found" in result.errors[0].lower()

    migrate_mod.get_duckdb_path = original


@pytest.mark.asyncio
async def test_replay_engine_empty_db(tmp_path):
    """ReplayEngine returns empty timeline for a DB with no data."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.ops.replay import ReplayEngine

    config = load_config()
    db_path = tmp_path / "test.duckdb"

    schema_file = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "schema.sql"
    migrations_dir = Path(__file__).resolve().parent.parent / "src" / "hermes" / "db" / "migrations"

    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))
        for mig in sorted(migrations_dir.glob("*.sql")):
            conn.execute(mig.read_text(encoding="utf-8"))

    engine = ReplayEngine(config)
    engine._db_path = db_path

    result = await engine.replay(
        start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    assert result.n_heartbeats == 0
    assert result.n_signals == 0
    assert len(result.timeline) == 0
    assert len(result.errors) == 0


# === CLI ===


def test_cli_replay_help():
    """`platform replay --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["replay", "--help"])
    assert result.exit_code == 0
    assert "--start" in result.output
    assert "--end" in result.output


def test_cli_alert_test_help():
    """`platform alert-test --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["alert-test", "--help"])
    assert result.exit_code == 0


def test_cli_load_test_help():
    """`platform load-test --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["load-test", "--help"])
    assert result.exit_code == 0
    assert "--duration-sec" in result.output
    assert "--rate-per-sec" in result.output
