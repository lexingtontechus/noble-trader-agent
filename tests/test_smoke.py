"""
Smoke tests for Phase 0 — verify the skeleton works.

Run with:
    pytest tests/test_smoke.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


# === Config loading ===


def test_config_loads():
    """Config loads from YAML without error."""
    from hermes.core.config import load_config

    config = load_config()
    assert config is not None
    assert config.environment in ["development", "staging", "production"]


def test_config_has_venues():
    """Config has at least one venue enabled."""
    from hermes.core.config import load_config

    config = load_config()
    enabled = [k for k, v in config.venues.items() if v.enabled]
    assert len(enabled) > 0, "No venues enabled in config"


def test_config_has_portfolio_allocation():
    """Portfolio allocation sums to ~1.0."""
    from hermes.core.config import load_config

    config = load_config()
    total = sum(config.portfolio.target_allocation.values())
    assert abs(total - 1.0) < 0.01, f"Portfolio allocation sums to {total}, expected 1.0"


def test_config_hash_is_deterministic():
    """Same config produces same hash."""
    from hermes.core.config import get_config_hash, load_config

    config1 = load_config()
    config2 = load_config()
    # load_config is cached, so this should be the same object
    assert get_config_hash(config1) == get_config_hash(config2)


# === Secret resolver ===


def test_secret_resolver_returns_string():
    """SecretResolver.get_secret returns a string for known keys."""
    from hermes.core.secrets import get_secret_or_none

    # HERMES_LOG_LEVEL should be in .env or default
    value = get_secret_or_none("hermes.log_level", "INFO")
    assert isinstance(value, str)


def test_secret_redact():
    """redact() hides secret values."""
    from hermes.core.secrets import redact

    assert redact("") == "<empty>"
    # 3-char string is shorter than default `visible=4`, so fully masked
    assert redact("abc") == "***"
    # 6-char string shows first 4 + ellipsis + length
    result = redact("secret")
    assert "..." in result
    assert "6 chars" in result
    # Long secret shows prefix but not the full value
    long_secret = "sk-1234567890abcdef"
    redacted = redact(long_secret, visible=4)
    assert redacted.startswith("sk-1")
    assert long_secret not in redacted  # full value must not appear


def test_secret_not_found_raises():
    """SecretNotFoundError raised for missing keys."""
    from hermes.core.secrets import SecretNotFoundError, get_secret

    with pytest.raises(SecretNotFoundError):
        get_secret("definitely.does.not.exist.xyz123")


# === DuckDB ===


def test_duckdb_schema_applies(tmp_path):
    """Schema applies to a fresh DuckDB file."""
    import duckdb

    from hermes.core.config import load_config
    from hermes.db.migrate import SCHEMA_FILE

    config = load_config()

    # Use temp DB
    db_path = tmp_path / "test.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        schema_sql = SCHEMA_FILE.read_text(encoding="utf-8")
        conn.execute(schema_sql)

        # Verify tables exist
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "signal_heartbeats" in table_names
        assert "account_snapshots" in table_names
        assert "trade_journal" in table_names
        assert "config_history" in table_names
        assert "audit_log" in table_names


def test_duckdb_test_row_roundtrip(tmp_path):
    """Can write and read back a test row."""
    import duckdb
    import json
    import uuid

    from hermes.db.migrate import SCHEMA_FILE

    db_path = tmp_path / "test.duckdb"
    with duckdb.connect(str(db_path)) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        test_hash = "test123"
        conn.execute(
            """
            INSERT INTO config_history (config_hash, ts, config_json, source, rationale)
            VALUES (?, now(), ?, 'test', 'smoke test')
            """,
            [test_hash, json.dumps({"test": True})],
        )

        result = conn.execute(
            "SELECT config_hash, source FROM config_history WHERE config_hash = ?",
            [test_hash],
        ).fetchone()
        assert result is not None
        assert result[0] == test_hash
        assert result[1] == "test"


# === CLI ===


def test_cli_version():
    """`platform version` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "hermes" in result.output


def test_cli_help():
    """`platform --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "init" in result.output
    assert "health" in result.output


# === Logging ===


def test_logging_setup():
    """Logging setup doesn't error."""
    from hermes.core.logging import setup_logging, get_logger

    setup_logging(level="INFO", format="text", output="stdout")
    log = get_logger(__name__)
    log.info("test_message", key="value")
