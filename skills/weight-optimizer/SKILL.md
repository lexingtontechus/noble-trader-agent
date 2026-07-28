---
name: weight-optimizer
slug: weight-optimizer
version: 1.0.0
description: >
  Proposes adjustments to the 4-source P_win blend weights (p_pattern,
  p_regime, p_markov_hold_n, p_timesfm) and the calibration_bias
  shrink factor, based on per-source attribution analysis over a trailing
  30-day window. Output is a GitHub-PR-ready diff against
  `config/default.yaml` + a rationale per §2.2. Operator approves /
  rejects via PR review — never auto-applied.
---

# Weight Optimizer Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by weekly cron `9bfe2464dc4d` (Saturday 04:00). Pulls the
trailing 30 days of `pnl_realized` + the corresponding `signal_heartbeats`
rows, decomposes PnL by source contribution, and proposes weight
adjustments. Output is a structured proposal that the operator reviews
via GitHub PR — **never auto-applied**.

## Architecture

1. Load 30-day `pnl_realized` joined to `signal_heartbeats` on
   `heartbeat_id` (when available).
2. For each source (markov / regime / pattern / timesfm), compute the
   attribution: how much of the PnL is explained by that source's
   p_win being on the right side of the trade.
3. Compare attribution to the current blend weight. If a source is
   under-weighted relative to its attribution share, propose an
   increase. If over-weighted, propose a decrease.
4. Compute the calibration_bias shrink factor: if recent realized
   win-rate has diverged from signal-time p_win, propose a shrink
   adjustment.
5. Generate the proposal: a diff against `config/default.yaml → ev_engine.*`
   weights + a 1-2 sentence rationale per §2.2.
6. Write to `weight_proposals` table + emit a GitHub PR via `noble weight
   propose` (operator approves via PR review).

## Scope

This skill ONLY:

- Proposes adjustments to blend weights + calibration_bias.
- Writes to `weight_proposals` table + emits a GitHub PR.
- Reads from `pnl_realized`, `signal_heartbeats`, `hermes_hypotheses`
  (live only), `config_history`.

This skill NEVER:

- Auto-applies weight changes. Operator review is mandatory.
- Touches the trade journal, risk decisions, or circuit breaker events.
- Modifies signal generation, decision tree params, or position sizing.
- Makes any external API call other than the GitHub API (for PR creation).
  Hermes executes this skill directly via its own reasoning loop. The GitHub
  API is the only external dependency, used solely for PR creation after
  Hermes has produced the proposal.
- References trades outside the trailing 30-day window.

## Core Rules

1. **Every proposal cites attribution.** "p_timesfm was under-weighted
   (0.25 vs. 0.34 attribution share over 30 days)" — not "we should
   trust timesfm more".
2. **`rationale` is 1-2 sentences** per §2.2 — the "why this proposal,
   in plain English" hook.
3. **Maximum weight change per proposal: ±0.05.** No radical re-weighting
   in a single week. If a source needs more, propose incrementally over
   multiple weeks.
4. **Calibration_bias shrink adjustment: max ±0.10 per proposal.**
5. **Never propose removing a source entirely.** Minimum weight per
   source = 0.05. If a source is genuinely broken, that's a separate
   deprecation discussion.
6. **Proposals are advisory.** The operator may merge, modify, or
   close without merge. Never auto-merge.
7. **Cite the trade count.** Proposals based on <30 trades in the
   window must include a low-confidence flag.
8. **No PII, no account numbers.**

## Workflow

```
1. Weekly cron 9bfe2464dc4d invokes:
     python -m hermes.analysis.weight_optimizer --window 30d

2. Load 30-day pnl_realized + matching signal_heartbeats.

3. Call skill_invoker(skills/weight-optimizer/SKILL.md, payload={
     "trades": [...],
     "current_weights": {"p_pattern": 0.30, "p_regime": 0.25,
                         "p_markov_hold_n": 0.20, "p_timesfm": 0.25},
     "current_calibration_bias": 0.05,
   }) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     INSERT INTO weight_proposals (
       proposal_id, ts, window_days, trade_count,
       proposed_weights, proposed_calibration_bias,
       rationale_llm, attribution_breakdown,
       proposal_status
     ) VALUES (
       ?, ?, ?, ?,
       ?, ?, ?, ?,
       'generated'
     )

     # Emit GitHub PR
     subprocess.run([
       "noble", "weight", "propose",
       "--proposal-id", proposal_id,
       "--auto-pr",
     ])

5. On failure (skill_invoker raises or returns empty):
     INSERT INTO weight_proposals (
       proposal_id, ts, window_days, trade_count,
       proposal_status
     ) VALUES (
       ?, ?, ?, ?,
       'llm_unavailable'
     )

     # No GitHub PR is emitted on failure — the operator can rerun the
     # weekly cron next week, or trigger manually once the inference
     # router is available again.
```

## Output Schema

```json
{
  "rationale": "<1-2 sentence hook for the PR description>",
  "proposed_weights": {
    "p_pattern":       <float>,
    "p_regime":        <float>,
    "p_markov_hold_n": <float>,
    "p_timesfm":       <float>
  },
  "proposed_calibration_bias": <float>,
  "attribution_breakdown": {
    "p_pattern":       {"attribution_share": <float>, "current_weight": <float>, "delta": <float>},
    "p_regime":        {"attribution_share": <float>, "current_weight": <float>, "delta": <float>},
    "p_markov_hold_n": {"attribution_share": <float>, "current_weight": <float>, "delta": <float>},
    "p_timesfm":       {"attribution_share": <float>, "current_weight": <float>, "delta": <float>}
  },
  "trade_count": <int>,
  "low_confidence": <bool>
}
```

## References

(future)
- `references/attribution_math.md` — how per-source attribution is computed
- `references/blend_weights.md` — current weights + historical changes

## Examples

(future)
