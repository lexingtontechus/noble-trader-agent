-- ============================================================
-- Migration 019 — Trade Postmortem (Phase 1A)
--
-- Adds: trade_postmortem table for hypothesis + LLM postmortem +
-- human notes keyed by signal_id (1:1 with trade_signals_blended).
--
-- Also cleans up earlier draft columns + tables that are no longer
-- in scope:
--   * DROP TABLE hermes_hypotheses (hypothesis moves into
--     trade_postmortem.hypothesis per-signal)
--   * DROP v3-era LLM columns from trade_journal (postmortem_llm,
--     lessons_llm, rationale_llm, *_schema_version, *_skill_slug,
--     *_skill_hash, *_hermes_session_id, postmortem_generated_at)
--
-- The pre-existing trade_journal table (journal_id PK, written by
-- the orchestrator on every position close) is untouched except
-- for the v3 column drops. The new trade_postmortem table is the
-- LLM/human layer, separate concern, separate grain (per-signal,
-- not per-trade).
--
-- Idempotent: all DROP uses IF EXISTS; CREATE TABLE uses IF NOT
-- EXISTS. Safe to re-run on dev / staging / tenant deployments.
-- ============================================================

-- === Cleanup: hermes_hypotheses table (no longer used) ===
DROP TABLE IF EXISTS hermes_hypotheses;

-- === Cleanup: v3-era LLM columns on trade_journal ===
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_llm;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_schema_version;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_skill_slug;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_skill_hash;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_hermes_session_id;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS postmortem_generated_at;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS lessons_llm;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS rationale_llm;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS rationale_schema_version;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS rationale_skill_slug;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS rationale_skill_hash;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS rationale_hermes_session_id;
ALTER TABLE trade_journal DROP COLUMN IF EXISTS rationale_generated_at;

-- === Cleanup: hermes_sessions table (no longer used) ===
DROP TABLE IF EXISTS hermes_sessions;

-- === trade_postmortem — 1:1 with trade_signals_blended ===
-- Keyed by signal_id. The Hermes agent reads skills/trade_journal/SKILL.md
-- and updates rows here. NULL postmortem_status = not yet generated;
-- 'llm_unavailable' = last attempt failed (retry); 'generated' = success;
-- 'reviewed' = trader acked; 'skipped' = trader dismissed.
CREATE TABLE IF NOT EXISTS trade_postmortem (
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

CREATE INDEX IF NOT EXISTS idx_journal_status
    ON trade_postmortem (postmortem_status);

CREATE INDEX IF NOT EXISTS idx_journal_generated_at
    ON trade_postmortem (postmortem_generated_at DESC);

-- === Record schema version ===
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (19, 'Phase 1A: trade_postmortem table (hypothesis + LLM postmortem + human notes, keyed by signal_id)');
