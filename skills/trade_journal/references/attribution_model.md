# Attribution Model Reference

How to decompose PnL by regime / timing / direction when writing a
postmortem. This mirrors the implementation in
`noble-trader-agent/src/hermes/agent/attribution.py` — the LLM should not
invent a different decomposition; it should narrate the one the agent already
computes.

## The three PnL components

Every closed trade in `pnl_realized` has these three attribution columns
(populated by `agent/attribution.py::attribute_trade`):

| Column           | Meaning                                                                              | Sign convention |
|------------------|--------------------------------------------------------------------------------------|-----------------|
| `direction_pnl`  | PnL attributable to the **direction** of the trade (long/short call). Computed as the price drift over the hold period in the trade's direction, scaled by position size. | Positive = the direction call was correct. |
| `timing_pnl`     | PnL attributable to **entry timing** within the bar/brick. Computed as the difference between actual entry price and the brick-close benchmark, scaled by size. | Positive = entry was better than benchmark. Negative = entry was worse (chased). |
| `regime_pnl`     | PnL attributable to **regime persistence** — the difference between the actual hold-period return and the expected return for the trade's regime tag. | Positive = regime held and rewarded the setup. Negative = regime shifted against the trade. |

These three components plus fees / funding / slippage sum (approximately) to
`net_pnl`:

```
net_pnl ≈ direction_pnl + timing_pnl + regime_pnl
           - fees_total - funding_pnl_neg - slippage_cost
```

(Note: `funding_pnl` is stored as the funding paid/received; if positive, it
helped; if negative, it cost. The decomposition above treats funding as a
cost when negative.)

## How to use attribution in a postmortem

### Direction-dominant outcome

If `|direction_pnl|` > 60% of `|net_pnl|`:

- State that the outcome was direction-driven.
- If positive: the directional call was right. The postmortem should note
  *why* it was right (regime confidence, p_win, signal source strength) —
  but only if those metrics are in the payload.
- If negative: the directional call was wrong. Do not call it a "mistake" —
  state that the directional edge did not materialize and reference the
  signal-time p_win if it was below 0.50.

### Timing-dominant outcome

If `|timing_pnl|` > 30% of `|net_pnl|`:

- State that entry timing was the dominant factor.
- If positive: entry was favorable vs. benchmark. Note that the directional
  edge was small enough that timing mattered.
- If negative: entry was unfavorable (chased). This is a filter candidate —
  propose a hypothesis that gates future entries on timing PnL estimate.

### Regime-dominant outcome

If `|regime_pnl|` > 30% of `|net_pnl|`:

- State that regime behavior was the dominant factor.
- If positive: regime held and rewarded the setup as expected.
- If negative: regime shifted against the trade. Reference
  `regime_context.new_state` if a meta-regime transition occurred during
  the hold. If no transition is recorded, the shift was within-regime
  volatility — note this distinctly.

## What NOT to do

1. **Do not invent a fourth component.** If you can't decompose the PnL into
   direction + timing + regime + costs, say so explicitly.
2. **Do not assign blame to a config that wasn't active.** The trade record
   carries `config_hash`; if you reference a config setting, it must be in
   the payload's `signal_snapshot.config` sub-object.
3. **Do not use percentages without absolute numbers.** "Direction PnL was
   92% of the gain" is fine, but always pair it with the absolute number
   ("+$380.20 of +$412.50").
4. **Do not conflate `regime_at_close` with `regime_pnl`.** The first is a
   tag (a string); the second is a number (a PnL component). They are
   independent.

## Cross-references

- `agent/attribution.py` — the source-of-truth implementation
- `db/migrations/006_pnl_tables.sql` — schema for `pnl_realized` + the
  three attribution columns
- `references/hypothesis_format.md` — how to phrase attribution-driven
  hypotheses
