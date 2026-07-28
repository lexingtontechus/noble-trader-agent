-- ============================================================
-- Hermes Trading Platform — DuckDB Migration 014
-- Bayesian alpha tracking on pnl_realized
-- ============================================================
-- Adds columns to pnl_realized for Bayesian alpha computation:
--   p_win_agent       : The agent's blended P_win at trade entry time
--                       (after re-blending with pattern stats from DuckDB)
--   p_win_server      : The server's P_win from the heartbeat (before
--                       agent re-blend)
--   alpha_at_entry    : The alpha value used for this trade (snapshot
--                       at entry, so we can back-test alpha decay)
--   ev_per_dollar     : The EV/$ from the heartbeat (used for alpha
--                       weighting — higher EV = more informative sample)
--
-- These columns let the BayesianAlpha module compute a rolling
-- posterior on P_win accuracy, which feeds back as a position-sizing
-- modulator (NEVER a gate). See agent/bayesian_alpha.py.
-- ============================================================

ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS p_win_agent DOUBLE;
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS p_win_server DOUBLE;
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS alpha_at_entry DOUBLE;
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS ev_per_dollar DOUBLE;

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (14, 'Bayesian alpha tracking columns on pnl_realized');
