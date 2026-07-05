"""
Config history manager — audit trail for every config change.

Every change to config/default.yaml (manual or optimization-driven) is
recorded in the `config_history` DuckDB table BEFORE the file is written.

Schema (from db/schema.sql):
    config_hash         VARCHAR PRIMARY KEY
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now()
    config_json         JSON NOT NULL
    source              VARCHAR NOT NULL   -- file | hermes | human | init
    rationale           TEXT
    author              VARCHAR            -- NEW: who made the change
    diff                JSON               -- NEW: what changed (key → {old, new})

Two new columns (author, diff) are added via migration 010 if not present.

Usage:
    from hermes.db.config_history import (
        write_config_to_history,
        get_config_history,
        get_config_by_hash,
        diff_configs,
        apply_config_change,
        rollback_config,
    )
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
import yaml

from hermes.core.config import HermesConfig, get_config_hash, _find_config_file
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


# ============================================================
# Schema migration — add `author` + `diff` columns if missing
# ============================================================

_SCHEMA_PATCHED = False


def _ensure_schema_columns(config: HermesConfig) -> None:
    """Add author + diff columns to config_history if they don't exist.

    Idempotent — safe to call on every request. DuckDB's ALTER TABLE ADD COLUMN
    doesn't support IF NOT EXISTS before v0.7, so we check information_schema.
    """
    global _SCHEMA_PATCHED
    if _SCHEMA_PATCHED:
        return

    import duckdb

    db_path = get_duckdb_path(config)
    with duckdb.connect(str(db_path)) as conn:
        for col in ("author", "diff"):
            try:
                cols = conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'config_history' AND column_name = ?",
                    [col],
                ).fetchall()
                if not cols:
                    conn.execute(f"ALTER TABLE config_history ADD COLUMN {col} VARCHAR")
                    log.info("config_history_column_added", column=col)
            except Exception as e:
                log.debug("config_history_column_check_failed", col=col, error=str(e))

    _SCHEMA_PATCHED = True


# ============================================================
# Read / write config_history
# ============================================================


def write_config_to_history(
    config: HermesConfig,
    source: str,
    rationale: str,
    author: str = "system",
    diff: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Write the current config state to config_history.

    Args:
        config: The HermesConfig to snapshot (must already be loaded with secrets resolved)
        source: 'file' | 'hermes' | 'human' | 'init'
        rationale: Free-form explanation (required for 'hermes' and 'human' sources)
        author: Who made the change (username, 'agent', 'init', etc.)
        diff: Optional {key_path: {old: ..., new: ...}} for set/promote operations

    Returns:
        The config_hash of the written row.
    """
    _ensure_schema_columns(config)

    config_hash = get_config_hash(config)
    config_json = json.dumps(config.model_dump(mode="json"), default=str, sort_keys=True)
    diff_json = json.dumps(diff, default=str) if diff else None

    import duckdb

    db_path = get_duckdb_path(config)
    with duckdb.connect(str(db_path)) as conn:
        # Use INSERT OR IGNORE — if the hash already exists (re-applying same config),
        # don't duplicate the row.
        conn.execute(
            """
            INSERT OR IGNORE INTO config_history
                (config_hash, ts, config_json, source, rationale, author, diff)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                config_hash,
                datetime.now(timezone.utc),
                config_json,
                source,
                rationale,
                author,
                diff_json,
            ],
        )

    log.info(
        "config_history_written",
        hash=config_hash,
        source=source,
        author=author,
        rationale=rationale[:100],
    )
    return config_hash


def get_config_history(
    config: HermesConfig, limit: int = 20,
) -> list[dict[str, Any]]:
    """Return recent config_history rows, newest first."""
    _ensure_schema_columns(config)

    import duckdb

    db_path = get_duckdb_path(config)
    with duckdb.connect(str(db_path), read_only=True) as conn:
        result = conn.execute(
            """
            SELECT config_hash, ts, source, rationale, author, diff
            FROM config_history
            ORDER BY ts DESC
            LIMIT ?
            """,
            [int(limit)],
        ).fetchdf()

    if result.empty:
        return []
    rows = result.to_dict("records")
    # Parse diff JSON for each row
    for row in rows:
        if row.get("diff") and isinstance(row["diff"], str):
            try:
                row["diff"] = json.loads(row["diff"])
            except Exception:
                pass
    return rows


def get_config_by_hash(
    config: HermesConfig, config_hash: str,
) -> dict[str, Any] | None:
    """Fetch a single config_history row by hash. Returns None if not found."""
    _ensure_schema_columns(config)

    import duckdb

    db_path = get_duckdb_path(config)
    with duckdb.connect(str(db_path), read_only=True) as conn:
        result = conn.execute(
            """
            SELECT config_hash, ts, config_json, source, rationale, author, diff
            FROM config_history
            WHERE config_hash = ?
            """,
            [config_hash],
        ).fetchdf()

    if result.empty:
        return None
    row = result.iloc[0].to_dict()
    if isinstance(row.get("config_json"), str):
        try:
            row["config_json"] = json.loads(row["config_json"])
        except Exception:
            pass
    if isinstance(row.get("diff"), str):
        try:
            row["diff"] = json.loads(row["diff"])
        except Exception:
            pass
    return row


# ============================================================
# Config file operations (set / promote / rollback)
# ============================================================


def _load_yaml_file(config_path: Path) -> dict[str, Any]:
    """Load the raw YAML config (without secret resolution)."""
    with config_path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_yaml_file(config_path: Path, data: dict[str, Any]) -> None:
    """Write config dict back to YAML file.

    Note: PyYAML doesn't preserve comments. If comment preservation is critical,
    switch to ruamel.yaml. For now, this is acceptable — the audit trail is in
    DuckDB, not in YAML comments.
    """
    with config_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def _get_nested(data: dict[str, Any], key_path: str) -> Any:
    """Get a nested value by dotted path: 'a.b.c' → data['a']['b']['c']."""
    keys = key_path.split(".")
    value: Any = data
    for k in keys:
        if not isinstance(value, dict) or k not in value:
            raise KeyError(f"Key path '{key_path}' not found (failed at '{k}')")
        value = value[k]
    return value


def _set_nested(data: dict[str, Any], key_path: str, value: Any) -> None:
    """Set a nested value by dotted path. Creates intermediate dicts if missing."""
    keys = key_path.split(".")
    current = data
    for k in keys[:-1]:
        if k not in current or not isinstance(current[k], dict):
            current[k] = {}
        current = current[k]
    current[keys[-1]] = value


def _coerce_value(value: str) -> Any:
    """Coerce a string CLI arg to the appropriate Python type.

    'true'/'false' → bool, '123' → int, '1.5' → float, else string.
    """
    if value.lower() in ("true", "yes", "on"):
        return True
    if value.lower() in ("false", "no", "off"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    # Try to parse as JSON (lists, dicts, null)
    try:
        return json.loads(value)
    except (json.JSONDecodeError, ValueError):
        pass
    return value


def apply_config_change(
    config: HermesConfig,
    key_path: str,
    new_value: Any,
    *,
    source: str,
    rationale: str,
    author: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Apply a single key change to default.yaml + record in config_history.

    Args:
        config: Current loaded HermesConfig
        key_path: Dotted path (e.g. 'circuit_breakers.volatility.vol_mult_threshold')
        new_value: New value (string from CLI — will be coerced)
        source: 'human' | 'hermes' | 'init'
        rationale: Required explanation
        author: Who made the change
        config_path: Override for config file path (defaults to auto-detected)

    Returns:
        {old_value, new_value, config_hash, key_path}

    Raises:
        KeyError: if the key_path doesn't exist in the config
        ValueError: if rationale is empty for human/hermes sources
    """
    if not rationale.strip() and source in ("human", "hermes"):
        raise ValueError(f"rationale is required for source='{source}'")

    if config_path is None:
        config_path = _find_config_file()

    # Coerce string value to appropriate type
    if isinstance(new_value, str):
        new_value = _coerce_value(new_value)

    # Load raw YAML (not resolved — we want to preserve secret: references)
    raw = _load_yaml_file(config_path)

    # Get old value (raises KeyError if path doesn't exist)
    old_value = _get_nested(raw, key_path)

    # Apply the change
    _set_nested(raw, key_path, new_value)

    # Write back to file
    _save_yaml_file(config_path, raw)

    # Reload config to compute new hash
    from hermes.core.config import load_config
    # Clear the lru_cache so load_config reads the updated file
    load_config.cache_clear()
    new_config = load_config(str(config_path))

    # Build the diff
    diff = {key_path: {"old": old_value, "new": new_value}}

    # Write to config_history
    config_hash = write_config_to_history(
        new_config,
        source=source,
        rationale=rationale,
        author=author,
        diff=diff,
    )

    log.info(
        "config_change_applied",
        key=key_path,
        old=old_value,
        new=new_value,
        hash=config_hash,
        source=source,
        author=author,
    )

    return {
        "key_path": key_path,
        "old_value": old_value,
        "new_value": new_value,
        "config_hash": config_hash,
    }


def rollback_config(
    config: HermesConfig,
    target_hash: str,
    *,
    author: str,
    rationale: str,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Rollback default.yaml to a previous config_history entry.

    Args:
        config: Current loaded HermesConfig
        target_hash: The config_hash to rollback to
        author: Who initiated the rollback
        rationale: Why the rollback is being done
        config_path: Override for config file path

    Returns:
        {rolled_back_to: hash, new_hash: ..., config_json: ...}

    Raises:
        KeyError: if target_hash not found in config_history
    """
    target = get_config_by_hash(config, target_hash)
    if target is None:
        raise KeyError(f"Config hash not found: {target_hash}")

    # The stored config_json has secrets resolved (from when it was written).
    # We need to write it back to default.yaml — but we DON'T want to leak
    # resolved secrets into the YAML file. So we only restore the non-secret
    # fields, and keep current secret: references intact.
    #
    # Strategy: load current YAML (with secret: refs), then deep-merge the
    # target config_json on top, but skip any field whose current value
    # starts with 'secret:'.
    if config_path is None:
        config_path = _find_config_file()

    current_raw = _load_yaml_file(config_path)
    target_config = target.get("config_json", {})

    def deep_merge_preserve_secrets(current: Any, target: Any) -> Any:
        """Merge target into current, but preserve any 'secret:' values in current."""
        if isinstance(current, dict) and isinstance(target, dict):
            result = dict(current)
            for k, v in target.items():
                if k in result:
                    result[k] = deep_merge_preserve_secrets(result[k], v)
                else:
                    result[k] = v
            return result
        # Leaf value — only override if current is NOT a secret reference
        if isinstance(current, str) and current.startswith("secret:"):
            return current  # preserve the secret: reference
        return target

    merged = deep_merge_preserve_secrets(current_raw, target_config)

    _save_yaml_file(config_path, merged)

    # Reload + record
    from hermes.core.config import load_config
    load_config.cache_clear()
    new_config = load_config(str(config_path))

    new_hash = write_config_to_history(
        new_config,
        source="human",
        rationale=f"ROLLBACK to {target_hash}: {rationale}",
        author=author,
        diff={"_rollback": {"target_hash": target_hash, "target_ts": str(target.get("ts"))}},
    )

    log.info("config_rolled_back", target=target_hash, new_hash=new_hash, author=author)
    return {
        "rolled_back_to": target_hash,
        "new_hash": new_hash,
        "target_ts": str(target.get("ts")),
    }


def diff_configs(
    config: HermesConfig, hash_a: str, hash_b: str,
) -> list[dict[str, Any]]:
    """Compute the diff between two config_history entries.

    Returns a list of {key_path, value_a, value_b} for every leaf that differs.
    """
    a = get_config_by_hash(config, hash_a)
    b = get_config_by_hash(config, hash_b)
    if a is None:
        raise KeyError(f"Hash not found: {hash_a}")
    if b is None:
        raise KeyError(f"Hash not found: {hash_b}")

    cfg_a = a.get("config_json", {})
    cfg_b = b.get("config_json", {})

    diffs: list[dict[str, Any]] = []

    def walk(path: str, va: Any, vb: Any) -> None:
        if isinstance(va, dict) and isinstance(vb, dict):
            all_keys = set(va.keys()) | set(vb.keys())
            for k in sorted(all_keys):
                walk(f"{path}.{k}" if path else k, va.get(k), vb.get(k))
        elif va != vb:
            diffs.append({"key_path": path, "value_a": va, "value_b": vb})

    walk("", cfg_a, cfg_b)
    return diffs


# ============================================================
# Promote (for the optimization loop)
# ============================================================


def promote_config(
    config: HermesConfig,
    changes: dict[str, Any],
    *,
    rationale: str,
    author: str = "hermes",
    hypothesis_id: str | None = None,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Apply multiple key changes at once (used by optimization promotion).

    Args:
        config: Current loaded HermesConfig
        changes: {key_path: new_value, ...} — multiple keys to change
        rationale: Why the promotion is being applied
        author: 'hermes' for auto-promote, 'human' for manual
        hypothesis_id: Optional hypothesis ID to link in the rationale
        config_path: Override for config file path

    Returns:
        {changes_applied, config_hash, diff}
    """
    if not rationale.strip():
        raise ValueError("rationale is required for config promotion")

    if config_path is None:
        config_path = _find_config_file()

    raw = _load_yaml_file(config_path)
    diff: dict[str, dict[str, Any]] = {}

    for key_path, new_value in changes.items():
        try:
            old_value = _get_nested(raw, key_path)
        except KeyError:
            old_value = None  # new key being added
        _set_nested(raw, key_path, new_value)
        diff[key_path] = {"old": old_value, "new": new_value}

    _save_yaml_file(config_path, raw)

    from hermes.core.config import load_config
    load_config.cache_clear()
    new_config = load_config(str(config_path))

    full_rationale = rationale
    if hypothesis_id:
        full_rationale = f"[hypothesis {hypothesis_id}] {rationale}"

    config_hash = write_config_to_history(
        new_config,
        source="hermes" if author == "hermes" else "human",
        rationale=full_rationale,
        author=author,
        diff=diff,
    )

    log.info(
        "config_promoted",
        n_changes=len(changes),
        hash=config_hash,
        hypothesis=hypothesis_id,
        author=author,
    )

    return {
        "changes_applied": len(changes),
        "config_hash": config_hash,
        "diff": diff,
    }
