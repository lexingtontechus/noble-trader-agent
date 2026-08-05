---
title: "Noble Trader Desktop Plugin"
description: "The Noble Trader desktop plugin surfaces portfolio, setup, and status inside the Hermes desktop app. It calls the agent's web app directly at http://127.0.0.1:8080/api/plugin/*."
date: "2026-08-04"
tags: [plugin, desktop-plugin, distribution, pip]
---

# Noble Trader Desktop Plugin — Documentation

## Overview

The Noble Trader quant stack operates as supervised processes (watchdog
cron → loops → MT4/MT5 bridge) driven by the Hermes agent profile
`noble-trader-quant-hf-manager`. The Noble Trader desktop plugin embeds
portfolio, setup, and signal health directly inside the **Hermes desktop app**
as a native tab — alongside the agent's chat, sessions, files, and terminal panes.

## Architecture: Direct Fetch to Agent Web App

The plugin's frontend (`desktop/plugin.js`) makes **direct HTTPS calls** to the
Noble Trader agent's web app:

```
desktop/plugin.js
  │
  │  fetch('http://127.0.0.1:8080/api/plugin/brokerage')
  │  fetch('http://127.0.0.1:8080/api/plugin/portfolio')
  │  fetch('http://127.0.0.1:8080/api/plugin/setup-status')
  │  fetch('http://127.0.0.1:8080/api/plugin/status')
  │
  ▼
src/hermes/web/app.py   ← FastAPI app on port 8080 (started by watchdog)
```

### Key design decisions:
1. **No `ctx.rest()`** — The Hermes desktop app's `ctx.rest()` routes through
   `hermes serve` (headless mode), which does NOT mount Noble Trader's routes.
   This caused 404 errors and gateway crashes.
2. **Direct `fetch()`** — Plugin calls the agent web app on `127.0.0.1:8080`
   directly. This port is started by the watchdog's `dashboard` loop.
3. **CORS enabled** — `app.py` has CORS middleware allowing `*` origins on
   `/api/plugin/*` routes (auth-exempt, read-only, non-sensitive data).
4. **No `plugin_api.py` backend** — The old `dashboard/plugin_api.py` mounted
   routes at `/api/plugins/noble-trader/*` via the Hermes gateway. This is
   no longer used. The plugin uses `/api/plugin/*` paths on the agent web app.

## Plugin File Structure

```
.hermes/plugins/noble-trader/
├── plugin.yaml              # Python plugin (agent tools: noble_balance, etc.)
├── __init__.py              # Python plugin: register_tools + on_session_start hook
├── desktop/
│   └── plugin.js            # Electron desktop UI (ESM, React.createElement)
├── dashboard/
│   ├── manifest.json        # Legacy dashboard manifest (retained for compat)
│   ├── plugin_api.py        # Stub routes — actual routes are in app.py
│   └── dist/
│       ├── index.js         # Legacy browser dashboard (not used by desktop app)
│       └── style.css
└── plugin.js                # Root copy of desktop/plugin.js (for Electron loader)
```

## Frontend (`desktop/plugin.js`)

The runtime frontend is a single plain-ESM module:

1. **Imports SDK primitives:** `import { cn, ROUTES_AREA, SIDEBAR_NAV_AREA } from "@hermes/plugin-sdk"`
2. **Calls agent web app directly:** `fetch('http://127.0.0.1:8080/api/plugin/brokerage')`
   - No `ctx.rest()` or `ctx.socket()` — direct HTTP calls
   - No IPC layer dependency on Hermes gateway
3. **Retries with limit:** `useRemoteData()` hook has a max 10 retries with
   exponential backoff (capped at 30s) — prevents infinite loops that crash
   the gateway during agent cold-start.
4. **Graceful degradation:** If backend is unreachable, shows "$0.00 USD"
   instead of NaN or error dialogs.

### Three tabs:
- **Portfolio tab:** Fetches `/api/plugin/brokerage` + `/api/plugin/portfolio` +
  `/api/plugin/status`. Renders equity cards, positions list, recent trades,
  and signal health. Always shows a numeric value (defaults to $0.00).
- **Setup tab:** Local form that POSTs credentials to `/api/plugin/setup`.
  No auto-redirects on load. User can navigate freely between tabs.
- **Status tab:** Fetches `/api/plugin/health` + `/api/plugin/status`. Shows
  process health and connection badges.

## Backend (`/api/plugin/*` routes in `app.py`)

All routes are auth-exempt and CORS-enabled:

| Route | Purpose |
|-------|---------|
| `GET /api/plugin/health` | Plugin health check |
| `GET /api/plugin/setup-status` | Returns setup state + missing vars |
| `GET /api/plugin/portfolio` | Portfolio metrics + account snapshot |
| `GET /api/plugin/brokerage` | Live MetaApi equity + positions + trades |
| `GET /api/plugin/status` | Process health + subsystem status |
| `GET /api/plugin/config` | Redacted config |
| `POST /api/plugin/setup` | Write credentials to `.env` |

## Auto-start: Watchdog via `on_session_start` Hook

The Python plugin (`__init__.py`) registers a Hermes `on_session_start`
lifecycle hook. When Hermes starts:
- Hook launches `scripts/watchdog.sh` detached
- Watchdog starts Redis + agent dashboard (`:8080`) + all supervised loops
- No manual `watchdog.sh` launch needed

The watchdog is idempotent (single-instance lock + name-based liveness),
so the `on_session_start` hook + the 5-min cron (`noble-stack-watchdog`)
can both fire safely.

## Dual-mode MetaApi (demo + live)

The Setup tab collects two MetaApi credential pairs:

| Var | Purpose |
|-----|---------|
| `METAAPI_TOKEN_DEMO` / `METAAPI_ACCOUNT_ID_DEMO` | Demo (paper) account |
| `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` | Live account |

Active pair selected by `NT_MODE` (`demo` | `live`, default `demo`).
After ≥20 closed trades with positive realized PnL, auto-graduates to `live`.

## Security

- **Auth inherited:** Routes go through the dashboard's session-token or OAuth
  auth middleware
- **Path traversal protection:** `_safe_plugin_api_relpath()` validates manifest paths
  (GHSA-5qr3-c538-wm9j fix)
- **Secret redaction:** Config responses use `redact_config_for_display()`
