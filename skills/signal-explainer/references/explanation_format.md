# Explanation Format Reference

The `signal-explainer` skill produces a JSON object that the
`SignalExplainer` service writes to the `signal_explanations` table.
The skill_invoker callable (injected by the agent runtime) returns
the parsed JSON; `SignalExplainer.generate_explanations_for_day()`
performs the INSERT/UPDATE.

There is no separate "JSON-block parser" module — the agent's own
inference router is responsible for producing well-formed JSON. If
the router returns malformed output, the skill_invoker raises and
`SignalExplainer` writes `explanation_status='llm_unavailable'` (the
next `noble explanation backfill --retry-failed` run will retry).

## Schema

```json
{
  "rationale":         "<string, 1-2 sentences, REQUIRED>",
  "explanation":       "<string, 4-6 sentences, REQUIRED>",
  "source_breakdown":  {
    "markov":           <float, OPTIONAL>,
    "regime":           <float, OPTIONAL>,
    "pattern":          <float, OPTIONAL>,
    "timesfm":          <float, OPTIONAL>,
    "calibration_bias": "<up|down|none>",
    "ev":               <float>,
    "kelly_f":          <float>
  },
  "prompt_tokens":     <int or null>,
  "completion_tokens": <int or null>
}
```

### Field rules

| Field               | Type    | Required | Length                | Notes |
|---------------------|---------|----------|-----------------------|-------|
| `rationale`         | string  | yes      | 1-2 sentences (~15-40 words) | Plain English tooltip text. No jargon without context. |
| `explanation`       | string  | yes      | 4-6 sentences (~60-150 words) | Structured walkthrough. Names dominant source, calibration direction, EV sign, Kelly fraction. Every number from input. |
| `source_breakdown`  | object  | yes      | 4-7 keys              | Per-source P_win + calibration_bias + ev + kelly_f. Omit unavailable sources rather than fabricate. |
| `prompt_tokens`     | int     | no       | -                     | Cost tracking. Null if the router doesn't report. |
| `completion_tokens` | int     | no       | -                     | Cost tracking. Null if the router doesn't report. |

### `source_breakdown` field rules

| Key                | Type    | Required                        | Notes |
|--------------------|---------|---------------------------------|-------|
| `markov`           | float   | only if `p_markov` was non-NULL | Direct copy of `signal.p_markov`. |
| `regime`           | float   | only if `p_regime` was non-NULL | Direct copy of `signal.p_regime`. |
| `pattern`          | float   | only if `p_pattern` was non-NULL | Direct copy of `signal.p_pattern`. |
| `timesfm`          | float   | only if `p_timesfm` was non-NULL | Direct copy of `signal.p_timesfm`. |
| `calibration_bias` | string  | yes                             | `"up"` if bias < 0, `"down"` if bias > 0, `"none"` if 0 or NULL. |
| `ev`               | float   | yes                             | Direct copy of `signal.ev`. |
| `kelly_f`          | float   | yes                             | Direct copy of `signal.kelly_f` (raw, NOT `effective_kelly`). |

## What makes a `rationale` "good"

A good rationale names **the bet** in plain English, anchored to one
or two metrics from the payload. It is **not** a quantitative
breakdown — that's `explanation`'s job.

### Good

- "Long BTC-USD on a strong up-regime call (p_regime 0.71) and a
  Markov-state continuation pattern. Kelly sized at 0.24 — conviction
  is meaningful but not maxed."
- "Short SOL-USD after TimesFM flipped bearish on the 15m horizon;
  the pattern source agrees. Negative funding (-0.012% 8h) is a
  tailwind on the short side."
- "Range-regime neutral on ETH-USD — the four sources disagree (p_regime
  0.52, p_markov 0.48, p_pattern 0.55, TimesFM unavailable). No
  edge; no position."

### Bad

- "The model thinks BTC will go up." — Vague; doesn't name the
  metric or the source.
- "Regime confidence is 0.78 and the blend produced p_win 0.62 with
  ev 0.18 and kelly 0.24, after a calibration bias shrink of 0.03."
  — This is the explanation, not the rationale. The rationale is the
  1-2 sentence hook.
- "Likely a strong long signal." — "Likely" is forbidden. State what
  the signal is, not what the model believes.
- "Buy BTC-USD now!" — Marketing tone, not analytical.

## What makes an `explanation` "good"

A good explanation walks through the four blend stages in order:
sources available → dominant source → calibration direction → EV
sign → Kelly fraction. Every number comes from the payload.

### Good

- "All four P_win sources were available at signal time. The Markov
  source dominated the log-odds pool (p_markov 0.71, weighted 0.30 —
  pulled the blend toward 0.65). The pattern source agreed (p_pattern
  0.62); TimesFM and regime were more cautious (0.55 and 0.58
  respectively). Calibration bias was +0.03 (overconfident), so the
  final p_win was shrunk down to 0.62. EV is +0.18 per $1 of risk;
  raw Kelly is 0.28, shrunk to 0.24 by the server-side soft gate
  (p_win_kelly_shrink = 0.86)."

### Bad

- "The signal was strong." — No metrics, no stages.
- "Markov was the main driver." — Names the source but doesn't
  quantify. What was p_markov? What was its weight?
- "EV was good and Kelly was reasonable." — "Good" and "reasonable"
  are not numbers.
- "The blend should produce a winner." — Forward-looking. Forbidden.
- "We recommend increasing the Kelly cap for up-regime signals."
  — Recommendation, not explanation. Forbidden.

## Forbidden content

### In `rationale`

- Jargon without context: "regime", "Kelly", "EV", "P_win" — unless
  the term is immediately explained in the same sentence.
- Account numbers, API keys, IP addresses, user IDs.
- Phrases: "the model thinks", "we believe", "likely", "probably".
- Invented metrics. If `p_timesfm` is NULL, don't mention TimesFM.
- Forward-looking statements ("will continue", "is expected to").

### In `explanation`

- All of the above.
- Recommendations for parameter changes ("should increase kelly_f
  cap", "consider tightening stop"). The explanation is forensic.
- Comparisons to other signals or trades. Each explanation is
  self-contained.
- Repeated content from `rationale`. The explanation builds on the
  rationale; it doesn't copy it.

## What's NOT in the output (compared to early drafts)

| Removed field          | Why removed |
|------------------------|-------------|
| `degraded`             | Removed. v10 has no degraded-mode machinery. The skill_invoker either returns a result dict or raises; `SignalExplainer` catches the raise and writes `explanation_status='llm_unavailable'`. |
| `skill_slug` / `skill_hash` / `schema_version` | Removed. v10 has no audit table; the `signal_explanations` row itself is the audit trail. |
| `hermes_session_id`    | Removed. Same reason. |
| `parsed`               | Removed. The result dict is flat — `rationale`, `explanation`, `source_breakdown` are top-level keys, not nested under `parsed`. |

## Worked examples (3)

See `examples/winning_signal.md`, `examples/marginal_signal.md`, and
`examples/regime_shift_signal.md` for end-to-end input → output
examples with notes on what makes each output good.

## Cross-references

- `skills/signal-explainer/SKILL.md` — parent skill file
- `references/signal_format.md` — input payload schema
- `references/blend_math.md` — how the 4-source log-odds pool combines
- `db/migrations/020_signal_explanations.sql` — `signal_explanations`
  table definition
