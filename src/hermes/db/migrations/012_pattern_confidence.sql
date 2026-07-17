-- Migration 012: persist learned pattern confidence on blended signals.
-- Closes the learn->live loop: the synthesizer now reads pattern_performance
-- (aggregated from executed + sim trades by brick_pattern) and records the
-- learned Wilson confidence on every blended signal, so the conviction boost
-- is auditable and visible in trade_signals_blended.

ALTER TABLE trade_signals_blended ADD COLUMN IF NOT EXISTS pattern_confidence DOUBLE DEFAULT 0.0;
CREATE INDEX IF NOT EXISTS idx_blended_pattern_conf
    ON trade_signals_blended (brick_pattern, pattern_confidence DESC);

INSERT INTO schema_version (version, description)
VALUES (12, 'Pattern confidence: trade_signals_blended.pattern_confidence');
