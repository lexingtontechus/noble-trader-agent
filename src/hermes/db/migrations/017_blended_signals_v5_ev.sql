-- ============================================================
-- Hermes Trading Platform — DuckDB Migration 017
-- v5 EV fields on trade_signals_blended (Phase C)
-- ============================================================
-- The BlendedSignal model (synthesizer.py:39) gains three new
-- fields for the agent-side 4-source logit-pool P_win re-blend:
--
--   p_win_agent       : locally re-blended P_win (agent's own
--                       pattern_performance overrides the server's
--                       p_pattern, which is always 0.5).
--   markov_persistence: single-step T[current][target] — used by
--                       the decision tree for adaptive TP/SL
--                       thresholds (trending vs mean-reverting).
--   markov_hold_n     : T^N multi-step hold probability (N=tp_bricks).
--                       This is the same value as heartbeat.p_markov
--                       (renamed for clarity at the BlendedSignal
--                       layer; the heartbeat field name stays
--                       p_markov for backward compat).
--   p_win_server      : backend's pre-blended p_win (for audit /
--                       calibration comparison vs p_win_agent).
--   p_pattern_local   : Wilson-confident pattern win-rate from
--                       pattern_performance (the value the agent
--                       used as p_pattern in its local blend).
--
-- These columns let backtest / dashboard / attribution analyses
-- compare the agent's locally-blended p_win_agent against the
-- server's pre-blended p_win, and against the realized outcome.
-- ============================================================

ALTER TABLE trade_signals_blended ADD COLUMN IF NOT EXISTS p_win_agent DOUBLE;
ALTER TABLE trade_signals_blended ADD COLUMN IF NOT EXISTS markov_persistence DOUBLE;
ALTER TABLE trade_signals_blended ADD COLUMN IF NOT EXISTS markov_hold_n DOUBLE;
ALTER TABLE trade_signals_blended ADD COLUMN IF NOT EXISTS p_win_server DOUBLE;
ALTER TABLE trade_signals_blended ADD COLUMN IF NOT EXISTS p_pattern_local DOUBLE;

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (17, 'v5 EV fields on trade_signals_blended (Phase C: p_win_agent + markov)');
