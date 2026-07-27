-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v14 (Trade Journal)
-- ============================================================
-- DEPRECATED / REMOVED:
-- This migration originally (incorrectly) added a set of LLM-postmortem
-- columns to trade_journal:
--     hypothesis, postmortem_llm, postmortem_human, postmortem_status,
--     postmortem_generated_at, prompt_tokens, completion_tokens
-- plus idx_journal_status / idx_journal_generated_at.
--
-- That was part of a Phase 1A per-signal LLM postmortem pipeline that was
-- an incorrect/abandoned implementation and never shipped in the runtime
-- (no code reads or writes trade_journal.postmortem_status). The live
-- postmortem path is src/hermes/agent/learning.py::_generate_postmortem(),
-- which writes a plain string into the legacy trade_journal.postmortem column
-- (schema.sql owns trade_journal). The orphaned columns/indexes below are
-- therefore deleted.
--
-- schema.sql is the authoritative creator of trade_journal (legacy shape).
-- This migration is now a no-op for the table and idempotently cleans up any
-- orphaned columns/indexes a previously-applied version may have left behind.
-- ============================================================

-- Drop orphaned LLM-postmortem columns if present (safe no-op when absent).
-- No runtime code consumes these.
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_llm;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_human;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_status;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_generated_at;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS prompt_tokens;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS completion_tokens;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS hypothesis;

DROP INDEX IF EXISTS idx_journal_status;
DROP INDEX IF EXISTS idx_journal_generated_at;

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (14, 'Phase 14: trade_journal (deprecated LLM-postmortem columns removed; table owned by schema.sql)');
