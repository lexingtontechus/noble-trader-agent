# Example: Regime Shift Signal (Transition Mid-Window)

This is a worked example for the `signal-explainer` skill. The input
payload is below; the expected output follows. The agent's inference
router should produce output that matches the structure and tone of
this example — but with the specific metrics and signal details from
the actual input.

## Scenario

A SOL-USD short signal emitted 90 seconds after a meta-regime
transition fired (range → down, triggered by funding_flip). The
signal itself was emitted under the new "down" regime tag, but the
meta-regime snapshot at signal time still shows the transition
freshly. The trader needs to understand the role of the regime
shift in the signal's emission.

## Input payload

```json
{
  "heartbeat_id": "HB-2026-07-22-G7H8I9",
  "symbol": "SOL-USD",
  "signal": {
    "heartbeat_id": "HB-2026-07-22-G7H8I9",
    "symbol": "SOL-USD",
    "strategy_id": "noble_v5_renko",
    "signal": "sell",
    "ts_received": "2026-07-22T16:01:30Z",
    "entry_price": 178.20,
    "stop_loss": 181.05,
    "take_profit": 172.50,
    "aggression": "aggressive",
    "brick_size": 0.85,
    "sl_bricks": 3.35,
    "tp_bricks": 6.70,
    "regime": "down",
    "regime_conf": 0.81,
    "regime_shift": true,
    "prev_regime": "range",
    "shift_at": "2026-07-22T16:00:00Z",
    "shifts_24h": 3,
    "ev": 0.22,
    "ev_per_dollar": 0.077,
    "p_win": 0.66,
    "p_regime": 0.69,
    "p_imbalance": 0.62,
    "p_markov": 0.62,
    "p_pattern": 0.64,
    "p_timesfm": 0.59,
    "ev_scale": 1.0,
    "timesfm_horizon": "15m",
    "markov_current_state": "trending_down",
    "tail_risk_score": 0.28,
    "tail_risk_action": "proceed",
    "kelly_f": 0.31,
    "effective_kelly": 0.27,
    "sources_used": "[\"p_regime\",\"p_markov\",\"p_pattern\",\"p_timesfm\"]",
    "weights_used": "{\"p_regime\":0.35,\"p_markov\":0.25,\"p_pattern\":0.20,\"p_timesfm\":0.20}",
    "p_win_kelly_shrink": 0.87,
    "calibration_bias": -0.02,
    "calibration_status": "underconfident"
  },
  "regime_context": {
    "ts": "2026-07-22T16:00:00Z",
    "prev_state": "range",
    "new_state": "down",
    "confidence": 0.76,
    "posterior_probs": "{\"down\":0.76,\"range\":0.16,\"up\":0.04,\"trending_down\":0.03,\"trending_up\":0.01}",
    "trigger": "funding_flip",
    "funding_rate_8h": -0.0185,
    "book_depth_percentile": 0.68,
    "spread_percentile": 0.42,
    "posterior_entropy": 0.96
  }
}
```

## Expected output

```json
{
  "rationale": "Short SOL-USD 90s after a meta-regime range→down transition (trigger: funding flip to -0.0185% 8h). Regime source led the blend (p_regime 0.69); Kelly sized at 0.27 (raw 0.31).",
  "explanation": "All four P_win sources were available at signal time. The regime source dominated the log-odds pool (p_regime 0.69, weighted 0.35 — pulled the blend toward 0.66), supported by pattern (0.64) and Markov (0.62, current state trending_down). TimesFM was the most cautious (0.59). The blend was emitted 90s after a meta-regime transition (range→down at 16:00:00Z, trigger: funding_flip — funding flipped to -0.0185% 8h). Calibration bias was -0.02 (underconfident status), so the final p_win was adjusted up to 0.66. EV is +0.22 per $1 of risk (entry 178.20, stop 181.05, target 172.50 — a 2:1 RR). Raw Kelly is 0.31, shrunk to 0.27 by the server-side soft gate (p_win_kelly_shrink = 0.87). The meta-regime snapshot at signal time was down-state with confidence 0.76 (entropy 0.96 — moderate); the negative funding is a tailwind for the short.",
  "source_breakdown": {
    "markov": 0.62,
    "regime": 0.69,
    "pattern": 0.64,
    "timesfm": 0.59,
    "calibration_bias": "up",
    "ev": 0.22,
    "kelly_f": 0.31
  },
  "prompt_tokens": 420,
  "completion_tokens": 235
}
```

## Notes on this example

- **Calibration direction is `"up"`** because `calibration_bias = -0.02`
  (negative). The explanation uses "adjusted up" to match the
  direction — the calibration system found the blend was
  underconfident and pushed p_win up.
- **The regime shift is the central narrative.** The rationale leads
  with the 90s-post-shift timing; the explanation quantifies the
  shift (range→down at 16:00:00Z, funding_flip trigger, funding
  flipped to -0.0185% 8h). Every detail comes from
  `signal.regime_shift` + `signal.shift_at` + `regime_context`.
- **Funding tailwind called out.** Negative funding on a short is a
  tailwind (you receive funding payments). The explanation makes
  this connection explicit without inventing any numbers.
- **Dominant source is regime, not Markov** — unlike the
  `winning_signal` example. The explanation correctly identifies
  regime as the leader (weighted 0.35, P_win 0.69) rather than
  defaulting to Markov.
- **Aggressive aggression is noted in the entry** but not belabored
  in the explanation — it's a trader choice, not a blend output. The
  explanation stays focused on what the blend produced.
- **No "the shift caused the signal" framing.** The shift happened
  first; the signal was emitted under the new regime tag. The
  explanation describes the timing relationship factually without
  causal claims about what "caused" what.
- **No "should have waited" or "aggressive entry was a mistake".**
  The skill does not second-guess the signal. The entry aggression
  was a trader choice; the explanation respects it.
