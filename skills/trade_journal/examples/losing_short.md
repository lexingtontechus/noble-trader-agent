# Example: Losing Short with Regime Shift

## Input payload

```json
{
  "signal_id": "HB-2026-07-22-Y",
  "symbol": "BTC-USD",
  "venue": "mt5_bridge",
  "direction": "short",
  "ts_emitted": "2026-07-22T14:00:00Z",
  "signal": {
    "p_win": 0.48, "ev": -0.02, "kelly_f": 0.12,
    "p_regime": 0.45, "p_markov": 0.50, "p_timesfm": 0.42, "p_pattern": 0.51,
    "meta_regime": "range"
  },
  "pnl": {
    "trade_id": "T-2026-07-22-CD34",
    "net_pnl": -280.40, "gross_pnl": -262.10, "r_multiple": -1.4,
    "hold_duration_sec": 480,
    "direction_pnl": -280.20, "timing_pnl": 18.40, "regime_pnl": -10.30,
    "fees_total": -8.20, "funding_pnl": 0, "slippage_cost": -10.10,
    "exit_reason": "stop_loss", "config_hash": "abc123",
    "regime_at_close": "up"
  },
  "existing_hypothesis": null,
  "postmortem_human": null
}
```

## Expected output

```json
{
  "postmortem_llm": "Short BTC-USD stopped at -1.4R after 8min when meta-regime shifted range→up (trigger price_momentum, confidence 0.71). Direction PnL was -$280.20 (the move against us); timing PnL was +$18.40 (entry was reasonable at signal time). The shift fired 90s after entry and was not in the signal-time snapshot — signal was placed under range-regime (p_win 0.48, ev -0.02, kelly 0.12). Exit was stop_loss.",
  "hypothesis": "Short BTC-USD betting that range-regime would persist ≥30min; signal-time ev was -0.02 (marginal), kelly 0.12 (small). Thesis was that the recent range high would hold; it broke 90s post-entry.",
  "prompt_tokens": 410,
  "completion_tokens": 195
}
```

## Notes on this example

- **Single hypothesis** that names the bet. v10 produces one per-signal
  thesis statement; v3's `hypotheses[]` array is removed.
- **Forensic, not prescriptive.** The postmortem states what happened
  (regime shift 90s post-entry) and what the bet was (range would
  persist). It does not propose a `range_regime_stability_score` gate
  or an `entry.ev_min_threshold_short` change — those were v3
  `proposed_change` patterns, removed in v10.
- **No blame on the operator or the strategy.** The loss is attributed
  to a regime transition + a marginal-EV signal — both observable in
  the data.
- **The hypothesis generalizes** — it doesn't just summarize the trade,
  it names the bet ("range would persist ≥30min") so a future reader
  can see at a glance what the trade was actually betting on.
