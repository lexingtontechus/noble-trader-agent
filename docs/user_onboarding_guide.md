# Noble Trader — User Onboarding Guide

> **Audience:** a new user booting the Hermes Quant stack for the first time.
> **Goal:** get from zero → a live, *constrained* account that is learning the
> platform and the agent, without blowing up on day one.

This guide is the single entry point for "how do I start." It is intentionally
**conservative** — every new account starts in **cold-start** mode, the tightest
risk envelope the stack has, and stays there until it has earned its way out.

---

## 0. Two onboarding processes (don't confuse them)

There are **two separate onboarding flows** — one for the **platform**, one for
**Hermes**. The user operates in Hermes first (agent CLI, natural-language queries);
the dashboard (portfolio, sims) is a secondary, familiar web view. The external
website is only the subscription + credential-copy frontend.

1. **Platform onboarding (external website).** You subscribe to a plan, create your
   platform account, and receive your entitlements. As part of this you are given the
   **Noble Trader Redis URL**, **TradingView API key**, and **MT4/MT5 bridge token** —
   the credentials Hermes needs. This flow lives on the external site (not in this repo).
2. **Hermes onboarding (first-run wizard).** You paste those copied credentials into
   Hermes's `/setup` wizard. Hermes writes them to `.env`, auto-generates auth secrets,
   auto-migrates its local DuckDB, and drops you into **cold-start**. This is the
   agent's first-run setup and is documented below.

> The hand-off is: **platform gives you the creds → you paste them into Hermes's
> wizard.** Hermes is where you then live and operate; the platform site is just where
> you signed up and grabbed the keys.

---

## 1. What "cold-start" means (read this first)

A brand-new account has **no proven track record**. The stack therefore constrains
it with hard caps that are *far* below the normal trading limits, so you learn the
agent's behavior on tiny size before ever risking real capital at scale.

| Control | Cold-start value | Normal tier-1 (after exit) |
|---|---|---|
| Max notional per trade | **$100** | $2,000 |
| Max position % of equity | **0.2%** | 1.5% |
| Concurrent new positions | **≤ 3** | (selection layer top-3/cycle) |
| Total new exposure | **≤ 5% of equity** | (account-level caps apply) |
| Human-approval gate | tier-1 auto (no human needed) | tier-3 needs approval |
| Unsupported venue | **hard-rejected at L5** | same |

These caps live in `config/default.yaml → autonomy.cold_start` and are **applied
automatically** — you do not toggle them. You cannot "accidentally" trade large on
day one.

### How you leave cold-start

Cold-start **auto-exits** when BOTH conditions are met (checked after each closed trade):

1. **Count:** ≥ `exit_after_n_trades` (default **20**) closed trades, **AND**
2. **Expectancy:** realized expectancy **> 0** (net positive).

Until then, the agent is capped, the selection layer only admits the **top 3** ranked
candidates per 5-minute cycle, and anything excess is **dropped** (not re-queued —
over-trading is a risk). This is by design: the platform is teaching you
and itself on small size.

> **Your job during cold-start:** watch. Read the snapshots. Watch the agent take a
> few tiny trades, see the approval/execution flow, and confirm the guardrails hold.
> Do **not** try to bypass the caps — that defeats the safety purpose and the normal
> limits are still modest ($2k / 1.5%) because most accounts are $5–10k, max $25k.

---

### 2.0 First-run wizard (recommended path)

The fastest way to onboard is the **web wizard** at `GET /setup` (daisyUI-styled).
It is launched as part of the CLI workflow via:

```bash
./.venv/Scripts/python.exe -m hermes.app setup            # serves wizard at http://127.0.0.1:8080/setup
./.venv/Scripts/python.exe -m hermes.app setup --print-url  # headless: prints URL + checklist only
```

Flow (this is the **Hermes** step — after platform signup gave you the creds):
1. On the **platform** site you subscribed and copied your **Noble Trader Redis URL**
   + **TradingView API key** (and MT4/MT5 bridge token).
2. Run `platform setup`; open the printed URL and paste them into the Hermes wizard
   form. The wizard:
   - writes them to `.env` (the secrets backend),
   - **auto-generates** the three auth secrets (`HERMES_SESSION_SECRET`,
     `HERMES_ADMIN_PASSWORD`, `HERMES_AGENT_TOKEN`),
   - **auto-migrates** the local DuckDB,
   - shows you the exact cold-start caps you're agreeing to,
   - then drops you into the platform (cold-start already active).
3. After setup the wizard is hidden — the homepage becomes Portfolio. `platform init`
   also detects an incomplete setup and prints the wizard command as a next step.

If you prefer manual setup, copy `.env.example` → `.env` and fill the vars below.
**Required** ones have no fallback.

### 2.1 Secrets backend (required)
| Var | Value | Notes |
| --- | --- | --- |
| `SECRETS_BACKEND` | `env_file` | resolver type |
| `SECRETS_ENV_FILE_PATH` | `./.env` | where secrets live |

### 2.2 Noble Trader upstream (required — the signal source)
| Var | Value |
| --- | --- |
| `NOBLE_TRADER_REDIS_URL` | `redis://<nt-redis-host>:<port>` (Upstash, RESP) |
| `NOBLE_TRADER_REDIS_CHANNEL` | `signal.raw.noble_trader` |
| `NOBLE_TRADER_REDIS_CONSUMER_GROUP` | `hermes-l0` |

> This is the **only** legitimate signal source. NT pushes qualified heartbeats
> there every ~5 min. `trading:config:{symbol}` hashes are a pull snapshot and are
> **never** used to seed the optimizer.

### 2.3 Brokerage — MT4/MT5 bridge (primary)

The user's brokerage is the **MT4/MT5 bridge**: an EA (or `mt5_mcp`) posts heartbeats
to `bridge_relay.py` → `signal.raw.noble_trader`. Alpaca + Hyperliquid are **deprecated**
(`enabled: false` in `config/default.yaml`) — kept only for reference/compat.

| Var | Value |
| --- | --- |
| `MT4_MT5_BRIDGE_TOKEN` | shared secret the EA/relay uses to authenticate |
| `MT4_MT5_SOURCE_ID` | (optional) e.g. `mt4_plexytrade` — stamps source attribution |
| `MT4_MT5_RELAY_URL` | (optional) `http://localhost:9100` — relay HTTP endpoint |

### 2.4 Hermes infra (required)
| Var | Value |
| --- | --- |
| `HERMES_DUCKDB_PATH` | `./data/hermes.duckdb` |
| `HERMES_REDIS_URL` | `redis://localhost:6379/1` (local cache + internal bus) |
| `HERMES_LOG_LEVEL` | `INFO` |
| `HERMES_ENVIRONMENT` | `development` \| `staging` \| `production` |
| `HERMES_SESSION_SECRET` | 64+ char random (auto-generated by the wizard) |
| `HERMES_ADMIN_USERNAME` | dashboard admin user |
| `HERMES_ADMIN_PASSWORD` | strong (≥16 char, auto-generated by the wizard) |
| `HERMES_AGENT_TOKEN` | 64+ char bearer token (auto-generated by the wizard) |

> The three auth secrets are **auto-generated by the `/setup` wizard** and saved to
> `.env` — you don't need to create them by hand.

### 2.5 Price data + notifications (recommended)
| Var | Value | Why |
| --- | --- | --- |
| `TRADINGVIEW_API_KEY` | RapidAPI key | OHLCV for crypto/forex/stocks/commodities |
| `TRADINGVIEW_API_HOST` | `tradingview-data1.p.rapidapi.com` | adapter default |
| `TRADINGVIEW_BASE_URL` | `https://tradingview-data1.p.rapidapi.com` | adapter default |

> **Supabase is NOT required** for the client platform. It is only reachable via the
> optional `backfill` CLI command (historical data) and is never called in the live loop.
> It is intentionally **not** collected by the wizard.

> **Trade approvals — delivered by Hermes, through the channels you set up here.**
> Tier-3 (large / new-strategy) trades are queued in the agent's local DuckDB and
> delivered two ways, both Hermes-owned: (1) a **push to your configured channels**
> (Discord webhook / Telegram bot) — set in this wizard / `config.default.yaml →
> notifications.*`), and (2) the in-app **Approvals** queue (dashboard `/approvals` +
> `noble pending` / `noble approve`). You approve in chat or in the dashboard; either
> re-publishes to L3. For Telegram, paste **both** the Bot Token (from @BotFather) and
> the **Chat ID** (from `getUpdates`) — Hermes sends the approval alert there. Channels
> are optional but recommended — if none are set, the in-app queue still catches every
> pending decision. (Discord needs a server where you have Manage Webhooks.)

---

## 3. First account snapshot (cold-start → live read)

Once `.env` is filled (wizard or manual) and the stack is up, take your **first account
snapshot**. This is the anchor the risk engine uses — **live brokerage equity is the
source of truth**, never a static figure.

```bash
cd noble-trader-agent/repo
unset PYTHONPATH                       # repo venv must NOT inherit the global PYTHONPATH
./.venv/Scripts/python.exe -m hermes.app noble balance   # live equity across venues
./.venv/Scripts/python.exe -m hermes.app noble assets    # held assets + NT regime + renko
```

- `noble balance` prints equity across the connected venues (MT4/5 bridge + any enabled
  venue) summed. **This number is authoritative** for drawdown/risk.
- `noble assets` enriches each held position with the latest Noble Trader signal,
  the Hermes 7-state MetaRegime overlay, and a renko brick ladder.

If you hold nothing yet, that's fine — cold-start will keep you flat until signals
arrive and the selection layer admits the top-3. The snapshot just confirms the
**plumbing is live** (Redis upstream reachable, brokerage/bridge connected, DB migrated).

> Verify the watchdog/cron is auto-restarting the loops (dashboard / monitor /
> synthesize / risk / execute / ingest + optimizer watcher). Restart must be automatic
> — you should never hand-relaunch after a sleep. See the `noble` skill's auto-restart
> recipe.

---

## 4. Day-one operating posture

1. **Stay in cold-start.** Don't edit `autonomy.cold_start` or `tier_1` caps on day one.
   They exist to protect you. Normal tier-1 is still only $2k / 1.5%.
2. **Watch, don't override.** Let the agent rank + admit top-3 signals. Excess is
   dropped on purpose.
3. **Check snapshots daily.** `noble balance` + `noble assets` is your quick health read.
4. **Approve when asked.** If a tier-3 trade lands in the queue, open the dashboard
   **Approvals** page (`/approvals`) or run `noble pending`, review it, then:
   ```bash
   ./.venv/Scripts/python.exe -m hermes.app noble pending    # list awaiting approval
   ./.venv/Scripts/python.exe -m hermes.app noble approve <decision_id>
   ```
   **Decision deadline:** a proposed trade must be approved within the hot-symbol
   window — `autonomy.tier_3.approval_decision_ttl_sec` (default **5 min**). After that
   it expires and can no longer be approved (re-submit via a fresh signal if still
   wanted). This keeps approvals locked to the live decision window, not open-ended.
5. **User-initiated trades are allowed (GC).** Want to buy something with no signal?
   The agent runs a **mandatory simulation** (non-configurable), sizes to your active
   caps, and routes it through the same risk gate:
   ```bash
   ./.venv/Scripts/python.exe -m hermes.app noble trade --symbol COINBASE:BTCUSD --side BUY --equity 5000
   ```
6. **Trust the guardrails.** Unsupported venues are hard-rejected at L5 (M4 = correct
   reject). Duplicate decisions are de-duplicated at L3 (no double-fills). Kill-switch
   and circuit breakers are ON by default.

6. **Report bugs / check entitlement via Hermes (not forks).** If something breaks:
   ```bash
   ./.venv/Scripts/python.exe -m hermes.app noble bug --description "monitor loop crashed on startup" --repo owner/noble-trader-agent
   ```
   It captures **redacted** environment + config + log tail and opens a GitHub Issue
   with your Git/pkg token (`GITHUB_TOKEN`, set in the wizard). Secrets are redacted
   before anything leaves your machine. Verify entitlement any time:
   `noble entitlement` (proves the Git token authenticates). The Git/pkg token is the
   single platform credential — it authenticates install, bug filing, and entitlement.

---

## 5. When cold-start ends (what changes)

After ≥20 closed trades **and** positive realized expectancy, the account auto-exits
cold-start. Limits widen to normal tier-1 ($2k / 1.5%), the selection layer still caps
admissions to top-3/cycle, and tier-3 trades now require **your** approval via the
queue. Nothing else changes — the same L4→L5→L3 pipeline, the same guardrails.

---

## 6. Quick reference

| Need | Command |
| --- | --- |
| Live equity | `noble balance` |
| Held assets + regime | `noble assets` |
| Pending approvals | `noble pending` (or dashboard → Approvals) |
| Approve a decision | `noble approve <id>` (or dashboard → Approvals → Approve) |
| File a bug report | `noble bug --description "..." [--repo owner/name] [--dry-run]` |
| Check entitlement | `noble entitlement` (proves Git/pkg token authenticates) |
| User-initiated trade | `noble trade --symbol X --side BUY --equity N` |
| First-run wizard | `platform setup` (serves the daisyUI wizard at http://127.0.0.1:8080/setup) |
| Wizard URL only (headless) | `platform setup --print-url` |
| This guide | `noble userguide` |
| Config audit | `noble config --audit` |
| Request config change | `noble config --set 'KEY=VALUE' --why '<reason>'` |

**Golden rule:** the agent runs the stack and only escalates *real* anomalies. You
intervene for approvals and strategy direction — not for routine ticks.

---

*See also: `docs/hardening_test_plan.md` (how the approval/execution guardrails were
hardened), `docs/roadmap.md` §15 (user-driven discovery + trading).*
