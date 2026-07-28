# Signal Format Reference

The `signal-explainer` skill consumes a payload built by the
`SignalExplainer` service (`src/hermes/ops/signal_explainer.py`) from
the `signal_heartbeats` DuckDB table. The payload is a single JSON
object with three top-level keys: `heartbeat_id`, `symbol`, `signal`,
and `regime_context`.

There is no separate "schema validator" module — the agent's own
inference router is responsible for handling missing keys gracefully.
If a field is NULL in `signal_heartbeats`, it is omitted from the
payload (or sent as `null`); the skill MUST NOT invent values for
missing fields.

## Top-level payload shape

```json
{
  "heartbeat_id":   "<string, REQUIRED>",
  "symbol":         "<string, REQUIRED>",
  "signal":         { "<heartbeat field>": "<value>", ... },
  "regime_context": { "<meta_regime_history field>": "<value>", ... } | null
}
```

## `signal` object — fields from `signal_heartbeats`

The `signal` object mirrors the columns written by the agent's L0
heartbeat processor. Every column is included; NULLs are passed as
`null`.

### Identity + timing

| Field            | Type      | Notes |
|------------------|-----------|-------|
| `heartbeat_id`   | string    | UUID assigned by L0. 1:1 with the `signal_explanations` row. |
| `symbol`         | string    | e.g. "BTC-USD". |
| `strategy_id`    | string    | Strategy that emitted the signal. |
| `signal`         | string    | `buy` \| `sell` \| `neutral`. |
| `ts_received`    | timestamp | Agent wall-clock when the heartbeat arrived. |

### Entry / exit

| Field          | Type    | Notes |
|----------------|---------|-------|
| `entry_price`  | float   | NULL if `signal='neutral'`. |
| `stop_loss`    | float   | NULL if not applicable. |
| `take_profit`  | float   | NULL if not applicable. |
| `aggression`   | string  | `passive` \| `mid` \| `aggressive`. |

### Renko

| Field        | Type  | Notes |
|--------------|-------|-------|
| `brick_size` | float | Renko brick size in price units. |
| `sl_bricks`  | float | Stop distance in bricks. |
| `tp_bricks`  | float | Target distance in bricks. |

### Upstream regime

| Field           | Type    | Notes |
|-----------------|---------|-------|
| `regime`        | string  | Upstream regime tag (e.g. `up`, `down`, `range`). |
| `regime_conf`   | float   | Upstream regime confidence in [0, 1]. |
| `regime_shift`  | boolean | TRUE if a regime transition fired on this heartbeat. |
| `prev_regime`   | string  | NULL if no transition. |
| `shift_at`      | timestamp | NULL if no transition. |
| `shifts_24h`    | int     | Count of regime transitions in the trailing 24h. |

### Upstream EV engine — the 4 sources

These are the per-source P_win values that the blend pools. The skill
uses them to identify the dominant source for `explanation` and to
populate `source_breakdown`.

| Field         | Type  | Notes |
|---------------|-------|-------|
| `ev`          | float | Final EV after blending. |
| `ev_per_dollar` | float | EV per $1 of risk. |
| `p_win`       | float | Final blended P_win. |
| `p_regime`    | float | Regime source P_win (NULL if regime source unavailable). |
| `p_imbalance` | float | Order-book imbalance P_win (legacy; not in source_breakdown). |
| `p_markov`    | float | Markov source P_win. |
| `p_pattern`   | float | Pattern source P_win (agent-side Wilson LB). |
| `p_timesfm`   | float | TimesFM source P_win (NULL if TimesFM unreachable). |
| `ev_scale`    | float | EV scaling factor applied pre-Kelly. |

### TimesFM / Markov

| Field                 | Type   | Notes |
|-----------------------|--------|-------|
| `timesfm_horizon`     | string | e.g. "15m", "1h". |
| `markov_current_state`| string | e.g. "trending_up", "range_high". |

### Tail risk

| Field              | Type   | Notes |
|--------------------|--------|-------|
| `tail_risk_score`  | float  | In [0, 1]. |
| `tail_risk_action` | string | e.g. `skip`, `shrink_50`, `proceed`. |

### Kelly

| Field             | Type  | Notes |
|-------------------|-------|-------|
| `kelly_f`         | float | Raw Kelly fraction. Goes into `source_breakdown.kelly_f`. |
| `effective_kelly` | float | Post-shrink Kelly (after `p_win_kelly_shrink` applied). |

### v5 source breakdown (migration 016)

These three fields capture what the backend actually had available
at signal time and how it re-normalized weights. The skill uses
them to narrate *why* one source dominated (e.g. "TimesFM was
unreachable, so the blend re-weighted toward Markov").

| Field                | Type   | Notes |
|----------------------|--------|-------|
| `sources_used`       | string | JSON array, e.g. `'["p_regime","p_markov","p_pattern"]'`. |
| `weights_used`       | string | JSON object of backend's re-normalized weights. |
| `p_win_kelly_shrink` | float  | Server-side soft-gate Kelly shrink factor (1.0 = full, <1.0 = shrunk). |

### Calibration (migration 015)

| Field                 | Type   | Notes |
|-----------------------|--------|-------|
| `calibration_bias`    | float  | Positive = overconfident (shrink p_win down); negative = underconfident (shrink up); 0/NULL = no adjustment. |
| `calibration_status`  | string | e.g. `calibrated`, `overconfident`, `underconfident`. |

## `regime_context` object — fields from `meta_regime_history`

The latest `meta_regime_history` row for the symbol at or before
`ts_received`. This is the meta-regime snapshot the signal was
emitted under.

If no `meta_regime_history` row exists for the symbol (e.g. the
symbol is new, or meta-regime hasn't run yet), `regime_context` is
`null` — the skill MUST handle this case (omit regime_context
references from the explanation rather than fabricate).

| Field                    | Type      | Notes |
|--------------------------|-----------|-------|
| `ts`                     | timestamp | When the meta-regime snapshot was written. |
| `prev_state`             | string    | Previous meta-regime state (NULL on first observation). |
| `new_state`              | string    | Current meta-regime state. |
| `confidence`             | float     | Posterior confidence in `new_state`. |
| `posterior_probs`        | JSON      | Full posterior over all 7 states. |
| `trigger`                | string    | e.g. `price_momentum`, `funding_flip`, `cross_asset_corr`. |
| `funding_rate_8h`        | float     | 8h funding rate at snapshot time. |
| `book_depth_percentile`  | float     | Order-book depth percentile (liquidity proxy). |
| `spread_percentile`      | float     | Spread percentile (cost-of-execution proxy). |
| `posterior_entropy`      | float     | Posterior entropy (uncertainty in regime call). |

## Cross-references

- `skills/signal-explainer/SKILL.md` — the parent skill file
- `db/schema.sql` — `signal_heartbeats` table definition (lines 27-88)
- `db/migrations/015_calibration_bias.sql` — `calibration_bias` +
  `calibration_status` columns
- `db/migrations/016_signal_heartbeats_v5_sources.sql` —
  `p_pattern`, `sources_used`, `weights_used`, `p_win_kelly_shrink`
  columns
- `references/blend_math.md` — how the 4-source log-odds pool combines
- `references/explanation_format.md` — output schema + worked examples
