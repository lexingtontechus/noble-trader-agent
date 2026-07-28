-- ============================================================
-- Hermes Trading Platform — DuckDB Migration 015
-- Calibration bias on signal_heartbeats (v5)
-- ============================================================
-- Adds a column to signal_heartbeats for the server-reported
-- calibration_bias. This is the bias fetched by the orchestrator
-- (orchestrator.py: fetch_calibration_bias) and forwarded in the
-- agent_payload. The agent stores it on the heartbeat row so that
-- backtest/sim can apply it as a P_win correction at replay time.
--
-- Production usage: agent synthesizer can optionally shrink
-- regime_conf toward 0.5 when calibration_bias > 0.10 (overconfident).
-- This is a soft modulation, not a gate.
--
-- Sim/backtest usage: the backtest engine applies calibration_bias
-- as a linear P_win correction (see backtest/engine.py:242):
--   p_win_corrected = max(0.01, min(0.99, raw_p_win - calibration_bias))
-- This lets sim runs reflect the calibration drift that production
-- would have experienced at that point in time.
-- ============================================================

ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS calibration_bias DOUBLE;
ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS calibration_status VARCHAR;

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (15, 'Calibration bias columns on signal_heartbeats (v5 EV rework)');
