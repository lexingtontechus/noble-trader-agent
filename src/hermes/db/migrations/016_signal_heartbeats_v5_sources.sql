-- ============================================================
-- Hermes Trading Platform — DuckDB Migration 016
-- v5 EV source breakdown on signal_heartbeats (Phase C)
-- ============================================================
-- Persists the per-source breakdown that the backend started
-- sending in the agent_payload (orchestrator.py:1547-1556). The
-- agent's NobleTraderHeartbeat.to_duckdb_row() now writes these
-- columns; backtest replay (engine.py:222+) reads them so that
-- historical heartbeats can be re-blended locally via
-- compute_blended_p_win instead of trusting the pre-blended p_win.
--
-- Columns:
--   p_pattern           : server-side P_pattern (always 0.5 — no
--                         trade journal on server). Agent overrides
--                         with local pattern_performance Wilson LB.
--   sources_used        : JSON array of source keys the backend
--                         actually had available (e.g.
--                         '["p_regime","p_pattern","p_markov"]' when
--                         TimesFM was unreachable). Stored as VARCHAR
--                         because DuckDB list columns require newer
--                         versions; JSON-in-VARCHAR is the standard
--                         pattern this codebase uses elsewhere.
--   weights_used        : JSON object of backend's renormalised
--                         weights over the available sources.
--   p_win_kelly_shrink  : server-side soft-gate Kelly shrink factor
--                         (1.0 = full kelly, <1.0 = shrunk).
--
-- NOTE: calibration_bias + calibration_status already exist via
-- migration 015 — not re-added here.
-- ============================================================

ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS p_pattern DOUBLE;
ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS sources_used VARCHAR;
ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS weights_used VARCHAR;
ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS p_win_kelly_shrink DOUBLE;

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (16, 'v5 EV source breakdown on signal_heartbeats (Phase C)');
