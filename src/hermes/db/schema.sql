-- ============================================================
-- Hermes Trading Platform — DuckDB Schema v1
-- Phase 0: foundational tables only.
-- Subsequent phases add more tables (see roadmap §6).
-- ============================================================

-- === Schema version tracking ===
CREATE TABLE IF NOT EXISTS schema_version (
    version             INTEGER PRIMARY KEY,
    applied_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    description         VARCHAR
);

-- === Config history (every config version for reproducibility) ===
CREATE TABLE IF NOT EXISTS config_history (
    config_hash         VARCHAR PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    config_json         JSON NOT NULL,
    source              VARCHAR NOT NULL,           -- file | hermes | human | init
    rationale           TEXT
);

CREATE INDEX IF NOT EXISTS idx_config_history_ts ON config_history (ts DESC);

-- === Noble Trader heartbeat ingestion (§6.2.6) ===
-- Every heartbeat received, persisted before downstream processing.
CREATE TABLE IF NOT EXISTS signal_heartbeats (
    heartbeat_id        VARCHAR PRIMARY KEY,        -- UUID assigned by L0
    ts_received         TIMESTAMPTZ NOT NULL,
    ts_upstream         TIMESTAMPTZ NOT NULL,
    lag_ms              BIGINT NOT NULL,
    dedup_hash          VARCHAR NOT NULL,

    -- Identity
    symbol              VARCHAR NOT NULL,
    strategy_id         VARCHAR NOT NULL,
    type                VARCHAR NOT NULL,           -- "heartbeat"

    -- Upstream signal
    signal              VARCHAR NOT NULL,           -- buy | sell | neutral
    entry_price         DOUBLE,
    stop_loss           DOUBLE,
    take_profit         DOUBLE,
    aggression          VARCHAR,                    -- passive | mid | aggressive

    -- Renko
    brick_size          DOUBLE,
    sl_bricks            DOUBLE,
    tp_bricks            DOUBLE,

    -- Upstream regime
    regime              VARCHAR NOT NULL,
    regime_conf         DOUBLE NOT NULL,
    regime_shift        BOOLEAN NOT NULL,
    prev_regime         VARCHAR,
    shift_at            TIMESTAMPTZ,
    shifts_24h          INTEGER NOT NULL,

    -- Upstream EV engine
    ev                  DOUBLE,
    ev_per_dollar       DOUBLE,
    p_win               DOUBLE,
    p_regime            DOUBLE,
    p_imbalance         DOUBLE,
    p_markov            DOUBLE,
    ev_scale            DOUBLE,

    -- TimesFM
    p_timesfm           DOUBLE,
    timesfm_horizon     VARCHAR,

    -- Markov
    markov_current_state VARCHAR,

    -- Tail risk
    tail_risk_score     DOUBLE,
    tail_risk_action    VARCHAR,

    -- Kelly
    kelly_f             DOUBLE,
    effective_kelly     DOUBLE,

    -- Raw payload + L0 processing result
    raw_payload         JSON NOT NULL,
    accepted            BOOLEAN NOT NULL,
    reject_reason       VARCHAR,
    reprocessed_at      TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hb_ts_received  ON signal_heartbeats (ts_received DESC);
CREATE INDEX IF NOT EXISTS idx_hb_symbol       ON signal_heartbeats (symbol, ts_received DESC);
CREATE INDEX IF NOT EXISTS idx_hb_regime       ON signal_heartbeats (regime, ts_received DESC);
CREATE INDEX IF NOT EXISTS idx_hb_regime_shift ON signal_heartbeats (regime_shift, ts_received DESC);
CREATE INDEX IF NOT EXISTS idx_hb_signal       ON signal_heartbeats (signal, ts_received DESC);
CREATE INDEX IF NOT EXISTS idx_hb_dedup        ON signal_heartbeats (dedup_hash);

-- === Quarantine for malformed heartbeats ===
CREATE TABLE IF NOT EXISTS signal_heartbeats_quarantine (
    quarantine_id       VARCHAR PRIMARY KEY,
    ts_received         TIMESTAMPTZ NOT NULL,
    raw_payload         TEXT NOT NULL,
    parse_error         TEXT NOT NULL,
    schema_violations   TEXT[],
    resolved            BOOLEAN NOT NULL DEFAULT FALSE,
    resolved_at         TIMESTAMPTZ,
    resolution_note     TEXT
);

CREATE INDEX IF NOT EXISTS idx_quar_ts ON signal_heartbeats_quarantine (ts_received DESC);

-- === Account snapshots (§6.2.2) ===
CREATE TABLE IF NOT EXISTS account_snapshots (
    snapshot_id         VARCHAR PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL,
    snapshot_type       VARCHAR NOT NULL,           -- 1m | 5m | 1h | eod | on_event

    equity_total        DOUBLE NOT NULL,
    cash_usd            DOUBLE NOT NULL,
    cash_usdc           DOUBLE NOT NULL,
    margin_used         DOUBLE NOT NULL,
    margin_available    DOUBLE NOT NULL,
    leverage_gross      DOUBLE NOT NULL,
    leverage_net        DOUBLE NOT NULL,

    realized_pnl        DOUBLE NOT NULL,
    unrealized_pnl      DOUBLE NOT NULL,
    funding_pnl         DOUBLE NOT NULL,
    fees_paid           DOUBLE NOT NULL,

    gross_exposure_usd  DOUBLE NOT NULL,
    net_exposure_usd    DOUBLE NOT NULL,
    long_exposure_usd   DOUBLE NOT NULL,
    short_exposure_usd  DOUBLE NOT NULL,
    n_open_positions    INTEGER NOT NULL,
    n_venues            INTEGER NOT NULL,

    peak_equity         DOUBLE NOT NULL,
    drawdown_pct        DOUBLE NOT NULL,
    drawdown_usd        DOUBLE NOT NULL,
    time_in_dd_sec      INTEGER NOT NULL,

    var_1d_99           DOUBLE,
    cvar_1d_99          DOUBLE,
    beta_to_spy         DOUBLE,

    config_hash         VARCHAR NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snap_ts     ON account_snapshots (ts DESC);
CREATE INDEX IF NOT EXISTS idx_snap_type   ON account_snapshots (snapshot_type, ts DESC);

-- === Trade journal (§6.2.1) — narrative layer ===
CREATE TABLE IF NOT EXISTS trade_journal (
    journal_id          VARCHAR PRIMARY KEY,
    trade_id            VARCHAR NOT NULL,
    symbol              VARCHAR NOT NULL,
    venue               VARCHAR NOT NULL,
    strategy_id         VARCHAR NOT NULL,
    direction           VARCHAR NOT NULL,
    regime_tag          VARCHAR,

    entry_thesis        TEXT,
    entry_conviction    DOUBLE,
    entry_edge_estimate DOUBLE,
    entry_atr           DOUBLE,
    entry_stop_distance DOUBLE,
    entry_target        DOUBLE,

    exit_reason         VARCHAR,
    exit_pnl            DOUBLE,
    exit_r_multiple     DOUBLE,
    hold_duration_sec   INTEGER,
    max_favorable_exc   DOUBLE,
    max_adverse_exc     DOUBLE,

    postmortem          TEXT,
    lessons             TEXT[],
    hypothesis_ids      TEXT[],
    tags                TEXT[],

    opened_at           TIMESTAMPTZ NOT NULL,
    closed_at           TIMESTAMPTZ,
    created_by          VARCHAR NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_journal_symbol_time ON trade_journal (symbol, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_strategy    ON trade_journal (strategy_id, opened_at DESC);
CREATE INDEX IF NOT EXISTS idx_journal_regime      ON trade_journal (regime_tag, opened_at DESC);

-- === Risk decisions (§6.3) ===
CREATE TABLE IF NOT EXISTS risk_decisions (
    decision_id         VARCHAR PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    signal_id           VARCHAR NOT NULL,
    approved            BOOLEAN NOT NULL,
    requested_size_usd  DOUBLE NOT NULL,
    approved_size_usd   DOUBLE NOT NULL,
    limits_hit          TEXT[],
    reason              TEXT,
    circuit_breaker_level INTEGER,
    var_pre             DOUBLE,
    var_post            DOUBLE,
    config_hash         VARCHAR NOT NULL,
    autonomy_tier       INTEGER
);

CREATE INDEX IF NOT EXISTS idx_risk_ts ON risk_decisions (ts DESC);
CREATE INDEX IF NOT EXISTS idx_risk_approved ON risk_decisions (approved, ts DESC);

-- === Circuit breaker events (§6.3) ===
CREATE TABLE IF NOT EXISTS circuit_breaker_events (
    event_id            VARCHAR PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    breaker_type        VARCHAR NOT NULL,           -- volatility | risk | kill_switch
    level               INTEGER NOT NULL,
    symbol              VARCHAR,
    trigger_value       DOUBLE NOT NULL,
    threshold           DOUBLE NOT NULL,
    action_taken        VARCHAR NOT NULL,
    payload             JSON
);

CREATE INDEX IF NOT EXISTS idx_cb_ts ON circuit_breaker_events (ts DESC);
CREATE INDEX IF NOT EXISTS idx_cb_type ON circuit_breaker_events (breaker_type, ts DESC);

-- === Hermes hypotheses (§6.3) — learning loop ===
CREATE TABLE IF NOT EXISTS hermes_hypotheses (
    hypothesis_id       VARCHAR PRIMARY KEY,
    ts_created          TIMESTAMPTZ NOT NULL DEFAULT now(),
    hypothesis          TEXT NOT NULL,
    rationale           TEXT,
    proposed_change     JSON,
    backtest_result     JSON,
    status              VARCHAR NOT NULL,           -- proposed | backtested | shadow | live | rejected | retired
    confidence          DOUBLE,
    promoted_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_hyp_status ON hermes_hypotheses (status, ts_created DESC);
CREATE INDEX IF NOT EXISTS idx_hyp_ts ON hermes_hypotheses (ts_created DESC);

-- === Meta-regime history (§6.2.9) ===
CREATE TABLE IF NOT EXISTS meta_regime_history (
    event_id             VARCHAR PRIMARY KEY,
    ts                   TIMESTAMPTZ NOT NULL DEFAULT now(),
    symbol               VARCHAR,
    scope                VARCHAR NOT NULL,           -- asset | portfolio

    prev_state           VARCHAR,
    new_state            VARCHAR NOT NULL,
    confidence           DOUBLE NOT NULL,
    posterior_probs      JSON NOT NULL,
    transition_probs     JSON,

    upstream_regime      VARCHAR,
    upstream_regime_conf DOUBLE,
    cross_asset_corr_mean DOUBLE,
    funding_rate_8h      DOUBLE,
    book_depth_percentile DOUBLE,
    spread_percentile    DOUBLE,
    posterior_entropy    DOUBLE,

    trigger              VARCHAR NOT NULL,
    trigger_detail       JSON,

    pnl_5m_after         DOUBLE,
    pnl_15m_after        DOUBLE,
    pnl_1h_after         DOUBLE,
    correct_call         BOOLEAN
);

CREATE INDEX IF NOT EXISTS idx_mrh_ts       ON meta_regime_history (ts DESC);
CREATE INDEX IF NOT EXISTS idx_mrh_symbol   ON meta_regime_history (symbol, ts DESC);
CREATE INDEX IF NOT EXISTS idx_mrh_state    ON meta_regime_history (new_state, ts DESC);

-- === Audit log (general-purpose) ===
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id            VARCHAR PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor               VARCHAR NOT NULL,           -- hermes | human | system
    action              VARCHAR NOT NULL,
    target              VARCHAR,                    -- what was acted upon
    payload             JSON,
    result              VARCHAR NOT NULL,           -- success | failure
    error               TEXT,
    ip_address          VARCHAR,
    user_agent          VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log (ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log (actor, ts DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log (action, ts DESC);

-- === Record schema version ===
INSERT OR IGNORE INTO schema_version (version, description)
VALUES (1, 'Phase 0: foundational tables (config, heartbeats, snapshots, journal, risk, audit)');
