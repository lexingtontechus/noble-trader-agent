---
name: backtest-rigor-reviewer
slug: backtest-rigor-reviewer
version: 1.0.0
description: >
  Reviews each completed backtest run against the 6 statistical rigor
  checks (per backtest/statistics.py) and generates a 1-2 sentence
  rationale per §2.2 + a structured 3-5 sentence review. Output is
  FK-attached to the backtest run and surfaced in the dashboard's
  backtest review panel. Flags suspicious results (overfitting, sample
  size, regime bias) for operator attention.
---

# Backtest Rigor Reviewer Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked by `RigorChecker.run()` after a backtest completes (weekly cron
`38828550eca7` or operator-invoked). Reviews the backtest against the
6 rigor checks (sample size, multiple-comparison correction, regime
coverage, walk-forward, out-of-sample, parameter stability) and produces
a structured review.

## Architecture

Reads the backtest run from `backtest_runs` + the computed statistics
from `backtest_statistics` + any hypotheses that informed the run.
Produces:

1. `rationale` — 1-2 sentence hook per §2.2 ("does this backtest support
   promotion, in plain English"). Surfaced as the review's headline.
2. `review` — 3-5 sentence structured walkthrough:
   - Which rigor checks passed / failed.
   - Sample size assessment.
   - Regime coverage assessment.
   - Out-of-sample stability.
   - Recommendation: `promote_to_shadow` / `reject` / `needs_more_data`.
3. `flags` — list of specific concerns (e.g. "sample_size_low",
   "regime_bias_up", "param_instability").

## Scope

This skill ONLY:

- Reviews **completed** backtests (post-run).
- Writes to `backtest_reviews` (new table, FK → `backtest_runs.run_id`).
- Reads from `backtest_runs`, `backtest_statistics`, `hermes_hypotheses`.

This skill NEVER:

- Modifies the backtest itself or its statistics.
- Auto-promotes or auto-rejects hypotheses. The operator (or
  `promote_to_shadow` cron) does that.
- Touches live trading, risk decisions, or signal pipeline.
- Makes any external API call other than the agent's own inference
  router (which the agent owns and operates).

## Core Rules

1. **Output is post-run.** The backtest has already completed. The
   review describes what was found, not what should have been run.
2. **`rationale` is 1-2 sentences.** Plain English. Example: "Backtest
   supports shadow promotion — 4 of 6 rigor checks pass, sample size
   adequate (62 trades), but regime coverage is up-biased."
3. **`review` is 3-5 sentences.** Names each rigor check + pass/fail +
   why. Every number from the input.
4. **`flags` is a list of strings.** Use snake_case identifiers that the
   dashboard can render as badges. Examples: `sample_size_low`,
   `regime_bias_up`, `param_instability`, `oos_degradation`.
5. **Never auto-promote.** The recommendation is `promote_to_shadow` /
   `reject` / `needs_more_data` — the operator decides.
6. **Be skeptical.** A backtest that passes all 6 checks is rare; if
   one looks suspicious, flag it.
7. **No PII, no account numbers.**

## Workflow

```
1. RigorChecker.run(backtest_run_id) computes the 6 rigor checks +
   writes to backtest_statistics.

2. Build payload:
     payload = {
       "backtest_run":      <row from backtest_runs>,
       "statistics":        <row from backtest_statistics>,
       "rigor_checks":      <dict of check_name → pass/fail + detail>,
       "hypothesis":        <hermes_hypotheses row that prompted this backtest, if any>,
     }

3. Call skill_invoker(skills/backtest-rigor-reviewer/SKILL.md, payload) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

4. On success:
     INSERT INTO backtest_reviews (
       run_id, rationale_llm, review_llm, flags, recommendation,
       review_status, generated_at
     ) VALUES (
       ?, ?, ?, ?, ?,
       'generated', now()
     )

5. On failure (skill_invoker raises or returns empty):
     INSERT INTO backtest_reviews (
       run_id, review_status, generated_at
     ) VALUES (
       ?, 'llm_unavailable', now()
     )
```

## Output Schema

```json
{
  "rationale": "<1-2 sentence headline>",
  "review": "<3-5 sentence structured walkthrough>",
  "flags": ["<snake_case_flag>", "..."],
  "recommendation": "promote_to_shadow|reject|needs_more_data"
}
```

## References

(future)
- `references/rigor_checks.md` — the 6 checks + their pass/fail criteria
- `references/multiple_comparison.md` — Bonferroni / BH correction math

## Examples

(future)
