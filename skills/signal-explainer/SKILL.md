---
name: signal-explainer
slug: signal-explainer
version: 1.0.0
description: >
  Phase 1B composite skill: per-signal LLM rationale + structured
  explanation + per-source P_win breakdown, written to the
  `signal_explanations` table (1:1 with `signal_heartbeats`, keyed
  by `heartbeat_id`). Hermes agent reads this file, queries DuckDB
  for heartbeats needing explanations, builds payloads, runs its
  own inference router, and INSERTs/UPDATEs the rows.
---

# Signal Explainer Skill

## When to Use

The Hermes agent's nightly cron calls `noble explanation generate
--date $(yesterday)`. The CLI dispatches to
`SignalExplainer.generate_explanations_for_day()`, which selects
heartbeats needing explanations and invokes this skill once per
heartbeat. A separate retry cron calls `noble explanation backfill
--retry-failed --start <window> --end <window>` to retry heartbeats
where the previous attempt failed
(`explanation_status='llm_unavailable'`).

The skill is **post-decision**. The signal has already been emitted,
accepted by L0, and written to `signal_heartbeats`. The skill
explains *what was decided and how the blend produced it*; it does
not second-guess the signal or recommend parameter changes.

## Architecture

For each heartbeat needing an explanation:

1. Read the heartbeat row from `signal_heartbeats` (signal direction,
   entry / stop / target, regime + regime_conf + regime_shift, the
   4-source P_win values: p_regime / p_markov / p_pattern /
   p_timesfm, ev / ev_per_dollar / ev_scale, kelly_f /
   effective_kelly, calibration_bias / calibration_status,
   sources_used / weights_used / p_win_kelly_shrink, tail risk).
2. Read the latest `meta_regime_history` snapshot for the symbol at
   or before `ts_received` (new_state, prev_state, confidence,
   posterior_probs, trigger, funding_rate_8h, book_depth_percentile,
   spread_percentile, posterior_entropy). May be NULL if no regime
   history exists for the symbol yet — the skill must handle that.
3. Build a payload combining the heartbeat + the regime snapshot.
4. Run the skill (the agent's own reasoning loop) against the payload.
5. INSERT/UPDATE `signal_explanations` with the result:
   - `rationale` — the 1-2 sentence plain-English hook (tooltip text
     + client app rationale)
   - `explanation` — the 4-6 sentence structured walkthrough (operator
     "why?" drilldown)
   - `source_breakdown` — JSON object with per-source P_win
     contributions + calibration_bias direction + ev + kelly_f
     (serialized to VARCHAR on write — JSON-in-VARCHAR is the
     codebase's standard pattern, see migration 016)
   - `explanation_status` = `'generated'`
   - `explanation_generated_at` = `now()`
   - `prompt_tokens`, `completion_tokens` — cost tracking (nullable)
6. On failure (skill raises, returns empty, or LLM is unavailable):
   UPDATE `signal_explanations` with
   `explanation_status='llm_unavailable'`. The next backfill run with
   `--retry-failed` will retry.

## Scope

This skill ONLY:

- Reads from `signal_heartbeats` (the just-emitted signal) +
  `meta_regime_history` (latest snapshot for the symbol).
- Writes to `signal_explanations` (rationale, explanation,
  source_breakdown, explanation_status, explanation_generated_at,
  prompt_tokens, completion_tokens, updated_at).
- Generates one row per `heartbeat_id` (1:1 with `signal_heartbeats`).

This skill NEVER:

- Modifies the signal itself, the 4-source blend, calibration_bias,
  EV, Kelly, or any live trading parameter.
- Delays signal emission. The signal has already been emitted + L0
  accepted before this skill runs.
- Touches `trade_signals_blended`, `pnl_realized`, `trade_postmortem`,
  or any other table.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).
- Publishes to any Redis channel or message bus. Subscribers (Hermes
  web dashboard, AlertManager, nobletradingapp) read directly from
  `signal_explanations` via the existing Hermes web API.

## Retry contract

A row in `signal_explanations` is "needs work" iff:

```sql
explanation_status IS NULL
OR explanation_status = 'llm_unavailable'
```

Rows where `explanation_status IN ('generated', 'reviewed', 'skipped')`
are never selected by `generate` or `backfill` (unless `--force` is
passed, in which case `'generated'` rows are re-selected but
`'reviewed'` and `'skipped'` rows are still protected — they are
human-acked / human-dismissed).

Status semantics:

| Status             | Meaning                                                  |
|--------------------|----------------------------------------------------------|
| NULL               | Never processed. Selected by `generate` + `backfill`.    |
| `generated`        | Skill ran successfully. Re-selected only with `--force`. |
| `llm_unavailable`  | Last attempt failed. Selected by `backfill --retry-failed`. |
| `reviewed`         | Operator acked the explanation. Never overwritten.       |
| `skipped`          | Operator dismissed the explanation. Never overwritten.   |

## Rationale contract

The `rationale` field is **the tooltip text**. It appears in:

- The nobletradingapp signal tooltip (the "why this signal?" hover).
- The Hermes web dashboard signal table (the inline rationale column).
- The AlertManager alert payload (the "why?" field on the alert body).

It MUST be 1-2 sentences, plain English, no jargon ("regime", "Kelly",
"EV") without context. The trader should be able to read it in 3-5
seconds and understand what the signal was betting on.

If the payload is missing a metric (e.g., `p_timesfm` is NULL because
TimesFM was unreachable at signal time), the rationale MUST NOT invent
it — omit the TimesFM framing entirely and lean on the sources that
*are* present.

## Explanation contract

The `explanation` field is the operator's "why?" drilldown. It appears
when the operator clicks the rationale in the dashboard. It MUST:

1. Name the dominant source (which of the 4 P_win sources drove the
   blend toward the final `p_win`).
2. State the calibration_bias direction (up / down / none) and its
   effect on the final `p_win`.
3. State the EV sign and magnitude.
4. State the Kelly fraction (`kelly_f` or `effective_kelly`) and the
   `p_win_kelly_shrink` if non-trivial.
5. Reference the regime tag from `signal_heartbeats.regime` and, if
   available, the meta-regime snapshot from `regime_context.new_state`.
6. Be 4-6 sentences. Every number MUST come from the payload — no
   fabrication.

## Source breakdown contract

The `source_breakdown` field is a JSON object the dashboard's
source-strength meter renders. Schema:

```json
{
  "markov":           <float in [0,1]>,
  "regime":           <float in [0,1]>,
  "pattern":          <float in [0,1]>,
  "timesfm":          <float in [0,1]>,
  "calibration_bias": "<up|down|none>",
  "ev":               <float>,
  "kelly_f":          <float>
}
```

- The four P_win source values come directly from the heartbeat's
  `p_markov` / `p_regime` / `p_pattern` / `p_timesfm` columns.
- If a source was unavailable at signal time (NULL in the heartbeat),
  omit the key from the JSON rather than fabricate a value.
- `calibration_bias` is the direction of the bias: `"up"` if
  `calibration_bias > 0`, `"down"` if `< 0`, `"none"` if 0 or NULL.
- `ev` is the heartbeat's `ev` value.
- `kelly_f` is the heartbeat's `kelly_f` value (NOT
  `effective_kelly` — the latter is post-shrink; the meter shows the
  raw Kelly to make the shrink visible as a separate UI element).

## Workflow

```
1. SignalExplainer.generate_explanations_for_day(date) SELECTs
   heartbeats from signal_heartbeats LEFT JOIN signal_explanations
   LEFT JOIN LATERAL meta_regime_history (latest snapshot at or
   before ts_received) WHERE ts_received::DATE = date AND
   (explanation_status IS NULL OR explanation_status =
   'llm_unavailable') AND explanation_status NOT IN ('reviewed',
   'skipped') AND accepted = TRUE.

2. For each row, build payload:
     payload = {
       "heartbeat_id":   <heartbeat_id>,
       "symbol":         <symbol>,
       "signal":         { ... signal_heartbeats fields ... },
       "regime_context": { ... latest meta_regime_history for symbol
                              at or before ts_received, or null ... }
     }

3. Call skill_invoker(skills/signal-explainer/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router; SignalExplainer
   is constructed with it by the caller — the CLI raises a clear
   error if it's None)

4. On success:
     INSERT INTO signal_explanations (
       heartbeat_id, rationale, explanation, source_breakdown,
       explanation_status, explanation_generated_at,
       prompt_tokens, completion_tokens, created_at, updated_at
     ) VALUES (
       ?, ?, ?, ?,
       'generated', now(),
       ?, ?,
       now(), now()
     )
     ON CONFLICT (heartbeat_id) DO UPDATE SET
       rationale = excluded.rationale,
       explanation = excluded.explanation,
       source_breakdown = excluded.source_breakdown,
       explanation_status = 'generated',
       explanation_generated_at = excluded.explanation_generated_at,
       prompt_tokens = excluded.prompt_tokens,
       completion_tokens = excluded.completion_tokens,
       updated_at = excluded.updated_at;

   # Subscribers read directly from signal_explanations via the
   # existing Hermes web API. The dashboard's /api/signals endpoint
   # JOINs to signal_explanations on heartbeat_id; AlertManager's
   # alert evaluation query JOINs the same way; nobletradingapp
   # reads the rationale field from the signal API response.

5. On failure (exception, empty result, or LLM unavailable):
     INSERT INTO signal_explanations (
       heartbeat_id, explanation_status, created_at, updated_at
     ) VALUES (?, 'llm_unavailable', now(), now())
     ON CONFLICT (heartbeat_id) DO UPDATE SET
       explanation_status = 'llm_unavailable',
       updated_at = excluded.updated_at;
```

## Output Schema

```json
{
  "rationale":         "<1-2 sentence plain-English hook>",
  "explanation":       "<4-6 sentence structured walkthrough>",
  "source_breakdown":  {
    "markov":           <float>,
    "regime":           <float>,
    "pattern":          <float>,
    "timesfm":          <float>,
    "calibration_bias": "<up|down|none>",
    "ev":               <float>,
    "kelly_f":          <float>
  },
  "prompt_tokens":     <int or null>,
  "completion_tokens": <int or null>
}
```

### Field rules

| Field               | Type    | Required | Length                | Notes |
|---------------------|---------|----------|-----------------------|-------|
| `rationale`         | string  | yes      | 1-2 sentences (~15-40 words)  | Plain English. Tooltip text. No jargon without context. |
| `explanation`       | string  | yes      | 4-6 sentences (~60-150 words) | Structured walkthrough. Names dominant source, calibration direction, EV sign, Kelly fraction. Every number from input. |
| `source_breakdown`  | object  | yes      | 4-7 keys (see below)          | Per-source P_win + calibration_bias + ev + kelly_f. Omit unavailable sources rather than fabricate. |
| `prompt_tokens`     | int     | no       | -                              | LLM cost tracking (nullable) |
| `completion_tokens` | int     | no       | -                              | LLM cost tracking (nullable) |

### Forbidden content in `rationale`

- Jargon without context: "regime", "Kelly", "EV", "P_win" — unless
  the term is immediately explained in the same sentence.
- Account numbers, API keys, IP addresses, user IDs.
- Phrases: "the model thinks", "we believe", "likely", "probably".
  State what the signal was, not what the model believed.
- Invented metrics. If `p_timesfm` is NULL, don't mention TimesFM.
- Forward-looking statements ("will continue", "is expected to").

### Forbidden content in `explanation`

- All of the above.
- Recommendations for parameter changes ("should increase kelly_f
  cap", "consider tightening stop"). The explanation is forensic; it
  describes what *was* decided, not what *should be* decided.
- Comparisons to other signals or trades. Each explanation is
  self-contained.
- Repeated content from `rationale`. The explanation builds on the
  rationale; it doesn't copy it.

## Cron model

```
# Nightly: generate explanations for yesterday's signals
noble explanation generate --date $(date -d 'yesterday' +%Y-%m-%d)

# Retry cron: backfill failed explanations from the last 7 days
noble explanation backfill --retry-failed \
  --start $(date -d '7 days ago' +%Y-%m-%d) \
  --end $(date -d 'yesterday' +%Y-%m-%d)
```

The agent owns these crons. The CLI commands are the entry points;
`SignalExplainer` (in `src/hermes/ops/signal_explainer.py`) is the
in-process service. The agent runtime passes its own inference router
to the `SignalExplainer` constructor as `skill_invoker`.

## References

- `references/signal_format.md` — schema for the input signal payload
- `references/blend_math.md` — how the 4-source log-odds pool combines
- `references/explanation_format.md` — output schema + worked examples
- `examples/winning_signal.md` — dominant-source long, full PnL attribution
- `examples/marginal_signal.md` — sub-0.55 p_win signal, regime-shift context
- `examples/regime_shift_signal.md` — signal emitted across a meta-regime transition
