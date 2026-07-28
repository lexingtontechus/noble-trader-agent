# Noble Trader Stack — Audit Worklog

**Date opened:** 2026-07-23
**Author:** Ultron (Developer agent, noble-trader-workspace maintenance)
**Repos:** noble-trader-fastapi-backend, noble-trader-proxy, nobletradingapp
(noble-trader-admin/client), noble-trader-agent (Talaria),
noble-trader-hermes-agent-profile.
**Overall status:** Read-only audit. No code written. Items are tracked below with
severity, repo, evidence (file:line), and remediation. "Build Code" is gated per
item (C1 validated; others pending your go-ahead).

**Index**
- C1 — Missing `platform agent` CLI command (VALIDATED) — §A
- C2 — Workflow verification: backend → proxy → agent signal flow — §B
- C3 — Proxy is not a git repository (deploy-blocked) — §C  ✅ COMPLETE
- H1 — Profile↔agent venue credential mismatch — §D  ✅ RESOLVED (by M3 deep sweep, 2026-07-24)
- H2 — Backend version drift (README 7.7.0 vs code 7.2.0) — §E
- H3 — Stray syntax-error file committed — §F
- H4 — CORS env-var split-brain — §G
- H5 — Backend `.env.example` missing security/operative vars — §H
- M1 — Large uncommitted local divergence (backend 257 / agent 68) — §I
- M2 — Stream-producer ambiguity (resolved by C2) — §J
- M3 — Dead root server scripts in agent — §K
- M4 — Legacy main_v1..v4.py dead duplicates — §L
- L1 — Profile README vs distribution.yaml version drift — §M
- L2 — Agent README vs AGENTS.md venue drift — §N
- L3 — No single `__version__` source of truth (backend) — §O
- L4 — Agent pyproject entrypoint OK (informational) — §P

================================================================================

# §A — C1: Missing `platform agent` CLI command  [VALIDATED]

**Status:** VALIDATED — 4 cron jobs are silently dead. Build Code authorized pending
your go-ahead (directive: "only Build Code for C1 if validated").

## A.1 Summary

The Hermes profile `noble-trader-hermes-agent-profile` schedules 4 strategic
cron jobs that invoke `platform agent <subcommand>`. The `agent` command group
does **not exist** in `noble-trader-agent/src/hermes/app.py`. The cron wrapper
`run_guarded.sh` runs `python -m hermes.app "$@"` without checking the child's
exit code, so every failure is silent (prints nothing, exits 0 from the
wrapper's perspective only if `health` passed — the actual `click` error is
lost because `run_guarded.sh` does not `set -e` and the final line is an
unguarded exec).

**Net effect:** EOD self-learning, shadow-mode promotion checks, underperformance
auto-rollback, and monthly maintenance have NOT been running (or have been
erroring out) since the `agent` group was dropped/never added, despite being
documented and scheduled.

## A.2 End-to-end trace (verified)

```
cron/jobs.json  (noble-trader-hermes-agent-profile)
  ├─ "NT EOD self-learning"          → bash scripts/cron/eod.sh
  ├─ "NT shadow promotions"         → bash scripts/cron/shadow.sh
  ├─ "NT underperformance check"    → bash scripts/cron/underperf.sh
  └─ "NT monthly maintenance"       → bash scripts/cron/monthly.sh
        │
        ▼  (each script is a 5-line wrapper)
  scripts/cron/{eod,shadow,underperf,monthly}.sh
     exec "$DIR/run_guarded.sh" agent --eod | --check-shadow-promotions | --check-underperformance | --monthly-maintenance
        │
        ▼
  scripts/cron/run_guarded.sh
     "$VENVPY" -m hermes.app "$@"      # = python -m hermes.app agent --eod ...
        │
        ▼
  src/hermes/app.py  (click CLI)
     → click: "No such command 'agent'."  ← FAILS HERE (exit 2)
     (run_guarded.sh has no `set -e`, does not check `$?` after the exec,
      so the error is swallowed)
```

### A.2.1 Evidence — cron jobs (jobs.json)
- `cron/jobs.json:6`  → `eod.sh` — "wraps `platform agent --eod`"
- `cron/jobs.json:48` → `shadow.sh` — "wraps `platform agent --check-shadow-promotions`"
- `cron/jobs.json:90` → `underperf.sh` — "wraps `platform agent --check-underperformance`"
- `cron/jobs.json:256`→ `monthly.sh` — "wraps `platform agent --monthly-maintenance`"

### A.2.2 Evidence — wrapper scripts (noble-trader-agent)
- `scripts/cron/eod.sh:6`      → `exec "$DIR/run_guarded.sh" agent --eod`
- `scripts/cron/shadow.sh:5`   → `exec "$DIR/run_guarded.sh" agent --check-shadow-promotions`
- `scripts/cron/underperf.sh:5`→ `exec "$DIR/run_guarded.sh" agent --check-underperformance`
- `scripts/cron/monthly.sh:5`  → `exec "$DIR/run_guarded.sh" agent --monthly-maintenance`
- `scripts/cron/run_guarded.sh:25` → `"$VENVPY" -m hermes.app "$@"` (no `set -e`; no exit-code check)

### A.2.3 Evidence — CLI absence (app.py, DEFINITIVE)
Every registered command/group enumerated from `src/hermes/app.py`:
```
@click.group()                                  (line 74)  -> cli
@cli.command()                                  (97, 172, 879, 885, 948, 1062, 1127=setup,
                                                 1211, 1317, 1556, 1652, 1852, 2035,
                                                 2232, 2271, 2302, 2378, 2507, 2624,
                                                 2739, 2833, 2892, 2950=meta-regime,
                                                 3023, 3122, 3180)
@config.command(name=...)                        (show, set, history, diff, rollback, promote)
@symbols.command(name=...)                       (list, add, activate, deactivate,
                                                 validate, validate-all, sync, show)
```
- **No `@cli.group(name="agent")` and no `@agent.command(...)`** anywhere.
- `grep -rnE "'agent'|\"agent\"|name=\"agent\"" src/hermes` → only matches are the
  web-auth role string (`web/app.py:317,334,436`), NOT a CLI command.
- `learning.py` exposes `run_eod_analysis()` (line 442) as an async method but
  has **no click entry / `if __name__ == "__main__"` / main()** — it is only
  reachable from inside the Python process, not from the `platform` CLI.

### A.2.4 Corroborating documentation (proves it was specified, not accidental)
- `noble-trader-hermes-agent-profile/skills/trading/noble-trader-quant-hf-manager/SKILL.md:131`
  → `- `platform agent [--eod] [--list-hypotheses]` — decision tree + self-learning + hypotheses`
- Same SKILL.md:189 → `Daily EOD: `platform agent --eod` → postmortems + hypotheses.`
- Past cron **run artifact**: `noble-trader-hermes-agent-profile/cron/output/073b3f377296/2026-07-09_17-12-45.md:130`
  → lists `platform agent [--eod] [--list-hypotheses]` in the command reference.
- `noble-trader-agent/docs/agent_onboarding.md` §6/§7 (referenced by every wrapper's
  header comment) describes the EOD / shadow / underperf / monthly schedule that
  depends on `platform agent`.

## A.3 Is `platform agent` a "real process" and needed? — VERDICT: YES

- **Real:** It is specified in the cron jobs, documented in the bundled skill
  and onboarding docs, and referenced in a historical cron run log. The
  underlying logic (`learning.py` EOD analysis) exists. It was simply never
  wired into the `click` CLI.
- **Needed:** It is the ONLY automation path for the four strategic loops that
  constitute the "hedge-fund discipline" (postmortems → hypotheses → shadow
  promotion → underperformance rollback → monthly pruning). Without it, the
  self-learning loop and config auto-governance are non-functional.

> Note: the 7 *operational* cron jobs (watchdog, security-gate, optimize, rigor,
> vacuum, metaregime, account_snapshot) call commands that DO exist
> (`platform optimize`, `platform rigor`, `platform meta-regime --retrain`, etc.)
> and are unaffected by C1. Only the 4 `agent`-prefixed jobs are broken.

## A.4 Remediation plan (for Build Code phase — NOT executed yet)

Add an `agent` command group to `src/hermes/app.py` and wire it to the existing
logic. Subcommands required:

| Subcommand | Backing implementation to wire | Status to confirm before coding |
|------------|-------------------------------|----------------------------------|
| `--eod` | `agent/learning.py:442 run_eod_analysis()` | exists ✓ |
| `--check-shadow-promotions` | locate in `agent/learning.py` or `simulation/` | **verify symbol before coding** |
| `--check-underperformance` | locate (likely `agent/learning.py` or `portfolio/`) | **verify symbol before coding** |
| `--monthly-maintenance` | locate (likely `ops/` or `db/`) | **verify symbol before coding** |
| `--list-hypotheses` | referenced in SKILL.md; locate | **verify symbol before coding** |

Recommended shape (pseudo):
```python
@cli.group()
@click.pass_context
def agent(ctx): ...

@agent.command()
@click.pass_context
def eod(ctx): ...
# ... one command per flag above, each delegating to the discovered function
```

Also fix `run_guarded.sh` to `set -e` / check `$?` so future breakage is loud,
not silent (separate low-priority hardening).

## A.5 Validation gate

Per directive, Build Code is authorized **only after validation**. Validation
is complete: the command is confirmed missing, the call chain is confirmed
broken, and the requirement is confirmed real+needed. Awaiting your explicit
"Build Code" to implement §A.4 (after I locate the 3 not-yet-pinned backing
functions for the non-EOD subcommands).

================================================================================

# §B — C2: Workflow verification (backend → proxy → agent)  [VERIFIED]

**Status:** VERIFIED against codebase + docs. Topology confirmed; resolves the
earlier F4/M2 ambiguity. No code change requested.

## B.1 Topology (as built)

```
noble-trader-fastapi-backend (LightningAI sweep orchestrator)
   │  XADD "signal.raw.noble_trader"  (qualified signals)
   ▼
Redis Stream: signal.raw.noble_trader
   ├─► noble-trader-proxy   (consumer group "proxy")     → /quotes /history /api/ta /sse/alerts (HTTP+SSE to agents)
   └─► noble-trader-agent   (consumer group "hermes-l0" → renamed "noble-1"; see B.6) → execution + journaling (Talaria)

OPTIONAL parallel producer (NOT the backend):
noble-trader-agent/bridges/mt4_mt5/bridge_relay.py
   │  XADD "signal.raw.noble_trader"  (EA/MT5 heartbeats, source_id attribution)
   ▼
   (same shared stream — fan-in with the backend)
```

## B.2 Evidence — backend is a real producer
- `regime_platform/sweep_orchestrator/orchestrator.py:1757-1759`:
  `pipe.publish("signal.raw.noble_trader", agent_payload)` AND
  `pipe.xadd("signal.raw.noble_trader", ...)` — confirms persistent XADD.
- `orchestrator.py:1225` "Only publish qualified signals to signal.raw.noble_trader"
  and `:1512-1527` comment block describing the push model (XADD + PUBLISH) that
  downstream consumers read from.
- `services/stream_splitter.py:77` `RAW_STREAM = "signal.raw.noble_trader"`;
  `:321` `await self._redis.xadd(target.stream, enriched_fields, maxlen=10000)`
  — a second backend-side publisher that splits raw signals into per-plan streams.

## B.3 Evidence — proxy is a real consumer
- `noble-trader-proxy/src/proxy/redis/subscriber.py:2` "XREADGROUP consumer for
  `signal.raw.noble_trader`"; `:64` `stream=settings.REDIS_SIGNAL_STREAM`;
  `:126` `streams={settings.REDIS_SIGNAL_STREAM: ">"}` → XREADGROUP loop.
- Group name sourced from `settings.REDIS_CONSUMER_GROUP` (default `proxy`,
  per README + `settings.py:36`).

## B.4 Evidence — agent is a real consumer (+ optional bridge producer)
- `noble-trader-agent/src/hermes/transport/redis_subscriber.py:4-5` "Reads from the
  `signal.raw.noble_trader` Redis STREAM (XADD/XREAD) ... Uses a consumer group
  (`hermes-l0`)"; `:91-93` channel defaults to `signal.raw.noble_trader`,
  consumer_group defaults to `hermes-l0`; `:218` XREADGROUP with that group.
- `noble-trader-agent/bridges/mt4_mt5/bridge_relay.py:6` "XADDs to the shared
  upstream" channel; `:203` `r.xadd(channel, {"payload": json.dumps(envelope)})`;
  `:219` `--channel` default `signal.raw.noble_trader`. This is a SEPARATE,
  OPTIONAL producer path (MT4/MT5 EA → bridge_relay) that feeds the SAME stream.
  It is NOT the backend; it is an alternative/additional source.

## B.5 Conclusion / resolution of F4+M2
- The "backend > proxy > agent" directive is **accurate as a fan-out**: the
  backend is the primary producer; the proxy and agent are independent consumers
  on distinct groups (`proxy` vs `hermes-l0`). Both see every signal; no
  consumer↔consumer coupling.
- The `bridge_relay.py` path means the agent repo can ALSO publish to the same
  stream (heartbeats from a live MT4/MT5 EA). This explains the AGENTS.md wording
  ("bridge EA → bridge_relay → signal.raw.noble_trader") — it is a producer
  option, complementary to the backend, not a contradiction. Both can coexist.
- **No code fix required for C2** (the consumer wiring was already correct). The
  only lingering action was documentary: state the backend XOR bridge as the
  *active* producer per deployment.

## B.6 Update — 2026-07-23: agent now consumes FROM the proxy (code refactor)
**User directive:** "agent consumes from the proxy (real refactor, not a doc fix)."
The qualified-signal stream `signal.raw.noble_trader` is produced by the backend
(the proxy does NOT filter — it reads the already-qualified stream). To make the
proxy the single ingestion hop (backend → proxy → agent) rather than a 2nd direct
backend consumer:
- **Proxy** (`src/proxy/redis/signal_fanout.py` + `subscriber.py`, `settings.py`
  `SIGNAL_FANOUT_STREAM` default `signal.proxy.noble_trader`): re-publishes each
  qualified signal it reads from `signal.raw.noble_trader` onto `signal.proxy.noble_trader`
  via its write-capable `REDIS_FANOUT_URL` (`pub_<32hex>` ACL user, same as
  alerts:fanout). Guarded: skipped when `REDIS_FANOUT_URL` empty.
- **Agent** (`src/hermes/transport/redis_subscriber.py` + `config/default.yaml`
  `signal_source: proxy`, `proxy_channel: signal.proxy.noble_trader`,
  `consumer_group: noble-1`): now reads the proxy-forwarded stream with its OWN
  group `noble-1` (does NOT join the proxy's group). **Auto-fallback** to
  `signal.raw.noble_trader` when the proxy stream is absent, so ingestion never
  goes silent.
- **Docs:** `AGENTS.md` §2 rewritten to the qualified-signal topology; proxy
  README notes the re-publish. `bridge_relay.py` (MT4/MT5 EA) remains an
  independent OPTIONAL producer onto `signal.raw.noble_trader`.
- **Note:** `signal.raw.noble_trader` is already qualified (backend qualifies
  before XADD); the proxy forwards it verbatim. No proxy-side filtering added
  (per user: "the proxy doesn't filter").
- **Verification:** all changed `.py` compile; `config/default.yaml` parses;
  fallback logic present. Live end-to-end untested (proxy is a new/undeployed
  service, per user — no instance to point at).

================================================================================

# §C — C3: Proxy is not a git repository  [✅ COMPLETE — 2026-07-23]

**Repo:** noble-trader-proxy
**Severity:** 🔴 Critical (deploy blocker) → resolved
**Finding (original):** The proxy directory had **no `.git`** and no remote.
**Decision (C3.2):** User confirmed this is a **new original repo** — fresh `git init`
+ create remote + push (not a clone-from-elsewhere).

## C.1 Actions taken (all verified by real tool output)
1. **Secret-scan before init:** grepped `src tests .env.example` for secret
   patterns — only `eyJtest` test fixtures + the `.env.example` placeholder
   (`eyJhbGciOi...anon_key...`). No real secrets. Safe to commit.
2. **`git init -b main`** → initialized empty repo.
3. **`git add -A --dry-run`** → 53 files staged (source, tests, docs, config);
   **no `.env`, no secrets**. Committed.
4. **Commit `3417ec5`** — "Initial commit: noble-trader-proxy v0.3.0 (P3.5)"
   (53 files; CRLF autocrlf warnings only, harmless).
5. **Remote created on GitHub via REST API** (cached PAT from Windows Credential
   Manager; token never echoed to CLI). `POST /user/repos` → **HTTP 201**.
   Repo: `https://github.com/lexingtontechus/noble-trader-proxy.git`, **private**,
   description set, `auto_init:false`.
6. **`git remote add origin`** + **`git push -u origin main`** →
   `[new branch] main -> main`, `PUSH_EXIT=0`, branch tracking set.
7. **Verified:** `git ls-remote --heads origin` returns
   `3417ec5a0095f64ce4353457d651765305a4cad5  refs/heads/main` — matches the
   local pushed commit. Authenticated API confirms `private:true`,
   `default_branch:"main"`.

## C.2 Result
- Proxy is now a real GitHub repo (private) under `lexingtontechus`.
- Local + remote `main` are in sync at commit `3417ec5`.
- Future `git push` works via the cached credential (WCM). No `gh` needed.

## C.3 Remaining (out of scope for C3 — owner action)
- **Railway deploy:** Dockerfile + railway.toml already present in repo. To go
  live: in Railway dashboard "New Project → Deploy from GitHub repo", select
  `noble-trader-proxy`, set `REDIS_URL` / `SUPABASE_URL` / `SUPABASE_ANON_KEY`
  (per README "Railway deployment"). The `license-validate` edge function it
  calls is still MISSING in the admin repo — see §C-adjacent item (proxy→admin
  license gap, tracked under H-license in the index). **C3 does not fix that;**
  it only establishes the repo + deploy-readiness.
- **`.github/` workflows:** none present (consistent with sibling repos). Optional.

**Status:** ✅ Done. No source code changed; only repo scaffolding + push.

================================================================================

# §D — H1: Profile↔agent venue credential mismatch  [✅ RESOLVED — 2026-07-24]

**Resolution:** The M3 deep-sweep (2026-07-24) applied exactly the prescribed
remediation. Re-verified 2026-07-24:

- `noble-trader-agent/config/default.yaml:28,55` — `alpaca.enabled:false`,
  `hyperliquid.enabled:false` (DEPRECATED); `:75` `mt4_mt5.enabled:true` (PRIMARY).
- `config/default.yaml:82-84` expects `secret:mt4_mt5_bridge_token/source_id/relay_url`;
  `:106` `tradingview.api_key: secret:tradingview.api_key`.
- `noble-trader-hermes-agent-profile/distribution.yaml:48-57` now declares
  `MT4_MT5_BRIDGE_TOKEN` (required) + `MT4_MT5_SOURCE_ID`/`MT4_MT5_RELAY_URL`
  (optional) in `env_requires`; `:61-87` demotes `ALPACA_*`/`HYPERLIQUID_*` to
  `required:false`.
- `core/secrets.py:53/67` resolves `secret:NAME` → `os.getenv("NAME")`, and the
  `/setup` wizard (`web/templates/setup.html:39`) collects `MT4_MT5_BRIDGE_TOKEN`
  — so the profile→agent credential contract is closed.
- `distribution.yaml` also now declares `TRADINGVIEW_API_KEY` (optional) for the
  TradingView price-data source (`tradingview.enabled:true`), closing the minor
  completeness gap noted in remediation.

**Net:** no longer breaks profile install; the live (mt4/5) venue is fully
declared and wired. Closed — no further action.

**Repo:** noble-trader-hermes-agent-profile (distribution.yaml) ↔ noble-trader-agent (config/default.yaml)
**Severity:** 🟠 High (breaks profile install)
**Finding:** `distribution.yaml` `env_requires` lists `ALPACA_API_KEY/SECRET` and
`HYPERLIQUID_*` as **required**, but `config/default.yaml` sets both
`alpaca.enabled:false` and `hyperliquid.enabled:false` (marked DEPRECATED) and
`mt4_mt5.enabled:true` (PRIMARY venue). The agent needs `secret:mt4_mt5_bridge_token`,
`mt4_mt5_source_id`, `mt4_mt5_relay_url` — none of which the profile declares.
**Evidence:**
- `config/default.yaml:28` `alpaca.enabled: false  # DEPRECATED`; `:55` `hyperliquid.enabled: false  # DEPRECATED`; `:75` `mt4_mt5.enabled: true  # PRIMARY`.
- `config/default.yaml:82-84` `secret:mt4_mt5_bridge_token/source_id/relay_url`.
- `distribution.yaml:46-69` requires ALPACA + HYPERLIQUID; no mt4_mt5 keys.
**Remediation (Build Code):** Add `mt4_mt5_bridge_token`, `mt4_mt5_source_id`,
`mt4_mt5_relay_url` (required) to `distribution.yaml` `env_requires`; optionally add
`tradingview.api_key` (tradingview.enabled:true). Demote `ALPACA_*`/`HYPERLIQUID_*`
to `required: false` (or remove). Keep README's venue note in sync.
**Build Code:** authorized once you confirm the venue decision (mt4_mt5 primary).

================================================================================

# §E — H2: Backend version drift  [OPEN]

**Repo:** noble-trader-fastapi-backend
**Severity:** 🟠 High
**Finding:** README declares **v7.7.0**; code says **v7.2.0** in three places.
**Evidence:** `main.py:3,141,422,593` `v7.2.0`; `pyproject.toml:7` `version = "7.2.0"`;
`docs/openapi.yaml:5,124` `version: "7.2.0"`.
**Remediation (Build Code):** Either bump code to 7.7.0 (if the README is the
truth) or correct the README to 7.2.0 (if code is truth) — pick one source of
truth (see L3).
**Build Code:** authorized.

================================================================================

# §F — H3: Stray syntax-error file committed  [OPEN]

**Repo:** noble-trader-fastapi-backend
**Severity:** 🟠 High (build hygiene)
**Finding:** `regime_platform/auth/__init__ copy.py` (104 B) is a stray editor
artifact — line 1 is a Windows path; it is a SyntaxError and breaks `compileall`
/ whole-tree lint. Never imported at runtime (harmless in prod, noisy in CI).
**Evidence:** file present; `compileall` flags SyntaxError.
**Remediation (Build Code):** Delete the file (and any sibling `* copy.py`
artifacts). Add a pre-commit glob guard if desired.
**Build Code:** authorized.

================================================================================

# §G — H4: CORS env-var split-brain  [RESOLVED — 2026-07-23]

**Repo:** noble-trader-fastapi-backend
**Severity:** 🟠 High
**Original finding:** `main.py` reads `CORS_ALLOWED_ORIGINS` for the live CORS middleware,
but `settings.py` defined an unused `CORS_ORIGINS`/`cors_origins_list`, and
`.env.example` documented only the *unused* `CORS_ORIGINS`. Setting the documented
var had no effect on CORS.
**Resolution (per user directive — clean up dead CORS, don't wire it up):** Since the
agent's `src/hermes/web` (port 8080) is a same-origin FastAPI+SPA that needs NO CORS,
the backend `CORS_ORIGINS` was confirmed dead config. Removed it rather than unify:
- `regime_platform/core/settings.py`: deleted `CORS_ORIGINS` field (was line 192),
  deleted `cors_origins_list` property (209-212), fixed the docstring example (line 12).
- `.env.example` + `.env.local`: replaced the misleading `CORS_ORIGINS=` block with a
  correct note that the live middleware reads `CORS_ALLOWED_ORIGINS` (with an example line).
- `docs/Setup/deployment.md:113`: table row corrected `CORS_ORIGINS` → `CORS_ALLOWED_ORIGINS`.
- `main.py` live `CORS_ALLOWED_ORIGINS` middleware left intact (this is the real var).
- `main_v1..v4.py`: left intact (legacy standalone demos, intentional `allow_origins=["*"]`).
- Agent repo: confirmed NO active CORS code (only this worklog referenced it).
**Verification:** settings.py compiles; grep confirms no `CORS_ORIGINS`/`cors_origins_list`
remains in active backend code (only the intentional `CORS_ALLOWED_ORIGINS` in main.py).

## §G.1 — Boot checklist artifacts for noble-trader-hermes-agent profile  [COMPLETE — 2026-07-23]

**Repo:** noble-trader-hermes-agent-profile (+ live profile data dir `~/.hermes/profiles/noble-agent/`)
**Context:** User confirmed the agent gateway boot should (a) run a startup checklist, and
(b) the checklist should launch `platform dashboard` (web UI, port 8080); the 6 loop
commands are interactive/on-demand, NOT auto-started.
**Deliverables (written to BOTH the git repo and the live profile dir):**
- `BOOT.md` — free-form startup checklist: health gate → `platform init` only if uninitialized
  → launch `platform dashboard --host 0.0.0.0 --port 8080` (background) → confirm `/health` 200
  → loops noted as on-demand. Replies `[SILENT]` when clean.
- `hooks/boot.py` — gateway-hook loader (pattern from the Nous boot.md tutorial) that reads
  `BOOT.md` on gateway boot and spawns a one-shot agent on a daemon thread. Skips silently
  if `BOOT.md` absent.
**Verification:** both `BOOT.md` and `hooks/boot.py` exist in live + tracked locations;
`hooks/boot.py` parses (AST) clean in both. (Runtime boot not exercised here — needs a live gateway.)

================================================================================

# §H — H5: Backend `.env.example` missing security/operative vars  [RESOLVED — 2026-07-23]

**Repo:** noble-trader-fastapi-backend
**Severity:** 🟠 High
**Original finding:** `.env.example` omitted several code-referenced vars, incl. two
security-critical ones.
**Evidence (original):** `MCP_INTERNAL_KEY` (admin-grant API key — `jwt_auth.py` ×7 sites),
`CORS_ALLOWED_ORIGINS` (done in H4), `AUTH_ENABLED`, `DEPLOY_ENV`, `TIMESFM_SERVICE_URL`,
`MAIN_URL`, `SWEEP_ORCHESTRATOR_PORT`, `SIGNAL_COOLDOWN_MINUTES`, `RUNTIME_CONFIG_TTL`,
`MARKOV_*` (×4), `TIMESFM_GATE_MODE/REQUIRED/DISABLE_FALLBACK`.
**Resolution (Build Code — 2026-07-23):**
- Added `AUTH_ENABLED=true` to `.env.example` + `.env.local` with a ⚠️ SECURITY comment
  (master auth kill-switch; default "true"; never false in prod). Ref auth_config.py:32.
- Added `MCP_INTERNAL_KEY=` (empty) with an explicit ⚠️ SECRET comment: it grants
  role="admin" (subject "mcp-internal") in every auth path; read via os.getenv with NO
  default; includes a `secrets.token_urlsafe(48)` generate command; warns never to reuse
  example strings like "mcp-service-key".
- Added the remaining operative vars as commented examples (correct for .env.example):
  `DEPLOY_ENV`, `MAIN_URL`, `TIMESFM_SERVICE_URL`, `SWEEP_ORCHESTRATOR_PORT`,
  `SIGNAL_COOLDOWN_MINUTES`, `RUNTIME_CONFIG_TTL`, `MARKOV_ALPHA/BETA/GAMMA/DELTA`,
  `TIMESFM_GATE_MODE/REQUIRED/DISABLE_FALLBACK` — each with a one-line usage note + ref.
**Verification:** grep confirms all 15 previously-missing vars now present in `.env.example`
(both security vars active lines; operative vars as commented examples). `.env.local` also
has the two security vars. No code changed (docs-only); backend startup unaffected.

================================================================================

# §I — M1: Large uncommitted local divergence  [OPEN — needs triage]

**Repo:** noble-trader-fastapi-backend (257 entries) + noble-trader-agent (68 entries)
**Severity:** 🟡 Medium
**Finding:** Significant uncommitted work. Backend diff touches 16 `regime_platform/`
files + deletes 2 docs (DISCORD_DELIVERY_FIX.md, SIGNAL_GEN_FLOW_COMPARISON.md) +
modifies `.env.example`/README/background.py. Agent diff touches 38 `src/` files +
`config/default.yaml` + `web/templates/symbols.html` + adds `tests/test_phase1.py`.
**Evidence:** `git status -s` counts: backend 257, agent 68; `git diff HEAD --stat`.
**Remediation:** Triage per your direction — commit-ready vs scratch. DO NOT commit
without explicit instruction (no secrets in diffs — verify before commit).
**Build Code:** N/A (decision needed).

================================================================================

# §J — M2: Stream-producer ambiguity  [RESOLVED by §B/C2]

**Repo:** cross-repo
**Severity:** 🟡 Medium → resolved
**Finding (prior):** backend/proxy docs say LightningAI sweep is the producer;
agent AGENTS.md says MT4/MT5 bridge EA is the producer. **Resolution (§B):** both
are valid producers into the SAME stream `signal.raw.noble_trader`. Backend
orchestrator XADDs (primary); bridge_relay.py XADDs (optional, from live EA).
Proxy + agent consume via distinct groups. No contradiction — only a documentation
clarity gap.
**Remediation:** Doc-only — clarify active producer per deployment in README/AGENTS.md.

================================================================================

# §K — M3: Dead root server scripts in agent  [OPEN]

**Repo:** noble-trader-agent
**Severity:** 🟡 Medium
**Finding:** Stale pre-FastAPI server scripts at repo root, not imported anywhere.
**Evidence:** `flask_server.py`, `basic_flask.py`, `auth_server.py`, `clean_server.py`,
`final_server.py`, `python_http_server.py`, `run_server.py`, `run_clean_server.py`
— none referenced by `src/hermes/` or any entrypoint.
**Remediation (Build Code):** Archive to `.archive/` (consistent with existing
`.archive/dashboard-2026-07-16`) or delete. Low risk.
**Build Code:** authorized.

================================================================================

# §L — M4: Legacy main_v1..v4.py dead duplicates  [OPEN]

**Repo:** noble-trader-fastapi-backend
**Severity:** 🟡 Medium
**Finding:** `main_v1.py`..`main_v4.py` are dead duplicates of `main.py`; add confusion.
**Evidence:** repo root listing; `main.py` is the active app (imports resolve).
**Remediation (Build Code):** Archive or delete after confirming nothing references
them (no import found). Low risk.
**Build Code:** authorized.

================================================================================

# §M — L1: Profile README vs distribution.yaml version drift  [OPEN]

**Repo:** noble-trader-hermes-agent-profile
**Severity:** 🟢 Low
**Finding:** `README.md:124` says `version: 1.0.0`; `distribution.yaml:9` says `version: 1.1.0`.
**Remediation (Build Code):** Bump README to 1.1.0 (or vice-versa). Authoritative
version should match the deployed profile.
**Build Code:** authorized.

================================================================================

# §N — L2: Agent README vs AGENTS.md venue drift  [OPEN]

**Repo:** noble-trader-agent
**Severity:** 🟢 Low
**Finding:** README still documents Alpaca(paper)+Hyperliquid(testnet) as live
venues; `AGENTS.md`+config declare them DEPRECATED in favor of mt4_mt5 bridge.
**Remediation (Build Code):** Update README venue section to match AGENTS.md
(aligned with H1 decision).
**Build Code:** authorized (paired with H1).

================================================================================

# §O — L3: No single `__version__` source of truth (backend)  [OPEN]

**Repo:** noble-trader-fastapi-backend
**Severity:** 🟢 Low
**Finding:** Version string duplicated in `main.py`, `pyproject.toml`, `docs/openapi.yaml`
with no shared constant — root cause of H2 drift.
**Remediation (Build Code):** Introduce `regime_platform/__init__.py:__version__` and
import it in main.py/openapi generation; single source of truth.
**Build Code:** authorized (pairs with H2).

================================================================================

# §P — L4: Agent pyproject entrypoint OK  [INFORMATIONAL]

**Repo:** noble-trader-agent
**Severity:** 🟢 Low (no action)
**Finding:** `pyproject.toml` `[project.scripts] platform = "hermes.app:cli"` resolves
correctly (verified `app.py` has `cli` group). This is the correct entrypoint the
cron `run_guarded.sh` invokes.
**Action:** None. Noted to preempt false positives in future audits.

================================================================================

# Appendix — Verification method

- All findings verified by direct read-only inspection (grep, file listing,
  git status) on 2026-07-23. Deep code audits of backend `regime_platform` and
  agent `src/hermes` were run as parallel subagents and their Critical/High
  results independently re-confirmed by the agent's own greps (C1, H2, H3, H4).
- No files modified during audit. Worklog created/extended only.


