# Noble Trader — Realtime Broadcast Plan (Talaria)

Status: **Rescoped (2026-08-07)** — Option B claim-token plan claim. 2 plans + 2 broadcasts.
Owner: noble-trader-agent (Talaria client plugin) + noble-trader-fastapi-backend (sweep publisher) + nobletradingapp (claim mint + subscription truth).
Related: Supabase Realtime limits — https://supabase.com/docs/guides/realtime/limits

---

## 1. Plans

| Plan | Symbol count | Notes |
|------|-------------|-------|
| **Signal Scout** | 10 | Entry tier — qualified signals for 10 symbols |
| **Precision Pro** | 20 | Adds paper-portfolio validation broadcast |
| **Basket** | mixed | **TREATED AS A PLAN ENTRY ONLY — backend signal engine TBD.** Reserve `plan_id`; no client work now. |

- **Subscription truth = `subscriptions` table** (nobletradingapp Supabase): `user_id → plan_id`, status enum `pending/active/grace/expired/cancelled`, `current_period_end`, `grace_period_end`, `next_charge_url`. All live code (helio-webhook, renewal cron, all API routes) reads `subscriptions`.
- `user_subscriptions` is a **legacy orphan table** — exists in the live DB (verified 200), **zero references in all 4 repos**. Ignore it; candidate for a drop migration later. Do NOT wire Talaria to it.
- Live plan rows (verified via PostgREST): `signal_scout` `df980ef1-e41f-41db-9d04-2ad09da69626`, `precision_pro` `1b66e78e-e8d1-46b6-9887-b36e038131c5`, `basket_scalper` `479635b8-8d1f-40b2-9692-fd0118f72e7a`.
- `nt_symbol.plan_ids` is `UUID[]` (migration 032, FK-validated by trigger) — the client resolves its symbol list at runtime from `nt_symbol?plan_ids=cs.{plan_uuid}`. **The plan UUID comes from the server validation response, never from user input.**

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
- **Pre-gate (NEW):** the plugin must present a valid claim token before rendering anything (§5). Status routing gates the UI — an expired/cancelled subscription renders the paywall, not the dashboard. This is the *ongoing subscription security check*, and it runs server-side.

## 4. Free-plan limits fit (validated)

| Limit (Free) | Budget | Our usage |
|---|---|---|
| Concurrent connections | 200 | 1 socket/client, **open-tab-only** (socket while Talaria dashboard active). At 500 subscribers, concurrent ≈ active users (~100–150) → fits. Hard cap ~180–190 concurrent; fallback = poll. |
| Channels per connection | 100 | 1–2 per client |
| Messages/sec | 100 | ~1–2 broadcasts / 5-min sweep ≈ single-digit ev/s |
| Broadcast payload | 256 KB | ~1–2 KB per signal |

**200 concurrent connections is the budget** (user decision: "200 is fine").
**The one wall:** connections are shared across ALL plans per project — if total concurrent sockets approach 200, either throttle (open-tab-only), cap subscribers, or move to Pro ($25/mo, 500 connections).

## 5. Client design (Talaria plugin) — REVISED (Option B claim token)

1. **Plan claim = claim token. NO Clerk in the plugin, NO pasted plan slug/UUID/title.**
   - The portal (nobletradingapp, Clerk-authed web) mints a **high-entropy claim token** stored in a new `talaria_claims` table (`token`, `user_id`, `plan_id`, `expires_at`, `revoked_at`, `last_validated_at`). Minting allowed only while `subscriptions.status IN ('active','grace')`.
   - The plugin's Connect tab collects only three fields: **Supabase URL + public anon key + claim token**.
   - Why not Clerk in the plugin: the Clerk **secret cannot ship** in a distributed client, and obtaining a user JWT would require embedding Clerk's client SDK + publishable key + a sign-in webview — rejected. Option B keeps the plugin credential-free beyond a revocable token.
   - Why not a bare `user_id`: **spoofable** — the anon key is public, so anyone could send anyone's UUID. The token is the only thing the server will accept.

2. **Server-side validation — Supabase Edge Function `talaria-check`.**
   - Plugin sends `{token}` → function validates (exists, not revoked, not expired) → resolves `plan_id` → **re-checks the live `subscriptions` row** → returns `{ok, plan_slug, plan_uuid, sub_status, period_end, grace_end, next_charge_url}`.
   - The service role key stays server-side in the Edge Function env — never in the plugin.
   - The response's `plan_uuid` drives the symbol-list query and channel selection. The response's `sub_status` drives UI routing.

3. **Subscription status check cadence: on plugin load + every 24h** (user decision — NOT 60s; the 60s poll is for data fallback only, see §5.6). Routing:

   | `talaria-check` result | Render |
   |---|---|
   | Invalid / expired / revoked token | Connect screen (re-enter claim token) |
   | No subscription row | "Subscribe" CTA (pricing) |
   | `pending` | "Waiting for payment confirmation" (retry) |
   | `active` | **Talaria dashboard** |
   | `grace` | Dashboard + "renews {date}" banner (still entitled) |
   | `expired` / `cancelled` | **Paywall screen** with payment link (`next_charge_url` / pricing) |

4. **Symbol resolution** — `nt_symbol?plan_ids=cs.{plan_uuid}` (UUID from the `talaria-check` response, NOT pasted/derived client-side) → the 10/20 symbol list (anon-RLS compatible).

5. **Realtime socket** — open-tab-only; subscribe `signals` (all plans), `paper` (Pro only, decided by the server-returned plan); hot banner + paper section update live.

6. **Data poll fallback — UNCHANGED at 60s/5min** (the existing fallback when the socket is closed; a dropped socket never blanks the dashboard). The 24h cadence applies to the **subscription check only**, not to data refresh.

7. **Claim token lifecycle & security:**
   - High-entropy (≥32 hex chars), stored hashed server-side.
   - Revocable from the portal (revoke-all-devices); auto-revoke when the subscription leaves `active/grace` (cron sweep alongside `send-renewal-reminders`).
   - Token TTL (e.g. 30 days) vs subscription validity are separate: the 24h re-check catches an expired subscription inside a still-valid token. Re-mint from the portal when the token itself expires.
   - A stolen token = the user's own subscription until revoked — short TTL + revocation is the mitigation.

## 6. Build order (Option-D incremental — REVISED step 1)

1. **Claim plumbing (nobletradingapp):** `talaria_claims` migration + portal `POST /api/talaria-claim` (Clerk-protected; mints only for `active/grace`) + Supabase Edge Function `talaria-check` (+ revocation sweep).
2. **Talaria plugin (agent repo, id `talaria`):** Connect tab (URL + anon key + claim token), `talaria-check` call + 24h cadence, status routing, `signals` Realtime subscription, plan-gated symbol list, hot-signal banner + kelly histogram + 10-brick chart (components reused from the admin plugin). No backend sweep change needed for the client.
3. **Backend publisher:** sweep → Realtime broadcast for `signals` + `paper` (fire-and-forget, same pattern as `log_renko_bricks`).
4. **Verify:** Realtime logs for `too_many_connections`; poll fallback keeps the dashboard alive; revoked/expired token renders the paywall.

## 7. Notes / decisions

- **Clerk UX rejected for the plugin** (2026-08-07): the Clerk secret cannot be shared with a distributed client, and the JWT path would require Clerk client SDK + publishable key + sign-in webview inside the Hermes desktop app. **Option B claim token chosen:** server-minted, plugin holds only a revocable token, validation is a Supabase Edge Function. Clerk stays exactly where it is today: the web app (portal/pricing).
- "Basket" is a **plan**, not a separate product/plugin — `basket_scalper` UUID already seeded; backend signal engine TBD and out of scope here.
- One plugin artifact for all plans; gating is **data-driven (plan_ids) + server-validated (claim token)** — never packaged plugins, never client-trusted plan.
- The paper-vs-equal-weight widget shows a **relative** metric (`paper_minus_equal_wt`); realized PnL card is the **absolute** result — they answer different questions.
