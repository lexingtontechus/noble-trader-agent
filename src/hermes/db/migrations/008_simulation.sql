-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v8 (Phase 8 additions)
-- Adds: simulation_runs, simulation_trades, param_optimizations
-- ============================================================

CREATE TABLE IF NOT EXISTS simulation_runs (
    run_id               VARCHAR PRIMARY KEY,
    ts_started           TIMESTAMPTZ NOT NULL,
    ts_finished          TIMESTAMPTZ,
    duration_sec         INTEGER,

    mode                 VARCHAR NOT NULL,
    triggered_by         VARCHAR DEFAULT 'manual',
    hermes_hypothesis_id VARCHAR,

    start_ts             TIMESTAMPTZ NOT NULL,
    end_ts               TIMESTAMPTZ NOT NULL,
    symbols              TEXT[] NOT NULL,
    venues               TEXT[] DEFAULT '{}',
    regime_filter        VARCHAR,

    config_hash          VARCHAR DEFAULT '',
    config_json          JSON,

    n_trades             INTEGER DEFAULT 0,
    win_rate             DOUBLE DEFAULT 0,
    avg_r_multiple       DOUBLE DEFAULT 0,
    sharpe               DOUBLE DEFAULT 0,
    sortino              DOUBLE DEFAULT 0,
    calmar               DOUBLE DEFAULT 0,
    max_drawdown_pct     DOUBLE DEFAULT 0,
    max_drawdown_usd     DOUBLE DEFAULT 0,
    profit_factor        DOUBLE DEFAULT 0,
    ulcer_index          DOUBLE DEFAULT 0,
    net_pnl_usd          DOUBLE DEFAULT 0,
    net_pnl_bps          DOUBLE DEFAULT 0,
    entry_alpha_bps      DOUBLE DEFAULT 0,

    deflated_sharpe      DOUBLE,
    walk_forward_oos_sharpe DOUBLE,
    monte_carlo_5pct_sharpe DOUBLE,
    bootstrap_sharpe_lower DOUBLE,
    bootstrap_sharpe_upper DOUBLE,
    rigor_checks_passed  INTEGER DEFAULT 0,
    rigor_checks_failed  TEXT[],
    accepted             BOOLEAN DEFAULT FALSE,

    promoted_to_shadow   BOOLEAN DEFAULT FALSE,
    shadow_started_at    TIMESTAMPTZ,
    shadow_ended_at      TIMESTAMPTZ,
    shadow_sharpe        DOUBLE,
    promoted_to_live     BOOLEAN DEFAULT FALSE,
    promotion_decision   VARCHAR DEFAULT 'pending',

    baseline_sharpe      DOUBLE,
    beat_baseline        BOOLEAN DEFAULT FALSE,
    error                TEXT
);

CREATE INDEX IF NOT EXISTS idx_sim_ts         ON simulation_runs (ts_started DESC);
CREATE INDEX IF NOT EXISTS idx_sim_mode       ON simulation_runs (mode, ts_started DESC);
CREATE INDEX IF NOT EXISTS idx_sim_accepted   ON simulation_runs (accepted, ts_started DESC);
CREATE INDEX IF NOT EXISTS idx_sim_promoted   ON simulation_runs (promoted_to_live, ts_started DESC);

CREATE TABLE IF NOT EXISTS simulation_trades (
    sim_trade_id         VARCHAR PRIMARY KEY,
    run_id               VARCHAR NOT NULL,
    trade_num            INTEGER NOT NULL,
    ts_opened            TIMESTAMPTZ NOT NULL,
    ts_closed            TIMESTAMPTZ,
    symbol               VARCHAR NOT NULL,
    venue                VARCHAR NOT NULL,
    direction            VARCHAR NOT NULL,
    meta_regime          VARCHAR,
    upstream_regime      VARCHAR,

    size_usd             DOUBLE NOT NULL,
    kelly_fraction       DOUBLE,
    masaniello_stake     DOUBLE,
    conviction_score     DOUBLE,

    entry_price          DOUBLE NOT NULL,
    stop_price           DOUBLE NOT NULL,
    target_price         DOUBLE NOT NULL,
    exit_price           DOUBLE,
    exit_reason          VARCHAR,

    entry_strategy       VARCHAR DEFAULT '',
    execution_method     VARCHAR DEFAULT '',
    brick_pattern_at_entry VARCHAR DEFAULT '',
    nt_entry_price       DOUBLE DEFAULT 0,

    gross_pnl            DOUBLE,
    fees                 DOUBLE,
    slippage_cost        DOUBLE,
    funding_pnl          DOUBLE,
    net_pnl              DOUBLE,
    r_multiple           DOUBLE,
    hold_duration_sec    INTEGER,
    entry_alpha_bps      DOUBLE,
    pnl_attribution      JSON
);

CREATE INDEX IF NOT EXISTS idx_simtr_run    ON simulation_trades (run_id, trade_num);
CREATE INDEX IF NOT EXISTS idx_simtr_symbol ON simulation_trades (symbol, ts_opened DESC);
CREATE INDEX IF NOT EXISTS idx_simtr_regime ON simulation_trades (meta_regime, ts_opened DESC);

CREATE TABLE IF NOT EXISTS param_optimizations (
    trial_id             VARCHAR PRIMARY KEY,
    run_id               VARCHAR NOT NULL,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    trial_num            INTEGER NOT NULL,

    params               JSON NOT NULL,
    objective_primary    DOUBLE,
    objective_secondary  DOUBLE,
    sharpe               DOUBLE,
    win_rate             DOUBLE,
    max_drawdown_pct     DOUBLE,
    calmar               DOUBLE,

    rigor_pass           BOOLEAN DEFAULT FALSE,
    rigor_failed_checks  TEXT[],
    reject_reason        VARCHAR,

    status               VARCHAR DEFAULT 'complete',
    pruned_by            VARCHAR,

    promoted_to_shadow   BOOLEAN DEFAULT FALSE,
    promoted_to_live     BOOLEAN DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_opt_run      ON param_optimizations (run_id, trial_num);
CREATE INDEX IF NOT EXISTS idx_opt_status   ON param_optimizations (status, ts DESC);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (8, 'Phase 8: simulation_runs, simulation_trades, param_optimizations tables');
