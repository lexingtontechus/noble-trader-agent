-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v2 (Phase 1 additions)
-- Adds: Noble Trader Supabase mirror tables
-- ============================================================

-- === Mirror of nt_sweep_result (§6.2.10) ===
CREATE TABLE IF NOT EXISTS nt_sweep_results_local (
    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_table         VARCHAR NOT NULL DEFAULT 'nt_sweep_result',

    nt_id                INTEGER NOT NULL,
    symbol               VARCHAR NOT NULL,
    asset_class          VARCHAR NOT NULL,
    brick_size           DOUBLE NOT NULL,
    sl_bricks            INTEGER NOT NULL,
    tp_bricks            INTEGER NOT NULL,
    sharpe               DOUBLE,
    total_return         DOUBLE,
    annual_return        DOUBLE,
    max_drawdown_pct     DOUBLE,
    win_rate             DOUBLE,
    n_trades             INTEGER,
    profit_factor        DOUBLE,
    regime               VARCHAR,
    regime_conf          DOUBLE,
    kelly_f              DOUBLE,
    markov_p_up          DOUBLE,
    markov_p_dn          DOUBLE,
    sweep_window         VARCHAR,
    sweep_duration_ms    INTEGER,
    n_combos_tested      INTEGER,
    error                TEXT,
    sweep_timestamp      TIMESTAMPTZ NOT NULL,
    source               VARCHAR,

    dq_anomalies         TEXT[],
    dq_trusted           BOOLEAN NOT NULL DEFAULT TRUE,

    PRIMARY KEY (nt_id)
);

CREATE INDEX IF NOT EXISTS idx_ntsrl_symbol   ON nt_sweep_results_local (symbol, sweep_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ntsrl_regime   ON nt_sweep_results_local (regime, sweep_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ntsrl_trusted  ON nt_sweep_results_local (dq_trusted, sweep_timestamp DESC);

-- === Mirror of nt_regime_log (§6.2.10) ===
CREATE TABLE IF NOT EXISTS nt_regime_log_local (
    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_table         VARCHAR NOT NULL DEFAULT 'nt_regime_log',

    nt_id                INTEGER NOT NULL,
    symbol               VARCHAR NOT NULL,
    asset_class          VARCHAR NOT NULL,
    brick_size           DOUBLE NOT NULL,
    sl_bricks            INTEGER NOT NULL,
    tp_bricks            INTEGER NOT NULL,
    sharpe               DOUBLE,
    total_return         DOUBLE,
    annual_return        DOUBLE,
    max_drawdown_pct     DOUBLE,
    win_rate             DOUBLE,
    n_trades             INTEGER,
    profit_factor        DOUBLE,
    regime               VARCHAR,
    regime_conf          DOUBLE,
    kelly_f              DOUBLE,
    markov_p_up          DOUBLE,
    markov_p_dn          DOUBLE,
    sweep_window         VARCHAR,
    sweep_duration_ms    INTEGER,
    n_combos_tested      INTEGER,
    error                TEXT,
    sweep_timestamp      TIMESTAMPTZ NOT NULL,
    source               VARCHAR,

    minutes_since_last_sweep INTEGER,

    PRIMARY KEY (nt_id)
);

CREATE INDEX IF NOT EXISTS idx_ntrll_symbol   ON nt_regime_log_local (symbol, sweep_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ntrll_regime   ON nt_regime_log_local (regime, sweep_timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ntrll_ts       ON nt_regime_log_local (sweep_timestamp DESC);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (2, 'Phase 1: NT Supabase mirror tables (nt_sweep_results_local, nt_regime_log_local)');
