-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v7 (Phase 7 additions)
-- Adds: backtest_runs table
-- ============================================================

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id               VARCHAR PRIMARY KEY,
    ts_started           TIMESTAMPTZ NOT NULL,
    ts_finished          TIMESTAMPTZ,
    duration_sec         INTEGER,

    mode                 VARCHAR NOT NULL,
    start_ts             TIMESTAMPTZ NOT NULL,
    end_ts               TIMESTAMPTZ NOT NULL,
    symbols              TEXT[] NOT NULL,
    initial_equity       DOUBLE NOT NULL,

    n_heartbeats         INTEGER DEFAULT 0,
    n_signals_produced   INTEGER DEFAULT 0,
    n_signals_approved   INTEGER DEFAULT 0,
    n_signals_rejected   INTEGER DEFAULT 0,
    n_orders             INTEGER DEFAULT 0,
    n_fills              INTEGER DEFAULT 0,

    final_equity         DOUBLE DEFAULT 0,
    total_return_pct     DOUBLE DEFAULT 0,
    total_net_pnl        DOUBLE DEFAULT 0,
    max_drawdown_pct     DOUBLE DEFAULT 0,

    tear_sheet           JSON,
    config_hash          VARCHAR DEFAULT '',
    error                TEXT
);

CREATE INDEX IF NOT EXISTS idx_backtest_ts      ON backtest_runs (ts_started DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_mode    ON backtest_runs (mode, ts_started DESC);
-- Note: Cannot index on TEXT[] (symbols) in DuckDB — use a computed column or full scan

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (7, 'Phase 7: backtest_runs table for backtest results');
