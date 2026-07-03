-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v3 (Phase 2 additions)
-- Adds: price_monitor_events table
-- ============================================================

CREATE TABLE IF NOT EXISTS price_monitor_events (
    event_id             VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    event_type           VARCHAR NOT NULL,
    severity             VARCHAR NOT NULL,
    last_price           DOUBLE NOT NULL,
    spread_bps           DOUBLE,
    book_imbalance       DOUBLE,
    realized_vol_1m      DOUBLE,
    realized_vol_1h      DOUBLE,
    atr_14               DOUBLE,
    payload              JSON NOT NULL,
    position_id          VARCHAR,
    related_symbols      TEXT[],
    meta_regime_at_event VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_pme_ts        ON price_monitor_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_pme_symbol    ON price_monitor_events (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pme_type      ON price_monitor_events (event_type, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pme_severity  ON price_monitor_events (severity, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pme_position  ON price_monitor_events (position_id);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (3, 'Phase 2: price_monitor_events table for L2.8 Active Price Monitor');
