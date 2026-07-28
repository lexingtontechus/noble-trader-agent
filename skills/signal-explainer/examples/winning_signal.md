# Example: Winning Signal (Dominant Markov Source)

This is a worked example for the `signal-explainer` skill. The input
payload is below; the expected output follows. The agent's inference
router should produce output that matches the structure and tone of
this example — but with the specific metrics and signal details from
the actual input.

## Scenario

A clean BTC-USD long signal where all 4 sources are available, the
Markov source dominates the blend, calibration is mild (+0.03
overconfident shrink), and Kelly is shrunk by the server-side soft
gate. The trader should be able to read the rationale in 3-5 seconds
and the explanation in 15-20 seconds.

## Input payload

```json
{
  "heartbeat_id": "HB-2026-07-22-A1B2C3",
  "symbol": "BTC-USD",
  "signal": {
    "heartbeat_id": "HB-2026-07-22-A1B2C3",
    "symbol": "BTC-USD",
    "strategy_id": "noble_v5_renko",
    "signal": "buy",
    "ts_received": "2026-07-22T13:30:00Z",
    "entry_price": 67250.0,
    "stop_loss": 66800.0,
    "take_profit": 68100.0,
    "aggression": "mid",
    "brick_size": 75.0,
    "sl_bricks": 6.0,
    "tp_bricks": 12.0,
    "regime": "up",
    "regime_conf": 0.78,
    "regime_shift": false,
    "prev_regime": null,
    "shift_at": null,
    "shifts_24h": 2,
    "ev": 0.18,
    "ev_per_dollar": 0.040,
    "p_win": 0.62,
    "p_regime": 0.58,
    "p_imbalance": 0.55,
    "p_markov": 0.71,
    "p_pattern": 0.62,
    "p_timesfm": 0.55,
    "ev_scale": 1.0,
    "timesfm_horizon": "15m",
    "markov_current_state": "trending_up",
    "tail_risk_score": 0.22,
    "tail_risk_action": "proceed",
    "kelly_f": 0.28,
    "effective_kelly": 0.24,
    "sources_used": "[\"p_regime\",\"p_markov\",\"p_pattern\",\"p_timesfm\"]",
    "weights_used": "{\"p_regime\":0.25,\"p_markov\":0.30,\"p_pattern\":0.25,\"p_timesfm\":0.20}",
    "p_win_kelly_shrink": 0.86,
    "calibration_bias": 0.03,
    "calibration_status": "overconfident"
  },
  "regime_context": {
    "ts": "2026-07-22T13:15:00Z",
    "prev_state": "range",
    "new_state": "up",
    "confidence": 0.81,
    "posterior_probs": "{\"up\":0.81,\"range\":0.12,\"down\":0.04,\"trending_up\":0.02,\"trending_down\":0.01}",
    "trigger": "price_momentum",
    "funding_rate_8h": 0.0085,
    "book_depth_percentile": 0.72,
    "spread_percentile": 0.31,
    "posterior_entropy": 0.84
  }
}
```

## Expected output

```json
{
  "rationale": "Long BTC-USD on a Markov-state continuation (p_markov 0.71) in up-regime; the meta-regime shifted range→up 15min pre-signal. Kelly sized at 0.24 (raw 0.28, shrunk by the server-side soft gate).",
  "explanation": "All four P_win sources were available at signal time. The Markov source dominated the log-odds pool (p_markov 0.71, weighted 0.30 — pulled the blend toward 0.65). The pattern source agreed (p_pattern 0.62); regime was slightly above neutral (p_regime 0.58); TimesFM was the most cautious (p_timesfm 0.55). Calibration bias was +0.03 (overconfident status), so the final p_win was shrunk down to 0.62. EV is +0.18 per $1 of risk (entry 67250, stop 66800, target 68100). Raw Kelly is 0.28, shrunk to 0.24 by the server-side soft gate (p_win_kelly_shrink = 0.86). The meta-regime snapshot at signal time was up-state with confidence 0.81 (trigger: price_momentum 15min prior); funding was +0.0085% 8h (a small headwind for the long).",
  "source_breakdown": {
    "markov": 0.71,
    "regime": 0.58,
    "pattern": 0.62,
    "timesfm": 0.55,
    "calibration_bias": "down",
    "ev": 0.18,
    "kelly_f": 0.28
  },
  "prompt_tokens": 380,
  "completion_tokens": 210
}
```

## Notes on this example

- **Markov-dominant blend.** The explanation names Markov as the
  driver and quantifies its weight (0.30) and P_win (0.71). It does
  not say "Markov was the most important source" without numbers.
- **Calibration direction is `"down"`** because `calibration_bias > 0`
  means overconfident (shrink p_win down). The explanation uses the
  word "shrunk down" to match.
- **All four sources present** in `source_breakdown` because all
  four were non-NULL in the heartbeat.
- **Regime context integrated**, not just appended. The explanation
  references the 15min-pre-signal shift and the funding tailwind
  headwind, both from `regime_context`.
- **Numbers only from the input.** EV (+0.18), kelly_f (0.28),
  effective_kelly (0.24), p_win_kelly_shrink (0.86), funding
  (+0.0085%), confidence (0.81) — every figure traces back to the
  payload.
- **No forward-looking claims.** No "the signal should win" or "the
  trade is likely to hit target". The explanation describes what
  *was* decided; it does not predict what *will* happen.
- **No recommendations.** No "consider increasing Kelly in
  trending_up state" or "the calibration_bias suggests retraining".
  v10 explanations are forensic, not prescriptive.
