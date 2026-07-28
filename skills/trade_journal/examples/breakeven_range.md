# Example: Breakeven Exit

## Input payload

```json
{
  "signal_id": "HB-2026-07-22-Z",
  "symbol": "ETH-USD",
  "venue": "mt5_bridge",
  "direction": "long",
  "ts_emitted": "2026-07-22T15:00:00Z",
  "signal": {
    "p_win": 0.55, "ev": 0.05, "kelly_f": 0.10,
    "p_regime": 0.50, "p_markov": 0.52, "p_timesfm": 0.55, "p_pattern": 0.58,
    "meta_regime": "range"
  },
  "pnl": {
    "trade_id": "T-2026-07-22-EF56",
    "net_pnl": -2.10, "gross_pnl": 8.40, "r_multiple": 0.0,
    "hold_duration_sec": 1320,
    "direction_pnl": 8.40, "timing_pnl": -6.30, "regime_pnl": 0,
    "fees_total": -4.20, "funding_pnl": 0, "slippage_cost": -6.30,
    "exit_reason": "time_stop", "config_hash": "abc123",
    "regime_at_close": "range"
  },
  "existing_hypothesis": null,
  "postmortem_human": null
}
```

## Expected output

```json
{
  "postmortem_llm": "Long ETH-USD exited at breakeven via time_stop after 22min; +0.0R, -$2.10 net (fees $4.20 + slippage $6.30 against direction PnL +$8.40). Direction PnL was +$8.40 (small favorable drift in range-regime), timing PnL was -$6.30 (entry was 1.2 ATR from brick-close — chased). The marginal direction alpha was eaten by timing anti-alpha and costs; hold duration of 22min suggests the position was never conviction-grade.",
  "hypothesis": "Range-regime long ETH-USD on marginal signal (p_win 0.55, ev 0.05); thesis was that direction alpha would dominate timing anti-alpha. It did not — the trade exited at breakeven via time_stop.",
  "prompt_tokens": 395,
  "completion_tokens": 175
}
```

## Notes on this example

- **Breakeven ≠ no thesis.** The skill still generates a hypothesis —
  the per-signal thesis names what the trade was betting on (direction
  alpha dominating timing anti-alpha), even though the bet lost to
  costs. Breakeven trades are often the most informative because they
  reveal marginal-EV behavior.
- **Time-stop interaction.** Note that `exit_reason` is `time_stop` —
  the postmortem references the exit reason, not just the PnL.
- **No `proposed_change` recommendations.** v10 postmortems are
  forensic. The thesis describes the bet; it does not propose config
  edits like `entry.timing_pnl_min_threshold_long` or
  `position_management.time_stop_sec.range_low_conf` (those were v3
  patterns, removed).
- **No "should have" language.** The postmortem says "the trade was
  never conviction-grade" — that's a forensic statement about the
  signal quality, not a value judgment about the operator.
