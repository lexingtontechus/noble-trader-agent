# Postmortem Format Reference

The `trade_journal` skill produces a JSON object that the
`TradeJournal` service writes to the `trade_postmortem` table. The
skill_invoker callable (injected by the agent runtime) returns the
parsed JSON; `TradeJournal.generate_postmortem_for_day()` performs
the UPDATE.

There is no separate "JSON-block parser" module in v10 — the agent's
own inference router is responsible for producing well-formed JSON. If
the router returns malformed output, the skill_invoker raises and
`TradeJournal` writes `postmortem_status='llm_unavailable'` (the
next `noble journal backfill --retry-failed` run will retry).

## Schema

```json
{
  "postmortem_llm":    "<string, 2-4 sentences, REQUIRED>",
  "hypothesis":        "<string, 1-2 sentences, OPTIONAL>",
  "prompt_tokens":     <int or null>,
  "completion_tokens": <int or null>
}
```

### Field rules

| Field               | Type    | Required | Length                       | Notes |
|---------------------|---------|----------|------------------------------|-------|
| `postmortem_llm`    | string  | yes      | 2-4 sentences (~40-120 words) | Forensic. State what happened. No apologies. |
| `hypothesis`        | string  | no       | 1-2 sentences (~15-40 words)  | Only generate if the existing `hypothesis` column on `trade_postmortem` is NULL. See `hypothesis_format.md`. |
| `prompt_tokens`     | int     | no       | -                            | Cost tracking. Null if the router doesn't report. |
| `completion_tokens` | int     | no       | -                            | Cost tracking. Null if the router doesn't report. |

### Forbidden content in `postmortem_llm`

- Account numbers, API keys, IP addresses, user IDs (even if in payload).
- Phrases: "unfortunately", "regrettably", "mistakenly", "should have",
  "could have", "would have". State what happened, not what ought to have.
- Invented metrics. If the payload doesn't have a metric, don't mention it.
- Invented regime tags. Use exactly the `meta_regime` value from
  `trade_signals_blended`.
- v3-style `proposed_change` recommendations or backtestable claims.
  The v10 postmortem is forensic, not prescriptive.

## What's NOT in the v10 output (was in v3)

The following v3 fields are removed in v10. The skill MUST NOT produce
them:

| Removed field          | Why removed |
|------------------------|-------------|
| `postmortem`           | Renamed to `postmortem_llm` (clearer; the column on `trade_postmortem` is `postmortem_llm`). |
| `rationale`            | Folded into `postmortem_llm`. The 2-4 sentence postmortem should already explain "why this matters". |
| `lessons`              | Removed. Per-trade lessons list was a v3 concept; v10 keeps the per-trade thesis in `hypothesis` and lets the operator / future skills derive cross-trade lessons from the postmortem corpus. |
| `hypotheses[]`         | Removed. v10 has a single `hypothesis` per signal (per-trade thesis), not a list of testable claims. |
| `hypotheses[].proposed_change` | Removed. No `HypothesisTracker`, no backtest promotion pipeline, no config-key validation. |

## Worked examples (5)

### Example 1 — Profitable long in up-regime

Input payload (abbreviated):

```json
{
  "signal_id": "HB-2026-07-22-X",
  "symbol": "BTC-USD",
  "direction": "long",
  "signal": {
    "ts_emitted": "2026-07-22T13:30:00Z",
    "p_win": 0.62, "ev": 0.18, "kelly_f": 0.24,
    "meta_regime": "up"
  },
  "pnl": {
    "net_pnl": 412.50, "r_multiple": 2.1, "hold_duration_sec": 1830,
    "direction_pnl": 380.20, "timing_pnl": 32.30, "regime_pnl": 0,
    "fees_total": -8.40, "funding_pnl": -2.10, "slippage_cost": -5.10,
    "exit_reason": "target_hit", "config_hash": "abc123"
  },
  "existing_hypothesis": null,
  "postmortem_human": null
}
```

Output:

```json
{
  "postmortem_llm": "Long BTC-USD captured +2.1R over 30min in up-regime (p_win 0.62, ev 0.18, kelly 0.24); direction PnL contributed 92% of the +$412.50 gain, timing added +$32.30. Fees ($8.40) + funding (-$2.10) + slippage ($5.10) cost 15.6 bps of gross. Exit was target_hit; the meta-regime tag at close matched the signal-time call.",
  "hypothesis": "Long BTC-USD betting on up-regime continuation; meta-regime confidence was 0.78 and the range→up transition fired pre-entry. Expected hold 30-60min, target +2R at the brick-projected level.",
  "prompt_tokens": 420,
  "completion_tokens": 180
}
```

### Example 2 — Losing short in regime shift

```json
{
  "postmortem_llm": "Short BTC-USD stopped at -1.4R after 8min when meta-regime shifted range→up (trigger price_momentum, confidence 0.71). Direction PnL was -$280.20 (the move against us); timing PnL was +$18.40 (entry was reasonable at signal time). The shift fired 90s after entry and was not in the signal-time snapshot — signal was placed under range-regime (p_win 0.48, ev -0.02, kelly 0.12). Exit was stop_loss.",
  "hypothesis": "Short BTC-USD betting that range-regime would persist ≥30min; signal-time ev was -0.02 (marginal), kelly 0.12 (small). Thesis was that the recent range high would hold; it broke 90s post-entry.",
  "prompt_tokens": 410,
  "completion_tokens": 195
}
```

### Example 3 — Breakeven exit

```json
{
  "postmortem_llm": "Long ETH-USD exited at breakeven via time_stop after 22min; +0.0R, -$2.10 net (fees $4.20 + slippage $6.30 against direction PnL +$8.40). Direction PnL was +$8.40 (small favorable drift in range-regime), timing PnL was -$6.30 (entry was 1.2 ATR from brick-close — chased). The marginal direction alpha was eaten by timing anti-alpha and costs; hold duration of 22min suggests the position was never conviction-grade.",
  "hypothesis": "Range-regime long ETH-USD on marginal signal (p_win 0.55, ev 0.05); thesis was that direction alpha would dominate timing anti-alpha. It did not — the trade exited at breakeven via time_stop.",
  "prompt_tokens": 395,
  "completion_tokens": 175
}
```

### Example 4 — Profitable short with funding tailwind

```json
{
  "postmortem_llm": "Short SOL-USD captured +1.6R over 45min in down-regime (p_win 0.58, ev 0.15, kelly 0.20); direction PnL was +$310, funding contributed +$18.40 over the hold. Funding tailwind is consistent with the down-regime tag — funding flipped negative 2h prior to entry. Funding contribution (5.9% of gross) is meaningful but not dominant; direction remains the primary edge. Exit was target_hit.",
  "hypothesis": "Short SOL-USD in down-regime with negative funding tailwind (funding flipped -2h prior). Betting that funding + direction together would carry the trade to +1.5R within 45min.",
  "prompt_tokens": 430,
  "completion_tokens": 188
}
```

### Example 5 — Loss with stale config

```json
{
  "postmortem_llm": "Long XAU-USD stopped at -1.1R after 14min. Entry was sized at kelly_f=0.20 per config_hash abc123, but p_win at signal time was 0.42 (below the 0.50 threshold the current config would skip). The config used was 6h stale relative to the latest calibration_bias update. Exit was stop_loss. Loss is attributable to a stale config; the signal would have been skipped under the current calibration.",
  "hypothesis": "Long XAU-USD taken under a stale config (6h lag); signal-time p_win was 0.42, below the 0.50 skip threshold the current config would enforce. Thesis was that the prior calibration still applied; it did not.",
  "prompt_tokens": 415,
  "completion_tokens": 170
}
```
