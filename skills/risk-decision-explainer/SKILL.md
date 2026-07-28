---
name: risk-decision-explainer
slug: risk-decision-explainer
version: 1.0.0
description: >
  Generates a 1-2 sentence rationale per §2.2 for each risk-decision
  (approved or rejected) made by `portfolio/risk_gate.py`. Output is
  FK-attached to the `risk_decisions` row and surfaced in the dashboard's
  "Why was this approved/blocked?" tooltip.
---

# Risk Decision Explainer Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `RiskGate.evaluate()` after a risk-decision is made (approve
or reject). Async — does not block the decision. The decision still
goes through; this skill attaches an explanation.

## Architecture

Reads the risk-decision from `risk_decisions` + the most recent
`account_snapshots` for context + any recent `circuit_breaker_events`
that may have influenced the decision. Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("why was this approved /
   blocked, in plain English"). This is the dashboard tooltip text.
2. `explanation` — 3-5 sentence structured walkthrough of which limits
   were checked, which hit, what the pre/post VaR was, and whether any
   circuit breakers were active.

## Scope

This skill ONLY:

- Generates explanations for **completed** risk-decisions (post-evaluation).
- Writes to `risk_decisions.reason_llm` + `risk_decisions.rationale_llm`
  (new columns, migration TBD).
- Reads from `risk_decisions`, `account_snapshots`,
  `circuit_breaker_events`.

This skill NEVER:

- Modifies the risk-decision itself. The decision is final.
- Modifies risk thresholds, autonomy tiers, or circuit breaker configs.
- Recommends specific trade actions.
- Touches the trade journal or hypotheses.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).

## Core Rules

1. **Output is post-decision.** The risk-decision has already been made.
   The explanation describes what was decided, not what should be.
2. **`rationale` is 1-2 sentences.** Plain English. "Approved $5k BTC
   long — within sizing limit, VaR remains at 1.8%." or "Blocked $20k
   SOL short — would breach sector concentration cap (SOL already 12%
   of book)."
3. **`explanation` is 3-5 sentences.** Names the limits checked, which
   hit, the pre/post VaR, and any active breakers. Every number from
   the input.
4. **Never assign blame to a config that wasn't active.**
5. **No PII, no account numbers.**

## Workflow

```
1. RiskGate.evaluate(signal) → decision (approved or rejected).
   Decision is written to risk_decisions.

2. Build payload:
     payload = {
       "risk_decision":      <row from risk_decisions>,
       "account_snapshot":   <latest account_snapshots>,
       "active_breakers":    <list of recent circuit_breaker_events>,
     }

3. Call skill_invoker(skills/risk-decision-explainer/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     UPDATE risk_decisions SET
       reason_llm = result["rationale"],
       rationale_llm = result["explanation"],
       explanation_status = 'generated'
     WHERE decision_id = ?;

5. On failure (skill_invoker raises or returns empty):
     UPDATE risk_decisions SET
       explanation_status = 'llm_unavailable'
     WHERE decision_id = ?
```

## Output Schema

```json
{
  "rationale":   "<1-2 sentence plain-English hook>",
  "explanation": "<3-5 sentence structured walkthrough>"
}
```

## References

(future)

## Examples

(future)
