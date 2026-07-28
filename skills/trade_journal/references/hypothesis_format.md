# Hypothesis Format Reference

The `hypothesis` field produced by the `trade_journal` skill is a single
short string written to the `hypothesis` column of the `trade_postmortem`
DuckDB table (1:1 with `trade_signals_blended`, keyed by `signal_id`).

There is **no separate hypothesis lifecycle** in Phase 1A v10. The
`hermes_hypotheses` table, `HypothesisTracker`, and the
`proposed → backtested → shadow → live` promotion pipeline have all been
removed. A hypothesis is now a per-signal thesis statement, written once
when the postmortem is generated and preserved thereafter.

## Schema

```json
{
  "hypothesis": "<string, 1-2 sentences, REQUIRED only if existing column is NULL>"
}
```

### Field rules

| Field        | Type   | Required                              | Length                  | Notes |
|--------------|--------|---------------------------------------|-------------------------|-------|
| `hypothesis` | string | only if existing `hypothesis` is NULL | 1-2 sentences (~15-40 words) | Pre-trade thesis: what the trade was betting on. Derived from signal + regime context. |

## What makes a hypothesis "good"

A good hypothesis names the bet the trade was making, in plain language,
grounded in the signal-time context. It is **not** a recommendation for
future parameter changes (that was the v3 `proposed_change` pattern —
removed in v10). It is **not** a backtestable claim with a metric (that
was the v3 `testable` requirement — also removed). It is a 1-2 sentence
statement of the trade thesis.

### Good

- "Long BTC-USD betting on up-regime continuation; meta-regime confidence
  was 0.78 and the range→up transition fired pre-entry. Expected hold
  30-60min, target +2R at the brick-projected level."

- "Short SOL-USD in down-regime with negative funding tailwind (funding
  flipped -2h prior). Betting that funding + direction together would
  carry the trade to +1.5R within 45min."

- "Range-regime breakeven filter candidate: long ETH-USD taken on
  marginal signal (p_win 0.55, ev 0.05). Thesis was that direction
  alpha would dominate timing anti-alpha; it did not."

### Bad

- "Be more careful in range-regime." — Vague; doesn't name what was
  being bet on.
- "Improve the strategy." — Not a thesis.
- "The market was random today." — Not what the trade was betting on.
- "Increasing kelly_f cap from 0.24 to 0.28 in up-regime when confidence
  ≥0.80 improves total PnL." — This is a v3-style proposed_change claim,
  not a per-signal thesis. Don't generate these in v10.

## Preservation contract

The skill MUST NOT overwrite a non-NULL `hypothesis` column. The
`UPDATE` statement uses `hypothesis = COALESCE(hypothesis, ?)` so the
existing value (whether set by the trader via the dashboard wizard or
by a prior skill run) wins.

If the existing `hypothesis` is NULL, the skill SHOULD generate one. If
the skill chooses not to (e.g., the trade was too short or too marginal
to support a clear thesis), it MAY return `hypothesis: null` in the
output — the column stays NULL and the next `--force` regeneration can
try again.

## Cross-references

- `skills/trade_journal/SKILL.md` § "Hypothesis contract" — the
  preservation rule and when to generate vs. skip
- `db/migrations/019_llm_postmortem.sql` — `trade_postmortem` table
  definition (the `hypothesis` column is TEXT, nullable)
- `references/attribution_model.md` — how to ground the thesis in PnL
  decomposition (direction_pnl, timing_pnl, regime_pnl)
