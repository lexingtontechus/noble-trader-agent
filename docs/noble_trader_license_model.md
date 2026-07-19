# Noble Trader — License Model (design only)

Status: **plan / design.** No code. This document defines the license model for the
distributed Noble Trader agent. It replaces any earlier entitlement/license scaffolding
discussion; the canonical rule is below.

---

## 1. The model (canonical rule)

- **Subscription NEW or RENEWED** → the user's **Redis token** and **Git token** are
  **valid** (minted/refreshed by the upstream).
- **Subscription EXPIRED** → both tokens are **revoked** by the upstream.
- The **Git token is the license**. There is no separate license key, no tier, no HMAC
  license artifact. One credential = the license.
- The Git token is **long-lived** (recommend: no-expiry fine-grained GitHub PAT, scoped
  to the `noble-trader-agent` repo). Because it does not expire on its own, a long-lived
  token means **no change or impact to user actions or the big install/run flow** — the
  user installs once and runs until the subscription itself lapses.

**Single source of truth = subscription state.** Token validity is *derived from* the
subscription, not from a GitHub-side expiry date. The upstream revokes the GitHub token
at the GitHub side when the subscription lapses; the agent detects that as a normal 401.

---

## 2. Token lifecycle

```
 SUBSCRIBE / RENEW          SUBSCRIPTION LAPSES
        │                            │
        ▼                            ▼
  upstream mints:              upstream revokes:
   • Redis token               • Redis token  (drop from auth store)
   • Git token  (PAT)          • Git token     (GitHub: revoke token)
        │                            │
        ▼                            ▼
  tokens VALID                tokens REVOKED
  agent licensed              agent unlicensed (401 on Git, dead Redis stream)
        │                            │
        │                   user renews → upstream mints FRESH tokens
        ▼                            ▼
  user runs normally     user re-pastes new tokens, resumes
```

Redis + Git tokens are **coupled**: same lifecycle, bound to the subscription. The agent
treats them as one license state — if either is revoked, the tenant is unlicensed.

---

## 3. Upstream actions (where the real work is)

The upstream (Noble Trader platform / subscription system) owns issuance + revocation:

1. On **new / renewed** subscription: mint a **Redis token** (signal-source auth) and a
   **Git token** (fine-grained GitHub PAT, repo-scoped to `noble-trader-agent`, read +
   Issues write, long-lived / no-expiry).
2. Publish the agent as a **versioned package** to a private index (recommend GitHub
   Packages — reuses the Git token, adds no new system).
3. On **expiry**: **revoke both tokens** (GitHub token revoke API; Redis token drop).
4. On **renew after lapse**: issue **fresh** tokens (old revoked tokens stay dead).
5. Present the user a single install command containing the Git token + pinned version,
   plus the Redis token for the wizard.

The upstream does NOT need a license-server, Ed25519 signing, or revocation lists — plain
token mint/revoke is sufficient.

---

## 4. User actions

- **Install (once):** copy the install command from the site and run it:
  `pip install --index-url <private-index> --token <GIT_TOKEN> noble-trader-agent==<VERSION>`
- **First-run wizard (`platform setup`):** paste the **Git token** (the license) and the
  **Redis token**. Saved to `.env`.
- **Run:** agent operates normally; license = Git token present + authenticates.
- **On lapse:** signals stop (Redis dead) and license check warns. User **renews** →
  gets fresh tokens → re-pastes → resumes. No reinstall required.

Because the Git token is long-lived, the user **never re-pastes due to token expiry** —
only on (a) first install, (b) renew-after-lapse (fresh tokens), or (c) optional upstream
security rotation (not required by this model).

---

## 5. Agent behavior (consumer only — no orchestration of upstream)

- **Offline (startup):** Git token present → licensed. Instant, no network. Never blocks
  or bricks the stack.
- **Live check (`noble entitlement`, or optional periodic):** Git token authenticates to
  GitHub for the repo → licensed; **401 / revoked → unlicensed**.
- **Redis token dead** (no signals) → consistent with revoked state; surfaces as a
  signal-source error, not a separate license path.
- **On unlicensed:** warn + tell the user to renew / re-paste tokens. **Degrade
  gracefully** — keep running so the user can renew; do not brick.
- **No self-minting, no token rotation, no tier logic, no separate license key.**

---

## 6. Revocation detection (agent side, minimal)

| Signal | Meaning | Agent response |
|---|---|---|
| Git token 401 from GitHub | Token revoked (subscription lapsed) | Mark unlicensed; warn user to renew |
| Redis stream dead / auth reject | Redis token revoked | Signal-source error; consistent with unlicensed |
| Both present + Git authenticates | Licensed | Normal operation |

Detection is plain HTTP status — no polling infra, no shared revocation list.

---

## 7. Edge cases

- **Lapse mid-run:** agent keeps running; signals stop; license warning shown. User renews
  → pastes fresh tokens → resumes. No restart of the whole stack required (config reload
  picks up new `.env`).
- **Renew reuses vs issues fresh:** recommend **fresh on renew** so a lapsed user cannot
  keep using old tokens. Upstream decision.
- **User loses token:** re-run subscription retrieval on the site; re-paste. Agent has no
  recovery path (by design — upstream owns issuance).
- **Offline forever:** agent runs on "token present" assumption; live 401 only caught
  when network available. Acceptable for a long-lived-token model.

---

## 8. Open decisions (upstream-owned)

1. **Git token type / lifespan:** recommend fine-grained PAT, long-lived / no-expiry.
   (Your model assumes long-lived — confirm.)
2. **Private index:** recommend GitHub Packages (reuses Git token).
3. **Fresh-on-renew vs reuse:** recommend fresh.
4. **Agent live-check cadence:** startup-only vs periodic (e.g. daily). Startup-only is
   sufficient for a long-lived token; periodic catches mid-cycle revocation sooner.
5. **Graceful degrade vs hard-stop on lapse:** recommend degrade (keep running, warn).

---

## 9. Out of scope (explicitly not in this model)

- Tiers / per-feature gating.
- Separate HMAC/asymmetric license keys.
- License-server, revocation lists, key rotation.
- Auto-updater / plugin interface (separate design).
- Any agent-side token minting.

The agent is a **consumer of tokens the upstream issues and revokes**. That is the whole
license model.
