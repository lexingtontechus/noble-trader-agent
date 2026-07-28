---
name: trade_journal
slug: trade_journal
version: 1.0.0
description: >
  Phase 1A composite skill: per-signal LLM postmortem + hypothesis +
  lessons-learned, written to the `trade_postmortem` table (1:1 with
  `trade_signals_blended`, keyed by `signal_id`). Hermes agent reads
  this file, queries DuckDB for signals needing postmortems, builds
  payloads, runs its own inference router, and UPDATEs the rows.
---

# Trade Journal Skill

## When to Use

The Hermes agent's nightly cron calls `noble journal generate --date
$(yesterday)`. The CLI dispatches to `TradeJournal.generate_postmortem_for_day()`,
which selects signals needing postmortems and invokes this skill once
per signal. A separate retry cron calls `noble journal backfill
--retry-failed --start <window> --end <window>` to retry signals where
the previous attempt failed (`postmortem_status='llm_unavailable'`).

The skill is **post-decision**. The signal has already been emitted,
the trade has already been closed (or is no longer active), and the
PnL attribution is already in `pnl_realized`. The skill explains what
happened and why; it does not second-guess the decision.

## Architecture

For each signal needing a postmortem:

1. Read the signal row from `trade_signals_blended` (entry/exit prices,
   stop, target, kelly, brick size, meta-regime, sizing, strategy).
2. Read the PnL attribution from `pnl_realized` (net_pnl, r_multiple,
   hold_duration_sec, direction_pnl, timing_pnl, regime_pnl, fees,
   funding, slippage, exit_reason, config_hash).
3. Read the existing `trade_postmortem` row (if any) for the signal —
   in particular `postmortem_human` (the trader's own note) and
   `hypothesis` (the pre-trade thesis, if set).
4. Build a payload combining all of the above.
5. Run the skill (the agent's own reasoning loop) against the payload.
6. UPDATE `trade_postmortem` with the result:
   - `postmortem_llm` — the generated postmortem text
   - `hypothesis` — only set if the existing value is NULL (preserve
     pre-trade thesis if the trader already entered one)
   - `postmortem_status` = `'generated'`
   - `postmortem_generated_at` = `now()`
   - `prompt_tokens`, `completion_tokens` — cost tracking (nullable)
7. On failure (skill raises, returns empty, or LLM is unavailable):
   UPDATE `trade_postmortem` with `postmortem_status='llm_unavailable'`.
   The next backfill run with `--retry-failed` will retry.

## Scope

This skill ONLY:

- Reads from `trade_signals_blended` (signal data) + `pnl_realized`
  (PnL attribution) + `trade_postmortem` (existing human note +
  hypothesis, if any).
- Writes to `trade_postmortem` (postmortem_llm, hypothesis,
  postmortem_status, postmortem_generated_at, prompt_tokens,
  completion_tokens, updated_at).
- Generates one row per `signal_id` (1:1 with `trade_signals_blended`).

This skill NEVER:

- Modifies the signal itself, the 4-source blend, calibration_bias,
  EV, Kelly, or any live trading parameter.
- Touches the existing `trade_journal` table (per-trade deterministic
  postmortems written by the orchestrator on position close — separate
  concern).
- Touches `trade_signals_blended`, `pnl_realized`, or any other
  upstream table.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).
- Publishes to any Redis channel or message bus.

## Retry contract

A row in `trade_postmortem` is "needs work" iff:

```sql
postmortem_status IS NULL
OR postmortem_status = 'llm_unavailable'
```

Rows where `postmortem_status IN ('generated', 'reviewed', 'skipped')`
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
| `reviewed`         | Trader acked the postmortem. Never overwritten.          |
| `skipped`          | Trader dismissed the postmortem. Never overwritten.      |

## Human-note contract

If `postmortem_human` is non-NULL (the trader entered a note via the
dashboard wizard), the skill MUST incorporate it into the generated
`postmortem_llm`. The trader's note is treated as ground-truth context
about what they were thinking at trade time; the postmortem should
reference it, expand on it, or note where it diverges from the
quantitative record.

The skill MUST NOT overwrite `postmortem_human`. The trader's note is
preserved verbatim in that column; the LLM-generated postmortem goes
in `postmortem_llm`.

## Hypothesis contract

If `hypothesis` is NULL (no pre-trade thesis was recorded), the skill
SHOULD generate one — a 1-2 sentence statement of what the trade was
betting on, derived from the signal + regime context. This becomes the
`hypothesis` column.

If `hypothesis` is non-NULL (the trader or a prior skill run already
set it), the skill MUST preserve it. The skill can reference it in
`postmortem_llm` but must not overwrite the column.

## Workflow

```
1. TradeJournal.generate_postmortem_for_day(date) SELECTs signals
   from trade_signals_blended JOIN pnl_realized LEFT JOIN
   trade_postmortem WHERE ts_emitted::DATE = date AND
   (postmortem_status IS NULL OR postmortem_status = 'llm_unavailable')
   AND postmortem_status NOT IN ('reviewed', 'skipped').

2. For each row, build payload:
     payload = {
       "signal_id":            <signal_id>,
       "symbol":               <symbol>,
       "venue":                <venue>,
       "direction":            <direction>,
       "ts_emitted":           <ts_emitted>,
       "signal": { ... trade_signals_blended fields ... },
       "pnl":     { ... pnl_realized fields ... },
       "existing_hypothesis":  <hypothesis or null>,
       "postmortem_human":     <trader note or null>,
     }

3. Call skill_invoker(skills/trade_journal/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router; TradeJournal
   is constructed with it by the caller — the CLI raises a clear
   error if it's None)

4. On success:
     UPDATE trade_postmortem SET
       postmortem_llm = result["postmortem_llm"],
       hypothesis = COALESCE(hypothesis, result["hypothesis"]),
       postmortem_status = 'generated',
       postmortem_generated_at = now(),
       prompt_tokens = result["prompt_tokens"],
       completion_tokens = result["completion_tokens"],
       updated_at = now()
     WHERE signal_id = ?;

5. On failure (exception, empty result, or LLM unavailable):
     UPDATE trade_postmortem SET
       postmortem_status = 'llm_unavailable',
       updated_at = now()
     WHERE signal_id = ?;
```

## Output Schema

```json
{
  "postmortem_llm":    "<2-4 sentences: market context, TP/SL lesson, regime mismatch>",
  "hypothesis":        "<1-2 sentences: pre-trade thesis, only if existing is NULL>",
  "prompt_tokens":     <int or null>,
  "completion_tokens": <int or null>
}
```

### Field rules

| Field               | Type    | Required | Length                | Notes |
|---------------------|---------|----------|-----------------------|-------|
| `postmortem_llm`    | string  | yes      | 2-4 sentences (~40-120 words) | Forensic. State what happened. No apologies. |
| `hypothesis`        | string  | no       | 1-2 sentences (~15-40 words)  | Only generate if existing `hypothesis` column is NULL. |
| `prompt_tokens`     | int     | no       | -                     | LLM cost tracking (nullable) |
| `completion_tokens` | int     | no       | -                     | LLM cost tracking (nullable) |

### Forbidden content in `postmortem_llm`

- Account numbers, API keys, IP addresses, user IDs.
- Phrases: "unfortunately", "regrettably", "mistakenly", "should have",
  "could have", "would have". State what happened, not what ought to have.
- Invented metrics. If the payload doesn't have a metric, don't mention it.
- Invented regime tags. Use exactly the `meta_regime` value from
  `trade_signals_blended`.

## Cron model

```
# Nightly: generate postmortems for yesterday's signals
noble journal generate --date $(date -d 'yesterday' +%Y-%m-%d)

# Retry cron: backfill failed postmortems from the last 7 days
noble journal backfill --retry-failed --start $(date -d '7 days ago' +%Y-%m-%d) --end $(date -d 'yesterday' +%Y-%m-%d)
```

The agent owns these crons. The CLI commands are the entry points;
`TradeJournal` (in `src/hermes/ops/trade_journal.py`) is the in-process
service. The agent runtime passes its own inference router to the
`TradeJournal` constructor as `skill_invoker`.

## References

- `references/postmortem_format.md` — output schema + worked examples
- `references/hypothesis_format.md` — hypothesis generation rules
- `references/attribution_model.md` — input fields from `pnl_realized`
- `examples/winning_long.md`, `examples/losing_short.md`,
  `examples/breakeven_range.md` — worked end-to-end examples
