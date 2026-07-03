-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v5 (Phase 5 additions)
-- Adds: orders, order_events, fills tables
-- ============================================================

CREATE TABLE IF NOT EXISTS orders (
    order_id             VARCHAR PRIMARY KEY,
    trade_id             VARCHAR NOT NULL,
    signal_id            VARCHAR,
    risk_decision_id     VARCHAR,
    ts_created           TIMESTAMPTZ NOT NULL,

    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    side                 VARCHAR NOT NULL,
    order_type           VARCHAR NOT NULL,
    time_in_force        VARCHAR NOT NULL,

    qty_requested        DOUBLE NOT NULL,
    price_limit          DOUBLE,
    leverage             DOUBLE DEFAULT 1.0,

    qty_filled           DOUBLE NOT NULL DEFAULT 0,
    avg_fill_price       DOUBLE,
    status               VARCHAR NOT NULL,

    algo                 VARCHAR,
    venue_order_id       VARCHAR,

    total_fees           DOUBLE NOT NULL DEFAULT 0,
    total_slippage       DOUBLE NOT NULL DEFAULT 0,
    maker_rebate         DOUBLE NOT NULL DEFAULT 0,

    config_hash          VARCHAR NOT NULL,
    position_id          VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_orders_trade   ON orders (trade_id);
CREATE INDEX IF NOT EXISTS idx_orders_signal  ON orders (signal_id);
CREATE INDEX IF NOT EXISTS idx_orders_status  ON orders (status, ts_created DESC);
CREATE INDEX IF NOT EXISTS idx_orders_symbol  ON orders (symbol, ts_created DESC);
CREATE INDEX IF NOT EXISTS idx_orders_venue   ON orders (venue, ts_created DESC);

CREATE TABLE IF NOT EXISTS order_events (
    event_id             VARCHAR PRIMARY KEY,
    order_id             VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL,
    event_type           VARCHAR NOT NULL,
    payload              JSON NOT NULL,
    seq_num              BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_order ON order_events (order_id, seq_num);
CREATE INDEX IF NOT EXISTS idx_events_ts    ON order_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_events_type  ON order_events (event_type, ts DESC);

CREATE TABLE IF NOT EXISTS fills (
    fill_id              VARCHAR PRIMARY KEY,
    order_id             VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    side                 VARCHAR NOT NULL,
    qty                  DOUBLE NOT NULL,
    price                DOUBLE NOT NULL,
    fee                  DOUBLE NOT NULL,
    fee_currency         VARCHAR NOT NULL,
    is_maker             BOOLEAN NOT NULL,
    liquidity            VARCHAR NOT NULL,
    arrival_price        DOUBLE NOT NULL,
    slippage_bps         DOUBLE NOT NULL,
    venue_fill_id        VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_fills_ts       ON fills (ts DESC);
CREATE INDEX IF NOT EXISTS idx_fills_symbol   ON fills (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_fills_order    ON fills (order_id);
CREATE INDEX IF NOT EXISTS idx_fills_venue    ON fills (venue, ts DESC);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (5, 'Phase 5: orders, order_events, fills tables for L3 execution layer');
