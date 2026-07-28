---
name: anomaly-explainer
slug: anomaly-explainer
version: 1.0.0
description: >
  Generates a 1-2 sentence rationale per §2.2 + a 3-5 sentence
  explanation for each anomaly event detected by
  `monitor/anomaly_detector.py`. Output is FK-attached to the
  `monitor_events` row and surfaced in the ops dashboard.
---

# Anomaly Explainer Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `AnomalyDetector.on_event()` when a price / volume / spread /
funding anomaly is detected. Async — does not block the detector. The
detector still fires the alert; this skill attaches an explanation.

## Architecture

Reads the anomaly event from `monitor_events` + the most recent
`account_snapshots` row for context. Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("what just happened, in
   plain English"). Surfaced as the alert's primary text.
2. `explanation` — 3-5 sentence walkthrough of what the anomaly is, what
   metric triggered it, what the historical baseline is, and what the
   likely implications are. Surfaced when the operator clicks the alert.
3. `severity_assessment` — `low` / `medium` / `high` based on the
   anomaly's deviation from baseline + the account's current exposure.

## Scope

This skill ONLY:

- Generates explanations for **detected** anomalies (post-trigger).
- Writes to `anomaly_explanations` (new table, FK → `monitor_events.event_id`).
- Reads from `monitor_events` + `account_snapshots` for context.

This skill NEVER:

- Modifies anomaly detection thresholds or triggers.
- Suppresses or delays an anomaly alert.
- Recommends specific trade actions (the operator decides).
- Touches the trade journal, hypotheses, or signal pipeline.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).

## Core Rules

1. **Output is post-trigger.** The anomaly has already fired. The
   explanation describes what was detected, not what should be done.
2. **`rationale` is 1-2 sentences.** Plain English. Name the symbol +
   the anomaly type. Examples: "BTC-USD tick velocity 4.2σ above 30-day
   baseline" or "ETH funding flipped negative -0.018% in 15min".
3. **`explanation` is 3-5 sentences.** Names the triggering metric, the
   baseline, the deviation, and the likely direction of impact. Every
   number from the input.
4. **`severity_assessment` is one of** `low` / `medium` / `high`. The
   operator uses this to triage.
5. **Never assign blame to a config that wasn't active.**
6. **No PII, no account numbers.**

## Workflow

```
1. AnomalyDetector.on_event(event) writes to monitor_events + fires alert.

2. Build payload:
     payload = {
       "anomaly_event": <row from monitor_events>,
       "account_snapshot": <latest account_snapshots row>,
     }

3. Call skill_invoker(skills/anomaly-explainer/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     INSERT INTO anomaly_explanations (
       event_id, rationale_llm, explanation_llm, severity_assessment,
       explanation_status, generated_at
     ) VALUES (
       ?, ?, ?, ?,
       'generated', now()
     )

5. On failure (skill_invoker raises or returns empty):
     INSERT INTO anomaly_explanations (
       event_id, explanation_status, generated_at
     ) VALUES (
       ?, 'llm_unavailable', now()
     )
```

## Output Schema

```json
{
  "rationale":   "<1-2 sentence plain-English hook>",
  "explanation": "<3-5 sentence structured walkthrough>",
  "severity_assessment": "low|medium|high"
}
```

## References

(future)

## Examples

(future)
