# Example: Marginal Signal (TimesFM Unavailable)

This is a worked example for the `signal-explainer` skill. The input
payload is below; the expected output follows. The agent's inference
router should produce output that matches the structure and tone of
this example — but with the specific metrics and signal details from
the actual input.

## Scenario

A sub-0.55 p_win ETH-USD long signal where TimesFM was unreachable
at signal time, so the blend re-weighted the remaining three
sources. The signal is emitted but Kelly is shrunk hard by the
server-side soft gate (effective_kelly is 6% of raw). The trader
needs to understand why this signal was emitted at all given the
marginal p_win.

## Input payload

```json
{
  "heartbeat_id": "HB-2026-07-22-D4E5F6",
  "symbol": "ETH-USD",
  "signal": {
    "heartbeat_id": "HB-2026-07-22-D4E5F6",
    "symbol": "ETH-USD",
    "strategy_id": "noble_v5_renko",
    "signal": "buy",
    "ts_received": "2026-07-22T14:42:00Z",
    "entry_price": 3285.0,
    "stop_loss": 3262.0,
    "take_profit": 3328.0,
    "aggression": "passive",
    "brick_size": 8.0,
    "sl_bricks": 2.875,
    "tp_bricks": 5.375,
    "regime": "range",
    "regime_conf": 0.65,
    "regime_shift": false,
    "prev_regime": null,
    "shift_at": null,
    "shifts_24h": 4,
    "ev": 0.05,
    "ev_per_dollar": 0.022,
    "p_win": 0.54,
    "p_regime": 0.52,
    "p_imbalance": 0.50,
    "p_markov": 0.55,
    "p_pattern": 0.58,
    "p_timesfm": null,
    "ev_scale": 0.7,
    "timesfm_horizon": null,
    "markov_current_state": "range_high",
    "tail_risk_score": 0.41,
    "tail_risk_action": "shrink_50",
    "kelly_f": 0.18,
    "effective_kelly": 0.03,
    "sources_used": "[\"p_regime\",\"p_markov\",\"p_pattern\"]",
    "weights_used": "{\"p_regime\":0.33,\"p_markov\":0.40,\"p_pattern\":0.27}",
    "p_win_kelly_shrink": 0.30,
    "calibration_bias": 0.0,
    "calibration_status": "calibrated"
  },
  "regime_context": {
    "ts": "2026-07-22T14:30:00Z",
    "prev_state": "range",
    "new_state": "range",
    "confidence": 0.62,
    "posterior_probs": "{\"range\":0.62,\"up\":0.18,\"down\":0.15,\"trending_up\":0.03,\"trending_down\":0.02}",
    "trigger": "scheduled",
    "funding_rate_8h": 0.0024,
    "book_depth_percentile": 0.55,
    "spread_percentile": 0.48,
    "posterior_entropy": 1.42
  }
}
```

## Expected output

```json
{
  "rationale": "Long ETH-USD on a marginal range-regime signal (p_win 0.54); TimesFM was unreachable so the blend leaned on Markov (0.55) and pattern (0.58). Kelly shrunk hard to 0.03 — conviction is low.",
  "explanation": "Three of four P_win sources were available at signal time (TimesFM was unreachable — omitted from sources_used). The Markov source led the re-weighted pool (p_markov 0.55, weight 0.40), with pattern close behind (p_pattern 0.58, weight 0.27); regime was nearly neutral (p_regime 0.52, weight 0.33). The blend landed at p_win 0.54 — just above the 0.50 emission threshold. No calibration bias was applied (calibration_status: calibrated). EV is +0.05 per $1 of risk after the 0.7 ev_scale shrink (entry 3285, stop 3262, target 3328 — a tight 2.9-brick stop). Raw Kelly is 0.18 but the server-side soft gate shrunk it hard (p_win_kelly_shrink = 0.30) and tail_risk_action = shrink_50 — the tail_risk_score of 0.41 triggered the additional shrink, leaving effective_kelly at 0.03. The meta-regime was range-state at signal time with confidence 0.62 (entropy 1.42 — high uncertainty); funding was +0.0024% 8h (negligible).",
  "source_breakdown": {
    "markov": 0.55,
    "regime": 0.52,
    "pattern": 0.58,
    "calibration_bias": "none",
    "ev": 0.05,
    "kelly_f": 0.18
  },
  "prompt_tokens": 410,
  "completion_tokens": 240
}
```

## Notes on this example

- **TimesFM omitted from `source_breakdown`** because `p_timesfm`
  was NULL in the heartbeat. The skill does NOT fabricate a value.
- **The explanation calls out TimesFM's absence explicitly** — the
  trader needs to know why the blend leaned on Markov. This is the
  kind of context the rationale can't fit but the explanation must
  include.
- **Calibration direction is `"none"`** because `calibration_bias = 0.0`
  and `calibration_status = "calibrated"`. The explanation notes the
  absence explicitly rather than skipping it.
- **The Kelly shrink is the central story.** Raw Kelly 0.18 →
  effective 0.03 is a 6x shrink. The explanation traces it through
  `p_win_kelly_shrink` (0.30) and `tail_risk_action` (shrink_50)
  and `tail_risk_score` (0.41). Every number is from the payload.
- **`ev_scale` is mentioned** because it explains why EV is +0.05
  even though the stop/target ratio is favorable. The explanation
  connects the dots: ev_scale 0.7 × raw EV ≈ +0.07 → reported +0.05.
- **Meta-regime entropy (1.42) is referenced** as a high-uncertainty
  flag — the trader can see why the agent's soft gates kicked in.
- **No recommendations.** The explanation does not say "consider
  skipping signals when entropy > 1.4" or "the TimesFM outage should
  trigger a circuit breaker". It describes what was decided; future
  parameter changes are Phase 3 territory (weight-optimizer skill).
