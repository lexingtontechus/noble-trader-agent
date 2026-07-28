---
name: narrative-classifier
slug: narrative-classifier
version: 1.0.0
description: >
  Classifies incoming news / filings / social narrative into a 0-1 score
  per symbol, blended with the existing 4-source P_win via a new
  `p_narrative` sub-weight. Hot-path-adjacent but fallback-safe: if Hermes
  is unavailable or the news feed is down, the blend silently drops back
  to the 4-source baseline (no regression). Per LLM-INTEGRATION-STRATEGY §2.
---

# Narrative Classifier Skill

> **Phase 1A v10 — scoped contract.** This skill is a forward-looking
> contract for Phase 1B/2/3. It is NOT yet implemented. The workflow
> below uses the v10 `skill_invoker` callable seam (constructor-injected
> by the agent runtime). When this skill is implemented, the caller
> service (mirroring `TradeJournal` in `src/hermes/ops/trade_journal.py`)
> will own the SELECT/INSERT/UPDATE plumbing; the skill_invoker owns the
> inference. See `LLM-INTEGRATION-STRATEGY.md` and the canonical
> `skills/trade_journal/SKILL.md` exemplar for the v10 contract.

## When to Use

Invoked continuously by `sources/narrative.py` (new module) as news items
arrive. Output is cached per symbol with a TTL (default 15 min) and fed
into `SignalSynthesizer` as a 5th source: `p_narrative`. The blend
becomes a 5-source log-odds pool when narrative data is fresh, and
silently falls back to the 4-source baseline when narrative data is stale
or unavailable.

**This is the ONLY Phase 2 skill that touches the hot path.** All other
skills are async / cron / operator-invoked.

## Architecture

For each incoming news item:

1. The adapter (`sources/narrative.py`) fetches the raw text + symbol
   tagging from the configured feed.
2. This skill classifies the item: produces a `p_narrative` score in
   [-1, +1] (negative = bearish narrative, positive = bullish), a
   `confidence` score in [0, 1], and a 1-sentence `rationale` per §2.2.
3. The score is cached per symbol with TTL (default 15 min). When
   `SignalSynthesizer` runs, it pulls the latest cached `p_narrative`
   for the symbol; if cache is fresh, blend as 5th source (weight
   configurable, default 0.10); if stale, drop to 4-source baseline.

## Scope

This skill ONLY:

- Classifies incoming narrative items into `p_narrative` + `confidence`
  + `rationale`.
- Writes to `narrative_signals` (new) and updates `signal_heartbeats.p_narrative`.
- Reads from the external feed + `signal_heartbeats` for context.

This skill NEVER:

- Modifies the 4-source blend weights directly. The blend adjustment is
  done by `SignalSynthesizer` based on the cached `p_narrative`.
- Decides whether to trade. That's `HermesDecisionTree` territory.
- Invents news items. If the feed is empty, return `p_narrative=0,
  confidence=0`.
- Touches the trade journal, hypotheses, or risk decisions.
- Makes any external API call other than the configured news feed adapter.
  Hermes executes this skill directly via its own reasoning loop. The news
  feed adapter is the only external dependency, used solely to fetch raw
  news items before Hermes classifies them.

## Core Rules

1. **`p_narrative` in [-1, +1].** Negative = bearish, positive = bullish,
   0 = neutral / unknown. Never outside this range.
2. **`confidence` in [0, 1].** 0 = no real signal in the text, 1 = clear
   directional language. Be conservative — most news is noise.
3. **`rationale` is 1 sentence** per §2.2. Quote a phrase from the source
   text if it supports the score.
4. **Cite the source.** Include `source_url` and `source_id` in the
   output so the operator can audit.
5. **Fallback-safe.** If the skill_invoker raises (LLM unavailable),
   the caller writes `classification_status='llm_unavailable'` to
   `narrative_signals` and does NOT update the cache. The synthesizer's
   4-source baseline takes over.
6. **No PII, no account numbers.**
7. **Hot-path-aware.** If the skill_invoker times out, it raises and
   the caller writes `classification_status='llm_unavailable'`. Never
   block a signal — the synthesizer always falls back to the 4-source
   baseline when the cache is stale.

## Workflow

```
1. sources/narrative.py receives a news item:
     { "source_id": "...", "symbol": "BTC-USD", "text": "...",
       "url": "...", "ts": "..." }

2. Call skill_invoker(skills/narrative-classifier/SKILL.md, payload=news_item) → result
   (skill_invoker is the agent's own inference router, injected by the
   caller — the service class constructor accepts it as a kwarg; the
   CLI raises a clear RuntimeError if it's None)

3. On success:
     INSERT INTO narrative_signals (
       source_id, symbol, p_narrative, confidence, rationale,
       classification_status, generated_at
     ) VALUES (
       ?, ?, ?, ?, ?,
       'generated', now()
     )

     narrative_cache.set(symbol, result, ttl=900)  # 15 min

4. On failure (skill_invoker raises or returns empty):
     INSERT INTO narrative_signals (
       source_id, symbol, classification_status, generated_at
     ) VALUES (
       ?, ?, 'llm_unavailable', now()
     )
     # narrative_cache is NOT updated — stale cache falls back to
     # the 4-source baseline (no regression to the blend).

5. SignalSynthesizer.emit(signal) reads narrative_cache.get(symbol):
     if cached and fresh:
       blend as 5th source (weight 0.10)
     else:
       blend as 4-source baseline (no regression)
```

## Output Schema

```json
{
  "p_narrative": <float in [-1, +1]>,
  "confidence":  <float in [0, 1]>,
  "rationale":   "<1 sentence, with quoted phrase from source>",
  "source_url":  "<string>",
  "source_id":   "<string>",
  "topics":      ["<string>", "..."]    // e.g. ["funding", "regulation", "macro"]
}
```

## References

(future)
- `references/scoring_rubric.md` — examples of p_narrative scores for
  various news types
- `references/blend_integration.md` — how p_narrative is folded into the
  4-source log-odds pool

## Examples

(future)
