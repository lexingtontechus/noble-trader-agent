---
name: eod-briefing
slug: eod-briefing
version: 1.0.0
description: >
  Generates a 1-2 sentence rationale per §2.2 + a structured end-of-day
  briefing (3-5 paragraphs) summarizing the day's trades, hypotheses
  generated, risk incidents, and tomorrow's outlook. Run after the
  trade-postmortem skill completes. Delivered via Hermes env to the
  operator's configured notification channels (Telegram / Discord).
---

# EOD Briefing Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `SelfLearningLoop.run_eod_briefing()` (new method, called
after `run_eod_analysis()` finishes). Produces a daily briefing
delivered to the operator via `AlertManager` (Telegram / Discord /
dashboard).

Runs in the EOD cron window (16:30 daily, after `trade-postmortem`).

## Architecture

Reads today's `trade_journal` entries + newly-proposed `hermes_hypotheses`
+ `account_snapshots` (start-of-day vs end-of-day) + `risk_decisions` +
`circuit_breaker_events` from today + `meta_regime_history` transitions
today. Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("what kind of day was it,
   in plain English"). Delivered as the briefing's headline.
2. `briefing` — 3-5 paragraph structured walkthrough:
   - Paragraph 1: P&L summary (realized PnL, win rate, R-multiple dist).
   - Paragraph 2: Notable trades (top winner + top loser, with the LLM
     rationale from `trade-postmortem`).
   - Paragraph 3: Hypotheses generated today (count + the most
     promising one).
   - Paragraph 4: Risk incidents (any breakers, any VaR breaches, any
     rejected signals).
   - Paragraph 5: Tomorrow's outlook (active meta-regimes, pending
     approvals, hypotheses entering shadow).

## Scope

This skill ONLY:

- Generates a **daily** EOD briefing (post-close, post-postmortem).
- Writes to `eod_briefings` (new table, one row per day).
- Reads from `trade_journal`, `hermes_hypotheses`, `account_snapshots`,
  `risk_decisions`, `circuit_breaker_events`, `meta_regime_history`.

This skill NEVER:

- Modifies any of the underlying records it reads.
- Recommends specific weight changes (Phase 3 territory).
- Touches live trading, signal generation, or risk decisions.
- Calls any external API other than the agent's own inference router
  (which the agent owns and operates).

## Core Rules

1. **`rationale` is 1-2 sentences.** Plain English headline. Example:
   "Up-day: +$820 realized over 7 trades (5W/2L), no risk incidents,
   one new sizing hypothesis proposed."
2. **`briefing` is 3-5 paragraphs.** Follow the structure in the
   Architecture section. Every number from the input.
3. **Acknowledge both wins and losses.** A briefing that only praises
   is not useful.
4. **Reference the LLM postmortem text** when discussing notable trades
   — pull the `rationale_llm` column from `trade_journal`, not just the
   PnL numbers.
5. **Be concrete about hypotheses.** "Hypothesis: increase kelly_f cap
   in up-regime when confidence ≥0.80" — not "we made a sizing
   hypothesis".
6. **No PII, no account numbers.**

## Workflow

```
1. SelfLearningLoop.run_eod_analysis() finishes (trade-postmortem skill
   has run for all today's trades).

2. SelfLearningLoop.run_eod_briefing() invoked.

3. Build payload:
     payload = {
       "date":              <today's date>,
       "trades":            <today's trade_journal rows>,
       "hypotheses":        <today's hermes_hypotheses rows>,
       "start_snapshot":    <first account_snapshots row of the day>,
       "end_snapshot":      <last account_snapshots row of the day>,
       "risk_decisions":    <today's risk_decisions rows>,
       "breaker_events":    <today's circuit_breaker_events rows>,
       "regime_transitions": <today's meta_regime_history rows>,
     }

4. Call skill_invoker(skills/eod-briefing/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

5. On success:
     INSERT INTO eod_briefings (
       date, rationale_llm, briefing_llm,
       briefing_status, generated_at
     ) VALUES (
       ?, ?, ?,
       'generated', now()
     )

     AlertManager.notify(result["rationale"] + "\n\n" + result["briefing"])

6. On failure (skill_invoker raises or returns empty):
     INSERT INTO eod_briefings (
       date, briefing_status, generated_at
     ) VALUES (
       ?, 'llm_unavailable', now()
     )
```

## Output Schema

```json
{
  "rationale": "<1-2 sentence headline>",
  "briefing":  "<3-5 paragraph structured walkthrough>",
  "paragraphs": [
    "<P&L summary>",
    "<notable trades>",
    "<hypotheses generated>",
    "<risk incidents>",
    "<tomorrow's outlook>"
  ]
}
```

## References

(future)

## Examples

(future)
