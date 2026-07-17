"""
Symbol Registry — runtime-mutable source of truth for the trading universe.

The `symbols` DuckDB table holds the active trading universe. This module
wraps DuckDB CRUD operations and provides:

  * list_active_symbols()  — what `stream` / `monitor` / `synthesize`
                             default to when --symbols is omitted
  * add_symbol()           — register a new symbol (idempotent upsert)
  * deactivate_symbol()    — soft-delete (preserves historical rows)
  * activate_symbol()      — re-enable a previously deactivated symbol
  * validate_symbol()      — live-test that the venue can fetch a price
  * seed_from_config()     — first-run bootstrap from default.yaml

Schema lives in db/migrations/009_symbols.sql.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


# ────────────────────────────────────────────────────────────────────
# Pydantic-like dataclass (kept as a plain class to avoid pulling
# Pydantic into the DB layer — keeps the dependency graph clean).
# ────────────────────────────────────────────────────────────────────


class Symbol:
    """One row in the `symbols` table."""

    __slots__ = (
        "symbol", "venue", "asset_class",
        "base_ccy", "quote_ccy", "tick_size", "min_notional", "max_leverage",
        "is_active", "added_at", "added_by",
        "deactivated_at", "deactivated_by", "rationale",
        "last_validated_at", "last_price",
        "validation_status", "validation_error",
        "exchange", "symbol_bare",
    )

    def __init__(self, **kwargs: Any) -> None:
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k))

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}

    def __repr__(self) -> str:
        return (
            f"Symbol(symbol={self.symbol!r}, venue={self.venue!r}, "
            f"asset_class={self.asset_class!r}, is_active={self.is_active})"
        )


# ────────────────────────────────────────────────────────────────────
# Connection helper
# ────────────────────────────────────────────────────────────────────


def _connect(config: HermesConfig):
    """Open a DuckDB connection to the Hermes database."""
    import duckdb
    db_path = get_duckdb_path(config)
    return duckdb.connect(str(db_path))


def _row_to_symbol(row: tuple) -> Symbol:
    """Map a SELECT row to a Symbol instance."""
    return Symbol(
        symbol=row[0],
        venue=row[1],
        asset_class=row[2],
        base_ccy=row[3],
        quote_ccy=row[4],
        tick_size=row[5],
        min_notional=row[6],
        max_leverage=row[7],
        is_active=row[8],
        added_at=row[9],
        added_by=row[10],
        deactivated_at=row[11],
        deactivated_by=row[12],
        rationale=row[13],
        last_validated_at=row[14],
        last_price=row[15],
        validation_status=row[16],
        validation_error=row[17],
        exchange=row[18] if len(row) > 18 else None,
        symbol_bare=row[19] if len(row) > 19 else None,
    )


_SELECT_COLS = """
    symbol, venue, asset_class,
    base_ccy, quote_ccy, tick_size, min_notional, max_leverage,
    is_active, added_at, added_by,
    deactivated_at, deactivated_by, rationale,
    last_validated_at, last_price,
    validation_status, validation_error,
    exchange, symbol_bare
"""


# ────────────────────────────────────────────────────────────────────
# Read operations
# ────────────────────────────────────────────────────────────────────


def list_symbols(
    config: HermesConfig,
    active_only: bool = False,
    venue: str | None = None,
    asset_class: str | None = None,
) -> list[Symbol]:
    """List symbols, optionally filtered by active state / venue / asset class."""
    query = f"SELECT {_SELECT_COLS} FROM symbols"
    where: list[str] = []
    params: list[Any] = []

    if active_only:
        where.append("is_active = TRUE")
    if venue:
        where.append("venue = ?")
        params.append(venue)
    if asset_class:
        where.append("asset_class = ?")
        params.append(asset_class)

    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY is_active DESC, symbol ASC"

    with _connect(config) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_symbol(r) for r in rows]


def list_active_symbols(config: HermesConfig) -> list[str]:
    """Return just the symbol strings for all active rows — used as the default
    for `stream` / `monitor` / `synthesize` when --symbols is omitted."""
    with _connect(config) as conn:
        rows = conn.execute(
            "SELECT symbol FROM symbols WHERE is_active = TRUE ORDER BY symbol"
        ).fetchall()
    return [r[0] for r in rows]


def get_symbol(config: HermesConfig, symbol: str) -> Symbol | None:
    """Fetch a single symbol row by primary key. Returns None if not found."""
    with _connect(config) as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLS} FROM symbols WHERE symbol = ?",
            [symbol],
        ).fetchone()
    return _row_to_symbol(row) if row else None


# ────────────────────────────────────────────────────────────────────
# Write operations
# ────────────────────────────────────────────────────────────────────


def add_symbol(
    config: HermesConfig,
    symbol: str,
    venue: str,
    asset_class: str,
    *,
    base_ccy: str | None = None,
    quote_ccy: str = "USD",
    tick_size: float | None = None,
    min_notional: float | None = None,
    max_leverage: float | None = None,
    added_by: str = "cli",
    rationale: str | None = None,
    activate: bool = True,
) -> Symbol:
    """Register a new symbol or update an existing one.

    Idempotent: if the symbol already exists, only the mutable fields
    (tick_size, min_notional, max_leverage, rationale, is_active) are
    updated. The venue/asset_class are NOT changed once set — to move a
    symbol between venues, deactivate + add a new row.

    Raises:
        ValueError: if venue is not in the venues registry, or if the
                    venue's asset_classes list does not include asset_class.
    """
    _validate_venue_and_class(config, venue, asset_class)

    # Parse the exchange dimension (COINBASE:BTCUSD -> exchange=COINBASE, bare=BTCUSD).
    from hermes.db.symbol_key import parse_symbol_key, classify_asset_class

    key = parse_symbol_key(symbol)
    exchange = key.exchange
    symbol_bare = key.bare
    symbol_cell = key.qualified  # store qualified form when exchange known

    # Derive base_ccy from bare symbol if not provided (BTC/USD -> BTC, AAPL -> AAPL)
    if base_ccy is None:
        base_ccy = symbol_bare.split("/")[0] if "/" in symbol_bare else symbol_bare

    # Re-classify asset_class from the (bare) symbol unless the caller forced one.
    # The caller's asset_class still must be allowed by the venue; if our
    # classifier disagrees we prefer the classifier for PnL correctness but
    # fall back to the caller value when the venue rejects it.
    classified = classify_asset_class(symbol_bare)
    try:
        _validate_venue_and_class(config, venue, classified)
        asset_class = classified
    except ValueError:
        pass  # keep caller-supplied asset_class; let outer validation raise if bad

    now = datetime.now(timezone.utc)

    with _connect(config) as conn:
        # Try INSERT, on conflict UPDATE the mutable fields.
        existing = conn.execute(
            "SELECT 1 FROM symbols WHERE symbol = ?", [symbol_cell]
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE symbols
                SET base_ccy = COALESCE(?, base_ccy),
                    quote_ccy = ?,
                    tick_size = ?,
                    min_notional = ?,
                    max_leverage = ?,
                    rationale = COALESCE(?, rationale),
                    is_active = ?,
                    deactivated_at = CASE WHEN ? THEN NULL ELSE deactivated_at END,
                    deactivated_by = CASE WHEN ? THEN NULL ELSE deactivated_by END,
                    exchange = COALESCE(?, exchange),
                    symbol_bare = COALESCE(?, symbol_bare)
                WHERE symbol = ?
                """,
                [
                    base_ccy, quote_ccy,
                    tick_size, min_notional, max_leverage,
                    rationale,
                    activate,
                    activate, activate,
                    exchange, symbol_bare,
                    symbol_cell,
                ],
            )
            log.info("symbol_updated", symbol=symbol_cell, venue=venue, active=activate)
        else:
            conn.execute(
                """
                INSERT INTO symbols (
                    symbol, venue, asset_class,
                    base_ccy, quote_ccy, tick_size, min_notional, max_leverage,
                    is_active, added_at, added_by, rationale,
                    validation_status, exchange, symbol_bare
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                [
                    symbol_cell, venue, asset_class,
                    base_ccy, quote_ccy, tick_size, min_notional, max_leverage,
                    activate, now, added_by, rationale,
                    exchange, symbol_bare,
                ],
            )
            log.info(
                "symbol_added",
                symbol=symbol_cell, venue=venue, asset_class=asset_class,
                exchange=exchange, active=activate, added_by=added_by,
            )

    result = get_symbol(config, symbol_cell)
    assert result is not None, "symbol disappeared after upsert"
    return result


def touch_symbol_seen(config: HermesConfig, symbol: str) -> None:
    """Stamp `last_validated_at` without changing other fields.

    Used by the L0 subscriber to mark a symbol as "seen on the stream" so the
    auto-delist sweep (_delist_stale) can retire symbols that stop arriving —
    keeps the active universe dynamic without manual config.
    """
    now = datetime.now(timezone.utc)
    with _connect(config) as conn:
        conn.execute(
            "UPDATE symbols SET last_validated_at = ? WHERE symbol = ?",
            [now, symbol],
        )


def deactivate_symbol(
    config: HermesConfig,
    symbol: str,
    *,
    deactivated_by: str = "cli",
    rationale: str | None = None,
) -> Symbol:
    """Soft-delete: set is_active = FALSE. Preserves historical rows."""
    now = datetime.now(timezone.utc)
    with _connect(config) as conn:
        result = conn.execute(
            """
            UPDATE symbols
            SET is_active = FALSE,
                deactivated_at = ?,
                deactivated_by = ?,
                rationale = COALESCE(?, rationale)
            WHERE symbol = ?
            """,
            [now, deactivated_by, rationale, symbol],
        )
        if result.rowcount == 0:
            raise ValueError(f"Symbol not found: {symbol}")
    log.info("symbol_deactivated", symbol=symbol, by=deactivated_by)
    return get_symbol(config, symbol)  # type: ignore[return-value]


def activate_symbol(
    config: HermesConfig,
    symbol: str,
    *,
    activated_by: str = "cli",
) -> Symbol:
    """Re-enable a previously deactivated symbol."""
    with _connect(config) as conn:
        result = conn.execute(
            """
            UPDATE symbols
            SET is_active = TRUE,
                deactivated_at = NULL,
                deactivated_by = NULL
            WHERE symbol = ?
            """,
            [symbol],
        )
        if result.rowcount == 0:
            raise ValueError(f"Symbol not found: {symbol}")
    log.info("symbol_activated", symbol=symbol, by=activated_by)
    return get_symbol(config, symbol)  # type: ignore[return-value]


# ────────────────────────────────────────────────────────────────────
# Validation — probe the venue for a live price
# ────────────────────────────────────────────────────────────────────


def validate_symbol(
    config: HermesConfig,
    symbol: str,
) -> Symbol:
    """Live-test that the venue can fetch a price for this symbol.

    Updates last_validated_at, last_price, validation_status, and
    validation_error in the symbols table.
    """
    import asyncio

    row = get_symbol(config, symbol)
    if row is None:
        raise ValueError(f"Symbol not found: {symbol}")

    venue_name = row.venue
    venue_config = config.venues.get(venue_name)
    if venue_config is None or not venue_config.enabled:
        _record_validation(config, symbol, None, "failed",
                           f"venue '{venue_name}' not enabled")
        return get_symbol(config, symbol)  # type: ignore[return-value]

    # Lazy-import the adapter to avoid pulling async deps at module load.
    try:
        if venue_name == "alpaca":
            from hermes.transport.adapters.alpaca_adapter import AlpacaAdapter
            adapter = AlpacaAdapter(config)
        elif venue_name == "hyperliquid":
            from hermes.transport.adapters.hyperliquid_adapter import HyperliquidAdapter
            adapter = HyperliquidAdapter(config)
        elif venue_name == "tradingview":
            from hermes.transport.adapters.tradingview_adapter import TradingViewApiAdapter
            adapter = TradingViewApiAdapter(config)
        else:
            _record_validation(config, symbol, None, "failed",
                               f"no adapter for venue '{venue_name}'")
            return get_symbol(config, symbol)  # type: ignore[return-value]

        async def _probe() -> float | None:
            await adapter.connect()
            try:
                return await adapter.get_current_price(row.symbol)
            finally:
                await adapter.disconnect()

        price = asyncio.run(_probe())

        if price is not None and price > 0:
            _record_validation(config, symbol, price, "ok", None)
            log.info("symbol_validated", symbol=symbol, price=price)
        else:
            _record_validation(config, symbol, None, "failed",
                               "venue returned no price")
            log.warning("symbol_validation_no_price", symbol=symbol)

    except Exception as e:
        _record_validation(config, symbol, None, "failed", str(e)[:200])
        log.error("symbol_validation_error", symbol=symbol, error=str(e))

    return get_symbol(config, symbol)  # type: ignore[return-value]


def _record_validation(
    config: HermesConfig,
    symbol: str,
    price: float | None,
    status: str,
    error: str | None,
) -> None:
    now = datetime.now(timezone.utc)
    with _connect(config) as conn:
        conn.execute(
            """
            UPDATE symbols
            SET last_validated_at = ?,
                last_price = ?,
                validation_status = ?,
                validation_error = ?
            WHERE symbol = ?
            """,
            [now, price, status, error, symbol],
        )


# ────────────────────────────────────────────────────────────────────
# Seed — first-run bootstrap from default.yaml
# ────────────────────────────────────────────────────────────────────


def seed_from_config(
    config: HermesConfig,
    *,
    added_by: str = "init",
    overwrite_active: bool = False,
) -> int:
    """Populate the symbols table from config.portfolio.initial_symbols.

    Idempotent: existing rows are left alone unless `overwrite_active` is
    True (in which case their is_active flag is reset to match the config's
    presence in initial_symbols — symbols present in config are activated,
    symbols absent from config are left as-is).

    Returns the number of rows inserted (does not count updates).
    """
    initial = config.portfolio.initial_symbols
    if not initial:
        log.warning("seed_no_initial_symbols_in_config")
        return 0

    inserted = 0
    for entry in initial:
        sym = entry.get("symbol")
        venue = entry.get("venue")
        asset_class = entry.get("asset_class")
        if not (sym and venue and asset_class):
            log.warning("seed_skip_malformed_entry", entry=entry)
            continue

        existing = get_symbol(config, sym)
        if existing is None:
            add_symbol(
                config, sym, venue, asset_class,
                added_by=added_by,
                rationale="seeded from config/default.yaml",
            )
            inserted += 1
        elif overwrite_active and not existing.is_active:
            activate_symbol(config, sym, activated_by=added_by)

    log.info("seed_complete", inserted=inserted, total_in_config=len(initial))
    return inserted


# ────────────────────────────────────────────────────────────────────
# Internal validators
# ────────────────────────────────────────────────────────────────────


def _validate_venue_and_class(
    config: HermesConfig, venue: str, asset_class: str,
) -> None:
    """Raise ValueError if the venue doesn't exist or doesn't support this asset class."""
    venue_config = config.venues.get(venue)
    if venue_config is None:
        raise ValueError(
            f"Unknown venue '{venue}'. "
            f"Known venues: {sorted(config.venues.keys())}"
        )
    if not venue_config.enabled:
        raise ValueError(f"Venue '{venue}' is disabled in config/default.yaml")
    if asset_class not in venue_config.asset_classes:
        raise ValueError(
            f"Venue '{venue}' does not support asset_class '{asset_class}'. "
            f"Supported: {venue_config.asset_classes}"
        )
