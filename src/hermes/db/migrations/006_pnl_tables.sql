-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v6 (Phase 6 additions)
-- Adds: pnl_realized, pnl_unrealized tables
-- ============================================================

CREATE TABLE IF NOT EXISTS pnl_realized (
    pnl_id               VARCHAR PRIMARY KEY,
    trade_id             VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    strategy_id          VARCHAR DEFAULT '',
    regime_at_close      VARCHAR,

    gross_pnl            DOUBLE NOT NULL,
    fees_total           DOUBLE NOT NULL,
    funding_pnl          DOUBLE NOT NULL,
    slippage_cost        DOUBLE NOT NULL,
    net_pnl              DOUBLE NOT NULL,
    net_pnl_bps          DOUBLE NOT NULL,

    risk_amount          DOUBLE NOT NULL,
    r_multiple           DOUBLE NOT NULL,

    hold_duration_sec    INTEGER NOT NULL,
    n_fills              INTEGER NOT NULL,

    direction_pnl        DOUBLE,
    timing_pnl           DOUBLE,
    sizing_pnl           DOUBLE,
    regime_pnl           DOUBLE,

    config_hash          VARCHAR DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_pnl_realized_ts     ON pnl_realized (ts DESC);
CREATE INDEX IF NOT EXISTS idx_pnl_realized_symbol ON pnl_realized (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_pnl_realized_trade  ON pnl_realized (trade_id);
CREATE INDEX IF NOT EXISTS idx_pnl_realized_regime ON pnl_realized (regime_at_close, ts DESC);

CREATE TABLE IF NOT EXISTS pnl_unrealized (
    snapshot_id          VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    strategy_id          VARCHAR DEFAULT '',
    position_id          VARCHAR,

    position_qty         DOUBLE NOT NULL,
    avg_entry_price      DOUBLE NOT NULL,
    mark_price           DOUBLE NOT NULL,
    unrealized_gross     DOUBLE NOT NULL,
    unrealized_funding   DOUBLE NOT NULL,
    unrealized_fees_est  DOUBLE NOT NULL,
    unrealized_net       DOUBLE NOT NULL,
    position_notional    DOUBLE NOT NULL,
    position_risk        DOUBLE NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pnl_unrealized_ts   ON pnl_unrealized (ts DESC);
CREATE INDEX IF NOT EXISTS idx_pnl_unrealized_sym  ON pnl_unrealized (symbol, ts DESC);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (6, 'Phase 6: pnl_realized + pnl_unrealized tables for PnL analytics');
