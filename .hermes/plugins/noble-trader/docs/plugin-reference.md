# Noble Trader Desktop Plugin

> **Status:** Active — built 2026-08-02
> **Distribution:** Bundled in the `hermes-trading-platform` pip package, deployed to `~/.hermes/desktop-plugins/noble-trader/`

## Overview

The Noble Trader desktop plugin surfaces the trading platform's onboarding wizard,
portfolio data, and signal health directly inside the **Hermes desktop app** (the
Electron surface). It is a plain-ESM JavaScript module (`desktop/plugin.js`) that
imports `react` + `@hermes/plugin-sdk`, plus a FastAPI backend router
(`dashboard/plugin_api.py`) that shells out to the noble-trader-agent via
`scripts/agent_api_shim.py`.

Additionally, a **Python plugin** (`plugin.yaml` + `__init__.py`) registers
agent-side tools (`noble_balance`, `noble_assets`, `noble_status`) for in-chat
use and an `on_session_start` hook that auto-launches `scripts/watchdog.sh`.

---

## File Layout

```
.hermes/plugins/noble-trader/
├── plugin.yaml                # Python plugin manifest (agent tools)
├── __init__.py                # register_tools() + on_session_start watchdog hook
├── README.md                  # user-facing setup guide
├── dashboard/
│   ├── manifest.json          # Dashboard plugin manifest (web backend discovery)
│   └── plugin_api.py          # FastAPI router → /api/plugins/noble-trader/*
└── desktop/
    └── plugin.js              # ← Runtime UI the Electron app loads (plain ESM)
```

**Deployed copy** (after running the deploy script):

```
<hermes_home>/desktop-plugins/noble-trader/
├── plugin.js                  # ← Electron runtime-loader reads this root file
├── dashboard/
│   ├── manifest.json
│   └── plugin_api.py
```

> The deploy script copies `desktop/plugin.js` to `desktop-plugins/noble-trader/plugin.js`
> (the exact path the Electron runtime loader reads) plus the `dashboard/` directory
> for web-backend discovery.

---

## Backend Connection — Native Shim Only

The plugin's `plugin_api.py` communicates with the noble-trader-agent **exclusively**
via the **native shim** — it shells out to the agent's venv Python via
`scripts/agent_api_shim.py` with `PYTHONPATH` set to the agent's `src/` directory.

The legacy "import" mode (which tried `from hermes.web.app import ...`) never worked
in the Hermes dashboard process — it imported the wrong `hermes` package. The legacy
"proxy" mode (forwarding to `http://127.0.0.1:8080`) is retired. Both are removed.

All plugin data routes resolve through the shim:

| Route | Method | Shim op | Agent endpoint |
|---|---|---|---|
| `/api/plugins/noble-trader/portfolio` | GET | `portfolio` | `/api/plugin/portfolio` (read-only equity/positions) |
| `/api/plugins/noble-trader/status` | GET | `status` | `/api/plugin/status` (process + Redis health) |
| `/api/plugins/noble-trader/setup-status` | GET | `setup-status` | `/api/plugin/setup-status` (onboarding completeness) |
| `/api/plugins/noble-trader/health` | GET | local | — |
| `/api/plugins/noble-trader/config` | GET | local | — |
| `/api/plugins/noble-trader/setup` | POST | `setup` | `/setup` (write `.env` + migrate) |

No loopback fallback to `:8080` — the native shim is the only data path.

---

## Deployment

### Quick deploy

```bash
# From the repo checkout or installed package
python scripts/deploy_desktop_plugin.py

# Deploy to all profiles
python scripts/deploy_desktop_plugin.py --all

# Preview without writing
python scripts/deploy_desktop_plugin.py --dry-run
```

The script deploys **only** to `desktop-plugins/` — the Hermes web backend's
`plugins/` discovery path is pre-deployed in the runtime and cached at startup.

After deploying, restart the Hermes desktop app (or press ⌘K → "Reload desktop
plugins").

---

## Frontend Architecture

The runtime frontend (`desktop/plugin.js`) is a single plain-ESM module:

1. Imports `React` + `@hermes/plugin-sdk` for `ROUTES_AREA`/`SIDEBAR_NAV_AREA` and
   `ctx.registerMany([...])`. Uses `React.createElement` (no JSX) — no build step.
2. Reads data via **`ctx.rest()` (same-origin)** → Hermes-hosted plugin backend at
   `/api/plugins/noble-trader/*`, which dispatches to the agent via the native shim.
3. Exports a `NobleTraderPlugin` component with three tabs:
   - **Portfolio** — live equity, positions, metaregime (auto-shows Setup if
     onboarding is incomplete)
   - **Setup** — native wizard form (MetaApi demo + live credential pairs,
     `NT_MODE` select, `/validate-metaapi` live validation, JSON submit to
     `/setup`, auto-graduation from demo → live)
   - **Status** — process health, Redis connection, recent signals
4. Registers via `ctx.registerMany([{ id: 'page', area: ROUTES_AREA, data: { path:
   '/noble-trader' } }, { id: 'nav', area: SIDEBAR_NAV_AREA, data: { codicon:
   'graph', label: 'Noble Trader', path: '/noble-trader' } }])`.

**No build step required** — the plugin loads uncompiled. The desktop app watches
the directory for changes and hot-reloads on save.

### Onboarding flow

1. User opens the Noble Trader plugin in the Hermes desktop app.
2. `PortfolioTab` calls `useAgent('/setup-status')` on mount.
3. If `setup_complete === false`, the Setup tab auto-activates and the Portfolio
   tab shows a "Configure" button (clicking it switches to Setup).
4. User pastes credentials (collected as 2 fields — `NOBLE_TRADER_PROXY_REDIS_URL`
   and `NOBLE_TRADER_QUOTE_PROXY_URL` — plus MetaApi demo/live pairs and
   optional notification webhooks).
5. On submit, the frontend POSTs the raw form JSON to `/api/plugins/noble-trader/setup`.
6. The backend writes `.env`, generates `config/user.local.yaml` (if portfolio
   preferences were set), and runs `apply_migrations()` to cold-start the account.
7. `setup_status` flips to `complete`; the plugin auto-refreshes and Portfolio
   shows live data.

### Credential field mapping

| Plugin field | Env var written | Notes |
|---|---|---|
| `NOBLE_TRADER_PROXY_REDIS_URL` | `NOBLE_TRADER_PROXY_REDIS_URL` | Full Redis URL with Supabase `sub_<hex>` credentials embedded |
| `NOBLE_TRADER_QUOTE_PROXY_URL` | `NOBLE_TRADER_QUOTE_PROXY_URL` | Public SSE endpoint — **no separate credential** (auth via `X-Plan-Prefix` header derived from the Redis username) |
| `TRADINGVIEW_API_KEY` | `TRADINGVIEW_API_KEY` | RapidAPI key for price data |
| `METAAPI_TOKEN_DEMO` / `METAAPI_ACCOUNT_ID_DEMO` | `METAAPI_TOKEN_DEMO` / `METAAPI_ACCOUNT_ID_DEMO` | Demo (paper) account |
| `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` | `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` | Live account (auto-graduated to) |
| `NT_MODE` | `NT_MODE` | `demo` (default) or `live` |
| `DISCORD_WEBHOOK_URL` | `DISCORD_WEBHOOK_URL` | Optional trade-approval notifications |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | same | Optional trade-approval notifications |

There is no `NOBLE_TRADER_QUOTE_PROXY_URL` secret needed — the URL is public. Auth
on the proxy's `/sse/alerts` and `/quotes` endpoints uses the `X-Plan-Prefix`
header, derived from the Redis username (`pp-sub-...` → prefix `pp`). See
`sse_consumer.py:137` and `charts/_data.py:67`.

---

## Security

- Plugin HTTP routes go through the dashboard's auth middleware — every request
  must present a valid session token or cookie.
- The `plugin_api.py` `api` field in `manifest.json` is validated for path
  traversal by `_safe_plugin_api_relpath()` in the dashboard server.
- Secrets in config responses are redacted via `redact_config_for_display()`.
- `.env` is git-ignored; only `config/user.local.yaml` (non-secret user overrides)
  is optionally written by the wizard.

---

## Known Limitations

1. **No full HTML embedding:** The noble-trader-agent's web app uses Jinja2
   templates + Tailwind. The desktop plugin frontend is plain ESM JS. Full
   embedding would require an iframe or a full reimplementation in React.

2. **Agent-side tools are optional:** The Python plugin (`plugin.yaml`) requires
   explicit enablement via `plugins.enabled` in config.yaml. The desktop plugin
   (tabs) loads automatically once deployed.

---

## Related

- [Hermes Desktop Plugin SDK](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk)
- [Hermes Python Plugin System](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
- [AGENTS.md](../AGENTS.md) — Noble Trader stack operational playbook
- [noble-trader-quant-hf-manager skill](https://github.com/lexingtontechus/noble-trader-quant-hf-manager) — Hermes profile skill for stack management