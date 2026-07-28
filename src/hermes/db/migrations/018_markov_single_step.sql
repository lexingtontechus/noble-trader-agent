-- ============================================================
-- Hermes Trading Platform — DuckDB Migration 018
-- HIGH #8: single-step Markov T on signal_heartbeats
-- ============================================================
-- Audit 2026-07-22 issue #8: the agent's synthesizer was using
-- heartbeat.p_markov (T^N multi-step hold probability) as a proxy for
-- single-step T[current_state][target_state] when evaluating the
-- decision tree's adaptive-threshold check
-- (markov_persistence > 0.7 ⇒ "let winners run" branch). That
-- approximation is conservative-but-wrong in mean-reverting regimes
-- where T^N can be high while single-step T is low → adaptive
-- thresholds fire incorrectly.
--
-- Fix: backend now sends p_markov_single_step + markov_transition_matrix
-- in every agent_payload (orchestrator.py:1564-1586). This migration
-- persists both fields on signal_heartbeats so that:
--
--   1. Live heartbeats can be replayed with the correct single-step
--      value (no more T^N-as-proxy approximation).
--   2. Backtest replay of pre-HIGH-#8 heartbeats (which don't have
--      these columns) gracefully falls back to p_markov_single_step=0.5
--      (neutral) — the synthesizer's existing fallback path handles
--      this case.
--   3. The transition matrix is preserved for any future analysis that
--      wants to re-derive single-step probabilities or compute other
--      step counts (T^2, T^3, etc.) without re-running the Markov
--      chain fitting.
--
-- Columns:
--   p_markov_single_step      : T[current_state][target_state] in [0,1].
--                              target = 'UP' for long, 'DOWN' for short.
--                              Default 0.5 (neutral) for pre-HIGH-#8 rows.
--   markov_transition_matrix  : 3x3 dict-of-dicts keyed by {UP, DOWN, FLAT},
--                              JSON-encoded as VARCHAR (matches the
--                              sources_used / weights_used pattern from
--                              migration 016). NULL for pre-HIGH-#8 rows.
-- ============================================================

ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS p_markov_single_step DOUBLE DEFAULT 0.5;
ALTER TABLE signal_heartbeats ADD COLUMN IF NOT EXISTS markov_transition_matrix VARCHAR;

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (18, 'HIGH #8: single-step Markov T on signal_heartbeats');
