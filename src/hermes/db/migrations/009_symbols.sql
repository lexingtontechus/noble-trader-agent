-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v9 (Symbol Registry)
-- Adds: symbols dimension table for runtime-mutable symbol management.
--
-- This table is the source of truth for the active trading universe.
-- config/default.yaml.portfolio.initial_symbols is used only as a
-- seed on first `platform init` (see SymbolRegistry.seed_from_config).
--
-- Design notes:
--   * `is_active` controls whether a symbol participates in stream /
--     monitor / synthesize runs. Deactivating preserves historical
--     rows in signal_heartbeats, orders, fills, etc. (no FK cascade).
--   * `added_by` / `deactivated_by` audit columns track who changed
--     what. Values: 'init' | 'cli:<user>' | 'dashboard' | 'seed:<hash>'.
--   * `venue` and `asset_class` mirror the config default.yaml keys
--     so the same validation logic applies (venue must exist in
--     venues registry; venue.asset_classes must include asset_class).
-- ============================================================

CREATE TABLE IF NOT EXISTS symbols (
    symbol              VARCHAR PRIMARY KEY,
    venue               VARCHAR NOT NULL,                  -- alpaca | hyperliquid | oanda | ...
    asset_class         VARCHAR NOT NULL,                  -- crypto | equities | commodities | forex

    -- Optional reference data (populated lazily by `platform symbols validate`)
    base_ccy            VARCHAR,                           -- e.g. BTC, SOL, AAPL
    quote_ccy           VARCHAR DEFAULT 'USD',
    tick_size           DOUBLE,
    min_notional        DOUBLE,
    max_leverage        DOUBLE,

    -- Lifecycle
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    added_by            VARCHAR NOT NULL DEFAULT 'init',
    deactivated_at      TIMESTAMPTZ,
    deactivated_by      VARCHAR,
    rationale           TEXT,                              -- free-form note from operator

    -- Validation status (set by `platform symbols validate`)
    last_validated_at   TIMESTAMPTZ,
    last_price          DOUBLE,
    validation_status   VARCHAR DEFAULT 'pending',         -- pending | ok | failed | unknown
    validation_error    VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_symbols_active   ON symbols (is_active, venue);
CREATE INDEX IF NOT EXISTS idx_symbols_venue    ON symbols (venue, asset_class);
CREATE INDEX IF NOT EXISTS idx_symbols_class    ON symbols (asset_class, is_active);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (9, 'Symbol registry — runtime-mutable symbols table with is_active flag');
