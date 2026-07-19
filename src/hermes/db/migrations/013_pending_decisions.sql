-- P1 (GE): pending human-approval queue for tier-3 (requires_human_approval) decisions.
-- Decisions that need human sign-off live here until approved (or expired),
-- instead of being silently dropped by L3.
CREATE TABLE IF NOT EXISTS pending_decisions (
    decision_id      VARCHAR PRIMARY KEY,
    signal_id        VARCHAR NOT NULL,
    symbol           VARCHAR NOT NULL,
    venue            VARCHAR,
    direction        VARCHAR,
    requested_size_usd DOUBLE,
    approved_size_usd  DOUBLE,
    autonomy_tier    INTEGER,
    reason           VARCHAR,
    payload          JSON,                 -- full RiskDecision for re-publish on approve
    created_at       TIMESTAMP DEFAULT now(),
    expires_at       TIMESTAMP,
    status           VARCHAR DEFAULT 'pending'  -- pending | approved | expired | rejected
);

CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_decisions(status);
CREATE INDEX IF NOT EXISTS idx_pending_symbol ON pending_decisions(symbol);
