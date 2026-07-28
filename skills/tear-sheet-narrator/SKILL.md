---
name: tear-sheet-narrator
slug: tear-sheet-narrator
version: 1.0.0
description: >
  Generates a 1-2 sentence rationale per §2.2 + a structured 4-6 paragraph
  narrative for each performance tear-sheet (operator-invoked). Output is
  FK-attached to the tear-sheet run id and rendered alongside the
  tear-sheet charts in the dashboard.
---

# Tear Sheet Narrator Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `TearSheet.generate()` when the operator runs
`noble tearsheet --window 30d` (or clicks "Generate tear sheet" in the
dashboard). Produces the narrative accompaniment to the standard
tear-sheet charts (equity curve, drawdown, return distribution, regime
breakdown, win/loss by symbol).

## Architecture

Reads the 30-day (or operator-specified) window of `pnl_realized` +
`account_snapshots` + `signal_heartbeats` + `trade_journal`. Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("what kind of month was
   this, in plain English"). Surfaced as the tear-sheet's headline.
2. `narrative` — 4-6 paragraph structured walkthrough:
   - Paragraph 1: headline performance (PnL, Sharpe, max DD, win rate).
   - Paragraph 2: regime breakdown (which regimes contributed / cost).
   - Paragraph 3: timing analysis (entry timing PnL distribution).
   - Paragraph 4: risk incidents (any breakers, any VaR breaches).
   - Paragraph 5: hypothesis progress (live hypotheses that contributed,
     rejected hypotheses that would have helped).
   - Paragraph 6: outlook (what to watch next period).

## Scope

This skill ONLY:

- Generates narrative for a **completed** tear-sheet run.
- Writes to `tear_sheet_narratives` (new table, FK → `tear_sheet_runs.run_id`).
- Reads from `pnl_realized`, `account_snapshots`, `signal_heartbeats`,
  `trade_journal`.

This skill NEVER:

- Modifies the tear-sheet computations themselves.
- Recommends specific weight changes (Phase 3 territory).
- Touches live trading, risk decisions, or circuit breakers.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).

## Core Rules

1. **`rationale` is 1-2 sentences.** Plain English summary of the period.
2. **`narrative` is 4-6 paragraphs.** Follow the structure in the
   Architecture section above. Every number from the input.
3. **Never invent metrics.** If a metric isn't in the payload, omit it
   rather than fabricating.
4. **Acknowledge both wins and losses.** A tear-sheet that only praises
   is not useful.
5. **Be specific about regimes.** "up-regime contributed +$2.1k over 12
   trades" — not "regimes were generally favorable".
6. **No PII, no account numbers.**

## Workflow

```
1. Operator runs: noble tearsheet --window 30d
   → TearSheet.generate() computes the standard metrics + charts.
   → Writes a row to tear_sheet_runs with the run_id + computed metrics.

2. Build payload:
     payload = {
       "tear_sheet_run": <row from tear_sheet_runs>,
       "trades":         <list of pnl_realized rows in window>,
       "snapshots":      <list of account_snapshots rows in window>,
       "signals":        <list of signal_heartbeats rows in window>,
       "journal":        <list of trade_journal rows in window>,
     }

3. Call skill_invoker(skills/tear-sheet-narrator/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     INSERT INTO tear_sheet_narratives (
       run_id, rationale_llm, narrative_llm,
       narrative_status, generated_at
     ) VALUES (
       ?, ?, ?,
       'generated', now()
     )

5. On failure (skill_invoker raises or returns empty):
     INSERT INTO tear_sheet_narratives (
       run_id, narrative_status, generated_at
     ) VALUES (
       ?, 'llm_unavailable', now()
     )
```

## Output Schema

```json
{
  "rationale": "<1-2 sentence headline>",
  "narrative": "<4-6 paragraph structured walkthrough>",
  "paragraphs": [
    "<headline performance>",
    "<regime breakdown>",
    "<timing analysis>",
    "<risk incidents>",
    "<hypothesis progress>",
    "<outlook>"
  ]
}
```

## References

(future)

## Examples

(future)
