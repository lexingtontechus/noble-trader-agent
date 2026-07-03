-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v4 (Phase 3 additions)
-- Adds: trade_signals_blended table
-- ============================================================

CREATE TABLE IF NOT EXISTS trade_signals_blended (
    signal_id             VARCHAR PRIMARY KEY,
    ts_emitted            TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol                VARCHAR NOT NULL,
    venue                 VARCHAR NOT NULL,
    direction             VARCHAR NOT NULL,

    -- From NT (trusted)
    nt_entry_price        DOUBLE NOT NULL,
    nt_stop_price         DOUBLE NOT NULL,
    nt_target_price       DOUBLE NOT NULL,
    nt_effective_kelly    DOUBLE NOT NULL,
    nt_brick_size         DOUBLE NOT NULL,

    -- From Hermes (meta-regime)
    meta_regime           VARCHAR NOT NULL,
    meta_regime_confidence DOUBLE NOT NULL,
    sizing_multiplier     DOUBLE NOT NULL,

    -- Entry/execution decision
    entry_strategy        VARCHAR NOT NULL,
    execution_method      VARCHAR NOT NULL,
    entry_price_target    DOUBLE,
    limit_price           DOUBLE,
    final_size_usd        DOUBLE NOT NULL,
    final_size_pct        DOUBLE NOT NULL,
    risk_amount_usd       DOUBLE NOT NULL,

    -- Analysis
    brick_pattern         VARCHAR NOT NULL,
    expected_entry_alpha_bps DOUBLE NOT NULL,
    sizing_limits_hit     TEXT[],
    sizing_reason         VARCHAR,

    -- Autonomy + config
    autonomy_tier         INTEGER DEFAULT 0,
    config_hash           VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tsb_ts        ON trade_signals_blended (ts_emitted DESC);
CREATE INDEX IF NOT EXISTS idx_tsb_symbol    ON trade_signals_blended (symbol, ts_emitted DESC);
CREATE INDEX IF NOT EXISTS idx_tsb_direction ON trade_signals_blended (direction, ts_emitted DESC);
CREATE INDEX IF NOT EXISTS idx_tsb_regime    ON trade_signals_blended (meta_regime, ts_emitted DESC);
CREATE INDEX IF NOT EXISTS idx_tsb_strategy  ON trade_signals_blended (entry_strategy, ts_emitted DESC);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (4, 'Phase 3: trade_signals_blended table for L4 signal synthesis output');
