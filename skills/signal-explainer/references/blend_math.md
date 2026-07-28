# Blend Math Reference

How the 4-source P_win blend combines into the final `p_win` written
to `signal_heartbeats`. This mirrors the implementation in the Noble
Trader backend's EV engine — the skill must narrate the blend the
agent already computed, not invent a different one.

## The 4 sources

Each source produces an independent P_win estimate in [0, 1]:

| Source     | Column on `signal_heartbeats` | What it measures |
|------------|-------------------------------|------------------|
| `regime`   | `p_regime`                    | Regime-conditional win probability — what fraction of historical entries in this regime tag hit +1R. |
| `markov`   | `p_markov`                    | Markov-chain win probability conditioned on the current Markov state. |
| `pattern`  | `p_pattern`                   | Pattern-matching win probability (Wilson lower bound on historical pattern matches). Agent-side override of the backend's placeholder 0.5. |
| `timesfm`  | `p_timesfm`                   | TimesFM foundation-model forecast implied P_win. NULL when TimesFM is unreachable. |

A source can be **unavailable** at signal time (NULL column, NOT
listed in `sources_used`). The blend skips unavailable sources and
re-normalizes the remaining weights — this is captured in
`weights_used`.

## Log-odds pool

The blend is a **log-odds weighted pool** (not a simple average).
Each source's P_win is converted to log-odds, weighted, summed, and
converted back:

```
logit(p_i)  = ln(p_i / (1 - p_i))         for each available source i
weight_i    = weights_used[i]              (renormalized to sum=1 over available sources)
logit_blend = Σ weight_i × logit(p_i)
p_win       = 1 / (1 + exp(-logit_blend))
```

The log-odds form means a single high-confidence source can pull the
blend toward 0 or 1, but cannot push past 0.99 or below 0.01 (the
logit saturates). The blend is **never** the arithmetic mean of the
four P_win values — the skill must not describe it as such.

## Calibration bias shrink

After the blend produces `p_win`, the calibration step adjusts it:

```
p_win_corrected = clamp(p_win - calibration_bias, 0.01, 0.99)
```

- `calibration_bias > 0` (overconfident): blend is shrunk *down*
  toward 0.5. The skill reports this as `calibration_bias: "down"`.
- `calibration_bias < 0` (underconfident): blend is shrunk *up*
  toward 0.5... no, shrunk *away* from 0.5 toward 1 (for buy signals)
  — the calibration_bias *subtracts* from p_win, so a negative bias
  *adds*. The skill reports this as `calibration_bias: "up"`.
- `calibration_bias = 0` or NULL: no adjustment. The skill reports
  `calibration_bias: "none"`.

The skill should reference `calibration_status` (`overconfident` /
`underconfident` / `calibrated`) when narrating this — it's the
human-readable label.

## EV computation

```
ev          = (p_win_corrected × reward) - ((1 - p_win_corrected) × risk)
ev_per_dollar = ev / risk
```

Where `reward` = take_profit distance × direction sign × size, and
`risk` = stop_loss distance × direction sign × size. The agent's
backend computes these from `entry_price`, `stop_loss`,
`take_profit`, and the position size.

- `ev > 0` → positive expected value (the signal is statistically
  worth taking).
- `ev < 0` → negative expected value. The signal may still be
  emitted if Kelly sizing shrinks the position to near-zero (the
  risk is small enough that the negative EV is tolerable for
  portfolio-diversification reasons).

## Kelly sizing

```
kelly_f         = ev / reward          (raw Kelly fraction)
effective_kelly = kelly_f × p_win_kelly_shrink × <agent soft-gate>
```

- `kelly_f` is the raw edge-over-odds Kelly fraction. Goes into
  `source_breakdown.kelly_f`.
- `effective_kelly` is the post-shrink Kelly actually used for
  position sizing. The shrink comes from `p_win_kelly_shrink`
  (server-side soft gate, e.g. 0.5 = half Kelly) and any agent-side
  soft gates (regime-conditional caps, tail-risk adjustments).

## What the skill should narrate

The `explanation` field should walk through these stages in order:

1. **Which sources were available** (reference `sources_used`).
2. **Which source dominated the blend** (the source whose weighted
   logit contribution was largest in absolute value). State its
   P_win and the direction it pulled the blend.
3. **The calibration_bias direction** (if non-zero). State that the
   blend was shrunk (up or down) and reference `calibration_status`.
4. **The EV sign and magnitude**. State whether EV was positive or
   negative and the absolute value.
5. **The Kelly fraction**. State `kelly_f` (raw) and
   `effective_kelly` (post-shrink); if `p_win_kelly_shrink < 1.0`,
   note the soft-gate shrink.

## What the skill must NOT do

- **Do not re-derive `p_win` from the four source P_win values.**
  The blend has already been computed; the skill reports it.
- **Do not invent `weights_used` if it's NULL.** State that the
  default equal-weight pool was used (this is the agent's behavior
  when `weights_used` is missing).
- **Do not invent `calibration_bias` if it's NULL.** Report
  `calibration_bias: "none"` and move on.
- **Do not invent Kelly soft-gate values.** If
  `p_win_kelly_shrink` is NULL, assume 1.0 (no server-side shrink)
  and state that explicitly.

## Cross-references

- `skills/signal-explainer/SKILL.md` — parent skill file
- `references/signal_format.md` — input payload schema
- `references/explanation_format.md` — output schema + worked examples
- Backend EV engine (not in this repo — the agent's blend is the
  source of truth as recorded on `signal_heartbeats`)
