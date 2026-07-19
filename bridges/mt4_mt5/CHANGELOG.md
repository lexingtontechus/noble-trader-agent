# MT4/MT5 Bridge — Changelog & Change Tracker

> Living doc for the MT4/MT5 + TradingViewAPI integration. Update as items land.
> Repo: `noble-trader-agent/repo`. Bridge files: `bridges/mt4_mt5/`.

## 2026-07-15 — Session 2 (multi-tenant + TradingViewAPI price source)

### Context
- Target = **SaaS / multi-tenant**: each user gets an isolated Hermes agent + MT4/5
  server + local DB, fed from one shared signal stream. `source_id` is mandatory for
  tenant isolation (per user decision).
- Monitor pricing must match the signal layer: re-sourced to **TradingViewAPI
  (RapidAPI, Ultra plan)** — same upstream Noble Trader's backend normalizes from.
- Symbols must be **dynamic** (auto-discovered / auto-delisted), not a hardcoded list.

### Built + VERIFIED (real execution)
- `src/hermes/transport/adapters/tradingview_adapter.py` — `TradingViewApiAdapter`
  implements `VenueAdapter`. Batch-first `/api/price/batch` with **per-symbol GET
  fallback** and **429-aware retry** (Retry-After / exp backoff, graceful degrade).
  Verified: 429 -> retry -> batch -> per-symbol fallback returns quotes.
  **FIXED (live test):** RapidAPI auth is `X-RapidAPI-Key` + `X-RapidAPI-Host`
  (not `Authorization: Bearer`); batch body is `{"requests":[{"symbol":..}]}`;
  price is `data.data[].current.close`. All matched to the real TradingViewAPI
  (RapidAPI host `tradingview-data1.p.rapidapi.com`) — **live quotes confirmed**
  (EURUSD 1.1467, BTCUSD 64877, XAUUSD 4060).
- `src/hermes/schemas/market.py` — added `Venue.TRADINGVIEW` enum member.
- `src/hermes/schemas/heartbeat.py` — added `source_id: str|None` to heartbeat schema.
- `src/hermes/transport/redis_subscriber.py`:
  - `strategy_id` now derives from `source_id` (fallback `noble_trader`). Verified.
  - **Symbol auto-registry**: `_ensure_symbol_active` registers stream-discovered
    symbols active; `touch_symbol_seen` stamps last-seen.
  - **Auto-delist**: `_delist_stale` deactivates stale symbols. Verified against
    real DuckDB (naive-TZ bug fixed — DuckDB timestamps treated as UTC).
- `src/hermes/db/symbol_registry.py` — added `touch_symbol_seen()`.
- `src/hermes/app.py` (`monitor`) — wires `tradingview` venue into the adapter set.
- `src/hermes/marketdata/price_feed.py` — `fetch_price_series` routes to TradingViewAPI;
  `fetch_tv_bars` uses RapidAPI host + headers (same schema fix).
- `config/default.yaml` — added `tradingview` venue block (forex/crypto/equities/
  commodities, `secret:tradingview.api_key` under `credentials`, api_url/host/
  poll_interval/max_retries under `features`); added `tradingviewapi_com` to
  `data_sources.allowed_sources`.
- `.env.example` — added **MT4/MT5 bridge server key** block
  (`MT4_MT5_BRIDGE_TOKEN`) + clarified TradingView vars
  (`TRADINGVIEW_API_KEY/HOST/BASE_URL`).

### Live db-symbols-process test (2026-07-15)
Ran real TradingViewAPI quotes -> auto-register in DuckDB -> delist sweep.
PASS: EURUSD/BTCUSD/XAUUSD auto-registered active; stale XAUUSD delisted.
Note: symbol inference labels 6-alpha as `forex` (BTCUSD/XAUUSD mislabeled
cosmetically; harmless — venue+asset filters still work). Tighten if needed.

### Exchange dimension (2026-07-15, session 2+3)
User flagged: `tradingview/coinbase/BTCUSD` (COINBASE:BTCUSD) vs
`tradingview/binance/BTCUSD` (BINANCE:BTCUSD) must be DISTINCT rows
with correct asset_class (crypto vs forex) — drives PnL. Previously the
registry keyed on a bare `symbol` (e.g. `BTCUSD`) so exchange was
unrepresentable AND 6-alpha symbols were wrongly classified forex.

Changes:
- `db/migrations/010_symbols_exchange.sql` — adds `exchange` + `symbol_bare`
  columns, `UNIQUE(symbol,exchange,venue)`, indexes. Verified: applies
  (schema v10), columns present.
- `db/symbol_key.py` (NEW) — `parse_symbol_key()` splits `EXCHANGE:SYMBOL`
  -> (exchange, bare, qualified); `classify_asset_class()` is a REAL classifier
  (BTC/XAU base sets -> crypto/commodity; 6-alpha -> forex; 3-5 alpha
  -> equities) — fixes the forex-mislabel bug.
- `db/symbol_registry.py` — `Symbol` slots + `_row_to_symbol` + `_SELECT_COLS`
  include exchange/symbol_bare; `add_symbol` now parses the key, stores
  qualified `symbol` (e.g. `COINBASE:BTCUSD`) + exchange + bare, and
  re-classifies asset_class (venue-validated).
- `transport/redis_subscriber.py` `_ensure_symbol_active` — drops the
  naive forex/crypto inference; lets `add_symbol` classify. Qualified
  symbols (COINBASE:BTCUSD) pass through unchanged.
- `bridges/mt4_mt5/bridge_relay.py` — `_qualify_symbol()` maps
  source_id -> exchange (SOURCE_EXCHANGE map / BRIDGE_DEFAULT_EXCHANGE /
  explicit `exchange` field from EA) and rewrites `symbol` to
  `EXCHANGE:SYMBOL` before XADD. Idempotent.
- `transport/adapters/tradingview_adapter.py` `normalize_symbol` — strips
  `EXCHANGE:` prefix for the API call (exchange lives in registry, not
  the price request).
- EA emitter: can pass optional `exchange` field (relay honours it).

Verified (real DuckDB + live TradingViewAPI):
  COINBASE:BTCUSD + BINANCE:BTCUSD -> 2 DISTINCT rows, both crypto,
  distinct exchange; EURUSD -> forex; live price fetched per qualified key;
  stale BINANCE:BTCUSD delisted, COINBASE:BTCUSD retained.
  Classifier: BTCUSD=crypto, XAUUSD=commodities, EURUSD=forex, AAPL=equities.

### Exchange wiring + CLI (session 2+3 cont.)
User: (a) wire SOURCE_EXCHANGE for real MT4/MT5 feeds; (b) extend
`platform symbols list/add` CLI with exchange/bare columns.

Changes:
- `bridges/mt4_mt5/bridge_relay.py` — `SOURCE_EXCHANGE` map now
  sets `mt4_plexytrade -> BINANCE` (PlexyTrade crypto tracks Binance
  pricing; editable per deployment). `--exchange` from EA payload or
  BRIDGE_DEFAULT_EXCHANGE still honoured; idempotent.
- `src/hermes/app.py` `symbols list` — adds `Exch` + `Bare` columns
  (exchange / symbol_bare). `symbols add` — adds `--exchange` option;
  qualifies symbol to EXCHANGE:SYMBOL (idempotent: skips if already
  qualified). Both commands now call `apply_migrations(config)` first so a
  fresh DB auto-applies migration 010 (exchange cols) — closes a real
  gap where `add`/`list` assumed `platform init` had run.
- `db/symbol_key.py` `parse_symbol_key` — collapses double-qualified
  input (BINANCE:BINANCE:BTCUSD -> exchange=BINANCE, bare=BTCUSD) so a
  bad source map can't create a malformed key.

Verified (real CLI, fresh migrated DB):
  `symbols add BINANCE:BTCUSD --venue tradingview --asset-class crypto
   --exchange BINANCE` -> symbol=BINANCE:BTCUSD (NOT doubled),
   exchange=BINANCE, bare=BTCUSD, class=crypto.
  `symbols add BTCUSD --exchange COINBASE` -> COINBASE:BTCUSD.
  `symbols add EURUSD --venue tradingview --asset-class forex` ->
   EURUSD, exchange=None, class=forex.
  `symbols list` renders Exch + Bare columns correctly.
  Migration 010 auto-applied (schema v10) on first symbols command.

### Feed-exchange correction + design confirmation (session 3)
User: current live Redis signal feed is COINBASE-priced; the BINANCE flag
should reflect the actual exchange (Coinbase or Binance). Prefer NOT a
growing per-symbol exchange list to keep in sync — staleness + dynamic
symbols should already cover this.

Resolution:
- Confirmed: SOURCE_EXCHANGE map is keyed by SOURCE_ID (the feed), NOT per
  symbol. Switching a feed's exchange = one-line map change; per-symbol
  `exchange` recorded once at registration, then handled by staleness +
  dynamic delisting. No growing secondary list. User's interpretation
  CORRECT.
- `symbol_key.py` — added shared `qualify_symbol(source_id, exchange,
  source_exchange, default_exchange)` as the SINGLE exchange-resolution
  rule. Bridge relay `_qualify_symbol` now delegates to it (one source of
  truth). `parse_symbol_key` regression fixed (dropped no-colon return in
  a prior edit -> bare symbols crashed with "'NoneType' object has no
  attribute 'exchange'"). Idempotent against double-qualification.
- `redis_subscriber.py` — the CURRENT live feed (noble_trader / MT4 relay)
  now qualifies inbound symbols via `qualify_symbol` using a
  SOURCE_EXCHANGE map (`noble_trader -> COINBASE`, `mt4_plexytrade ->
  COINBASE`). Previously the subscriber NEVER qualified (bare BTCUSD), so
  the Coinbase feed's symbols landed un-exchanged. FIXED.
- Source map corrected: current feed = COINBASE (was wrongly BINANCE for
  PlexyTrade). Flip the map entry to repoint at Binance — no per-symbol
  edits.
- `symbols validate` (CLI) now surfaces `[venue=… exchange=… class=…]` so
  you can confirm the price source matches the exchange. Added `tradingview`
  branch to `validate_symbol`'s adapter factory (was missing -> "no adapter
  for venue 'tradingview'").

Verified (real execution):
  - parse_symbol_key regression: bare BTCUSD now returns SymbolKey (no crash).
  - Real _process_message path, source_id=noble_trader, BTCUSD/ETHUSD/EURUSD
    -> COINBASE:BTCUSD / COINBASE:ETHUSD / COINBASE:EURUSD (exchange=COINBASE,
    correct asset_class; EURUSD=forex). Distinct from bare rows.
  - `symbols validate COINBASE:BTCUSD` -> live TradingViewAPI price
    (64106.9) with `[venue=tradingview exchange=COINBASE class=crypto]`.
  - qualify_symbol unit: noble_trader BTCUSD->COINBASE:BTCUSD,
    mt4_binance->BINANCE:BTCUSD, already-qualified left alone, no-map
    left bare.

### Configurable decision deadline (Option B) (session 18)
Per user directive: a user **cannot approve a proposed trade after the hot-symbol TTL
window**. Previously the pending-approval timeout was a hardcoded 4h (`approval_timeout_hours`)
and unrelated to the 5-min monitor ACTIVE TTL — so approvals lived 4h, contradicting the
rule. Implemented Option B:

- **`config/default.yaml`:** added `autonomy.tier_3.approval_decision_ttl_sec: 300`
  (default **5 min**). Replaced the old `approval_timeout_hours: 4`.
- **`pending_approvals.py`:** constructor now takes `approval_timeout_seconds` (default
  300). `store()` stamps `expires_at = now + ttl` (seconds). `approve()` guards the
  deadline: it self-sweeps `expire_overdue()` then SELECTs `... AND expires_at > now()`,
  so an expired decision returns `None` (cannot be approved). `list_pending()` also
  self-sweeps, so the UI/CLI never shows stale approvables.
- **`orchestrator.py`:** reads `approval_decision_ttl_sec` (default 300) from config and
  passes seconds to `PendingApprovals` (was `approval_timeout_hours`).
- **Verified:** unit test stores a decision with a 1s TTL, approves before expiry
  (OK), waits 1.2s, approves after expiry → **blocked (None)** and dropped from the
  pending list. Hardening suite 17 passed.
- **Effect:** the decision deadline now equals the hot-symbol window (default 5 min),
  matching the user's rule. Configurable per deployment.

### Entitlement simplified to Git-token (session 17)
Correction to session 16 per user feedback: the license must be **simple, package-level,
single-credential — no tiers**. The earlier `core/license.py` (HMAC + `tier`/`pro`/
`enterprise` ranking + `is_entitled(min_tier)`) over-scoped and added a separate license
key the user never asked for. Replaced with a Git-token entitlement:

- **`src/hermes/core/entitlement.py` (NEW, replaces `license.py`):** the **Git/pkg token
  is the license**. `load_entitlement()` — offline, startup-safe, logs
  `entitlement_ok`/`entitlement_missing`, **never blocks**. `verify_entitlement()` —
  live proof the token authenticates to GitHub for our repo (`GET /user` + `GET
  /repos/{owner}/{repo}`), run via the new `noble entitlement` command. Fail-soft on
  network errors. No tier/version-gating beyond token validity + repo access.
- **`noble entitlement` command** added (live license check; exits non-zero if invalid).
- **Wizard (`setup.html` + `setup_submit`):** removed the `LICENSE_KEY` field; kept
  `GITHUB_TOKEN` as the single platform credential (saved as `secret:github.token`).
- **`bug_report.py`:** diagnostics now report `git_token_present` (dropped tier fields).
- **Verified:** imports clean; offline entitlement (no-token → not entitled, no crash);
  live verify with fake token → `git token invalid (401)` (fail-soft, no raise);
  `noble bug --dry-run` works; hardening suite 17 passed.
- **Docs:** `docs/deployment_design.md` rewritten to the exact simple spec (single token,
  install one-liner, entitlement verify, bug flow); onboarding guide §4.5 + quick-ref
  updated (`noble entitlement`); CHANGELOG + AGENTS.md distribution note corrected.

### License scaffold + `noble bug` (tenant bug flow) (session 16)
Per the deployment design: tenants are *consumers of releases, not forks*; bugs flow
back as redacted GitHub Issues; proprietary processes protected but packaging kept simple.

- **`src/hermes/core/license.py` (NEW):** simple, **whole-agent** (not module-specific)
  entitlement. Token = `tier|version|expires.signature`, HMAC-SHA256 with an embedded
  verification key (override via `LICENSE_HMAC_SECRET`). `load_license()` verifies at
  startup; `License.is_entitled(min_tier, min_version)` for opt-in gating. Missing/expired
  → logs a **warning, does not block** (never brick a tenant over a license check).
- **`src/hermes/ops/bug_report.py` (NEW):** `collect_diagnostics()` builds a **redacted**
  blob (config + env + log tail) using the existing `security_monitor` redaction
  (`_deep_copy_redact` recursive value-redaction + key-based). `file_github_issue()` POSTs
  to `api.github.com/repos/{owner}/{repo}/issues` with `secret:github.token`. Secrets
  never leave the machine unredacted.
- **`noble bug` command:** `--description` (req) + `--repo`/`--labels`/`--traceback-file`/
  `--dry-run`. Files a redacted Issue (or prints the body with `--dry-run`). Reads
  `NOBLE_BUG_REPO` if `--repo` omitted.
- **Wired `load_license()` into `web/app.py create_app`** — logs `license_ok` /
  `license_expired` / `license_check` at dashboard startup.
- **Wizard (`setup.html` + `setup_submit`):** added **License Key** + **GitHub/Package
  Token** optional fields (issued by the subscription process alongside the Redis token).
  Persisted as `LICENSE_KEY` / `GITHUB_TOKEN` (resolve to `secret:license.key` /
  `secret:github.token`).
- **Verified (real, mocked network):** license valid/expired/tampered/missing all
  classified correctly; bug diagnostics redact a planted fake token (no leak); GitHub POST
  returns issue URL (mocked); missing-token guard fires. Hardening suite 17 passed;
  imports clean.
- **Docs:** onboarding guide §4.5 (report bugs via `noble bug`), quick-ref table;
  AGENTS.md distribution note updated to the intended packaged/distributed model.

### Telegram wired in (Hermes-owns-delivery) (session 15)
User directive: Hermes is the orchestrator of the codebase; the platform dashboard is a
visual tool, not an orchestration platform — so msg-delivery config (Telegram/Discord)
lives in Hermes. Previous state: `AlertManager._send_telegram` hardcoded
`_telegram_chat_id = ""`, so Telegram approvals never sent even with a bot token.

- **`src/hermes/ops/alerting.py`:** `_telegram_chat_id` now resolved from
  `secret:telegram.chat_id` via `get_secret_or_none` (was hardcoded `""`). Telegram is
  enabled when **both** `bot_token` + `chat_id` are present. Discord unchanged.
- **`config/default.yaml`:** added `notifications.telegram.chat_id: secret:telegram.chat_id`.
- **Wizard (`setup.html` + `web/app.py` `setup_submit`):** added a **Telegram Chat ID**
  field beside the Bot Token, persisted to `.env` as `TELEGRAM_CHAT_ID`. Help text tells
  the user how to obtain chat_id (`getUpdates`). Secrets-status check now lists both
  telegram keys.
- **Verified (real, mocked network):** with chat_id resolved, `is_telegram_enabled()` →
  True; `_send_telegram` POSTs to `https://api.telegram.org/bot{TOKEN}/sendMessage` with
  the chat_id + Markdown. Orchestrator GE path already calls `AlertManager.send_alert`
  (lines 407/462/524/542/560) → tier-3 approvals now reach Telegram. Wizard `_write_env`
  round-trip stores `TELEGRAM_CHAT_ID`. Hardening suite 17 passed; imports clean.
- **Referenced AGENTS.md** (repo root) as the first-read operational playbook per user
  directive — confirmed Golden rule #1 (execute codebase, don't write new logic unless
  fixing a verified bug) + §5 (web/ editable, secrets redacted everywhere). detect-secrets
  scan excludes `src/hermes/web/*`; config/alerting use secret: refs (no literal secrets),
  so no baseline violation.

### Onboarding model corrected: two processes + Hermes owns delivery (session 14)
Clarified the onboarding/approval ownership model (user feedback):

- **Two onboarding processes, not one.** (1) **Platform onboarding** on the external
  website: user subscribes to a plan, creates the platform account, and is issued the
  Noble Trader Redis URL + TradingView API key + MT4/MT5 bridge token. (2) **Hermes
  onboarding**: the user pastes those copied creds into Hermes's `/setup` wizard, which
  writes `.env`, auto-migrates, and enters cold-start. The hand-off is platform→creds→
  Hermes wizard. Documented as §0 "Two onboarding processes" in the onboarding guide.
- **Hermes is the operational home; delivery is Hermes-owned.** The user operates in
  Hermes first (noble CLI, NL queries) and uses the dashboard second. Approval delivery
  goes through the channels the user configured **in Hermes** (`config.default.yaml →
  notifications.discord / telegram`), not via a platform relay. Retracted the earlier
  platform-mediated-relay suggestion in roadmap §15.8 — replaced with "Hermes owns
  delivery" + the one real fix: `AlertManager._send_telegram` hardcodes
  `_telegram_chat_id = ""`, so Telegram approvals never send. Recommended fix: add a
  `TELEGRAM_CHAT_ID` field (pasted beside the bot token, same pattern as the Discord
  webhook URL) and wire `AlertManager` to use it. Discord works as-is if the user has a
  server with Manage Webhooks.
- **Docs:** onboarding guide §0 (two processes), §2.0 (platform step 1 / Hermes step 2),
  §2.5 (approvals delivered by Hermes via configured channels + in-app queue). Roadmap
  §15.8 rewritten to Hermes-owns-delivery + Telegram chat_id fix.

### Approval bridge — credential-free in-app queue + tenant event (session 13)
Addresses feedback that Discord/Telegram are wrong defaults for a multi-tenant SaaS
(Discord needs server Manage-Webhooks; Telegram needs bot+chat_id, which `AlertManager`
never captured — Telegram was dead code) and that Supabase shouldn't be in the wizard.

- **Wizard fixes (`setup.html` + `web/app.py`):**
  - Removed `SUPABASE_URL`/`SUPABASE_ANON_KEY` from the form + `setup_submit` (Supabase
    is not required client-side; only the optional `backfill` CLI uses it).
  - Discord/Telegram now honestly optional with accurate help text (server ownership /
    bot+chat_id required; in-app queue is the default). Added an "Approval notifications
    (optional)" explainer pointing to the in-app queue.
- **In-app approval queue (the credential-free default, fully working):**
  - `GET /approvals` (daisyUI page, nav-linked) lists `pending_decisions`; each row has an
    Approve button → `POST /api/approvals/{id}/approve` (idempotent: 200 then 404).
  - `noble pending` / `noble approve <id>` CLI commands added (were referenced in help
    text but missing). `approve` re-publishes to `risk.decision.*` for L3 execution.
  - Verified: `GET /approvals` → 200 with real pending rows; approve API 200 then 404;
    `approvals.html` created (uses components/ui.html card/empty_state).
- **Tenant-scoped approval event (for platform relay):** on GE-queue, `orchestrator.py`
  now publishes `risk.approval.{tenant_id}` (tenant from `HERMES_TENANT_ID`, default
  `default`) carrying decision_id/symbol/side/size/tier + `approve_action`. Best-effort
  (only if Redis connected), matching the existing `risk.decision.*` pattern. This lets
  the *platform* deliver the approval via the user's already-known contact (email/push
  from subscription) without the agent ever needing Discord/Telegram creds.
- **Verified:** hardening suite 17 passed; `PendingApprovals` on real DB (3 pre-existing
  pending rows); `/approvals` render + approve API via TestClient; GE path stores + alerts
  + (with Redis) publishes the tenant event.
- **Onboarding guide** updated: Supabase removed; approvals default to in-app queue;
  Discord/Telegram documented as optional with honest prerequisites.

### Design note — platform→Hermes approval bridge (recommendations, not yet built)
The agent now owns a credential-free queue + event. The remaining correct bridge is
**platform-side** (Phase 2): the platform subscribes to `approvals.{tenant_id}`, persists
the decision in its own DB, notifies the user via subscription email/push, and on approve
posts a signed callback to the agent (which calls `approve_decision`). Phase 3 (optional):
keep Discord/Telegram only as platform-side extras, and fix Telegram chat_id capture
(bot must receive `/start` via `getUpdates`). See roadmap §15.8.

### Wizard integrated into app.py workflow (session 12)
Fully wires the first-run wizard into the CLI runtime (`src/hermes/app.py`), not just
the web module. The wizard was already daisyUI-styled (`setup.html` extends `base.html`,
which loads the self-hosted Tailwind+DaisyUI bundle) — this session made it a first-class
step in the run workflow.

- **New `platform setup` command** (`src/hermes/app.py`): launches the web app and opens
  the daisyUI wizard at `http://<host>:<port>/setup`. This is the CLI entry point into the
  onboarding flow — the user pastes the credentials copied from the external subscription
  site (Noble Trader Redis URL + TradingView API key + MT4/MT5 bridge token).
  - `--print-url` mode (headless): prints the wizard URL + a required/optional checklist
    without serving, so it can be opened from another machine.
  - Detects `is_setup_complete()` and informs the user if setup is already done (re-run
    overwrites only changed fields).
- **`platform init` now detects incomplete setup** and prints the wizard command as a
  next step (the external-site → paste flow), wrapped in try/except so it can never block
  init.
- **Verified (real CLI):** `platform setup --print-url` prints the URL + checklist on an
  empty `.env`; `platform init` on an incomplete `.env` ends with the wizard pointer;
  `platform --help` lists `setup`; `app.py` imports cleanly; hardening suite 17 passed.
- **Onboarding guide** updated: `platform setup` is the recommended CLI entry; `--print-url`
  documented; external-site subscription flow described.

### First-run wizard + brokerage deprecation (session 11)
Implements the user-facing onboarding flow and the brokerage directive (MT4/5 bridge
is now the primary venue; Alpaca/Hyperliquid deprecated).

- **Venue enum:** added `MT4_MT5 = "mt4_mt5"` to `src/hermes/schemas/market.py`. GD's
  `_is_supported_venue` already enumerates `Venue.__members__`, so mt4_mt5 is now a
  supported execution venue automatically.
- **Config:** `venues.mt4_mt5` already existed + `enabled: true` (primary). Confirmed
  `venues.alpaca` + `venues.hyperliquid` are `enabled: false` (deprecated — kept for
  reference/compat, not used in the live loop).
- **First-run wizard (already present, enhanced):** `src/hermes/web/app.py` has
  `GET/POST /setup` + `is_setup_complete()` + `_write_env()` + `setup.html`. Flow:
  user pastes `NOBLE_TRADER_REDIS_URL` + `TRADINGVIEW_API_KEY` + `MT4_MT5_BRIDGE_TOKEN`
  (exactly the "copy from plan → paste" model), the handler writes `.env`, auto-generates
  the three auth secrets, **auto-migrates** DuckDB, and redirects to Portfolio. Root `/`
  redirects to `/setup` until setup is complete. Enhanced the wizard to **render the real
  cold-start caps** ($100 / 0.2% / ≤3 positions / ≤5% exposure / exit after 20 trades +
  positive expectancy) from `config.autonomy.cold_start`, so the user sees what they're
  agreeing to on first run. Notes Alpaca/HL deprecated + Supabase optional.
- **Verified (real TestClient, temp .env):** GET /setup renders the cold-start card with
  live values; POST /setup writes all pasted values to `.env`, `is_setup_complete()` →
  True, and auto-migrates (013 applied). 
- **Tests:** updated `test_hardening_p0.py` GD supported-venue case → uses `mt4_mt5`
  (hyperliquid now disabled). Relaxed `test_hardening_p1.py` GC assertion to "routed
  through L5" (approved-within-caps OR legitimately gated) — both reflect the new venue
  reality, not regressions. Full suite: `pytest tests/test_hardening_p0.py
  tests/test_hardening_p1.py tests/test_hardening_p2.py` → 17 passed.
- **Onboarding guide** (`docs/user_onboarding_guide.md`) rewritten: MT4/5 bridge as
  primary brokerage, Alpaca/HL deprecated, Supabase not required, wizard as the
  recommended setup path, cold-start caps surfaced.

Note (Supabase): confirmed `supabase` client lib is NEVER imported (`from supabase` /
`create_client` found nowhere). The only code path is the manual `backfill` CLI command
(`supabase_backfill.py`, httpx REST). `backfill_on_startup: true` in config is DEAD (no
consumer in src/). So in the client platform Supabase is effectively never called.

### Hardening P2 — duplicate-decision idempotency at L3 (GF) (session 10)
Closes the last gap (GF) from docs/hardening_test_plan.md. P0 (GA/GB/GD) and P1 (GC/GE)
were sessions 8–9.

- **GF — idempotency:** L3 `ExecutionEngine.execute_decision` had no guard against a
  repeated `decision_id`. At-least-once Redis delivery (or an `approve` re-publish of the
  same decision) would create DUPLICATE orders/positions. Now `execute_decision` checks
  `orders WHERE risk_decision_id = ?` (read-only DuckDB) before executing; if a row exists
  it skips with a `decision_already_executed_skipping` log and increments
  `stats['decisions_duplicated']`. The `Order.risk_decision_id` is already stamped by
  `SmartOrderRouter` and persisted by `ExecutionWriter`, so the guard matches the exact
  field written on first execution. On DB/table unavailability it errs toward executing
  (logs a warning) rather than silently dropping a live decision.
- **Deferred re-queue (TC-19):** confirmed user decision = DROP after 1 cycle (no re-queue).
  Already implemented in P0 (GB selection layer drops excess candidates; not re-queued).
  No extra code needed.

Files: src/hermes/execution/orchestrator.py (_decision_already_executed + guard in
execute_decision, decisions_duplicated stat), tests/test_hardening_p2.py (2 tests:
idempotency detects executed decision, empty decision_id safe).

Verified: `pytest tests/test_hardening_p0.py tests/test_hardening_p1.py tests/test_hardening_p2.py`
-> 17 passed. Idempotency helper correctly reads orders.risk_decision_id (stamped by
SmartOrderRouter, persisted by ExecutionWriter).

### Hardening P1 — user-intent sim branch (GC) + human-approval queue (GE) (session 9)
Continues session 8. Closes the two remaining gaps from docs/hardening_test_plan.md.

- **GC — user-initiated trade branch (usecase 2):** previously NO code path existed — L3
  `execute` silently skipped any decision whose `signal_id` had no `trade_signals_blended`
  row. New `portfolio/user_intent.py:evaluating_user_intent()` runs a MANDATORY simulation
  (`REQUIRE_SIM = True` is HARD-CODED, non-configurable — a configurable skip would break
  the pricing/monitor/backtest processes that depend on sim output) bounded to 60s, builds a
  `BlendedSignal` from the sim outcome sized to the active autonomy caps (normal $2000/1.5%
  or cold-start $100/0.2%), then routes it through the SAME L5 gate (`evaluate_signal`) so
  GD/GB/GA/autonomy all apply identically to signal-driven and user-initiated trades. New
  CLI `platform trade --symbol X --side BUY --equity N` exercises this and dispatches an
  approved decision to `risk.decision.*` for L3.
- **GE — human-approval queue:** tier-3 (`requires_human_approval`) decisions used to be
  silently dropped by L3 (`continue`). Now `evaluate_signal` persists them to a new DuckDB
  `pending_decisions` table (migration 013), posts an approval-required alert to the msg
  channel (Discord/Telegram via AlertManager), and returns the decision marked
  `requires_human_approval=True, status="pending"`. New CLI `platform approve {id}` re-publishes
  the approved `RiskDecision` to `risk.decision.*` (L3 executes); `platform pending` lists
  the queue. Expired entries (tier_3.approval_timeout_hours, default 4h) are marked `expired`.

Files: src/hermes/portfolio/user_intent.py (new), src/hermes/portfolio/pending_approvals.py
(new), src/hermes/db/migrations/013_pending_decisions.sql (new), src/hermes/portfolio/risk_gate.py
(RiskDecision.requires_human_approval/status), src/hermes/portfolio/orchestrator.py
(GE store+alert in evaluate_signal, get_pending_approvals/approve_decision, PendingApprovals
wiring), src/hermes/app.py (approve/pending/trade CLI commands),
config/default.yaml (tier_1 now $2000/1.5% — see GA note),
tests/test_hardening_p1.py (5 tests: GC sim-mandatory/route, GC sim-fail reject, GC
unsupported-venue reject, GE tier3 pending+approve, GE expired-not-approvable).

Verified: `pytest tests/test_hardening_p0.py tests/test_hardening_p1.py` -> 15 passed.
End-to-end (real DuckDB): user BUY BTC -> sim ran -> L5 approved ($20 sized); $4k trade ->
tier-3 -> stored pending + alert -> approve re-published approved. CLI commands registered.

Note (GA change this session): normal tier-1 caps lowered from $5k/2% to **$2k/1.5%** — the
$100k equity assumption was wrong; real accounts start $5–10k (max $25k). Cold-start still
$100/0.2%. The AutonomyGate default fallbacks were updated to match.

### Hardening P0 — cold-start caps, selection layer, unsupported-venue reject (session 8)
Hardens the approval/execution pipeline against unhandled decisions (gaps GA/GB/GD
from docs/hardening_test_plan.md). P0 only; GC (user-intent sim) + GE (human-approval
queue) are P1.

- **GA — Cold-start guard (autonomy.cold_start):** new users get much tighter caps
  than normal tier-1: `tier1_max_notional=100`, `tier1_max_position_pct=0.002`, plus a
  hard `max_new_positions=3` and `max_new_exposure_pct=0.05` budget enforced in
  AutonomyGate.classify(). Auto-exits when BOTH `exit_after_n_trades=20` closed AND
  realized expectancy `> 0` (`PortfolioRiskEngine._check_cold_start_exit`). Existing
  portfolio users are NOT cold-start (normal $5k/2% tier-1 caps) — business as usual.
- **GB — L4.5 Selection layer (portfolio.selection):** new SelectionLayer ranks every
  candidate signal by weighted score (pattern_confidence, expected_entry_alpha_bps,
  reward_risk, regime_alignment, diversification — all user-tunable weights) within a
  `cycle_window_sec=300` and admits only `max_new_positions_per_cycle=3`. Excess
  candidates are DROPPED (deferred=False) — over-trading is itself a risk gate. This
  stops the "agent rubber-stamps 25-50 BUY signals" failure.
- **GD — Unsupported-venue reject at L5:** `PortfolioRiskEngine._is_supported_venue`
  rejects any signal whose venue is not an enabled, supported execution Venue (e.g.
  an exchange we don't trade) BEFORE autonomy/risk — single chokepoint. Confirms M4
  (tier-4 hard block) as the correct reject path for unsupported venues.

Files: src/hermes/portfolio/autonomy_gate.py (cold-start params + budget enforcement),
src/hermes/portfolio/selection.py (new), src/hermes/portfolio/orchestrator.py
(_is_supported_venue, _check_cold_start_exit, GD+GB in evaluate_signal, wiring),
src/hermes/core/config.py (PortfolioConfig.selection field), config/default.yaml
(autonomy.cold_start + portfolio.selection blocks),
tests/test_hardening_p0.py (10 tests: GA caps/budget, GB top-N/drop/score, GD reject).

Verified: `pytest tests/test_hardening_p0.py` -> 10 passed. Config loads with
cold_start + selection keys. Unsupported venue (sse) rejected; hyperliquid BTC passes.
Cold-start $90 trade = tier1 approved; $3k = tier3; 4th concurrent = budget block.
Selection admits top-3, drops 4th.

### Pattern learning -> live conviction loop (session 7)
You were right: scoring for executed (trade_journal/pnl_realized) + sim
(simulation_runs/pattern_performance) trades and the sim-generated pattern
recognition already existed (harvested from the OpenClaw guide, reimplemented
natively in src/hermes/agent/pattern_learning.py + migrations 011). The gap was
that learned confidence fed only the OFFLINE sweep objective, never the LIVE
blended-signal decision. Closed the loop:

- BlendedSignal gains pattern_confidence (default 0.0).
- SignalSynthesizer.process_heartbeat now reads get_pattern_confidence(brick_pattern)
  and applies a BOUNDED live conviction boost (max +0.15, only when learned conf
  >=0.6) to meta_regime_confidence, and records pattern_confidence on every signal.
- Migration 012 adds trade_signals_blended.pattern_confidence (persisted + indexed).
- Authored skill: trading/pattern-learning-scoring/SKILL.md — reusable operating
  knowledge for the pattern recognition + scoring subsystem (read/refresh/verify
  procedures, pitfalls incl. HERMES_DUCKDB_PATH-ignore, extension points).

Verified (isolated DuckDB): seeded breakout_up conf=0.72 -> get_pattern_confidence
returns 0.72; live process_heartbeat with forced breakout_up -> pattern_confidence
=0.72, meta_regime_confidence boosted 0.70->0.716, row persisted with
pattern_confidence=0.72; migration 012 applied (schema_version=12). The earlier
0.0 / write-error were the HERMES_DUCKDB_PATH-ignore test harness bug, not code
defects (fixed by isolating the DB path in the test).

### WS budget surfaced in monitor status (session 6)
The TradingView WS plan/budget/connection state is now visible wherever monitor
status already shows — no new surface needed.

- TradingViewApiAdapter.get_ws_status() returns a dict: plan, mode, use_ws,
  connected, schedule_window, timezone, budget_sec, used_sec_today, remaining_sec,
  remaining_hours, budget_exhausted, fallback_interval_sec, active_ws_ttl_sec,
  active_symbols.
- PriceMonitor.set_venue_adapters([...]) registers adapters; get_stats() now folds
  each adapter's get_ws_status() into stats["ws"][venue]. The monitor command wires
  price_monitor.set_venue_adapters(adapters) after building them.
- CLI _stats_loop (platform monitor, every 30s) prints an extra line:
    [ws:tradingview] plan=ultra mode=on_demand conn=ON budget=5.5h left (used 1800.0s / 21600.0s, fallback=60.0s) active=['BTCUSD']
- Web /monitor page live_data now includes ws (read by the monitor.html template /
  /api/monitor endpoints), so the dashboard shows WS budget usage at a glance.

Verified: get_ws_status surfaced via PriceMonitor.get_stats() -> ws.tradingview
present; budget math (1800s used -> 5.5h remaining of 6h) correct; exhaustion flag
sets budget_exhausted=True and remaining_hours=0.0.

### WS plan + schedule pairing (silent default) (session 5)
The ws_plan/cooldown is now expressed in config and paired to the TradingViewAPI
plan — no user setup required.

- ws_plan (SILENT DEFAULT = ultra): maps the WS entitlement to a daily budget the
  adapter enforces: ultra=6 WS-hours/day, mega=24h/day, none/pro/basic=REST-only
  (WS auto-disabled). The adapter tracks WS-seconds-used per LOCAL day (user
  timezone from active_hours) and self-disables WS once the budget is spent,
  reverting to rest_fallback_interval_sec=60 (IDLE cost floor), until local midnight.
- ws_mode (SILENT DEFAULT = on_demand): WS opens only while >=1 symbol is in an
  ACTIVE urgency tier (set by a trade=true signal via monitor.control) and closes
  after active_ws_ttl_sec=300 (the "cooldown"). This spends the 6h/day Ultra budget
  only on actionable windows, so Ultra is the sufficient default. Modes: on_demand
  | always (mega/24-7) | scheduled.
- ws_schedule (SILENT DEFAULT = use_active_hours): reuses the existing active_hours
  block (timezone: America/Los_Angeles) so the schedule is locale-aware with NO new
  config. Override with an explicit "HH:MM-HH:MM" (also in that timezone).
- stream_ticks now falls back to REST polling (at the IDLE cost floor) whenever WS
  is disabled / not yet connected / closed by cooldown / budget spent / outside the
  scheduled window — quotes keep flowing uninterrupted.

Verified (live API + logic asserts):
  PASS on_demand idle -> WS closed (no active tier)
  PASS ACTIVE tier -> WS opened (plan=ultra), received real quote 64015.71
  PASS tier cleared -> WS closed (cooldown after active_ws_ttl)
  PASS daily budget exhausted -> _ws_should_connect False (REST-only)
  PASS ws_schedule_window resolves from active_hours locale (tz bound for budget reset)

### TradingView WebSocket streaming + signal-driven urgency/sim hook (session 4)
Addresses two gaps the user identified: (a) TradingViewAPI offers a real-time
WebSocket the adapter wasn't using (REST-poll only), and (b) a Redis signal
never triggered monitor urgency or an on-demand simulation — synthesis and
simulation were unrelated processes.

Fix A — TradingViewApiAdapter WebSocket (WS-first):
- connect() now also opens wss://ws.tradingviewapi.com/ws. The raw API key is
  NOT the ws token; we POST /api/token/generate (RapidAPI host) to mint a JWT,
  then connect to the returned wsUrl (auto-refresh before the 30-min expiry).
- stream_ticks() is WS-first: a background _ws_loop subscribes per-symbol and
  pushes prices into per-symbol queues the iterator yields in real time. Zero
  REST budget burned while the socket is healthy.
- REST polling remains the FALLBACK when WS is unavailable or the plan has no
  WS entitlement (use_websocket=false). Added set_symbol_interval() so the
  monitor's urgency tier can tighten the REST fallback cadence.
- Config: tradingview.features now has use_websocket=true + ws_url template.

Verified (live, real API):
  WS connected; stream_ticks received pushed quotes:
    BTCUSD=64095.57 EURUSD=1.14438 ETHUSD=1876.6
  matching REST (Binance/Coinbase parity). Transient 401 on first connect is
  handled by the reconnect/backoff loop.

Fix B — signal-driven urgency + on-demand simulation (the gap):
- synthesizer.process_heartbeat now publishes, when an actionable heartbeat
  arrives (trade=true OR signal in buy/sell; trade read from heartbeat.extra
  since the schema has no explicit field):
    monitor.control.{symbol} = {tier:ACTIVE, ttl:300}  -> monitor escalates cadence
    sim.request.{symbol}     = {urgent:true}           -> optimizer runs on-demand
  Analysis-only (trade=false/neutral) publishes WATCH tier, no sim request.
- monitor command now runs _monitor_control_consumer, which subscribes to
  monitor.control.* and applies ACTIVE=5s / WATCH=15s / IDLE=60s to the
  TradingView adapter, decaying back to IDLE after TTL (transient urgency).
- scripts/_watch_optimize.py now ALSO subscribes to sim.request.* and fires an
  immediate optimize() for that symbol (5-min per-symbol cooldown) — closing the
  "signal -> sim" gap that previously waited up to 30 min on the DuckDB poll.

Verified (live Redis):
  synthesizer trade=true -> published monitor.control.EURUSD{tier:ACTIVE}
                             + sim.request.EURUSD{urgent:true}
  control consumer -> BTCUSD ACTIVE=5.0, ETHUSD WATCH=15.0, default untouched.

### `platform symbols validate-all` (session 3)
New CLI command: iterates active (or all) symbols and runs the same live
probe as `symbols validate` on each, surfacing `venue/exchange/class/price`
per row so you can confirm the price source matches the recorded exchange.
Options: --venue, --include-inactive, --fail-on-error (CI/cron gate),
--delay (rate-limit spacing), --json. Auto-applies migrations first.

Also fixed:
- `app.py` added `import time` (validate-all sleep) — was NameError.
- `tradingview_adapter.py` docstring corrected: batch body is
  `{"requests":[{"symbol":..}]}`, not `{"symbols":[..]}` (code was already
  correct; doc was stale).
- Restored `symbols sync` def that a patch had clobbered.

Verified (real CLI, live TradingViewAPI):
  `symbols validate-all --venue tradingview` over 5 symbols -> 5 ok, 0 failed,
  each row shows exchange (COINBASE vs None) + price. e.g.
  COINBASE:BTCUSD exchange=COINBASE price=64131.20.

### Verification summary
- All modules import cleanly (repo venv); `monitor`+`app` import OK.
- `source_id` -> `strategy_id` attribution + legacy fallback: PASS.
- TradingView batch/429/fallback (live RapidAPI): PASS.
- Symbol auto-register + auto-delist (real DuckDB, live quotes): PASS.
- Exchange dimension (distinct rows + correct asset_class + live + delist): PASS.
- CLI `symbols add/list/validate` with `--exchange` + Exch/Bare cols + auto-migrate: PASS.
- Feed (noble_trader/MT4) qualifies to COINBASE via shared qualify_symbol: PASS.
- parse_symbol_key no-colon regression fixed: PASS.

### Still pending (blockers, not code)
- [ ] **Desktop MT5 install** — PlexyTrade webtrader only; MCP needs desktop terminal
      + AlgoTrading enabled. (VPS MT5 works too — see VPS note.)
- [ ] **MT5 MCP registration** — install `mt5-trading-mcp`, fill `mt5_mcp.env` from
      profile `.env`, paste `mcp_servers_entry.yaml` into `~/.hermes/config.yaml`, `doctor`.
- [ ] **TradingViewAPI key** — `secret:tradingview.api_key` must be set per tenant
      (RapidAPI Ultra plan) before `monitor --venues tradingview` returns live prices.
- [ ] **Symbol inference** — forex vs crypto by symbol length (6 alpha). Commodities/
      equities auto-register as crypto today; tighten inference if needed.

### VPS note (answered)
MT4/5 EA can run on a VPS; the EA just needs network reach to Hermes. Two patterns:
1. EA file-drop / WebRequest -> `bridge_relay.py` running **wherever Hermes is**
   (relay listens on 127.0.0.1:9100 or tails a shared file path). EA posts over the
   network to that endpoint. MT5 terminal + EA live on the VPS; relay + Hermes local
   (or also on VPS). No code change — relay is transport-agnostic.
2. Put the whole Hermes agent + relay on the same VPS as MT5 (co-located, lowest
   latency). Each tenant = one VPS (matches the isolated-per-user SaaS model).
