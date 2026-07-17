-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v10 (Symbol Registry: exchange dimension)
-- Adds the exchange dimension so the SAME instrument on DIFFERENT
-- exchanges (e.g. COINBASE:BTCUSD vs BINANCE:BTCUSD) is a distinct,
-- correctly-classified registry row, and asset_class (crypto vs forex)
-- drives PnL math downstream.
--
-- Design:
--   * `exchange` is a nullable VARCHAR lookup column. When an instrument
--     is exchange-qualified, `symbol` stores the QUALIFIED form
--     ("BINANCE:BTCUSD"); bare symbols ("BTCUSD") stay bare for
--     legacy / non-exchange sources (Noble Trader pushes). This keeps
--     `symbol` as the join key for signal_heartbeats / pnl_* / fills
--     with NO migration of those tables.
--   * `asset_class` is now derived by a real classifier (not "6-alpha
--     == forex"), so BTCUSD/XAUUSD are crypto/commodity, EURUSD forex.
--   * UNIQUE(symbol, exchange, venue) prevents duplicate rows.
-- ============================================================

ALTER TABLE symbols ADD COLUMN IF NOT EXISTS exchange VARCHAR;  -- COINBASE | BINANCE | PLEXYTRADE | NULL
ALTER TABLE symbols ADD COLUMN IF NOT EXISTS symbol_bare VARCHAR;  -- instrument w/o exchange, e.g. BTCUSD

-- Split any pre-existing bare symbol into bare + (exchange NULL) for back-compat.
UPDATE symbols SET symbol_bare = symbol WHERE symbol_bare IS NULL AND exchange IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_symbols_uniq ON symbols (symbol, exchange, venue);
CREATE INDEX IF NOT EXISTS idx_symbols_exchange ON symbols (exchange, is_active);
CREATE INDEX IF NOT EXISTS idx_symbols_bare ON symbols (symbol_bare, is_active);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (10, 'Symbol registry — exchange dimension + asset_class classifier');
