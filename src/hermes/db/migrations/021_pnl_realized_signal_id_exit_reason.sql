-- ============================================================
-- Migration 021 — PnL realized: signal_id + exit_reason columns
--
-- Adds two columns to pnl_realized that the Phase 1A
-- TradeJournal._select_pending() query JOINs on / SELECTs but
-- were never added to the table:
--   * signal_id VARCHAR — the upstream trade_signals_blended
--     signal that the closed position was opened from. Used by
--     TradeJournal to JOIN pnl_realized to trade_signals_blended
--     (1:1 with trade_postmortem) so the postmortem skill payload
--     includes realized PnL attribution.
--   * exit_reason VARCHAR — the orchestrator's exit decision
--     reason string (e.g. 'tp_hit', 'sl_hit', 'manual',
--     'regime_change'). The orchestrator already had this value
--     in _on_position_closed() (decision.action.value /
--     decision.reason) but only wrote it to the legacy
--     trade_journal table, not to pnl_realized.
--
-- Both columns are nullable. Existing pnl_realized rows (from
-- before this migration) keep NULL — they can be backfilled by
-- joining through trade_journal.trade_id, but no backfill is
-- required for forward operation. New rows get the values
-- populated by ExecutionOrchestrator._on_position_closed().
--
-- Mirrors the migration 011 / 014 ALTER TABLE pattern for
-- pnl_realized. Idempotent: ADD COLUMN IF NOT EXISTS is safe to
-- re-run on dev / staging / tenant deployments.
-- ============================================================

ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS signal_id   VARCHAR;
ALTER TABLE pnl_realized ADD COLUMN IF NOT EXISTS exit_reason VARCHAR;

-- Index for the JOIN TradeJournal._select_pending() does:
--   LEFT JOIN pnl_realized pr ON tsb.signal_id = pr.signal_id
-- Without this index, every nightly `noble journal generate` /
-- `noble journal backfill` call does a full scan of pnl_realized
-- per trade_signals_blended row. With the index it's a point
-- lookup.
CREATE INDEX IF NOT EXISTS idx_pnl_realized_signal
    ON pnl_realized (signal_id);

-- === Record schema version ===
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (21, 'Phase 1A cleanup: pnl_realized.signal_id + exit_reason columns (enables TradeJournal._select_pending JOIN to trade_signals_blended)');
