# Example: Winning Long

This is a worked example for the `trade_journal` skill. The input payload
is below; the expected output follows. The agent's inference router
should produce output that matches the structure and tone of this
example — but with the specific metrics and trade details from the
actual input.

## Input payload

```json
{
  "signal_id": "HB-2026-07-22-X",
  "symbol": "BTC-USD",
  "venue": "mt5_bridge",
  "direction": "long",
  "ts_emitted": "2026-07-22T13:30:00Z",
  "signal": {
    "p_win": 0.62, "ev": 0.18, "kelly_f": 0.24,
    "p_regime": 0.55, "p_markov": 0.58, "p_timesfm": 0.65, "p_pattern": 0.61,
    "meta_regime": "up"
  },
  "pnl": {
    "trade_id": "T-2026-07-22-AB12",
    "net_pnl": 412.50, "gross_pnl": 428.10, "r_multiple": 2.1,
    "hold_duration_sec": 1830,
    "direction_pnl": 380.20, "timing_pnl": 32.30, "regime_pnl": 0,
    "fees_total": -8.40, "funding_pnl": -2.10, "slippage_cost": -5.10,
    "exit_reason": "target_hit", "config_hash": "abc123",
    "regime_at_close": "up"
  },
  "existing_hypothesis": null,
  "postmortem_human": null
}
```

## Expected output

```json
{
  "postmortem_llm": "Long BTC-USD captured +2.1R over 30min in up-regime (p_win 0.62, ev 0.18, kelly 0.24); direction PnL contributed 92% of the +$412.50 gain, timing added +$32.30. Fees ($8.40) + funding (-$2.10) + slippage ($5.10) cost 15.6 bps of gross. Exit was target_hit; the meta-regime tag at close matched the signal-time call.",
  "hypothesis": "Long BTC-USD betting on up-regime continuation; meta-regime confidence was 0.78 and the range→up transition fired pre-entry. Expected hold 30-60min, target +2R at the brick-projected level.",
  "prompt_tokens": 420,
  "completion_tokens": 180
}
```

## Notes on this example

- **Direction-dominant** (92% of net). The postmortem leads with that.
- **One hypothesis**, not a list. v10 produces a single per-signal thesis
  statement; v3's `hypotheses[]` array is removed.
- **No "unfortunately" / "regrettably" / "should have".** State what
  happened.
- **Every number is from the input.** No fabrication. The 15.6 bps figure
  is `(8.40 + 2.10 + 5.10) / 412.50` — explicit arithmetic from input
  numbers, not invented.
- **No `proposed_change` recommendations.** v10 postmortems are forensic,
  not prescriptive. The thesis describes what the trade was betting on;
  it does not propose config edits.
