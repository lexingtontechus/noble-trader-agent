# Worklog — Noble Trader Plugin Consolidation

## Goal

Refactor noble-trader-agent Hermes plugin architecture:
- Collapse build/deploy into `desktop-plugins/` only; drop the `plugins/` deploy
- Remove dead legacy paths (loopback `:8080` fallback, "import" runtime mode,
  "proxy" runtime mode)
- Establish clean separation: user-facing plugin (desktop) vs backend (agent)
- User never interacts with backend directly; plugin integrated into Hermes
  runtime at startup via `watchdog.sh` (on_session_start hook)
- Confirm `NOBLE_TRADER_QUOTE_PROXY_URL` needs no separate credentials

## Changes

### 1. `desktop/plugin.js` (Electron runtime frontend)
- Removed `AGENT_BASE` constant + `postToAgent()` loopback fetch helper
- Removed `:8080` loopback fallback; all data via Hermes `ctx.rest()` →
  `/api/plugins/noble-trader/*` (native shim is the sole data path)
- Updated `SETUP_FIELDS`: replaced `NOBLE_TRADER_SIGNAL_USER` /
  `NOBLE_TRADER_SIGNAL_PASSWORD` / `NOBLE_TRADER_REDIS_URL` with
  `NOBLE_TRADER_PROXY_REDIS_URL` (full URL) + `NOBLE_TRADER_QUOTE_PROXY_URL`
- Removed `validateSignal()` (no separate signal-URL validation — the Redis URL
  is validated by connecting to the proxy TCP endpoint)
- Added `setup-status` gate: `PortfolioTab` calls `useAgent('/setup-status')`
  on mount; if `setup_complete === false`, auto-switches to Setup tab and shows
  a "Configure" button on Portfolio
- Removed `source` prop from `ConnError` (no loopback tracking)
- Fixed `StatusTab`: changed `useAgent('/api/plugin/status')` → `'/status'`
  (avoid double-prefixing the path)

### 2. `dashboard/plugin_api.py` (backend FastAPI router)
- Removed `_resolve_runtime()` and all "import" / "proxy" / "unavailable" mode
  branching
- All data routes go through `_call_agent_shim()` → `agent_api_shim.py` → agent
  venv Python (native shim only)
- Removed `NOBLE_TRADER_API_URL` proxy-mode support
- `GET /setup` returns 410 Gone with updated notice (points to plugin Setup tab)
- `POST /setup` routes to agent shim op `setup` → agent's `/setup` endpoint

### 3. `src/hermes/web/app.py` (agent backend)
- `index()`: when setup incomplete, redirect to `/setup` (410 Gone notice) instead
  of silently sending to `/portfolio`
- `GET /setup`: updated 410 notice to list the correct credential fields
- `POST /setup`: already correct — validates `_SETUP_REQUIRED_KEYS` + writes `.env`
- `_SETUP_REQUIRED_KEYS`: already includes `NOBLE_TRADER_PROXY_REDIS_URL` +
  `NOBLE_TRADER_QUOTE_PROXY_URL` (no change needed)

### 4. `src/hermes/app.py` (Hermes CLI)
- Removed `--print-url` option from `platform setup` command
- Updated setup-incomplete instructions to list the new field names
- Updated `--host`/`--port` help text to note they're deprecated

### 5. `scripts/agent_api_shim.py`
- No changes needed — already routes `setup` (POST) → `/setup` and
  `setup-status` (GET) → `/api/plugin/setup-status`

### 6. `scripts/deploy_desktop_plugin.py`
- Removed dual-deploy to `plugins/` — now deploys **only** to
  `desktop-plugins/<name>/` (where the Electron runtime loader reads `plugin.js`)
- Removed `_sync_root_plugin_js()` helper (no more root `plugin.js` to sync)
- Deploy target structure: `desktop-plugins/noble-trader/plugin.js` +
  `desktop-plugins/noble-trader/dashboard/` (for web-backend discovery cache)
- Updated `find_plugin_source()` to look for `desktop/plugin.js` (not just
  `dashboard/manifest.json`)
- Removed stale references to `deploy_desktop_runtime_plugin.py` in docs

### 7. `config/default.yaml`
- Added `quote_proxy:` section under `noble_trader:` with
  `url: secret:noble_trader.quote_proxy_url` (pasted by user during onboarding)

### 8. `.env.example`
- Replaced `NOBLE_TRADER_REDIS_URL` + `NOBLE_TRADER_REDIS_CHANNEL` +
  `NOBLE_TRADER_REDIS_CONSUMER_GROUP` with `NOBLE_TRADER_PROXY_REDIS_URL`
  (full URL with embedded creds) + `NOBLE_TRADER_QUOTE_PROXY_URL`

### 9. Docs (`README.md`, `docs/plugin-reference.md`)
- Rewrote both to reflect native-shim-only backend connection
- Removed "Backend Connection Modes" table (import/proxy modes)
- Removed loopback `:8080` fallback references from architecture diagrams
- Added credential field mapping table with `NOBLE_TRADER_QUOTE_PROXY_URL`
  confirmation that it needs no separate credentials (auth via X-Plan-Prefix)

## Verification
- `node --check` on `plugin.js`: PASS
- `python -c "import py_compile; ..."` on `plugin_api.py`, `app.py`, `app.py`: PASS
- Deployed copy at `~/.hermes/plugins/noble-trader/desktop/plugin.js` matches
  source (both 27,732 bytes pre-edit, sync confirmed via deploy script)

## Key findings
- `NOBLE_TRADER_QUOTE_PROXY_URL` requires NO credentials — auth is via the
  `X-Plan-Prefix` header derived from the Redis username (`pp-sub-...` → `pp`).
  Safe to collect as a plain URL field (no validation against a secret).
- The "import" runtime mode in `plugin_api.py` was dead code — it imported
  `hermes.web.app` from the **Hermes desktop app's** Python env, not the agent's
  venv. Only the native shim (`agent_api_shim.py` → agent venv) works.
- The `plugins/` directory (web backend discovery) is NOT the same as
  `desktop-plugins/` (Electron frontend). The deploy script previously duplicated
  the entire plugin tree to both. Now it deploys only to `desktop-plugins/` —
  the `plugins/` copy in the runtime is pre-deployed/cached at backend startup.