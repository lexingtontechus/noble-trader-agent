---
name: breaker-narrator
slug: breaker-narrator
version: 1.0.0
description: >
  Generates a 1-2 sentence rationale per §2.2 + a 3-5 sentence narrative
  for each circuit breaker trigger. Output is FK-attached to the
  `circuit_breaker_events` row (in the `payload->narrative_llm` JSON
  field) and surfaced in the dashboard's breaker log.
---

# Breaker Narrator Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `CircuitBreaker.trigger()` when a volatility / risk /
kill-switch breaker fires. Async — does not block the breaker action.
The breaker still trips; this skill attaches a narrative.

## Architecture

Reads the breaker event from `circuit_breaker_events` + the most recent
`account_snapshots` for context. Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("what just tripped, in
   plain English"). Surfaced as the alert's primary text + delivered via
   `AlertManager` to Telegram / Discord.
2. `narrative` — 3-5 sentence walkthrough of what breaker tripped, what
   threshold was crossed, what the current value is, what action was
   taken, and what the operator should consider doing next (e.g. "wait
   for cooldown", "manually review positions", "no action — auto-recover").

## Scope

This skill ONLY:

- Generates narratives for **triggered** breakers (post-trip).
- Writes to `circuit_breaker_events.payload->narrative_llm` (JSON field).
- Reads from `circuit_breaker_events` + `account_snapshots`.

This skill NEVER:

- Modifies breaker thresholds, levels, or cooldowns.
- Suppresses or delays a breaker trip.
- Recommends specific trade actions beyond "wait for cooldown" / "review".
- Touches the trade journal, hypotheses, or signal pipeline.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).

## Core Rules

1. **Output is post-trip.** The breaker has already fired. The narrative
   describes what happened, not what should have.
2. **`rationale` is 1-2 sentences.** Plain English. Name the breaker type
   + the symbol (if applicable) + the threshold crossed. Example:
   "Volatility breaker L2 tripped for BTC-USD — 4h realized vol hit 4.8%
   vs 3.0% threshold."
3. **`narrative` is 3-5 sentences.** Names the breaker type, level,
   threshold, current value, action taken, and recommended operator
   response. Every number from the input.
4. **Never assign blame to a config that wasn't active.**
5. **No PII, no account numbers.**
6. **Never claim the breaker was wrong.** The breaker is deterministic;
   the LLM narrates it.

## Workflow

```
1. CircuitBreaker.trigger(event) writes to circuit_breaker_events +
   takes the configured action (pause entries / flatten positions / etc.).

2. Build payload:
     payload = {
       "breaker_event":    <row from circuit_breaker_events>,
       "account_snapshot": <latest account_snapshots>,
     }

3. Call skill_invoker(skills/breaker-narrator/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     UPDATE circuit_breaker_events
     SET payload = json_set(payload,
       '$.narrative_llm',   result["narrative"],
       '$.rationale_llm',   result["rationale"],
       '$.narrative_status', 'generated'
     )
     WHERE event_id = ?;

     # Also push the rationale via AlertManager
     AlertManager.notify(result["rationale"])

5. On failure (skill_invoker raises or returns empty):
     UPDATE circuit_breaker_events
     SET payload = json_set(payload,
       '$.narrative_status', 'llm_unavailable'
     )
     WHERE event_id = ?
```

## Output Schema

```json
{
  "rationale": "<1-2 sentence plain-English hook>",
  "narrative": "<3-5 sentence structured walkthrough>",
  "operator_action": "wait_for_cooldown|manual_review|no_action"
}
```

## References

(future)

## Examples

(future)
