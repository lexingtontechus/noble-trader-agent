"""
Hermes CLI — main entry point.

Usage:
    platform init          — bootstrap: load config, open DuckDB, apply schema, ping Redis
    platform health        — check health of all subsystems
    platform config show   — print loaded config (with secrets redacted)
    platform version       — print version

This is Phase 0 — just the skeleton. Subsequent phases add subcommands:
    platform stream        (Phase 2 — market data)
    platform ingest        (Phase 1 — upstream heartbeat ingestion)
    platform trade         (Phase 5 — execution)
    platform optimize      (Phase 8 — simulation engine)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
import structlog

from hermes import __version__
from hermes.core.config import (
    HermesConfig,
    get_config_hash,
    load_config,
    redact_config_for_display,
)
from hermes.core.logging import setup_logging

log = structlog.get_logger(__name__)


def _resolve_symbols(symbols_arg: str | None, config: HermesConfig) -> list[str]:
    """Resolve a --symbols CLI arg into a concrete list.

    If `symbols_arg` is provided, it's split on commas and returned as-is
    (CLI overrides everything). If omitted, the active symbols from the
    DuckDB `symbols` table are returned. If the table doesn't exist yet
    (pre-init), falls back to config.portfolio.initial_symbols.

    Always returns at least an empty list — callers should validate that
    the list is non-empty before proceeding.
    """
    if symbols_arg:
        return [s.strip() for s in symbols_arg.split(",") if s.strip()]

    # Try the DB-backed symbol registry first.
    try:
        from hermes.db.symbol_registry import list_active_symbols
        active = list_active_symbols(config)
        if active:
            return active
    except Exception as e:
        log.debug("symbols_db_unavailable", error=str(e))

    # Fallback: read from config (pre-init or empty DB).
    fallback = [
        entry["symbol"]
        for entry in config.portfolio.initial_symbols
        if entry.get("symbol")
    ]
    if fallback:
        log.info("symbols_fallback_to_config", count=len(fallback))
    return fallback


@click.group()
@click.version_option(__version__, prog_name="hermes")
@click.option(
    "--config",
    "config_path",
    type=click.Path(),
    default=None,
    help="Path to config/default.yaml (auto-discovers if omitted)",
)
@click.option(
    "--log-level",
    default=None,
    help="Override log level (DEBUG, INFO, WARNING, ERROR)",
)
@click.pass_context
def cli(ctx: click.Context, config_path: str | None, log_level: str | None) -> None:
    """Hermes — entry/execution optimization layer for Noble Trader signals."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    # We don't load config here — each subcommand loads what it needs
    # so `platform version` doesn't require a valid .env


@cli.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Bootstrap: load config, open DuckDB, apply schema, ping Redis.

    This is the first command to run after cloning the repo. It verifies:
    1. Config loads from YAML
    2. Secrets resolve from .env (or env vars)
    3. DuckDB opens and schema applies cleanly
    4. A test row can be written and read back
    5. Redis is reachable (if configured)
    """
    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)

    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "json"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    log.info("init_starting", version=__version__, environment=config.environment)

    # 1. Verify config
    config_hash = get_config_hash(config)
    log.info("config_loaded", hash=config_hash)
    _print_config_summary(config)

    # 2. Open DuckDB + apply schema
    duckdb_ok = _init_duckdb(config)
    if not duckdb_ok:
        log.error("init_failed", step="duckdb")
        sys.exit(1)

    # 3. Ping Redis (non-fatal if unreachable)
    redis_ok = _ping_redis(config)
    if not redis_ok:
        log.warning("redis_unreachable", note="init continues; some features disabled")

    # 4. Summary
    log.info(
        "init_complete",
        duckdb="ok" if duckdb_ok else "FAILED",
        redis="ok" if redis_ok else "unreachable",
        config_hash=config_hash,
    )

    click.echo("")
    click.echo("✓ Hermes initialized successfully" if duckdb_ok else "✗ Init failed")
    click.echo(f"  Config hash: {config_hash}")
    click.echo(f"  DuckDB: {'ok' if duckdb_ok else 'FAILED'}")
    click.echo(f"  Redis:  {'ok' if redis_ok else 'unreachable (non-fatal)'}")
    click.echo("")
    click.echo("Next steps:")
    click.echo("  1. Run `platform health` to verify all subsystems")
    click.echo("  2. Run `platform config show` to inspect loaded config")
    click.echo("  3. Review roadmap.md for Phase 1 (Upstream Ingestion) plan")


@cli.command()
@click.pass_context
def health(ctx: click.Context) -> None:
    """Check health of all subsystems."""
    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(level=config.log_level, format="text", output="stdout")

    results = {
        "config": _check_config(config),
        "duckdb": _check_duckdb(config),
        "redis": _check_redis(config),
        "secrets": _check_secrets(config),
    }

    all_ok = all(v["ok"] for v in results.values())

    click.echo("Hermes Health Check")
    click.echo("=" * 50)
    for name, result in results.items():
        status = "✓" if result["ok"] else "✗"
        click.echo(f"  {status} {name:10} {result['message']}")
    click.echo("=" * 50)
    click.echo(f"Overall: {'HEALTHY' if all_ok else 'UNHEALTHY'}")

    sys.exit(0 if all_ok else 1)


@cli.group()
def config() -> None:
    """Configuration commands."""
    pass


@config.command(name="show")
@click.pass_context
def config_show(ctx: click.Context) -> None:
    """Print loaded config with secrets redacted."""
    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    redacted = redact_config_for_display(config)
    click.echo(json.dumps(redacted, indent=2, default=str))


@config.command(name="set")
@click.argument("key_path")
@click.argument("value")
@click.option("--rationale", required=True, help="Why this change is being made (required for audit trail).")
@click.option("--author", default=None, help="Who is making the change (default: $USER or 'operator').")
@click.pass_context
def config_set(
    ctx: click.Context,
    key_path: str,
    value: str,
    rationale: str,
    author: str | None,
) -> None:
    """Set a single config value in config/default.yaml with audit trail.

    KEY_PATH is a dotted path (e.g. 'circuit_breakers.volatility.vol_mult_threshold').
    VALUE is the new value (coerced to bool/int/float/string/JSON automatically).

    The change is written to config/default.yaml AND recorded in the
    config_history DuckDB table with the rationale + author + diff.

    A restart is required for the change to take effect.

    \b
    Examples:
      platform config set circuit_breakers.volatility.vol_mult_threshold 3.0 \\
        --rationale "tightening vol CB after July spike"
      platform config set entry.brick_confirmation_count 3 \\
        --rationale "more conservative entry confirmation"
      platform config set signal.staleness_ms 60000 --rationale "longer staleness tolerance"
    """
    import os as _os
    from hermes.db.config_history import apply_config_change
    from hermes.portfolio.autonomy_gate import AutonomyGate

    config = load_config(ctx.obj.get("config_path"))
    author = author or _os.environ.get("USER", "operator")
    caller = "human"  # CLI is always human-driven

    # Build AutonomyGate from config
    autonomy_cfg = config.autonomy if hasattr(config, "autonomy") else {}
    if not isinstance(autonomy_cfg, dict):
        autonomy_cfg = {}
    gate = AutonomyGate(
        tier2_config_keys=autonomy_cfg.get("tier_2", {}).get("config_keys", []),
        tier3_config_keys=autonomy_cfg.get("tier_3", {}).get("config_keys", []),
        tier4_config_keys=autonomy_cfg.get("tier_4", {}).get("config_keys", []),
    )

    # Classify the change
    decision = gate.classify_config_change(key_path, caller=caller)
    if not decision.approved:
        click.echo(f"✗ Blocked by autonomy gate (tier {decision.tier}): {decision.reason}", err=True)
        click.echo(f"  This key requires human approval. Use --author to confirm.", err=True)
        sys.exit(1)

    if decision.tier in (3, 4):
        click.echo(f"  ⚠ Tier {decision.tier} key — changes are audited with extra scrutiny.", err=True)

    try:
        result = apply_config_change(
            config, key_path, value,
            source="human",
            rationale=rationale,
            author=author,
        )
    except KeyError as e:
        click.echo(f"✗ {e}", err=True)
        click.echo(f"  Available top-level keys: {sorted(config.model_dump().keys())}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    click.echo(f"✓ Config updated: {result['key_path']}")
    click.echo(f"    old: {result['old_value']}")
    click.echo(f"    new: {result['new_value']}")
    click.echo(f"    hash: {result['config_hash']}")
    click.echo(f"    author: {author}")
    click.echo(f"    rationale: {rationale}")
    click.echo("")
    click.echo("  ⚠ Restart required for change to take effect:")
    click.echo("    Ctrl+C the running platform, then re-run the commands.")


@config.command(name="history")
@click.option("--limit", default=20, type=int, help="Number of entries to show (default 20).")
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit JSON.")
@click.pass_context
def config_history(
    ctx: click.Context,
    limit: int,
    as_json: bool,
) -> None:
    """Show config change history (audit trail)."""
    from hermes.db.config_history import get_config_history

    if as_json:
        setup_logging(level="CRITICAL", format="text", output="stdout")
    config = load_config(ctx.obj.get("config_path"))
    rows = get_config_history(config, limit=limit)

    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return

    if not rows:
        click.echo("No config history yet. Run `platform init` to record the initial config.")
        return

    click.echo(f"{'Time':<20} {'Hash':<18} {'Source':<8} {'Author':<14} {'Rationale':<50}")
    click.echo("-" * 115)
    for r in rows:
        ts = str(r.get("ts", ""))[:19].replace("T", " ")
        h = (r.get("config_hash") or "")[:16]
        src = r.get("source", "?")
        author = (r.get("author") or "?")[:13]
        rationale = (r.get("rationale") or "")[:49]
        click.echo(f"{ts:<20} {h:<18} {src:<8} {author:<14} {rationale}")
    click.echo(f"\nTotal: {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}")


@config.command(name="diff")
@click.argument("hash_a")
@click.argument("hash_b")
@click.pass_context
def config_diff(
    ctx: click.Context,
    hash_a: str,
    hash_b: str,
) -> None:
    """Show the diff between two config_history entries.

    HASH_A and HASH_B are config_hash values (use `platform config history` to find them).
    """
    from hermes.db.config_history import diff_configs

    config = load_config(ctx.obj.get("config_path"))
    try:
        diffs = diff_configs(config, hash_a, hash_b)
    except KeyError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    if not diffs:
        click.echo("No differences — the two configs are identical.")
        return

    click.echo(f"Diff: {hash_a[:16]}... → {hash_b[:16]}...")
    click.echo(f"{'Key':<55} {'Value A → Value B'}")
    click.echo("-" * 100)
    for d in diffs:
        key = d["key_path"]
        va = _truncate_val(d["value_a"])
        vb = _truncate_val(d["value_b"])
        click.echo(f"{key:<55} {va} → {vb}")
    click.echo(f"\n{len(diffs)} field(s) differ.")


@config.command(name="rollback")
@click.argument("target_hash")
@click.option("--rationale", required=True, help="Why are you rolling back? (required for audit).")
@click.option("--author", default=None, help="Who is rolling back (default: $USER).")
@click.pass_context
def config_rollback(
    ctx: click.Context,
    target_hash: str,
    rationale: str,
    author: str | None,
) -> None:
    """Rollback config/default.yaml to a previous config_history entry.

    TARGET_HASH is the config_hash to restore (use `platform config history` to find it).
    The current config is preserved in history — rollback is itself an audited change.
    """
    import os as _os
    from hermes.db.config_history import rollback_config

    config = load_config(ctx.obj.get("config_path"))
    author = author or _os.environ.get("USER", "operator")

    try:
        result = rollback_config(
            config, target_hash,
            author=author, rationale=rationale,
        )
    except KeyError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    click.echo(f"✓ Rolled back to: {result['rolled_back_to']}")
    click.echo(f"    target time: {result['target_ts']}")
    click.echo(f"    new hash: {result['new_hash']}")
    click.echo(f"    author: {author}")
    click.echo(f"    rationale: {rationale}")
    click.echo("")
    click.echo("  ⚠ Restart required for change to take effect.")


@config.command(name="promote")
@click.option("--hypothesis-id", required=True, help="Hypothesis ID being promoted.")
@click.option("--change", "changes", multiple=True, required=True,
              help="Key=value pair to change (repeatable). E.g. --change entry.brick_confirmation_count=3")
@click.option("--rationale", required=True, help="Why this promotion is being applied.")
@click.option("--author", default="hermes", help="Who is promoting (default: hermes for auto-promote).")
@click.pass_context
def config_promote(
    ctx: click.Context,
    hypothesis_id: str,
    changes: tuple[str, ...],
    rationale: str,
    author: str,
) -> None:
    """Promote an optimization hypothesis to live config (agent path).

    Applies multiple key changes at once, records in config_history with
    source='hermes' + hypothesis_id linked in rationale.

    \b
    Example:
      platform config promote \\
        --hypothesis-id abc123 \\
        --change entry.brick_confirmation_count=3 \\
        --change execution.limit_offset_bps=5 \\
        --rationale "shadow Sharpe 1.6 ≥ 80% of backtest 1.8"
    """
    from hermes.db.config_history import promote_config
    from hermes.portfolio.autonomy_gate import AutonomyGate

    config = load_config(ctx.obj.get("config_path"))

    # Build AutonomyGate from config
    autonomy_cfg = config.autonomy if hasattr(config, "autonomy") else {}
    if not isinstance(autonomy_cfg, dict):
        autonomy_cfg = {}
    gate = AutonomyGate(
        tier2_config_keys=autonomy_cfg.get("tier_2", {}).get("config_keys", []),
        tier3_config_keys=autonomy_cfg.get("tier_3", {}).get("config_keys", []),
        tier4_config_keys=autonomy_cfg.get("tier_4", {}).get("config_keys", []),
    )

    # Parse --change key=value pairs
    change_dict: dict[str, Any] = {}
    for c in changes:
        if "=" not in c:
            click.echo(f"✗ Invalid --change format: '{c}' (expected key=value)", err=True)
            sys.exit(1)
        k, v = c.split("=", 1)
        change_dict[k.strip()] = v.strip()  # promote_config will coerce

    # Classify each change — agent (hermes) is blocked from tier 3/4 keys
    caller = "hermes" if author == "hermes" else "human"
    blocked_keys: list[str] = []
    notify_keys: list[str] = []
    for key_path in change_dict:
        decision = gate.classify_config_change(key_path, caller=caller)
        if not decision.approved:
            blocked_keys.append(f"{key_path} (tier {decision.tier}: {decision.reason})")
        elif decision.tier == 2:
            notify_keys.append(key_path)

    if blocked_keys:
        click.echo(f"✗ Blocked by autonomy gate — agent cannot promote these keys:", err=True)
        for k in blocked_keys:
            click.echo(f"    {k}", err=True)
        click.echo("", err=True)
        click.echo("  These keys require human approval. Use `platform config set` instead.", err=True)
        sys.exit(1)

    if notify_keys:
        click.echo(f"  ℹ Auto-promoting {len(notify_keys)} tier-2 key(s) with notification:")
        for k in notify_keys:
            click.echo(f"    {k}")

    try:
        result = promote_config(
            config, change_dict,
            rationale=rationale,
            author=author,
            hypothesis_id=hypothesis_id,
        )
    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)
    except KeyError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    click.echo(f"✓ Config promoted (hypothesis {hypothesis_id})")
    click.echo(f"    changes applied: {result['changes_applied']}")
    click.echo(f"    new hash: {result['config_hash']}")
    click.echo(f"    author: {author}")
    click.echo(f"    rationale: {rationale}")
    click.echo("")
    click.echo("  Diff:")
    for k, v in result["diff"].items():
        click.echo(f"    {k}: {v['old']} → {v['new']}")
    click.echo("")
    click.echo("  ⚠ Restart required for change to take effect.")


def _truncate_val(v: Any, max_len: int = 30) -> str:
    """Truncate a value for table display."""
    s = str(v)
    if len(s) > max_len:
        return s[:max_len - 3] + "..."
    return s


# ============================================================
# platform symbols — runtime-mutable symbol registry
# ============================================================


@cli.group()
def symbols() -> None:
    """Manage the symbol registry (DuckDB-backed).

    The `symbols` table is the source of truth for the active trading
    universe. Symbols can be added, deactivated, and validated without
    editing config/default.yaml.

    \b
    Examples:
      platform symbols list
      platform symbols add BTC/USD --venue alpaca --asset-class crypto
      platform symbols deactivate GLD --reason "switched to crypto-only"
      platform symbols validate BTC/USD
      platform symbols sync     # one-time seed from default.yaml
    """
    pass


@symbols.command(name="list")
@click.option(
    "--active-only", is_flag=True, default=False,
    help="Show only active symbols (default: show all).",
)
@click.option(
    "--venue", default=None,
    help="Filter by venue (alpaca, hyperliquid, ...).",
)
@click.option(
    "--asset-class", default=None,
    help="Filter by asset class (crypto, equities, commodities, forex).",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False,
    help="Emit JSON instead of a table (for piping into other tools).",
)
@click.pass_context
def symbols_list(
    ctx: click.Context,
    active_only: bool,
    venue: str | None,
    asset_class: str | None,
    as_json: bool,
) -> None:
    """List symbols in the registry."""
    from hermes.db.symbol_registry import list_symbols

    # In --json mode, silence structlog before load_config() so the output
    # is parseable JSON (load_config logs INFO-level messages by default).
    if as_json:
        setup_logging(level="CRITICAL", format="text", output="stdout")
    config = load_config(ctx.obj.get("config_path"))

    rows = list_symbols(
        config, active_only=active_only, venue=venue, asset_class=asset_class,
    )

    if as_json:
        click.echo(json.dumps([r.to_dict() for r in rows], indent=2, default=str))
        return

    if not rows:
        click.echo("No symbols found. Run `platform symbols sync` to seed from config.")
        return

    click.echo(
        f"{'Symbol':<14} {'Venue':<14} {'Class':<12} "
        f"{'Active':<7} {'Validated':<10} {'Last Price':>12}  Rationale"
    )
    click.echo("-" * 100)
    for r in rows:
        active = "✓ yes" if r.is_active else "✗ no"
        validated = (r.validation_status or "pending")[:10]
        price = f"{r.last_price:.4f}" if r.last_price else "-"
        rationale = (r.rationale or "")[:40]
        click.echo(
            f"{(r.symbol or ''):<14} {(r.venue or ''):<14} "
            f"{(r.asset_class or ''):<12} {active:<7} {validated:<10} "
            f"{price:>12}  {rationale}"
        )
    click.echo(f"\nTotal: {len(rows)} symbol(s)")


@symbols.command(name="add")
@click.argument("symbol")
@click.option("--venue", required=True, help="Venue key (alpaca, hyperliquid, ...).")
@click.option(
    "--asset-class", "asset_class", required=True,
    help="Asset class (crypto, equities, commodities, forex).",
)
@click.option("--base-ccy", default=None, help="Base currency (default: derived from symbol).")
@click.option("--quote-ccy", default="USD", help="Quote currency (default: USD).")
@click.option("--tick-size", type=float, default=None, help="Minimum price increment.")
@click.option("--min-notional", type=float, default=None, help="Minimum order notional in USD.")
@click.option("--max-leverage", type=float, default=None, help="Max leverage allowed by venue.")
@click.option("--rationale", default=None, help="Free-form note explaining why this symbol was added.")
@click.option(
    "--inactive", is_flag=True, default=False,
    help="Add the symbol in inactive state (default: active).",
)
@click.pass_context
def symbols_add(
    ctx: click.Context,
    symbol: str,
    venue: str,
    asset_class: str,
    base_ccy: str | None,
    quote_ccy: str,
    tick_size: float | None,
    min_notional: float | None,
    max_leverage: float | None,
    rationale: str | None,
    inactive: bool,
) -> None:
    """Add a new symbol to the registry (or update mutable fields if it exists)."""
    from hermes.db.symbol_registry import add_symbol

    config = load_config(ctx.obj.get("config_path"))
    try:
        row = add_symbol(
            config, symbol, venue, asset_class,
            base_ccy=base_ccy, quote_ccy=quote_ccy,
            tick_size=tick_size, min_notional=min_notional, max_leverage=max_leverage,
            added_by="cli",
            rationale=rationale,
            activate=not inactive,
        )
    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    click.echo(f"✓ Added symbol: {row.symbol}")
    click.echo(f"    venue={row.venue}  asset_class={row.asset_class}")
    click.echo(f"    active={row.is_active}  base_ccy={row.base_ccy}")
    if rationale:
        click.echo(f"    rationale: {rationale}")
    click.echo("")
    click.echo("  Next: validate the symbol with:")
    click.echo(f"    platform symbols validate {row.symbol}")


@symbols.command(name="activate")
@click.argument("symbol")
@click.pass_context
def symbols_activate(ctx: click.Context, symbol: str) -> None:
    """Re-enable a previously deactivated symbol."""
    from hermes.db.symbol_registry import activate_symbol

    config = load_config(ctx.obj.get("config_path"))
    try:
        row = activate_symbol(config, symbol, activated_by="cli")
    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)
    click.echo(f"✓ Activated: {row.symbol}")


@symbols.command(name="deactivate")
@click.argument("symbol")
@click.option("--reason", default=None, help="Why this symbol is being deactivated.")
@click.pass_context
def symbols_deactivate(
    ctx: click.Context, symbol: str, reason: str | None,
) -> None:
    """Soft-delete a symbol (sets is_active = FALSE). Historical rows are preserved."""
    from hermes.db.symbol_registry import deactivate_symbol

    config = load_config(ctx.obj.get("config_path"))
    try:
        row = deactivate_symbol(config, symbol, deactivated_by="cli", rationale=reason)
    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)
    click.echo(f"✓ Deactivated: {row.symbol}")
    if reason:
        click.echo(f"    reason: {reason}")


@symbols.command(name="validate")
@click.argument("symbol")
@click.pass_context
def symbols_validate(ctx: click.Context, symbol: str) -> None:
    """Live-test that the venue can fetch a price for this symbol."""
    from hermes.db.symbol_registry import validate_symbol

    config = load_config(ctx.obj.get("config_path"))
    try:
        row = validate_symbol(config, symbol)
    except ValueError as e:
        click.echo(f"✗ {e}", err=True)
        sys.exit(1)

    if row.validation_status == "ok":
        click.echo(f"✓ {row.symbol}: valid (last_price={row.last_price})")
    else:
        click.echo(
            f"✗ {row.symbol}: {row.validation_status} — {row.validation_error}",
            err=True,
        )
        sys.exit(1)


@symbols.command(name="sync")
@click.option(
    "--overwrite-active", is_flag=True, default=False,
    help="Re-activate symbols present in config that are currently inactive.",
)
@click.pass_context
def symbols_sync(ctx: click.Context, overwrite_active: bool) -> None:
    """Seed the symbols table from config/default.yaml.initial_symbols.

    Idempotent — only inserts symbols that don't yet exist in the table.
    Run this once after `platform init` to bootstrap the registry.
    """
    from hermes.db.symbol_registry import seed_from_config

    config = load_config(ctx.obj.get("config_path"))
    inserted = seed_from_config(config, added_by="cli", overwrite_active=overwrite_active)
    click.echo(f"✓ Sync complete: {inserted} new symbol(s) inserted from config.")


@symbols.command(name="show")
@click.argument("symbol")
@click.pass_context
def symbols_show(ctx: click.Context, symbol: str) -> None:
    """Show full details for one symbol."""
    from hermes.db.symbol_registry import get_symbol

    # Silence structlog before load_config() so the JSON output is parseable.
    setup_logging(level="CRITICAL", format="text", output="stdout")
    config = load_config(ctx.obj.get("config_path"))
    row = get_symbol(config, symbol)
    if row is None:
        click.echo(f"✗ Symbol not found: {symbol}", err=True)
        sys.exit(1)
    click.echo(json.dumps(row.to_dict(), indent=2, default=str))



@cli.command()
def version() -> None:
    """Print Hermes version."""
    click.echo(f"hermes {__version__}")


@cli.command()
@click.option(
    "--days-back",
    default=365,
    type=int,
    help="How many days of history to pull (default 365)",
)
@click.option(
    "--symbols",
    default=None,
    help="Comma-separated list of symbols to backfill (default = all)",
)
@click.pass_context
def backfill(ctx: click.Context, days_back: int, symbols: str | None) -> None:
    """Pull historical heartbeats from Noble Trader's Supabase into local DuckDB.

    Pulls from:
      - nt_sweep_result (weekly heavy + light sweeps)
      - nt_regime_log (periodic regime snapshots)

    See roadmap §6.2.10 for schema details.
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "json"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = symbols.split(",") if symbols else None

    click.echo("Starting Supabase backfill...")
    click.echo(f"  Days back: {days_back}")
    click.echo(f"  Symbols:   {symbol_list or 'all'}")
    click.echo("")

    from hermes.transport.supabase_backfill import SupabaseBackfiller

    async def run():
        backfiller = SupabaseBackfiller(config)
        try:
            stats = await backfiller.backfill(days_back=days_back, symbols=symbol_list)
            click.echo("")
            click.echo("Backfill complete:")
            click.echo(f"  nt_sweep_result rows ingested: {stats['sweep_results_ingested']}")
            click.echo(f"  nt_regime_log rows ingested:   {stats['regime_logs_ingested']}")
            click.echo(f"  Errors:                        {stats['errors']}")
            return stats
        finally:
            await backfiller.close()

    try:
        asyncio.run(run())
    except RuntimeError as e:
        click.echo(f"  ERROR: {e}", err=True)
        click.echo("  Make sure SUPABASE_URL and SUPABASE_ANON_KEY are set in .env", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--dry-run",
    is_flag=True,
    help="Validate config and exit without subscribing",
)
@click.pass_context
def ingest(ctx: click.Context, dry_run: bool) -> None:
    """Start the Noble Trader heartbeat subscriber (L0).

    Subscribes to Noble Trader's Redis heartbeat channel, validates each
    heartbeat, dedupes, persists to DuckDB, and re-publishes internally
    on signal.raw.hermes.{symbol}.

    Runs forever until Ctrl+C. See roadmap §2.0.
    """
    import asyncio
    import signal as signal_module

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "json"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    log.info("ingest_starting", version=__version__, dry_run=dry_run)

    if dry_run:
        click.echo("Dry run — validating config only:")
        nt_redis = config.upstream.get("noble_trader", {}).get("redis", {})
        click.echo(f"  NT Redis URL:    {nt_redis.get('url', '<not set>')}")
        click.echo(f"  NT Redis channel: {nt_redis.get('channel', '<not set>')}")
        click.echo(f"  Consumer group:   {nt_redis.get('consumer_group', '<not set>')}")
        click.echo(f"  Staleness ms:     {nt_redis.get('staleness_ms', 30000)}")
        click.echo("")
        click.echo("Config valid. Run without --dry-run to start subscribing.")
        return

    from hermes.transport.heartbeat_writer import HeartbeatWriter
    from hermes.transport.redis_subscriber import HeartbeatSubscriber

    async def run():
        writer = HeartbeatWriter(config)
        await writer.start()

        subscriber = HeartbeatSubscriber(config, writer)
        try:
            await subscriber.start()
            click.echo("Heartbeat subscriber running. Press Ctrl+C to stop.")
            click.echo("")
            click.echo("Stats will be printed every 60 seconds. Watch logs for activity.")

            # Print stats periodically
            stats_task = asyncio.create_task(_stats_loop(subscriber))

            # Wait for Ctrl+C
            stop_event = asyncio.Event()
            loop = asyncio.get_event_loop()

            def _signal_handler():
                log.info("stop_signal_received")
                stop_event.set()

            for sig in (signal_module.SIGINT, signal_module.SIGTERM):
                try:
                    loop.add_signal_handler(sig, _signal_handler)
                except NotImplementedError:
                    # Windows doesn't support add_signal_handler
                    pass

            await stop_event.wait()

            stats_task.cancel()
            try:
                await stats_task
            except asyncio.CancelledError:
                pass

        finally:
            await subscriber.stop()
            await writer.stop()

    async def _stats_loop(subscriber):
        """Print stats every 60 seconds."""
        while True:
            await asyncio.sleep(60)
            stats = subscriber.get_stats()
            log.info("ingest_stats", **stats)
            click.echo(
                f"  [stats] received={stats['received']} "
                f"accepted={stats['accepted']} "
                f"duplicates={stats['rejected_duplicate']} "
                f"stale={stats['rejected_stale']} "
                f"invalid={stats['rejected_invalid']} "
                f"regime_shifts={stats['regime_shifts']}"
            )

    try:
        asyncio.run(run())
    except RuntimeError as e:
        click.echo(f"  ERROR: {e}", err=True)
        if "not configured" in str(e):
            click.echo(
                "  Fill in NOBLE_TRADER_REDIS_URL and HERMES_REDIS_URL in .env",
                err=True,
            )
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@cli.command()
@click.option(
    "--host",
    default="127.0.0.1",
    help="Host to bind (default 127.0.0.1 — use 0.0.0.0 for network access)",
)
@click.option(
    "--port",
    default=8080,
    type=int,
    help="Port to bind (default 8080)",
)
@click.option(
    "--reload",
    is_flag=True,
    help="Enable auto-reload on code changes (development only)",
)
@click.pass_context
def dashboard(ctx: click.Context, host: str, port: int, reload: bool) -> None:
    """Start the web dashboard for visual monitoring.

    Opens a FastAPI web UI at http://127.0.0.1:8080 showing:
      - Connection status (DuckDB, Redis, Supabase, Alpaca, Hyperliquid)
      - Heartbeat ingest stats (total, accepted, rejected, regime shifts)
      - Recent heartbeats table (auto-refreshes every 10s)
      - Loaded config (secrets redacted)

    Press Ctrl+C to stop.
    """
    import uvicorn

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "json"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    # Initialize the web app with the loaded config
    from hermes.web.app import create_app

    app = create_app(config)

    click.echo("")
    click.echo("=" * 50)
    click.echo("  Hermes Dashboard starting...")
    click.echo(f"  URL: http://{host}:{port}")
    click.echo(f"  Environment: {config.environment}")
    click.echo(f"  Auto-reload: {reload}")
    click.echo("=" * 50)
    click.echo("")
    click.echo("Press Ctrl+C to stop.")
    click.echo("")

    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


@cli.command()
@click.option(
    "--symbols",
    required=False,
    default=None,
    help="Comma-separated list of symbols (default: all active symbols from the registry).",
)
@click.option(
    "--venues",
    default=None,
    help="Comma-separated venues (default: all enabled)",
)
@click.pass_context
def stream(ctx: click.Context, symbols: str, venues: str | None) -> None:
    """Stream live market data from venue WebSockets.

    Connects to venue WebSockets, receives ticks + order books,
    writes to Parquet (historical) and publishes to internal Redis (hot tier).

    Example:
      platform stream --symbols BTC/USD,SOL/USD,BTC-PERP
      platform stream --symbols BTC-PERP --venues hyperliquid
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)
    venue_list = venues.split(",") if venues else [
        v for v, vc in config.venues.items() if vc.enabled
    ]

    click.echo(f"Starting market data stream...")
    click.echo(f"  Symbols: {symbol_list}")
    click.echo(f"  Venues:  {venue_list}")

    from hermes.transport.parquet_writer import ParquetWriter
    from hermes.transport.adapters.alpaca_adapter import AlpacaAdapter
    from hermes.transport.adapters.hyperliquid_adapter import HyperliquidAdapter

    async def run():
        # Initialize adapters
        adapters = []
        if "alpaca" in venue_list:
            adapter = AlpacaAdapter(config)
            await adapter.connect()
            adapters.append(("alpaca", adapter))
        if "hyperliquid" in venue_list:
            adapter = HyperliquidAdapter(config)
            await adapter.connect()
            adapters.append(("hyperliquid", adapter))

        if not adapters:
            click.echo("No adapters connected. Check .env credentials.")
            return

        # Initialize Parquet writer
        writer = ParquetWriter(base_path="./data/parquet")
        await writer.start()

        # Group symbols by venue (simple: all to all venues for now)
        tasks = []
        for venue_name, adapter in adapters:
            # Normalize symbols for this venue
            venue_symbols = [adapter.normalize_symbol(s) for s in symbol_list]
            # Add back the venue-native symbol to the tick
            tasks.append(_stream_ticks_for_venue(adapter, venue_symbols, writer, venue_name))

        click.echo("  Streaming... Press Ctrl+C to stop.")

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            for _, adapter in adapters:
                await adapter.disconnect()
            await writer.stop()

    async def _stream_ticks_for_venue(adapter, symbols, writer, venue_name):
        """Stream ticks from one venue and write to Parquet."""
        try:
            async for tick in adapter.stream_ticks(symbols):
                # Write to Parquet
                await writer.write_tick(tick)
                # Publish to internal Redis (best-effort)
                click.echo(
                    f"  [{tick.ts.strftime('%H:%M:%S')}] {venue_name} "
                    f"{tick.symbol}: ${tick.price}"
                )
        except Exception as e:
            log.error("stream_error", venue=venue_name, error=str(e))

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@cli.command()
@click.option(
    "--symbols",
    required=False,
    default=None,
    help="Comma-separated list of symbols (default: all active symbols from the registry).",
)
@click.option(
    "--venues",
    default=None,
    help="Comma-separated venues (default: all enabled)",
)
@click.pass_context
def monitor(ctx: click.Context, symbols: str, venues: str | None) -> None:
    """Start the Active Price Monitor (L2.8).

    Connects to venue WebSockets, feeds ticks through the full monitor
    pipeline (aggregation, indicators, anomaly detection, stop watching,
    correlation, funding), and writes events to DuckDB + Redis.

    Example:
      platform monitor --symbols BTC/USD,SOL/USD,BTC-PERP
    """
    import asyncio
    import signal as signal_module

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)
    venue_list = venues.split(",") if venues else [
        v for v, vc in config.venues.items() if vc.enabled
    ]

    click.echo(f"Starting Active Price Monitor...")
    click.echo(f"  Symbols: {symbol_list}")
    click.echo(f"  Venues:  {venue_list}")

    from hermes.monitor.orchestrator import PriceMonitor
    from hermes.transport.adapters.alpaca_adapter import AlpacaAdapter
    from hermes.transport.adapters.hyperliquid_adapter import HyperliquidAdapter

    async def run():
        # Initialize monitor
        price_monitor = PriceMonitor(config)
        await price_monitor.start()

        # Initialize adapters
        adapters = []
        if "alpaca" in venue_list:
            adapter = AlpacaAdapter(config)
            await adapter.connect()
            adapters.append(("alpaca", adapter))
        if "hyperliquid" in venue_list:
            adapter = HyperliquidAdapter(config)
            await adapter.connect()
            adapters.append(("hyperliquid", adapter))

        if not adapters:
            click.echo("No adapters connected. Check .env credentials.")
            await price_monitor.stop()
            return

        # Stream ticks through monitor
        tasks = []
        for venue_name, adapter in adapters:
            venue_symbols = [adapter.normalize_symbol(s) for s in symbol_list]
            tasks.append(_monitor_ticks_for_venue(adapter, venue_symbols, price_monitor, venue_name))
            # Also stream order books
            tasks.append(_monitor_books_for_venue(adapter, venue_symbols, price_monitor, venue_name))

        # Stats printer
        stats_task = asyncio.create_task(_stats_loop(price_monitor))

        click.echo("  Monitoring... Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            log.info("stop_signal_received")
            stop_event.set()

        for sig in (signal_module.SIGINT, signal_module.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass  # Windows

        await stop_event.wait()

        stats_task.cancel()
        for task in tasks:
            task.cancel()
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass

        for _, adapter in adapters:
            await adapter.disconnect()
        await price_monitor.stop()

    async def _monitor_ticks_for_venue(adapter, symbols, price_monitor, venue_name):
        """Feed ticks from venue to monitor."""
        try:
            async for tick in adapter.stream_ticks(symbols):
                events = await price_monitor.on_tick(tick)
                for event in events:
                    click.echo(
                        f"  [{event.ts.strftime('%H:%M:%S')}] {venue_name} "
                        f"{event.event_type} {event.symbol}: "
                        f"${event.last_price} ({event.severity})"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("monitor_stream_error", venue=venue_name, error=str(e))

    async def _monitor_books_for_venue(adapter, symbols, price_monitor, venue_name):
        """Feed order books from venue to monitor."""
        try:
            async for book in adapter.stream_order_book(symbols):
                await price_monitor.on_order_book(book)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("monitor_book_stream_error", venue=venue_name, error=str(e))

    async def _stats_loop(price_monitor):
        """Print stats every 30 seconds."""
        while True:
            await asyncio.sleep(30)
            stats = price_monitor.get_stats()
            click.echo(
                f"  [stats] ticks={stats['ticks_processed']} "
                f"books={stats['books_processed']} "
                f"bars={stats['bars_closed']} "
                f"events={stats['events_emitted']}"
            )

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@cli.command()
@click.option(
    "--symbol",
    required=True,
    help="Symbol to backfill (e.g., BTC-PERP, BTC/USD, SOL/USD)",
)
@click.option(
    "--venue",
    required=True,
    type=click.Choice(["alpaca", "hyperliquid"]),
    help="Venue to pull from",
)
@click.option(
    "--timeframe",
    default="1m",
    help="Bar timeframe (1m, 5m, 1h, 1d)",
)
@click.option(
    "--days-back",
    default=30,
    type=int,
    help="Days of history to pull (default 30)",
)
@click.pass_context
def backfill_market(ctx: click.Context, symbol: str, venue: str, timeframe: str, days_back: int) -> None:
    """Pull historical market data (bars) from a venue's REST API.

    Stores in Parquet for offline analysis and DuckDB queries.

    Example:
      platform backfill-market --symbol BTC-PERP --venue hyperliquid --timeframe 1m --days-back 90
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    click.echo(f"Backfilling market data...")
    click.echo(f"  Symbol:    {symbol}")
    click.echo(f"  Venue:     {venue}")
    click.echo(f"  Timeframe: {timeframe}")
    click.echo(f"  Days back: {days_back}")

    from hermes.transport.adapters.alpaca_adapter import AlpacaAdapter
    from hermes.transport.adapters.hyperliquid_adapter import HyperliquidAdapter
    from hermes.transport.parquet_writer import ParquetWriter

    async def run():
        # Initialize adapter
        if venue == "alpaca":
            adapter = AlpacaAdapter(config)
        else:
            adapter = HyperliquidAdapter(config)

        await adapter.connect()

        # Fetch bars
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        venue_symbol = adapter.normalize_symbol(symbol)

        click.echo(f"  Fetching bars from {start.date()} to {end.date()}...")
        bars = await adapter.fetch_historical_bars(
            symbol=venue_symbol,
            timeframe=timeframe,
            start=start,
            end=end,
        )

        click.echo(f"  Fetched {len(bars)} bars")

        if bars:
            # Write to Parquet
            writer = ParquetWriter()
            await writer.start()
            await writer.write_bars(bars)
            await writer.stop()
            click.echo(f"  Written to Parquet")

        await adapter.disconnect()
        click.echo(f"  Done.")

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--symbols",
    required=False,
    default=None,
    help="Comma-separated list of symbols (default: all active symbols from the registry).",
)
@click.option(
    "--equity",
    default=100000,
    type=float,
    help="Account equity in USD (default 100000)",
)
@click.pass_context
def synthesize(ctx: click.Context, symbols: str, equity: float) -> None:
    """Start the L4 Signal Synthesizer (BEV combiner).

    Consumes Noble Trader heartbeats (from L0 internal Redis),
    enriches with 7-state meta-regime + renko analysis, produces
    blended entry/execution decisions.

    Writes to DuckDB trade_signals_blended table + publishes on
    signal.blended.{symbol} Redis channel.

    Example:
      platform synthesize --symbols BTC/USD,SOL/USD,BTC-PERP --equity 100000
    """
    import asyncio
    import signal as signal_module

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)

    click.echo(f"Starting L4 Signal Synthesizer...")
    click.echo(f"  Symbols: {symbol_list}")
    click.echo(f"  Equity:  ${equity:,.2f}")

    from hermes.signals.synthesizer import SignalSynthesizer
    from hermes.transport.heartbeat_writer import HeartbeatWriter
    from hermes.transport.redis_subscriber import HeartbeatSubscriber

    async def run():
        # Initialize synthesizer
        synthesizer = SignalSynthesizer(config)
        await synthesizer.start()

        # We need to consume heartbeats from the internal Redis channel
        # signal.raw.hermes.{symbol} (published by L0 subscriber)
        import redis.asyncio as aioredis

        hermes_redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
        if "<" in hermes_redis_url or hermes_redis_url.startswith("secret:"):
            click.echo("  ERROR: HERMES_REDIS_URL not configured in .env", err=True)
            await synthesizer.stop()
            return

        redis_client = aioredis.from_url(hermes_redis_url, decode_responses=True)
        await redis_client.ping()

        # Subscribe to internal heartbeat channels
        pubsub = redis_client.pubsub()
        channels = [f"signal.raw.hermes.{s}" for s in symbol_list]
        await pubsub.subscribe(*channels)
        click.echo(f"  Subscribed to: {channels}")
        click.echo("  Synthesizing... Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            log.info("stop_signal_received")
            stop_event.set()

        for sig in (signal_module.SIGINT, signal_module.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass  # Windows

        async def _process_loop():
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(timeout=1.0, ignore_subscribe_messages=True),
                        timeout=2.0,
                    )
                    if message and message["type"] == "message":
                        import json
                        from hermes.schemas.heartbeat import NobleTraderHeartbeat

                        try:
                            data = json.loads(message["data"])
                            # Reconstruct a minimal heartbeat from the internal republished message
                            # The internal channel has a subset of fields, so we need to
                            # also read from the full heartbeat in DuckDB if needed.
                            # For now, use the fields available.
                            hb = NobleTraderHeartbeat(
                                type="heartbeat",
                                symbol=data["symbol"],
                                ts=int(datetime.now(timezone.utc).timestamp() * 1000),
                                regime=data.get("regime", "unknown"),
                                regime_conf=data.get("regime_conf", 0.5),
                                signal=data["signal"],
                                entry_price=data["entry_price"],
                                stop_loss=data["stop_loss"],
                                take_profit=data["take_profit"],
                                aggression="mid",
                                brick_size=data["brick_size"],
                                sl_bricks=3,
                                tp_bricks=5,
                                kelly_f=data.get("kelly_f", 0.1),
                                effective_kelly=data.get("effective_kelly", 0.1),
                                ev=data.get("ev", 0),
                                ev_per_dollar=data.get("ev_per_dollar", 0),
                                p_win=data.get("p_win", 0.5),
                                p_regime=data.get("p_regime", 0.5),
                                p_imbalance=data.get("p_imbalance", 0.5),
                                p_markov=data.get("p_markov", 0.5),
                                ev_scale=data.get("ev_scale", 1.0),
                                markov_current_state="FLAT",
                                regime_shift=data.get("regime_shift", "false"),
                                prev_regime=None,
                                shift_at=0,
                                shifts_24h=0,
                            )

                            signal = await synthesizer.process_heartbeat(
                                hb, equity=equity
                            )
                            click.echo(
                                f"  [{signal.ts.strftime('%H:%M:%S')}] {signal.symbol} "
                                f"{signal.direction} → {signal.entry_strategy} "
                                f"({signal.meta_regime}) "
                                f"${signal.final_size_usd:.0f} "
                                f"[{signal.brick_pattern}]"
                            )

                        except Exception as e:
                            log.warning("heartbeat_parse_failed", error=str(e))
                except asyncio.TimeoutError:
                    continue

        processor = asyncio.create_task(_process_loop())

        # Stats printer
        async def _stats_loop():
            while not stop_event.is_set():
                await asyncio.sleep(60)
                stats = synthesizer.get_stats()
                click.echo(
                    f"  [stats] processed={stats['heartbeats_processed']} "
                    f"produced={stats['signals_produced']} "
                    f"blocked={stats['signals_blocked']}"
                )

        stats_task = asyncio.create_task(_stats_loop())

        await stop_event.wait()

        processor.cancel()
        stats_task.cancel()
        try:
            await processor
        except asyncio.CancelledError:
            pass
        try:
            await stats_task
        except asyncio.CancelledError:
            pass

        await pubsub.unsubscribe(*channels)
        await redis_client.close()
        await synthesizer.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@cli.command()
@click.option(
    "--equity",
    default=100000,
    type=float,
    help="Initial account equity in USD (default 100000)",
)
@click.pass_context
def risk(ctx: click.Context, equity: float) -> None:
    """Start the L5 Portfolio & Risk Engine.

    Subscribes to blended signals from L4 (signal.blended.{symbol}),
    evaluates each through the risk gate (circuit breakers, position limits,
    VaR, autonomy tiers), and produces risk decisions.

    Writes to DuckDB: risk_decisions, circuit_breaker_events, account_snapshots.
    Publishes on: risk.decision.{signal_id} Redis channel.

    Example:
      platform risk --equity 100000
    """
    import asyncio
    import signal as signal_module

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    click.echo(f"Starting L5 Portfolio & Risk Engine...")
    click.echo(f"  Initial equity: ${equity:,.2f}")

    from hermes.portfolio.orchestrator import PortfolioRiskEngine

    async def run():
        engine = PortfolioRiskEngine(config, initial_equity=equity)
        await engine.start()

        # Subscribe to blended signals from L4
        import redis.asyncio as aioredis

        hermes_redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
        if "<" in hermes_redis_url or hermes_redis_url.startswith("secret:"):
            click.echo("  ERROR: HERMES_REDIS_URL not configured in .env", err=True)
            await engine.stop()
            return

        redis_client = aioredis.from_url(hermes_redis_url, decode_responses=True)
        await redis_client.ping()

        # Subscribe to all blended signal channels (pattern subscription)
        pubsub = redis_client.pubsub()
        await pubsub.psubscribe("signal.blended.*")
        click.echo("  Subscribed to: signal.blended.*")
        click.echo("  Evaluating... Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            log.info("stop_signal_received")
            stop_event.set()

        for sig in (signal_module.SIGINT, signal_module.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        async def _process_loop():
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(timeout=1.0, ignore_subscribe_messages=True),
                        timeout=2.0,
                    )
                    if message and message["type"] == "pmessage":
                        import json
                        from hermes.signals.synthesizer import BlendedSignal

                        try:
                            data = json.loads(message["data"])
                            signal = BlendedSignal(**data)
                            decision = await engine.evaluate_signal(signal)

                            status = "✓ APPROVED" if decision.approved else "✗ REJECTED"
                            click.echo(
                                f"  [{decision.ts.strftime('%H:%M:%S')}] {signal.symbol} "
                                f"{status} "
                                f"${decision.approved_size_usd:.0f}/"
                                f"${decision.requested_size_usd:.0f} "
                                f"tier={decision.autonomy_tier} "
                                f"[{decision.reason[:50]}]"
                            )

                        except Exception as e:
                            log.warning("signal_parse_failed", error=str(e))
                except asyncio.TimeoutError:
                    continue

        async def _breaker_loop():
            """Check risk breakers every 10 seconds."""
            while not stop_event.is_set():
                await asyncio.sleep(10)
                events = await engine.check_risk_breakers()
                for event in events:
                    click.echo(
                        f"  [BREAKER] {event.breaker_type} level={event.level} "
                        f"{event.action_taken} ({event.payload.get('check', '')})"
                    )

        async def _stats_loop():
            while not stop_event.is_set():
                await asyncio.sleep(60)
                stats = engine.get_stats()
                metrics = engine.get_metrics()
                click.echo(
                    f"  [stats] evaluated={stats['signals_evaluated']} "
                    f"approved={stats['signals_approved']} "
                    f"rejected={stats['signals_rejected']} | "
                    f"equity=${metrics.equity_total:,.0f} "
                    f"DD={metrics.drawdown_pct:.2%} "
                    f"positions={metrics.n_open_positions}"
                )

        processor = asyncio.create_task(_process_loop())
        breaker_task = asyncio.create_task(_breaker_loop())
        stats_task = asyncio.create_task(_stats_loop())

        await stop_event.wait()

        for t in [processor, breaker_task, stats_task]:
            t.cancel()
        for t in [processor, breaker_task, stats_task]:
            try:
                await t
            except asyncio.CancelledError:
                pass

        await pubsub.punsubscribe("signal.blended.*")
        await redis_client.close()
        await engine.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@cli.command()
@click.option(
    "--equity",
    default=100000,
    type=float,
    help="Initial account equity in USD (default 100000)",
)
@click.option(
    "--paper/--live",
    default=True,
    help="Paper trading mode (default) or live mode",
)
@click.pass_context
def execute(ctx: click.Context, equity: float, paper: bool) -> None:
    """Start the L3 Execution Engine.

    Subscribes to risk decisions from L5 (risk.decision.*),
    creates orders via SmartOrderRouter, executes via paper engine
    (or live venue adapters), writes to DuckDB (orders, order_events, fills).

    In paper mode: simulated fills with slippage model.
    In live mode: real orders on Alpaca + Hyperliquid (USE WITH CAUTION).

    Example:
      platform execute --equity 100000 --paper
    """
    import asyncio
    import signal as signal_module

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    click.echo(f"Starting L3 Execution Engine...")
    click.echo(f"  Initial equity: ${equity:,.2f}")
    click.echo(f"  Mode: {'PAPER' if paper else 'LIVE'}")

    if not paper:
        click.echo("  WARNING: Live mode is not yet implemented. Using paper mode.", err=True)
        paper = True

    from hermes.execution.orchestrator import ExecutionEngine
    from hermes.portfolio.state import PortfolioStateService

    async def run():
        # Initialize portfolio state (shared with L5 in production; standalone for now)
        portfolio_state = PortfolioStateService(
            initial_equity=equity,
            config_hash=get_config_hash(config),
        )

        engine = ExecutionEngine(config, portfolio_state, paper_mode=paper)
        await engine.start()

        # Subscribe to risk decisions from L5
        import redis.asyncio as aioredis

        hermes_redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
        if "<" in hermes_redis_url or hermes_redis_url.startswith("secret:"):
            click.echo("  ERROR: HERMES_REDIS_URL not configured in .env", err=True)
            await engine.stop()
            return

        redis_client = aioredis.from_url(hermes_redis_url, decode_responses=True)
        await redis_client.ping()

        # Subscribe to all risk decision channels
        pubsub = redis_client.pubsub()
        await pubsub.psubscribe("risk.decision.*")
        click.echo("  Subscribed to: risk.decision.*")
        click.echo("  Executing... Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        loop = asyncio.get_event_loop()

        def _signal_handler():
            log.info("stop_signal_received")
            stop_event.set()

        for sig in (signal_module.SIGINT, signal_module.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                pass

        async def _process_loop():
            while not stop_event.is_set():
                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(timeout=1.0, ignore_subscribe_messages=True),
                        timeout=2.0,
                    )
                    if message and message["type"] == "pmessage":
                        import json
                        from hermes.portfolio.risk_gate import RiskDecision
                        from hermes.signals.synthesizer import BlendedSignal

                        try:
                            decision_data = json.loads(message["data"])
                            decision = RiskDecision(**decision_data)

                            if not decision.approved:
                                continue

                            # We need the original signal — fetch from DuckDB
                            # For now, reconstruct minimal signal from decision
                            # In production, L5 would include signal in the decision payload
                            # or we'd query DuckDB trade_signals_blended
                            import duckdb
                            from hermes.db.migrate import get_duckdb_path

                            db_path = get_duckdb_path(config)
                            with duckdb.connect(str(db_path), read_only=True) as conn:
                                result = conn.execute(
                                    "SELECT * FROM trade_signals_blended WHERE signal_id = ?",
                                    [decision.signal_id],
                                ).fetchdf()

                            if result.empty:
                                log.warning("signal_not_found", signal_id=decision.signal_id)
                                continue

                            signal_data = result.iloc[0].to_dict()
                            # Convert timestamp strings
                            for ts_field in ["ts_emitted"]:
                                if ts_field in signal_data:
                                    signal_data[ts_field] = str(signal_data[ts_field])

                            signal = BlendedSignal(**{
                                k: v for k, v in signal_data.items()
                                if k in BlendedSignal.model_fields
                            })

                            orders = await engine.execute_decision(
                                decision=decision,
                                signal=signal,
                                current_price=signal.nt_entry_price,
                            )

                            for order in orders:
                                click.echo(
                                    f"  [{order.ts_created.strftime('%H:%M:%S')}] "
                                    f"{order.symbol} {order.side.value} "
                                    f"{order.qty_requested} @ "
                                    f"{'market' if not order.price_limit else order.price_limit} "
                                    f"→ {order.status.value} "
                                    f"fill={order.avg_fill_price or 'pending'} "
                                    f"fees=${order.total_fees:.2f}"
                                )

                        except Exception as e:
                            log.warning("decision_parse_failed", error=str(e))
                except asyncio.TimeoutError:
                    continue

        async def _stats_loop():
            while not stop_event.is_set():
                await asyncio.sleep(60)
                stats = engine.get_stats()
                click.echo(
                    f"  [stats] decisions={stats['decisions_received']} "
                    f"orders={stats['orders_created']} "
                    f"filled={stats['orders_filled']} "
                    f"fees=${stats['total_fees']:.2f}"
                )

        processor = asyncio.create_task(_process_loop())
        stats_task = asyncio.create_task(_stats_loop())

        await stop_event.wait()

        processor.cancel()
        stats_task.cancel()
        try:
            await processor
        except asyncio.CancelledError:
            pass
        try:
            await stats_task
        except asyncio.CancelledError:
            pass

        await pubsub.punsubscribe("risk.decision.*")
        await redis_client.close()
        await engine.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        click.echo("\nStopped.")


@cli.command()
@click.option(
    "--equity",
    default=100000,
    type=float,
    help="Initial equity for tear sheet (if no snapshots exist)",
)
@click.pass_context
def pnl(ctx: click.Context, equity: float) -> None:
    """Generate PnL tear sheet (performance report).

    Computes 30+ performance metrics from equity curve + trade history:
    - Returns: total, annual, daily, best/worst day
    - Risk: Sharpe, Sortino, Calmar, Omega, VaR, CVaR
    - Drawdown: max DD, recovery, ulcer index
    - Trading: win rate, profit factor, avg R, expectancy
    - Distribution: skew, kurtosis
    - By regime: per-regime win rate + PnL

    Reads from DuckDB: account_snapshots, pnl_realized.
    Outputs to console + DuckDB.

    Example:
      platform pnl
    """
    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    from hermes.analytics.pnl_service import PnLService
    from hermes.analytics.tear_sheet import TearSheet
    from hermes.portfolio.state import PortfolioStateService

    portfolio_state = PortfolioStateService(initial_equity=equity, config_hash=get_config_hash(config))
    pnl_service = PnLService(config, portfolio_state)
    tear_sheet = TearSheet(pnl_service)

    click.echo("Generating PnL tear sheet...")
    metrics = tear_sheet.generate()

    if "error" in metrics:
        click.echo(f"  {metrics['error']} — only {metrics.get('n_points', 0)} data points")
        click.echo("  Run the platform for a while to accumulate snapshots + trades, then re-run.")
        return

    click.echo("")
    click.echo("=" * 60)
    click.echo("  Hermes PnL Tear Sheet")
    click.echo("=" * 60)

    # Summary
    s = metrics.get("summary", {})
    click.echo(f"\n  Period: {s.get('period_start', '?')[:19]} → {s.get('period_end', '?')[:19]}")
    click.echo(f"  Data points: {s.get('n_data_points', 0)}")
    click.echo(f"  Trades: {s.get('n_trades', 0)}")
    click.echo(f"  Initial equity: ${s.get('initial_equity', 0):,.2f}")
    click.echo(f"  Final equity: ${s.get('final_equity', 0):,.2f}")

    # Returns
    r = metrics.get("returns", {})
    click.echo(f"\n  --- Returns ---")
    click.echo(f"  Total return:       {r.get('total_return_pct', 0):.2f}%")
    click.echo(f"  Annual return:      {r.get('annual_return_pct', 0):.2f}%")
    click.echo(f"  Avg daily return:   {r.get('avg_daily_return_bps', 0):.2f} bps")
    click.echo(f"  Best day:          +{r.get('best_day_bps', 0):.2f} bps")
    click.echo(f"  Worst day:         {r.get('worst_day_bps', 0):.2f} bps")
    click.echo(f"  Positive days:      {r.get('positive_days_pct', 0):.1f}%")

    # Risk-adjusted
    ra = metrics.get("risk_adjusted", {})
    click.echo(f"\n  --- Risk-Adjusted ---")
    click.echo(f"  Sharpe ratio:       {ra.get('sharpe', 0):.3f}")
    click.echo(f"  Sortino ratio:      {ra.get('sortino', 0):.3f}")
    click.echo(f"  Calmar ratio:       {ra.get('calmar', 0):.3f}")
    click.echo(f"  Omega ratio:        {ra.get('omega', 0):.3f}")
    click.echo(f"  VaR 95% (daily):    {ra.get('var_95_daily_pct', 0):.2f}%")
    click.echo(f"  CVaR 95% (daily):   {ra.get('cvar_95_daily_pct', 0):.2f}%")
    click.echo(f"  Volatility (ann):   {ra.get('volatility_annualized_pct', 0):.2f}%")

    # Drawdown
    dd = metrics.get("drawdown", {})
    click.echo(f"\n  --- Drawdown ---")
    click.echo(f"  Max drawdown:       {dd.get('max_dd_pct', 0):.2f}% (${dd.get('max_dd_usd', 0):,.2f})")
    click.echo(f"  Current drawdown:   {dd.get('current_dd_pct', 0):.2f}%")
    click.echo(f"  Max DD duration:    {dd.get('max_dd_duration_hours', 0):.1f}h")
    click.echo(f"  Underwater:         {dd.get('underwater_pct', 0):.1f}% of time")
    click.echo(f"  Ulcer index:        {dd.get('ulcer_index', 0):.2f}")

    # Trading
    t = metrics.get("trading", {})
    click.echo(f"\n  --- Trading ---")
    click.echo(f"  Total trades:       {t.get('n_trades', 0)}")
    click.echo(f"  Win rate:           {t.get('win_rate_pct', 0):.1f}%")
    click.echo(f"  Profit factor:      {t.get('profit_factor', 0):.2f}")
    click.echo(f"  Avg win:            ${t.get('avg_win_usd', 0):,.2f}")
    click.echo(f"  Avg loss:           ${t.get('avg_loss_usd', 0):,.2f}")
    click.echo(f"  Expectancy:         ${t.get('expectancy_usd', 0):,.2f}")
    click.echo(f"  Avg R-multiple:     {t.get('avg_r_multiple', 0):.4f}")
    click.echo(f"  Avg hold time:      {t.get('avg_hold_duration_min', 0):.1f} min")
    click.echo(f"  Total net PnL:      ${t.get('total_net_pnl', 0):,.2f}")

    # By regime
    by_regime = t.get("by_regime", {})
    if by_regime:
        click.echo(f"\n  --- By Regime ---")
        click.echo(f"  {'Regime':<25} {'Trades':>7} {'Win%':>7} {'Total PnL':>12} {'Avg PnL':>10}")
        click.echo(f"  {'-'*25} {'-'*7} {'-'*7} {'-'*12} {'-'*10}")
        for regime, stats in sorted(by_regime.items(), key=lambda x: x[1]["total_pnl"], reverse=True):
            click.echo(
                f"  {regime:<25} {stats['n_trades']:>7} {stats['win_rate']:>6.1f}% "
                f"${stats['total_pnl']:>10,.2f} ${stats['avg_pnl']:>8,.2f}"
            )

    # Distribution
    dist = metrics.get("distribution", {})
    click.echo(f"\n  --- Distribution ---")
    click.echo(f"  Skewness:           {dist.get('skewness', 0):.3f}")
    click.echo(f"  Excess kurtosis:    {dist.get('kurtosis_excess', 0):.3f}")
    click.echo(f"  Mean daily:         {dist.get('mean_daily_bps', 0):.2f} bps")
    click.echo(f"  Std daily:          {dist.get('std_daily_bps', 0):.2f} bps")

    click.echo(f"\n{'=' * 60}")


@cli.command()
@click.option(
    "--symbols",
    required=False,
    default=None,
    help="Comma-separated list of symbols (default: all active symbols from the registry).",
)
@click.option(
    "--days-back",
    default=30,
    type=int,
    help="Days of history to backtest (default 30)",
)
@click.option(
    "--equity",
    default=100000,
    type=float,
    help="Initial equity (default 100000)",
)
@click.option(
    "--speed",
    default=0.0,
    type=float,
    help="Seconds between heartbeats (0 = max speed)",
)
@click.pass_context
def backtest(ctx: click.Context, symbols: str, days_back: int, equity: float, speed: float) -> None:
    """Run a backtest by replaying historical heartbeats through the Hermes pipeline.

    Reads Noble Trader heartbeats from the signal_heartbeats table (populated by
    `platform ingest` or `platform backfill`), replays them through L4 (synthesize)
    → L5 (risk) → L3 (execute paper), and generates a tear sheet.

    Writes results to backtest_runs table.

    Example:
      platform backtest --symbols BTC-PERP --days-back 90 --equity 100000
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    click.echo(f"Starting backtest...")
    click.echo(f"  Symbols:   {symbol_list}")
    click.echo(f"  Period:    {start.date()} → {end.date()} ({days_back} days)")
    click.echo(f"  Equity:    ${equity:,.2f}")
    click.echo(f"  Speed:     {'instant' if speed == 0 else f'{speed}s/heartbeat'}")
    click.echo("")

    from hermes.backtest.engine import BacktestEngine

    async def run():
        engine = BacktestEngine(config)
        result = await engine.run_heartbeat_replay(
            symbols=symbol_list,
            start=start,
            end=end,
            initial_equity=equity,
            speed=speed,
        )

        click.echo("")
        click.echo("=" * 60)
        click.echo("  Backtest Results")
        click.echo("=" * 60)
        click.echo(f"  Run ID:       {result.run_id}")
        click.echo(f"  Duration:     {result.duration_sec}s")
        click.echo(f"  Heartbeats:   {result.n_heartbeats}")
        click.echo(f"  Signals:      {result.n_signals_produced} produced, "
                   f"{result.n_signals_approved} approved, "
                   f"{result.n_signals_rejected} rejected")
        click.echo(f"  Orders:       {result.n_orders}")
        click.echo(f"  Fills:        {result.n_fills}")
        click.echo(f"  Final equity: ${result.final_equity:,.2f}")
        click.echo(f"  Total return: {result.total_return_pct:.2f}%")
        click.echo(f"  Net PnL:      ${result.total_net_pnl:,.2f}")
        click.echo(f"  Max DD:       {result.max_drawdown_pct:.2f}%")

        if result.error:
            click.echo(f"  ERROR: {result.error}")

        if result.tear_sheet and "error" not in result.tear_sheet:
            ts = result.tear_sheet
            ra = ts.get("risk_adjusted", {})
            t = ts.get("trading", {})
            click.echo(f"\n  --- Risk-Adjusted ---")
            click.echo(f"  Sharpe:       {ra.get('sharpe', 0):.3f}")
            click.echo(f"  Sortino:      {ra.get('sortino', 0):.3f}")
            click.echo(f"  Calmar:       {ra.get('calmar', 0):.3f}")
            click.echo(f"\n  --- Trading ---")
            click.echo(f"  Win rate:     {t.get('win_rate_pct', 0):.1f}%")
            click.echo(f"  Profit factor:{t.get('profit_factor', 0):.2f}")
            click.echo(f"  Avg R:        {t.get('avg_r_multiple', 0):.4f}")

        click.echo(f"\n{'=' * 60}")

        return result

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated list of symbols",
)
@click.option(
    "--days-back",
    default=30,
    type=int,
    help="Days of history for rigor check (default 30)",
)
@click.pass_context
def rigor(ctx: click.Context, symbols: str, days_back: int) -> None:
    """Run statistical rigor checks on backtest results.

    Checks:
    1. Walk-forward validation (OOS Sharpe within 80% of IS)
    2. Deflated Sharpe > 1.0
    3. Monte Carlo 5th percentile > 0
    4. Bootstrap CI lower bound > 0
    5. Regime coverage (4+ of 7 regimes positive)
    6. Capacity check

    Example:
      platform rigor --symbols BTC-PERP --days-back 90
    """
    import asyncio
    from datetime import datetime, timedelta, timezone

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)

    click.echo(f"Running statistical rigor checks...")
    click.echo(f"  Symbols: {symbol_list}")
    click.echo("")

    from hermes.backtest.statistics import run_rigor_checks
    from hermes.db.migrate import get_duckdb_path
    import duckdb
    import numpy as np

    db_path = get_duckdb_path(config)

    try:
        with duckdb.connect(str(db_path), read_only=True) as conn:
            # Load realized PnL as "returns"
            result = conn.execute(
                """
                SELECT net_pnl, regime_at_close FROM pnl_realized
                ORDER BY ts ASC
                """
            ).fetchdf()

            if result.empty:
                click.echo("  No PnL data found. Run a backtest first.")
                return

            # Convert PnL to returns (simplified: use pnl directly as "returns")
            returns = np.array(result["net_pnl"].tolist(), dtype=float)
            trades = result.to_dict("records")

        rigor_result = run_rigor_checks(returns, trades, n_trials=1)

        click.echo("=" * 60)
        click.echo("  Statistical Rigor Checks")
        click.echo("=" * 60)
        click.echo(f"  Trades:             {rigor_result.n_trades}")
        click.echo(f"  Checks passed:      {rigor_result.checks_passed}/6")
        click.echo(f"  Overall:            {'PASS' if rigor_result.passed else 'FAIL'}")
        click.echo("")

        wf = rigor_result.walk_forward
        click.echo(f"  1. Walk-Forward:    {'PASS' if wf.get('passed') else 'FAIL'}")
        click.echo(f"     Train Sharpe:    {wf.get('train_sharpe', 0)}")
        click.echo(f"     Test Sharpe:     {wf.get('test_sharpe', 0)}")
        click.echo(f"     Decay:           {wf.get('decay_pct', 0)}%")

        click.echo(f"  2. Deflated Sharpe: {'PASS' if rigor_result.deflated_sharpe > 1.0 else 'FAIL'}")
        click.echo(f"     Value:           {rigor_result.deflated_sharpe}")

        mc = rigor_result.monte_carlo
        click.echo(f"  3. Monte Carlo:     {'PASS' if mc.get('passed') else 'FAIL'}")
        click.echo(f"     5th percentile:  {mc.get('percentile_5', 0)}")
        click.echo(f"     p-value:         {mc.get('p_value', 0)}")

        click.echo(f"  4. Bootstrap CI:    {'PASS' if rigor_result.bootstrap_sharpe_lower > 0 else 'FAIL'}")
        click.echo(f"     Lower bound:     {rigor_result.bootstrap_sharpe_lower}")
        click.echo(f"     Upper bound:     {rigor_result.bootstrap_sharpe_upper}")

        click.echo(f"  5. Regime Coverage: {'PASS' if rigor_result.n_regimes_with_positive_expectancy >= 4 else 'FAIL'}")
        click.echo(f"     Positive regimes: {rigor_result.n_regimes_with_positive_expectancy}/7")

        click.echo(f"  6. Capacity:        {'PASS' if not rigor_result.capacity_constrained else 'FAIL'}")

        if rigor_result.checks_failed:
            click.echo(f"\n  Failed checks:")
            for fail in rigor_result.checks_failed:
                click.echo(f"    - {fail}")

        click.echo(f"\n{'=' * 60}")

    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated list of symbols",
)
@click.option(
    "--days-back",
    default=90,
    type=int,
    help="Days of history to optimize over (default 90)",
)
@click.option(
    "--n-trials",
    default=200,
    type=int,
    help="Number of Optuna trials (default 200)",
)
@click.pass_context
def optimize(ctx: click.Context, symbols: str, days_back: int, n_trials: int) -> None:
    """Run entry/execution optimization sweep (Phase 8).

    Uses Bayesian optimization (Optuna TPESampler) to find the best
    entry timing + execution method parameters. Compares each trial
    against the "blindly execute at market" baseline.

    All trials pass through 6 statistical rigor checks before acceptance.

    Example:
      platform optimize --symbols BTC-PERP --days-back 90 --n-trials 200
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)

    click.echo(f"Starting entry/execution optimization sweep...")
    click.echo(f"  Symbols:  {symbol_list}")
    click.echo(f"  Days back: {days_back}")
    click.echo(f"  Trials:   {n_trials}")
    click.echo("")

    from hermes.backtest.optimizer import RenkoSimulationEngine

    async def run():
        engine = RenkoSimulationEngine(config)
        results = await engine.run_entry_timing_sweep(
            symbols=symbol_list,
            days_back=days_back,
            n_trials=n_trials,
        )

        click.echo("")
        click.echo("=" * 60)
        click.echo("  Optimization Results")
        click.echo("=" * 60)
        click.echo(f"  Total trials:     {len(results)}")
        click.echo(f"  Accepted:         {sum(1 for r in results if r.accepted)}")
        click.echo(f"  Rejected:         {sum(1 for r in results if not r.accepted)}")
        click.echo(f"  Beat baseline:    {sum(1 for r in results if r.beat_baseline)}")

        # Show top 5 by Sharpe
        valid = [r for r in results if not r.error and r.sharpe > 0]
        valid.sort(key=lambda r: r.sharpe, reverse=True)

        if valid:
            click.echo(f"\n  Top 5 by Sharpe:")
            click.echo(f"  {'Trial':>5} {'Sharpe':>8} {'Win%':>7} {'DD%':>7} {'Alpha':>8} {'Accepted':>9}")
            for i, r in enumerate(valid[:5]):
                click.echo(
                    f"  {i+1:>5} {r.sharpe:>8.3f} {r.win_rate*100:>6.1f}% "
                    f"{r.max_drawdown_pct:>6.2f}% {r.entry_alpha_bps:>7.1f} "
                    f"{'YES' if r.accepted else 'NO':>9}"
                )

        stats = engine.get_stats()
        click.echo(f"\n  Engine stats: {stats}")
        click.echo(f"\n{'=' * 60}")

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--symbols",
    required=True,
    help="Comma-separated list of symbols",
)
@click.option(
    "--duration-days",
    default=7,
    type=int,
    help="Shadow duration in days (default 7)",
)
@click.pass_context
def shadow(ctx: click.Context, symbols: str, duration_days: int) -> None:
    """Start shadow mode for a new config (Phase 8).

    Runs a new config in parallel with live trading at 10% of live size.
    After the shadow period, checks if shadow Sharpe >= 80% of backtest Sharpe.

    Example:
      platform shadow --symbols BTC-PERP --duration-days 7
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    symbol_list = _resolve_symbols(symbols, config)

    click.echo(f"Starting shadow mode...")
    click.echo(f"  Symbols:       {symbol_list}")
    click.echo(f"  Duration:      {duration_days} days")
    click.echo(f"  Size:          10% of live cap")

    from hermes.backtest.optimizer import RenkoSimulationEngine

    async def run():
        engine = RenkoSimulationEngine(config)
        result = await engine.run_shadow_mode(
            config={"shadow": True},
            symbols=symbol_list,
            duration_days=duration_days,
        )
        click.echo(f"\n  Shadow run started: {result.run_id}")
        click.echo(f"  Check back in {duration_days} days with `platform pnl`")

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--trade-id",
    required=True,
    help="Trade ID to replay",
)
@click.pass_context
def counterfactual(ctx: click.Context, trade_id: str) -> None:
    """Run counterfactual analysis on a closed trade (Phase 8).

    Replays a closed trade under alternative entry/execution configs.
    "What if we'd waited for the brick close instead of market order?"

    Example:
      platform counterfactual --trade-id <uuid>
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    click.echo(f"Running counterfactual analysis for trade: {trade_id}")

    from hermes.backtest.optimizer import RenkoSimulationEngine

    async def run():
        engine = RenkoSimulationEngine(config)
        results = await engine.run_counterfactual(
            trade_id=trade_id,
            alternative_configs=[
                {"entry_strategy": "enter_now"},
                {"entry_strategy": "wait_for_brick_close"},
                {"entry_strategy": "wait_for_pullback"},
            ],
        )

        click.echo(f"\n  {'Strategy':<25} {'PnL':>10} {'Alpha':>8} {'Better?':>8}")
        click.echo(f"  {'-'*25} {'-'*10} {'-'*8} {'-'*8}")
        for r in results:
            strategy = r.params.get("entry_strategy", "?")
            click.echo(
                f"  {strategy:<25} ${r.net_pnl_usd:>9.2f} "
                f"{r.entry_alpha_bps:>7.1f} {'YES' if r.beat_baseline else 'NO':>8}"
            )

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--eod",
    is_flag=True,
    help="Run end-of-day analysis (postmortems + hypotheses)",
)
@click.option(
    "--list-hypotheses",
    is_flag=True,
    help="List all hypotheses",
)
@click.option(
    "--check-shadow-promotions",
    is_flag=True,
    help="Check for shadow hypotheses ready to promote to live (runs daily after EOD)",
)
@click.option(
    "--check-underperformance",
    is_flag=True,
    help="Check if any promoted configs are underperforming → auto-rollback (runs daily)",
)
@click.option(
    "--monthly-maintenance",
    is_flag=True,
    help="Run monthly maintenance (archive, vacuum, hypothesis review, DR test)",
)
@click.pass_context
def agent(
    ctx: click.Context,
    eod: bool,
    list_hypotheses: bool,
    check_shadow_promotions: bool,
    check_underperformance: bool,
    monthly_maintenance: bool,
) -> None:
    """Hermes Agent — self-learning loop, hypothesis tracking, decision journal.

    The Hermes agent evaluates existing positions through a decision tree
    (SL/TP/trail/flip/hold) and runs a self-learning loop that:
    1. Analyzes closed trades (postmortems)
    2. Generates improvement hypotheses
    3. Backtests hypotheses
    4. Shadow tests
    5. Promotes to live

    \b
    Scheduled tasks (agent owns its own cron — see docs/agent_onboarding.md):
      Daily 16:30 PT:  --eod
      Daily 16:35 PT:  --check-shadow-promotions
      Daily 16:40 PT:  --check-underperformance
      Monthly 1st:     --monthly-maintenance

    \b
    Examples:
      platform agent --eod                       Run EOD analysis
      platform agent --list-hypotheses           List all hypotheses
      platform agent --check-shadow-promotions   Check for ready shadow hypotheses
      platform agent --check-underperformance    Check for underperforming configs
      platform agent --monthly-maintenance       Run monthly maintenance
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    from hermes.agent.decision_tree import HermesDecisionTree
    from hermes.agent.learning import HypothesisTracker, SelfLearningLoop

    if list_hypotheses:
        tracker = HypothesisTracker(config)
        hypotheses = tracker.get_hypotheses()

        if not hypotheses:
            click.echo("  No hypotheses yet. Run `platform agent --eod` to generate some.")
            return

        click.echo(f"\n  Hypotheses ({len(hypotheses)} total):")
        click.echo(f"  {'ID':<10} {'Status':<15} {'Confidence':>10} {'Hypothesis'}")
        click.echo(f"  {'-'*10} {'-'*15} {'-'*10} {'-'*50}")
        for hyp in hypotheses:
            click.echo(
                f"  {hyp.hypothesis_id[:8]:<10} {hyp.status:<15} "
                f"{hyp.confidence:>10.2f} {hyp.hypothesis[:50]}"
            )
        return

    if eod:
        click.echo("Running EOD analysis...")

        async def run():
            loop = SelfLearningLoop(config)
            summary = await loop.run_eod_analysis()

            click.echo("")
            click.echo("=" * 60)
            click.echo("  EOD Analysis Complete")
            click.echo("=" * 60)
            click.echo(f"  Trades analyzed:     {summary.get('trades_analyzed', 0)}")
            click.echo(f"  Postmortems written: {summary.get('postmortems_written', 0)}")
            click.echo(f"  Hypotheses generated: {summary.get('hypotheses_generated', 0)}")

            if summary.get("regime_performance"):
                click.echo(f"\n  --- Regime Performance ---")
                click.echo(f"  {'Regime':<25} {'Trades':>7} {'Win%':>7} {'Total PnL':>12} {'Avg PnL':>10}")
                click.echo(f"  {'-'*25} {'-'*7} {'-'*7} {'-'*12} {'-'*10}")
                for regime, stats in summary["regime_performance"].items():
                    click.echo(
                        f"  {regime:<25} {stats['n_trades']:>7} "
                        f"{stats['win_rate']*100:>6.1f}% "
                        f"${stats['total_pnl']:>10,.2f} "
                        f"${stats['avg_pnl']:>8,.2f}"
                    )

            click.echo(f"\n{'=' * 60}")

        try:
            asyncio.run(run())
        except Exception as e:
            click.echo(f"  ERROR: {e}", err=True)
            sys.exit(1)
        return

    if check_shadow_promotions:
        from hermes.agent.agent_ops import check_shadow_promotions as _check
        click.echo("Checking for shadow hypotheses ready to promote...")
        result = _check(config)
        click.echo("")
        click.echo("=" * 60)
        click.echo("  Shadow Promotion Check Complete")
        click.echo("=" * 60)
        click.echo(f"  Checked:      {result['checked']} shadow hypotheses")
        click.echo(f"  Ready:        {result['ready']}")
        click.echo(f"  Promoted:     {result['promoted']}")
        click.echo(f"  Blocked:      {result['blocked']} (awaiting human approval)")
        if result["details"]:
            click.echo("")
            click.echo("  Details:")
            for d in result["details"]:
                action = d.get("action", "?")
                sym = ""
                if d.get("config_hash"):
                    sym = f" → hash {d['config_hash'][:12]}..."
                click.echo(f"    [{action}] {d['hypothesis_id'][:8]}: {d['hypothesis'][:60]}{sym}")
        click.echo("")
        return

    if check_underperformance:
        from hermes.agent.agent_ops import check_underperformance as _check
        click.echo("Checking for underperforming promoted configs...")
        result = _check(config)
        click.echo("")
        click.echo("=" * 60)
        click.echo("  Underperformance Check Complete")
        click.echo("=" * 60)
        click.echo(f"  Checked:         {result['checked']} hermes-promoted configs")
        click.echo(f"  Underperforming: {result['underperforming']}")
        click.echo(f"  Rolled back:     {result['rolled_back']}")
        if result["details"]:
            click.echo("")
            click.echo("  Details:")
            for d in result["details"]:
                action = d.get("action", "?")
                click.echo(
                    f"    [{action}] {d['config_hash'][:12]}... "
                    f"(live Sharpe {d.get('live_sharpe', '?'):.2f} vs backtest {d.get('backtest_sharpe', '?'):.2f}, "
                    f"{d.get('days_live', 0)} days live)"
                )
        click.echo("")
        return

    if monthly_maintenance:
        from hermes.agent.agent_ops import monthly_maintenance as _monthly
        click.echo("Running monthly maintenance...")
        result = _monthly(config)
        click.echo("")
        click.echo("=" * 60)
        click.echo("  Monthly Maintenance Complete")
        click.echo("=" * 60)
        click.echo(f"  Parquet files archived:  {result['archived_files']}")
        click.echo(f"  DuckDB VACUUM:           {'✓' if result['vacuumed'] else '✗'}")
        click.echo(f"  Hypothesis summary:      {result['hypothesis_summary']}")
        if result.get("stuck_hypotheses"):
            click.echo(f"  Stuck hypotheses (>14d in shadow): {len(result['stuck_hypotheses'])}")
            for h_id in result["stuck_hypotheses"]:
                click.echo(f"    {h_id[:12]}...")
        click.echo(f"  DR test:                 {result['dr_test']}")
        click.echo(f"  HMM retrain reminder:    {'✓ logged' if result['hmm_retrain_reminder'] else '✗'}")
        if result.get("nt_reminder_note"):
            click.echo(f"    {result['nt_reminder_note']}")
        click.echo("")
        click.echo("  Rotation reminders (every 90 days):")
        for r in result["rotation_reminders"]:
            click.echo(f"    • {r}")
        click.echo("")
        return

    # Default: show agent decision tree stats
    click.echo("Hermes Agent Decision Tree")
    click.echo("=" * 60)
    click.echo("")
    click.echo("  Position management thresholds:")
    click.echo("    Stop-loss:   pnl <= -1% → close")
    click.echo("    Take-profit: pnl >= +2.5% → close")
    click.echo("    Early profit: pnl >= +4.5% + same direction → close")
    click.echo("    Fading:      pnl > 0 + 2+ adverse bricks → trail stop")
    click.echo("    Flip:        opposite signal + conviction >= 0.7 → close + reverse")
    click.echo("    Hold:        same direction + no exit → hold")
    click.echo("    No signal:   hold with native stops")
    click.echo("")
    click.echo("  Commands:")
    click.echo("    platform agent --eod                       Run EOD analysis")
    click.echo("    platform agent --list-hypotheses           List hypotheses")
    click.echo("    platform agent --check-shadow-promotions   Check for ready shadow hypotheses")
    click.echo("    platform agent --check-underperformance    Check for underperforming configs")
    click.echo("    platform agent --monthly-maintenance       Run monthly maintenance")
    click.echo("")


@cli.command(name="meta-regime")
@click.option(
    "--retrain",
    is_flag=True,
    help="Retrain the meta-regime classifier (recalibrate thresholds from 30-day distribution)",
)
@click.pass_context
def meta_regime_cmd(ctx: click.Context, retrain: bool) -> None:
    """Meta-regime classifier management.

    Hermes's meta-regime classifier is RULE-BASED (the HMM lives upstream in
    Noble Trader). This command recalibrates the rule thresholds based on
    recent regime distribution.

    \b
    Scheduled task (agent owns its own cron):
      Monthly 1st:  platform meta-regime --retrain

    \b
    Example:
      platform meta-regime --retrain    Recalibrate thresholds from 30-day data
    """
    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    if retrain:
        from hermes.agent.agent_ops import retrain_meta_regime
        click.echo("Retraining meta-regime classifier...")
        result = retrain_meta_regime(config)
        click.echo("")
        click.echo("=" * 60)
        click.echo("  Meta-Regime Retrain Complete")
        click.echo("=" * 60)
        click.echo(f"  Samples (30d):           {result.get('samples', 0)}")
        click.echo(f"  Distribution:            {result.get('distribution', {})}")
        click.echo(f"  Config change proposed:  {result.get('config_change_proposed', False)}")
        if result.get("proposed_changes"):
            click.echo("  Proposed changes (tier 3 — human approval required):")
            for k, v in result["proposed_changes"].items():
                click.echo(f"    {k} = {v}")
        if result.get("next_step"):
            click.echo("")
            click.echo(f"  Next step: {result['next_step']}")
        if result.get("nt_reminder_note"):
            click.echo("")
            click.echo(f"  ⚠ Upstream: {result['nt_reminder_note']}")
        if result.get("error"):
            click.echo(f"  Error: {result['error']}")
        click.echo("")
        return

    # Default: show current meta-regime config
    mr_cfg = config.meta_regime if hasattr(config, "meta_regime") else {}
    if not isinstance(mr_cfg, dict):
        mr_cfg = {}
    click.echo("Meta-Regime Classifier Configuration")
    click.echo("=" * 60)
    click.echo(f"  HMM n_components:        {mr_cfg.get('hmm_n_components', 7)}")
    click.echo(f"  Retrain frequency (days): {mr_cfg.get('retrain_frequency_days', 30)}")
    click.echo(f"  Confidence floor:        {mr_cfg.get('confidence_floor', 0.55)}")
    click.echo(f"  Thresholds:              {mr_cfg.get('thresholds', {})}")
    click.echo("")
    click.echo("  Commands:")
    click.echo("    platform meta-regime --retrain    Recalibrate thresholds from 30-day data")
    click.echo("")


@cli.command()
@click.option(
    "--start",
    required=True,
    help="Start datetime (ISO format: 2026-07-01T14:00:00)",
)
@click.option(
    "--end",
    required=True,
    help="End datetime (ISO format: 2026-07-01T15:00:00)",
)
@click.option(
    "--symbols",
    default=None,
    help="Comma-separated symbols to filter (default: all)",
)
@click.option(
    "--n",
    default=50,
    type=int,
    help="Number of timeline events to display (default 50)",
)
@click.pass_context
def replay(ctx: click.Context, start: str, end: str, symbols: str | None, n: int) -> None:
    """Replay a historical session for forensic analysis (Phase 10).

    Loads all events from DuckDB in chronological order and reconstructs
    the full timeline of what happened during the specified period.

    Example:
      platform replay --start 2026-07-01T14:00:00 --end 2026-07-01T15:00:00 --symbols BTC-PERP
    """
    import asyncio
    from datetime import datetime

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    start_dt = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    symbol_list = symbols.split(",") if symbols else None

    click.echo(f"Replaying session: {start} → {end}")
    if symbol_list:
        click.echo(f"  Symbols: {symbol_list}")

    from hermes.ops.replay import ReplayEngine

    async def run():
        engine = ReplayEngine(config)
        result = await engine.replay(
            start=start_dt,
            end=end_dt,
            symbols=symbol_list,
        )

        click.echo("")
        click.echo("=" * 70)
        click.echo("  Replay Results")
        click.echo("=" * 70)
        click.echo(f"  Replay ID:     {result.replay_id}")
        click.echo(f"  Heartbeats:    {result.n_heartbeats}")
        click.echo(f"  Signals:       {result.n_signals}")
        click.echo(f"  Risk decisions:{result.n_signals}")
        click.echo(f"  Orders:        {result.n_orders}")
        click.echo(f"  Fills:         {result.n_fills}")
        click.echo(f"  Monitor events:{result.n_events}")
        click.echo(f"  Total timeline:{len(result.timeline)}")

        if result.errors:
            click.echo(f"  Errors:        {len(result.errors)}")
            for err in result.errors:
                click.echo(f"    - {err}")

        # Display timeline (last N events)
        if result.timeline:
            click.echo(f"\n  --- Timeline (last {n} events) ---")
            click.echo(f"  {'Timestamp':<22} {'Type':<18} {'Detail'}")
            click.echo(f"  {'-'*22} {'-'*18} {'-'*50}")

            for event in result.timeline[-n:]:
                ts_str = str(event["ts"])[:19]
                click.echo(f"  {ts_str:<22} {event['type']:<18} {event.get('detail', '')[:70]}")

        click.echo(f"\n{'=' * 70}")

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def alert_test(ctx: click.Context) -> None:
    """Send a test alert to configured notification channels (Phase 10).

    Tests Discord webhook and Telegram bot configuration.

    Example:
      platform alert-test
    """
    import asyncio

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    from hermes.ops.alerting import Alert, AlertManager, AlertSeverity

    async def run():
        manager = AlertManager(config)
        await manager.start()

        click.echo(f"  Discord:  {'enabled' if manager.is_discord_enabled() else 'disabled (set DISCORD_WEBHOOK_URL in .env)'}")
        click.echo(f"  Telegram: {'enabled' if manager.is_telegram_enabled() else 'disabled (set TELEGRAM_BOT_TOKEN in .env)'}")

        if not manager.is_discord_enabled() and not manager.is_telegram_enabled():
            click.echo("\n  No notification channels configured. Set up Discord webhook or Telegram bot in .env.")
            return

        click.echo("\n  Sending test alert...")
        await manager.send_alert(Alert(
            title="Hermes Test Alert",
            message="This is a test alert from the Hermes Trading Platform. If you see this, alerting is working correctly.",
            severity=AlertSeverity.INFO,
            source="alert-test",
            data={"version": __version__, "environment": config.environment},
        ))

        stats = manager.get_stats()
        click.echo(f"\n  Alerts sent:     {stats['alerts_sent']}")
        click.echo(f"  Discord sent:    {stats['discord_sent']}")
        click.echo(f"  Telegram sent:   {stats['telegram_sent']}")
        click.echo(f"  Errors:          {stats['errors']}")

        await manager.stop()

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--duration-sec",
    default=10,
    type=int,
    help="Duration of load test in seconds (default 10)",
)
@click.option(
    "--rate-per-sec",
    default=100,
    type=int,
    help="Heartbeats per second to simulate (default 100)",
)
@click.pass_context
def load_test(ctx: click.Context, duration_sec: int, rate_per_sec: int) -> None:
    """Run a load test on the DuckDB writer (Phase 10).

    Simulates high-frequency heartbeat ingestion to verify
    the system can handle the target load.

    Target: 10k signals/sec, 1k fills/sec, 100k heartbeats/day.

    Example:
      platform load-test --duration-sec 10 --rate-per-sec 500
    """
    import asyncio
    import time

    config_path = ctx.obj.get("config_path")
    config = load_config(config_path)
    setup_logging(
        level=config.log_level,
        format=config.logging.get("format", "text"),
        output=config.logging.get("output", "stdout"),
        file_path=config.logging.get("file_path"),
    )

    click.echo(f"Load test: {rate_per_sec} heartbeats/sec for {duration_sec}s")
    click.echo(f"  Total expected: {rate_per_sec * duration_sec} heartbeats")

    from hermes.transport.heartbeat_writer import HeartbeatWriter
    from hermes.schemas.heartbeat import parse_heartbeat
    from hermes.schemas.market import Venue
    import json

    VALID_HB = {
        "type": "heartbeat", "symbol": "BTC", "ts": 1735900800000,
        "regime": "low_vol_bull", "regime_conf": 0.85, "signal": "buy",
        "entry_price": 64441.0, "stop_loss": 63941.0, "take_profit": 65441.0,
        "aggression": "mid", "brick_size": 50.0, "sl_bricks": 3, "tp_bricks": 5,
        "kelly_f": 0.15, "effective_kelly": 0.12, "ev": 0.35, "ev_per_dollar": 0.12,
        "p_win": 0.62, "p_regime": 0.55, "p_imbalance": 0.48, "p_markov": 0.50,
        "ev_scale": 0.80, "markov_current_state": "UP", "regime_shift": "false",
        "prev_regime": None, "shift_at": 0, "shifts_24h": 2,
    }

    async def run():
        writer = HeartbeatWriter(config, batch_size=500, flush_interval_sec=0.5)
        await writer.start()

        hb = parse_heartbeat(json.dumps(VALID_HB))
        base_ts = datetime.now(timezone.utc)

        start_time = time.monotonic()
        sent = 0

        for i in range(rate_per_sec * duration_sec):
            row = hb.to_duckdb_row(
                ts_received=datetime.now(timezone.utc),
                dedup_hash=f"loadtest_{i}",
                raw_payload=json.dumps(VALID_HB),
            )
            await writer.enqueue(row)
            sent += 1

            # Pace to target rate
            elapsed = time.monotonic() - start_time
            expected = int(elapsed * rate_per_sec)
            if sent > expected + rate_per_sec:
                await asyncio.sleep(0.001)

        # Wait for flush
        await asyncio.sleep(2)
        await writer.stop()

        elapsed = time.monotonic() - start_time
        actual_rate = sent / elapsed if elapsed > 0 else 0
        stats = writer.get_stats()

        click.echo("")
        click.echo("=" * 60)
        click.echo("  Load Test Results")
        click.echo("=" * 60)
        click.echo(f"  Duration:       {elapsed:.1f}s")
        click.echo(f"  Sent:           {sent}")
        click.echo(f"  Written:        {stats['written']}")
        click.echo(f"  Errors:         {stats['errors']}")
        click.echo(f"  Actual rate:    {actual_rate:.0f} heartbeats/sec")
        click.echo(f"  Target rate:    {rate_per_sec} heartbeats/sec")
        click.echo(f"  Throughput:     {stats['written']/elapsed:.0f} writes/sec")
        click.echo("=" * 60)

    try:
        asyncio.run(run())
    except Exception as e:
        click.echo(f"  ERROR: {e}", err=True)
        sys.exit(1)


# === Helpers ===


def _print_config_summary(config: HermesConfig) -> None:
    """Print a human-readable summary of loaded config."""
    click.echo("")
    click.echo("Hermes Configuration Summary")
    click.echo("=" * 50)
    click.echo(f"  Environment:  {config.environment}")
    click.echo(f"  Log level:    {config.log_level}")
    click.echo(f"  Venues:")
    for name, venue in config.venues.items():
        status = "enabled" if venue.enabled else "disabled"
        click.echo(f"    - {name} ({status}): {venue.asset_classes}")
    click.echo(f"  Portfolio allocation:")
    for asset_class, pct in config.portfolio.target_allocation.items():
        click.echo(f"    - {asset_class}: {pct:.0%}")
    click.echo(f"  Initial symbols: {len(config.portfolio.initial_symbols)}")
    click.echo("=" * 50)
    click.echo("")


def _init_duckdb(config: HermesConfig) -> bool:
    """Open DuckDB, apply schema, write test row."""
    try:
        from hermes.db.migrate import apply_migrations, get_duckdb_path

        db_path = get_duckdb_path(config)
        click.echo(f"  Opening DuckDB at {db_path}...")
        apply_migrations(config)
        click.echo("  ✓ Schema applied")

        # Write the REAL config (not just a test row) to config_history.
        # This is the baseline audit entry — every future change is diffed against this.
        try:
            from hermes.db.config_history import write_config_to_history
            config_hash = write_config_to_history(
                config,
                source="init",
                rationale=f"platform init baseline (v{__version__})",
                author="init",
            )
            click.echo(f"  ✓ Config recorded in history (hash: {config_hash[:12]}...)")
        except Exception as ce:
            click.echo(f"  ⚠ Config history write skipped: {ce}")
            log.warning("config_history_init_failed", error=str(ce))

        # Seed symbols table from config/default.yaml.initial_symbols.
        # Idempotent — only inserts rows that don't yet exist.
        try:
            from hermes.db.symbol_registry import seed_from_config
            n_seeded = seed_from_config(config, added_by="init")
            if n_seeded:
                click.echo(f"  ✓ Seeded {n_seeded} symbol(s) from config into symbols registry")
            else:
                click.echo("  ✓ Symbols registry already seeded (no new inserts)")
        except Exception as se:
            click.echo(f"  ⚠ Symbols seed skipped: {se}")
            log.warning("symbols_seed_skipped", error=str(se))
        return True
    except Exception as e:
        click.echo(f"  ✗ DuckDB init failed: {e}")
        log.error("duckdb_init_failed", error=str(e))
        return False


def _ping_redis(config: HermesConfig) -> bool:
    """Ping Hermes Redis (non-fatal if unreachable or not yet configured)."""
    try:
        import redis as redis_lib

        redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
        # Detect unresolved placeholders
        if redis_url.startswith("secret:") or "<" in redis_url:
            click.echo(f"  ⊘ Redis not configured (placeholder in .env) — skipping ping")
            return False
        # Don't echo the full URL (may contain password)
        safe_url = redis_url.split("@")[-1] if "@" in redis_url else redis_url
        click.echo(f"  Pinging Redis at {safe_url}...")
        client = redis_lib.from_url(redis_url, socket_connect_timeout=2)
        pong = client.ping()
        if pong:
            click.echo("  ✓ Redis responded PONG")
            return True
        else:
            click.echo("  ✗ Redis did not respond PONG")
            return False
    except Exception as e:
        click.echo(f"  ✗ Redis unreachable: {e}")
        return False


def _check_config(config: HermesConfig) -> dict:
    return {"ok": True, "message": f"environment={config.environment}"}


def _check_duckdb(config: HermesConfig) -> dict:
    try:
        from hermes.db.migrate import get_duckdb_path

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return {"ok": False, "message": f"DB file missing at {db_path}"}
        import duckdb

        with duckdb.connect(str(db_path)) as conn:
            tables = conn.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_schema='main'"
            ).fetchone()[0]
        return {"ok": True, "message": f"{tables} tables, db at {db_path}"}
    except Exception as e:
        return {"ok": False, "message": str(e)}


def _check_redis(config: HermesConfig) -> dict:
    try:
        import redis as redis_lib

        redis_url = config.hermes_redis.get("url", "redis://localhost:6379/1")
        client = redis_lib.from_url(redis_url, socket_connect_timeout=2)
        if client.ping():
            return {"ok": True, "message": "PONG received"}
        return {"ok": False, "message": "no PONG"}
    except Exception as e:
        return {"ok": False, "message": str(e)[:80]}


def _check_secrets(config: HermesConfig) -> dict:
    """Verify that key secrets are resolvable (not still placeholders)."""
    from hermes.core.secrets import get_secret_or_none

    placeholders = []

    # Check 1: Raw secret resolution (can the backend find the key?)
    for key in ["hermes.duckdb_path", "hermes.redis_url"]:
        value = get_secret_or_none(key)
        if value is None:
            placeholders.append(f"{key} (not found in .env or environment)")
        elif "<" in str(value):
            placeholders.append(f"{key} (still has placeholder: {value})")

    # Check 2: Resolved config values (did the YAML secret: prefix resolve?)
    duckdb_path = config.duckdb.get("path", "")
    if duckdb_path.startswith("secret:") or "<" in str(duckdb_path):
        placeholders.append("config.duckdb.path (unresolved)")

    redis_url = config.hermes_redis.get("url", "")
    if redis_url.startswith("secret:") or "<" in str(redis_url):
        placeholders.append("config.hermes_redis.url (unresolved)")

    if placeholders:
        return {
            "ok": False,
            "message": f"placeholders remain: {', '.join(placeholders)}",
        }
    return {"ok": True, "message": "all required secrets resolved"}


if __name__ == "__main__":
    cli()
