-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v11 (Pattern learning)
-- Closes the learn->sim loop: links entry brick_pattern to outcomes
-- so per-pattern success-rate / confidence can be learned (harvested
-- concept from OpenClaw quant-skill guide, reimplemented on our data).
--
--   * pnl_realized.brick_pattern       — entry pattern on every closed trade
--     (trade_signals_blended already classifies it; denormalized here so
--     aggregation needs no join).
--   * simulation_trades.brick_pattern  — ADD to the v8 table (it has
--     brick_pattern_at_entry but the sim pipeline tags via this column).
--   * pattern_performance              — aggregated per-pattern stats +
--     Wilson confidence: the learned knowledge the sim search reads back.
-- ============================================================

-- pnl_realized (v6 table): add entry pattern column.
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS brick_pattern VARCHAR DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_pnl_realized_pattern ON pnl_realized (brick_pattern, ts DESC);

-- simulation_trades (v8 table): add a dedicated pattern column for sim learning.
ALTER TABLE simulation_trades ADD COLUMN IF NOT EXISTS brick_pattern VARCHAR DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_sim_trades_pattern ON simulation_trades (brick_pattern, symbol);

-- New aggregate table.
CREATE TABLE IF NOT EXISTS pattern_performance (
    pattern         VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,   -- executed | sim
    n               INTEGER NOT NULL DEFAULT 0,
    wins            INTEGER NOT NULL DEFAULT 0,
    losses          INTEGER NOT NULL DEFAULT 0,
    win_rate        DOUBLE NOT NULL DEFAULT 0.0,
    avg_r_multiple  DOUBLE NOT NULL DEFAULT 0.0,
    expectancy      DOUBLE NOT NULL DEFAULT 0.0,
    profit_factor   DOUBLE NOT NULL DEFAULT 0.0,
    confidence      DOUBLE NOT NULL DEFAULT 0.0,  -- Wilson lower bound (0-1), sample-size-aware
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pattern, source)
);

CREATE INDEX IF NOT EXISTS idx_pattern_perf_pattern ON pattern_performance (pattern, source);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (11, 'Pattern learning: brick_pattern on pnl_realized + simulation_trades, pattern_performance');
