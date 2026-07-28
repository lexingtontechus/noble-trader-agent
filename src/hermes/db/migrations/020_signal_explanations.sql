-- ============================================================
-- Migration 020 — Signal Explanations (Phase 1B)
--
-- Adds: signal_explanations table for LLM-generated rationale +
-- structured explanation + per-source breakdown, written by the
-- signal-explainer skill (skills/signal-explainer/SKILL.md).
--
-- Keyed by heartbeat_id (1:1 with signal_heartbeats). The Hermes
-- agent reads the skill file and updates rows here; subscribers
-- (dashboard, AlertManager, client app) read from this table via
-- the existing Hermes web API. No Redis pub/sub for explanations.
--
-- Schema mirrors trade_postmortem (Phase 1A):
--   * status column with the same lifecycle semantics
--     (NULL | generated | llm_unavailable | reviewed | skipped)
--   * prompt_tokens + completion_tokens for cost tracking
--   * created_at + updated_at audit stamps
--   * no separate audit table — the row itself is the audit trail
--   * no provider columns — Hermes is the runtime (no external LLM
--     abstraction)
--
-- Idempotent: CREATE TABLE uses IF NOT EXISTS; CREATE INDEX uses
-- IF NOT EXISTS. Safe to re-run on dev / staging / tenant
-- deployments.
-- ============================================================

-- === signal_explanations — 1:1 with signal_heartbeats ===
-- Keyed by heartbeat_id. The Hermes agent reads
-- skills/signal-explainer/SKILL.md and INSERTs/UPDATEs rows here.
-- NULL explanation_status = not yet generated;
-- 'llm_unavailable' = last attempt failed (retry);
-- 'generated' = success; 'reviewed' = operator acked;
-- 'skipped' = operator dismissed.
CREATE TABLE IF NOT EXISTS signal_explanations (
    heartbeat_id           VARCHAR PRIMARY KEY,    -- 1:1 with signal_heartbeats.heartbeat_id

    -- The 1-2 sentence hook — tooltip text + client app rationale
    rationale              TEXT,

    -- The 4-6 sentence structured walkthrough — operator's "why?"
    -- drilldown in the dashboard
    explanation            TEXT,

    -- Per-source P_win breakdown + calibration_bias + ev + kelly_f.
    -- Stored as JSON-in-VARCHAR (the codebase's standard pattern;
    -- see migration 016 for the same approach on signal_heartbeats).
    source_breakdown       VARCHAR,

    -- Lifecycle state (NULL = never generated)
    --   generated | llm_unavailable | reviewed | skipped
    explanation_status     VARCHAR,
    explanation_generated_at TIMESTAMPTZ,

    -- LLM cost tracking (nullable, backfill won't set these)
    prompt_tokens          INTEGER,
    completion_tokens      INTEGER,

    created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- FK to signal_heartbeats.heartbeat_id — the explanation always
    -- references an emitted signal. DuckDB enforces this via the
    -- REFERENCES clause.
    FOREIGN KEY (heartbeat_id) REFERENCES signal_heartbeats(heartbeat_id)
);

CREATE INDEX IF NOT EXISTS idx_explanation_status
    ON signal_explanations (explanation_status);

CREATE INDEX IF NOT EXISTS idx_explanation_generated_at
    ON signal_explanations (explanation_generated_at DESC);

-- === Record schema version ===
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (20, 'Phase 1B: signal_explanations table (rationale + explanation + source_breakdown, keyed by heartbeat_id)');
