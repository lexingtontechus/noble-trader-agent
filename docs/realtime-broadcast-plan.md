# Noble Trader — Realtime Broadcast Plan (Talaria)

Status: **Finalized scope** (2026-08-07) — 2 plans + 2 broadcasts.
Owner: noble-trader-agent (Talaria client plugin) + noble-trader-fastapi-backend (sweep publisher).
Related: Supabase Realtime limits — https://supabase.com/docs/guides/realtime/limits

---

## 1. Plans

| Plan | Symbol count | Notes |
|------|-------------|-------|
| **Signal Scout** | 10 | Entry tier — qualified signals for 10 symbols |
| **Precision Pro** | 20 | Adds paper-portfolio validation broadcast |
| **Basket** | mixed | **TREATED AS A PLAN ENTRY ONLY — backend signal engine TBD.** Reserve `plan_id`; no client work now. |

- Plans live in the `users` + `plans` tables of the **nobletradingapp** repo (external).
- `nt_symbol.plan_ids` (PostgREST) maps a plan to its symbols — the client resolves its symbol list at runtime from `nt_symbol?plan_ids=cs.{plan}`. No symbol list is baked into the plugin.

## 2. Broadcast channels (2 active)

| Channel | Content | Subscribed by |
|---------|---------|---------------|
| `signals` | Qualified signals: `{symbol, direction, kelly, entry_price, stop_loss, take_profit, ts}` | All plans (Signal Scout + Precision Pro) |
| `paper` | Paper-portfolio validation: position opened/closed, realized PnL, equity tick | Precision Pro only |

(Third channel `sweep` deferred — it was scoped for Basket/Elite; revisit when the Basket engine exists.)

## 3. Channel authorization (the paywall)

- `signals` → **public broadcast** (matches migration 107 anon RLS — signals are public-validation data).
- `paper` → **private broadcast**, RLS-gated by the user's plan claim.
- Enforcement is at **subscribe**, not publish: the sweep publishes both channels; the plugin's Realtime socket joins only the channels its plan claim authorizes.

## 4. Free-plan limits fit (validated)

| Limit (Free) | Budget | Our usage |
|---|---|---|
| Concurrent connections | 200 | 1 socket/client, **open-tab-only** (socket while Talaria dashboard active). At 500 subscribers, concurrent ≈ active users (~100–150) → fits. Hard cap ~180–190 concurrent; fallback = poll. |
| Channels per connection | 100 | 1–2 per client |
| Messages/sec | 100 | ~1–2 broadcasts / 5-min sweep ≈ single-digit ev/s |
| Broadcast payload | 256 KB | ~1–2 KB per signal |

**200 concurrent connections is the budget** (user decision: "200 is fine").
**The one wall:** connections are shared across ALL plans per project — if total concurrent sockets approach 200, either throttle (open-tab-only), cap subscribers, or move to Pro ($25/mo, 500 connections).

## 5. Client design (Talaria plugin)

1. **Plan claim in config** — provisioned at onboarding from the agent's user record (`users`/`plans` tables), stored in the plugin config; the plugin renders only what its claim authorizes.
2. **Symbol resolution** — `nt_symbol?plan_ids=cs.{plan}` → the 10/20 symbol list (anon-RLS compatible).
3. **Realtime socket** — open-tab-only; subscribe `signals` (all), `paper` (Pro); hot banner + paper section update live.
4. **Poll fallback** — existing 60s/5min poll continues when the socket is closed (dropped socket never blanks the dashboard).

## 6. Build order (Option-D incremental)

1. Plan claim plumbing + symbol resolution (Talaria step 1) — client side, no backend change.
2. Backend publisher: sweep → Realtime broadcast for `signals` + `paper` (fire-and-forget, same pattern as `log_renko_bricks`).
3. Migration (only if private `paper` channel needs RLS) — `user_plans` table + plan claim on Realtime authorization.
4. Verify: Realtime logs for `too_many_connections`; poll fallback keeps the dashboard alive.

## 7. Notes / decisions

- "Basket" is a **plan**, not a separate product/plugin — backend signal engine is TBD and out of scope here.
- One plugin artifact for all plans; gating is data-driven (plan_ids), not packaged plugins.
- The paper-vs-equal-weight widget shows a **relative** metric (`paper_minus_equal_wt`); realized PnL card is the **absolute** result — they answer different questions.
