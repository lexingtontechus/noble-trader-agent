-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v14 (Trade Journal)
-- ============================================================
-- Adds: trade_journal table for hypothesis + LLM postmortem +
-- human notes keyed by signal_id (1:1 with trade_signals_blended).
--
-- Idempotency note: schema.sql bootstraps a *legacy* trade_journal
-- (PK journal_id, columns postmortem/lessons/hypothesis_ids/tags) used by
-- learning.py / status.py. This migration must therefore ADD the v14
-- postmortem columns rather than assume it owns the CREATE. The
-- CREATE TABLE IF NOT EXISTS below covers the fresh-DB case (no schema.sql);
-- the ALTERs below cover the schema.sql-bootstrap case so that
-- postmortem_status (and friends) exist for the postmortem pipeline.
-- ============================================================

CREATE TABLE IF NOT EXISTS trade_journal (
    signal_id               VARCHAR PRIMARY KEY,

    -- Pre-trade thesis (set before/during entry, can be NULL)
    hypothesis              TEXT,

    -- LLM-generated postmortem (set after trade/exit)
    postmortem_llm          TEXT,
    postmortem_human        TEXT,

    -- Lifecycle state (NULL = never generated)
    --   generated | llm_unavailable | reviewed | skipped
    postmortem_status       VARCHAR,
    postmortem_generated_at TIMESTAMPTZ,

    -- LLM cost tracking (nullable, backfill won't set these)
    prompt_tokens           INTEGER,
    completion_tokens       INTEGER,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ensure v14 postmortem columns exist even when schema.sql already
-- bootstrapped the legacy trade_journal shape (no-op if present).
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS hypothesis              TEXT;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS postmortem_llm          TEXT;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS postmortem_human        TEXT;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS postmortem_status       VARCHAR;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS postmortem_generated_at TIMESTAMPTZ;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS prompt_tokens           INTEGER;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS completion_tokens       INTEGER;

CREATE INDEX IF NOT EXISTS idx_journal_status
    ON trade_journal (postmortem_status);

CREATE INDEX IF NOT EXISTS idx_journal_generated_at
    ON trade_journal (postmortem_generated_at DESC);

INSERT OR IGNORE INTO schema_version (version, description)
VALUES (14, 'Phase 14: trade_journal — hypothesis + LLM postmortem + human notes');
