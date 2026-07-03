"""
Phase 1 tests — heartbeat schema validation, L0 processing, DuckDB writer.

Run with:
    pytest tests/test_phase1.py -v
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest


# === Heartbeat schema validation ===


VALID_HEARTBEAT_PAYLOAD = {
    "type": "heartbeat",
    "symbol": "BTC",
    "ts": 1735900800000,  # 2025-01-03T08:00:00Z
    "regime": "low_vol_bull",
    "regime_conf": 0.85,
    "signal": "buy",
    "entry_price": 64441.0,
    "stop_loss": 63941.0,
    "take_profit": 65441.0,
    "aggression": "mid",
    "brick_size": 50.0,
    "sl_bricks": 3,
    "tp_bricks": 5,
    "kelly_f": 0.15,
    "effective_kelly": 0.12,
    "ev": 0.35,
    "ev_per_dollar": 0.12,
    "p_win": 0.62,
    "p_regime": 0.55,
    "p_imbalance": 0.48,
    "p_markov": 0.50,
    "ev_scale": 0.80,
    "markov_current_state": "UP",
    "p_timesfm": 0.48,
    "timesfm_horizon": "12h",
    "tail_risk_score": 0.60,
    "tail_risk_action": "reduce_50",
    "regime_shift": "false",
    "prev_regime": None,
    "shift_at": 0,
    "shifts_24h": 2,
}


def test_heartbeat_validates():
    """Valid heartbeat parses without error."""
    from hermes.schemas.heartbeat import parse_heartbeat

    hb = parse_heartbeat(json.dumps(VALID_HEARTBEAT_PAYLOAD), strategy_id="noble_trader")
    assert hb.symbol == "BTC"
    assert hb.signal == "buy"
    assert hb.regime == "low_vol_bull"
    assert hb.regime_conf == 0.85
    assert hb.strategy_id == "noble_trader"


def test_heartbeat_rejects_invalid_signal():
    """Invalid signal value raises HeartbeatValidationError."""
    from hermes.schemas.heartbeat import HeartbeatValidationError, parse_heartbeat

    payload = VALID_HEARTBEAT_PAYLOAD.copy()
    payload["signal"] = "hold"  # invalid — must be buy/sell/neutral
    with pytest.raises(HeartbeatValidationError):
        parse_heartbeat(json.dumps(payload))


def test_heartbeat_rejects_missing_field():
    """Missing required field raises HeartbeatValidationError."""
    from hermes.schemas.heartbeat import HeartbeatValidationError, parse_heartbeat

    payload = VALID_HEARTBEAT_PAYLOAD.copy()
    del payload["symbol"]
    with pytest.raises(HeartbeatValidationError):
        parse_heartbeat(json.dumps(payload))


def test_heartbeat_rejects_invalid_json():
    """Malformed JSON raises HeartbeatValidationError."""
    from hermes.schemas.heartbeat import HeartbeatValidationError, parse_heartbeat

    with pytest.raises(HeartbeatValidationError):
        parse_heartbeat("{not valid json")


def test_heartbeat_accepts_bytes_payload():
    """Parser accepts bytes (as Redis delivers)."""
    from hermes.schemas.heartbeat import parse_heartbeat

    payload_bytes = json.dumps(VALID_HEARTBEAT_PAYLOAD).encode("utf-8")
    hb = parse_heartbeat(payload_bytes)
    assert hb.symbol == "BTC"


def test_heartbeat_regime_shift_true():
    """regime_shift='true' parses correctly to boolean True in DuckDB row."""
    from hermes.schemas.heartbeat import parse_heartbeat

    payload = VALID_HEARTBEAT_PAYLOAD.copy()
    payload["regime_shift"] = "true"
    payload["prev_regime"] = "low_vol_bear"
    hb = parse_heartbeat(json.dumps(payload))
    row = hb.to_duckdb_row(
        ts_received=datetime.now(timezone.utc),
        dedup_hash="abc123",
        raw_payload="{}",
    )
    assert row["regime_shift"] is True
    assert row["prev_regime"] == "low_vol_bear"


def test_heartbeat_to_duckdb_row():
    """to_duckdb_row produces a dict with all expected keys."""
    from hermes.schemas.heartbeat import parse_heartbeat

    hb = parse_heartbeat(json.dumps(VALID_HEARTBEAT_PAYLOAD))
    row = hb.to_duckdb_row(
        ts_received=datetime.now(timezone.utc),
        dedup_hash="abc123",
        raw_payload='{"test": true}',
    )

    expected_keys = {
        "heartbeat_id", "ts_received", "ts_upstream", "lag_ms", "dedup_hash",
        "symbol", "strategy_id", "type", "signal", "entry_price", "stop_loss",
        "take_profit", "aggression", "brick_size", "sl_bricks", "tp_bricks",
        "regime", "regime_conf", "regime_shift", "prev_regime", "shift_at",
        "shifts_24h", "ev", "ev_per_dollar", "p_win", "p_regime", "p_imbalance",
        "p_markov", "ev_scale", "p_timesfm", "timesfm_horizon",
        "markov_current_state", "tail_risk_score", "tail_risk_action",
        "kelly_f", "effective_kelly", "raw_payload", "accepted", "reject_reason",
        "reprocessed_at",
    }
    assert set(row.keys()) == expected_keys
    assert row["symbol"] == "BTC"
    assert row["accepted"] is True
    assert row["dedup_hash"] == "abc123"


# === L0 processing ===


def test_dedup_hash_deterministic():
    """Same heartbeat produces same dedup hash."""
    from hermes.schemas.heartbeat import parse_heartbeat
    from hermes.transport.l0_processing import compute_dedup_hash

    hb1 = parse_heartbeat(json.dumps(VALID_HEARTBEAT_PAYLOAD))
    hb2 = parse_heartbeat(json.dumps(VALID_HEARTBEAT_PAYLOAD))
    assert compute_dedup_hash(hb1) == compute_dedup_hash(hb2)


def test_dedup_hash_differs_on_change():
    """Different heartbeats produce different dedup hashes."""
    from hermes.schemas.heartbeat import parse_heartbeat
    from hermes.transport.l0_processing import compute_dedup_hash

    hb1 = parse_heartbeat(json.dumps(VALID_HEARTBEAT_PAYLOAD))
    payload2 = VALID_HEARTBEAT_PAYLOAD.copy()
    payload2["signal"] = "sell"
    hb2 = parse_heartbeat(json.dumps(payload2))
    assert compute_dedup_hash(hb1) != compute_dedup_hash(hb2)


def test_deduper_detects_duplicates():
    """Deduper flags the same hash within the window."""
    from hermes.transport.l0_processing import Deduper

    deduper = Deduper(window_sec=5.0)
    assert deduper.is_duplicate("hash1") is False  # first time
    assert deduper.is_duplicate("hash1") is True   # second time = duplicate
    assert deduper.is_duplicate("hash2") is False  # different hash = not dup

    stats = deduper.get_stats()
    assert stats["checked"] == 3
    assert stats["duplicates"] == 1


def test_staleness_checker():
    """StalenessChecker flags old heartbeats."""
    from hermes.schemas.heartbeat import parse_heartbeat
    from hermes.transport.l0_processing import StalenessChecker

    # Heartbeat from 60 seconds ago
    old_payload = VALID_HEARTBEAT_PAYLOAD.copy()
    old_payload["ts"] = int(datetime.now(timezone.utc).timestamp() * 1000) - 60000
    hb_old = parse_heartbeat(json.dumps(old_payload))

    # Heartbeat from 5 seconds ago
    fresh_payload = VALID_HEARTBEAT_PAYLOAD.copy()
    fresh_payload["ts"] = int(datetime.now(timezone.utc).timestamp() * 1000) - 5000
    hb_fresh = parse_heartbeat(json.dumps(fresh_payload))

    checker = StalenessChecker(staleness_ms=30000)
    assert checker.is_stale(hb_old) is True
    assert checker.is_stale(hb_fresh) is False


def test_regime_shift_detector_upstream():
    """RegimeShiftDetector catches upstream regime_shift='true'."""
    from hermes.schemas.heartbeat import parse_heartbeat
    from hermes.transport.l0_processing import RegimeShiftDetector

    detector = RegimeShiftDetector()

    # First heartbeat — no shift
    payload1 = VALID_HEARTBEAT_PAYLOAD.copy()
    payload1["regime_shift"] = "false"
    hb1 = parse_heartbeat(json.dumps(payload1))
    assert detector.check_shift(hb1) is None

    # Second heartbeat — shift detected upstream
    payload2 = VALID_HEARTBEAT_PAYLOAD.copy()
    payload2["regime_shift"] = "true"
    payload2["prev_regime"] = "low_vol_bull"
    payload2["regime"] = "high_vol_bear"
    hb2 = parse_heartbeat(json.dumps(payload2))
    shift = detector.check_shift(hb2)
    assert shift is not None
    assert shift["new_regime"] == "high_vol_bear"
    assert shift["source"] == "upstream"


def test_regime_shift_detector_hermes_detected():
    """RegimeShiftDetector catches shifts even when upstream doesn't flag them."""
    from hermes.schemas.heartbeat import parse_heartbeat
    from hermes.transport.l0_processing import RegimeShiftDetector

    detector = RegimeShiftDetector()

    # First heartbeat
    payload1 = VALID_HEARTBEAT_PAYLOAD.copy()
    payload1["regime"] = "low_vol_bull"
    payload1["regime_shift"] = "false"
    hb1 = parse_heartbeat(json.dumps(payload1))
    assert detector.check_shift(hb1) is None

    # Second heartbeat — regime changed but upstream didn't flag
    payload2 = VALID_HEARTBEAT_PAYLOAD.copy()
    payload2["regime"] = "high_vol_bear"
    payload2["regime_shift"] = "false"
    hb2 = parse_heartbeat(json.dumps(payload2))
    shift = detector.check_shift(hb2)
    assert shift is not None
    assert shift["source"] == "hermes_detected"


# === DuckDB writer ===


@pytest.mark.asyncio
async def test_heartbeat_writer_writes_row(tmp_path):
    """HeartbeatWriter writes a row to DuckDB."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.db.migrate import apply_migrations
    from hermes.transport.heartbeat_writer import HeartbeatWriter
    from hermes.schemas.heartbeat import parse_heartbeat

    # Set up temp DuckDB
    config = load_config()
    db_path = tmp_path / "test.duckdb"

    # Apply schema to temp DB
    schema_file = (
        Path(__file__).resolve().parent.parent
        / "src" / "hermes" / "db" / "schema.sql"
    )
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(schema_file.read_text(encoding="utf-8"))

    # Create writer pointing at temp DB
    writer = HeartbeatWriter(config, batch_size=1, flush_interval_sec=0.1)
    writer._db_path = db_path  # override after init
    await writer.start()

    # Enqueue a heartbeat row
    hb = parse_heartbeat(json.dumps(VALID_HEARTBEAT_PAYLOAD))
    row = hb.to_duckdb_row(
        ts_received=datetime.now(timezone.utc),
        dedup_hash="test_hash_123",
        raw_payload='{"test": true}',
    )
    await writer.enqueue(row)

    # Wait for flush
    await asyncio.sleep(0.5)
    await writer.stop()

    # Verify row was written
    with duckdb.connect(str(db_path), read_only=True) as conn:
        count = conn.execute("SELECT COUNT(*) FROM signal_heartbeats").fetchone()[0]
        assert count == 1

        result = conn.execute(
            "SELECT symbol, signal, regime, dedup_hash FROM signal_heartbeats"
        ).fetchone()
        assert result[0] == "BTC"
        assert result[1] == "buy"
        assert result[2] == "low_vol_bull"
        assert result[3] == "test_hash_123"

    stats = writer.get_stats()
    assert stats["written"] == 1


# === Supabase backfill data quality checks ===


def test_dq_anomaly_detection_sharpe_too_high():
    """DQ check flags absurdly high Sharpe ratios."""
    from hermes.transport.supabase_backfill import SupabaseBackfiller

    row = {"sharpe": 3726.0, "max_drawdown_pct": 0, "profit_factor": 0}
    anomalies = SupabaseBackfiller._check_dq_anomalies(row)
    assert "sharpe_too_high" in anomalies
    assert "max_dd_zero" in anomalies
    assert "profit_factor_zero" in anomalies


def test_dq_anomaly_detection_regime_disagree():
    """DQ check flags when regime says bull but strategy is losing."""
    from hermes.transport.supabase_backfill import SupabaseBackfiller

    row = {
        "sharpe": -1.13,
        "regime": "high_vol_strong_bull",
        "max_drawdown_pct": -0.0836,
        "profit_factor": 0.8151,
    }
    anomalies = SupabaseBackfiller._check_dq_anomalies(row)
    assert "regime_strategy_disagree" in anomalies


def test_dq_anomaly_detection_clean_row():
    """DQ check passes for a clean row."""
    from hermes.transport.supabase_backfill import SupabaseBackfiller

    row = {
        "sharpe": 1.5,
        "regime": "low_vol_bull",
        "max_drawdown_pct": -0.05,
        "profit_factor": 2.0,
    }
    anomalies = SupabaseBackfiller._check_dq_anomalies(row)
    assert len(anomalies) == 0


# === CLI ===


def test_cli_ingest_dry_run():
    """`platform ingest --dry-run` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["ingest", "--dry-run"])
    assert result.exit_code == 0
    assert "Dry run" in result.output


def test_cli_backfill_help():
    """`platform backfill --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["backfill", "--help"])
    assert result.exit_code == 0
    assert "days-back" in result.output
