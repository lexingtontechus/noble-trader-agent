# Noble Trader Desktop Plugin

A [Hermes desktop plugin](https://hermes-agent.nousresearch.com/docs/developer-guide/desktop-plugin-sdk)
that surfaces the Noble Trader onboarding wizard, portfolio, and signal data
directly inside the Hermes desktop app as a native tab.

## What it does

- **Setup Wizard tab** — A **native form inside the plugin** that collects Noble
  Trader credentials (`NOBLE_TRADER_PROXY_REDIS_URL`, `NOBLE_TRADER_QUOTE_PROXY_URL`,
  `TRADINGVIEW_API_KEY`, MetaApi **demo + live** token/account pairs) and submits
  them straight to the agent backend `POST /setup` endpoint. No browser redirect
  — the whole wizard runs in the Hermes tab. Supports DEMO (paper) vs LIVE mode
  selection with automatic demo→live graduation after 20+ profitable trades.
  (The legacy `http://127.0.0.1:8080/setup` web wizard is retired — `GET /setup`
  returns `410 Gone` and points here.)
- **Portfolio tab** — Live equity across venues (MT4/MT5 bridge via MetaApi), held
  positions, renko ladders, and meta-regime state. Auto-shows Setup if onboarding
  is incomplete.
- **Status tab** — Watchdog health, supervised-process status, Redis connection,
  and recent signal heartbeats.

## Installation

### From pip package (recommended)

```bash
pip install noble-trader-agent
```

Then deploy the bundled plugin:

```bash
python -m hermes_trading_platform.scripts.deploy_desktop_plugin
# Or from a git checkout:
# python scripts/deploy_desktop_plugin.py
```

This copies the plugin to `~/.hermes/desktop-plugins/noble-trader/` so it's
available to **all profiles**.

### From git checkout

```bash
git clone https://github.com/lexingtontechus/noble-trader-agent.git
cd noble-trader-agent
python scripts/deploy_desktop_plugin.py
```

> **Deployment target.** The deploy script copies to `desktop-plugins/` only —
> that is the directory the Electron desktop app's runtime loader scans. The
> Hermes web backend's `plugins/` discovery path is pre-deployed in the runtime
> and cached at startup; this script does not touch it.

### Activation

1. Open the Hermes desktop app.
2. **Restart the app** (the runtime loader scans `desktop-plugins/` at startup;
   ⌘K → "Reload desktop plugins" also works).
3. The **"Noble Trader"** item appears in the sidebar and opens Portfolio /
   Setup / Status tabs with live data from the agent backend.

To enable agent-side tools (`noble_balance`, `noble_assets`, `noble_status`) in chat:

```bash
hermes config set plugins.enabled '[..., "noble-trader-desktop"]'
```

## Configuration

**Frontend (desktop-runtime `plugin.js`)** — connects via the Hermes desktop
`ctx.rest()` call, which hits the **Hermes-hosted** plugin backend at
`/api/plugins/noble-trader/*` (same process as the desktop app, no separate
server, no CORS needed).

**Python backend (`dashboard/plugin_api.py`)** — connects to the
noble-trader-agent **exclusively** via the **native shim** (`scripts/agent_api_shim.py`),
which shells out to the agent's own venv Python with `PYTHONPATH` set to the
agent's `src/` directory. This resolves `hermes.web.app` to the agent's package
(not the Hermes desktop app's `hermes` package).

The legacy "import" mode (which tried `from hermes.web.app import ...` in the
dashboard process and always failed) and "proxy" mode (forwarding to
`http://127.0.0.1:8080`) are **removed**. No loopback fallback to `:8080`.

### Credential fields

The Setup wizard collects these fields:

| Plugin field | Env var | Notes |
|---|---|---|
| `NOBLE_TRADER_PROXY_REDIS_URL` | `NOBLE_TRADER_PROXY_REDIS_URL` | Full Redis URL with Supabase `sub_xxxx:password` credentials embedded |
| `NOBLE_TRADER_QUOTE_PROXY_URL` | `NOBLE_TRADER_QUOTE_PROXY_URL` | Public SSE endpoint — **no separate credential** (auth via `X-Plan-Prefix` header derived from the Redis username) |
| `TRADINGVIEW_API_KEY` | `TRADINGVIEW_API_KEY` | RapidAPI key for price data |
| `METAAPI_TOKEN_DEMO` / `METAAPI_ACCOUNT_ID_DEMO` | same | Demo (paper) account |
| `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` | same | Live account (auto-graduated to) |
| `NT_MODE` | `NT_MODE` | `demo` (default) or `live` |
| `DISCORD_WEBHOOK_URL` | same | Optional trade-approval notifications |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | same | Optional trade-approval notifications |

## Plugin structure

```
.hermes/plugins/noble-trader/
├── plugin.yaml                # Python plugin manifest (agent-side tools)
├── __init__.py                # register_tools() + on_session_start hook (auto-starts watchdog)
├── README.md                  # This file
├── dashboard/
│   ├── manifest.json          # Desktop plugin manifest (tab, icon, entry)
│   └── plugin_api.py          # FastAPI router at /api/plugins/noble-trader/*
└── desktop/
    └── plugin.js              # ← Runtime UI the Electron app actually loads (plain ESM, @hermes/plugin-sdk)
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Hermes Desktop App                            │
│                                                                  │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐ │
│  │ Sidebar Tabs │  │  Workspace (Plugin Tab)                  │ │
│  │              │  │  ┌──────────────────────────────────────┐  │ │
│  │  ○ Skills    │  │  │  Noble Trader Dashboard             │  │ │
│  │  ● Noble...  │  │  │  - Setup Wizard Tab (native form)    │  │ │
│  │  ○ Analytics │  │  │  - Portfolio Tab                    │  │ │
│  │  ○ Kanban    │  │  │  - Status Tab                       │  │ │
│  └──────────────┘  │  └──────────────────────────────────────┘  │ │
│                    └──────────────────────────────────────────┘ │
│                           │                                      │
│                           │ ctx.rest('/portfolio')  ← SAME ORIGIN │
│                           ▼                                      │
│  ┌────────────────────────────────────────────┐                 │
│  │  Hermes Plugin Backend: plugin_api.py       │                 │
│  │  (FastAPI router at /api/plugins/noble-trader/*)            │
│  │  → native shim → agent_api_shim.py → agent venv Python       │
│  └────────────────────────┬───────────────────┘                 │
│                           │ (server-side; no browser CORS)       │
│                           ▼                                      │
│  ┌────────────────────────────────────────────┐                 │
│  │  Noble Trader Agent (deployed runtime)      │                 │
│  │  src/hermes/web/app.py                     │                 │
│  │  - /setup, /portfolio, /api/plugin/*       │                 │
│  └────────────────────────────────────────────┘                 │
└─────────────────────────────────────────────────────────────────┘
```

## Auto-start (watchdog via `on_session_start` hook)

The plugin's Python entry point (`__init__.py`) registers an
**`on_session_start`** Hermes hook that **auto-launches `scripts/watchdog.sh`**
detached when the Hermes agent session starts. This brings up the agent loops
automatically — no manual `watchdog.sh` launch needed. The watchdog is idempotent
(single-instance lock + name-based liveness), so re-launching is always safe.
The 5-minute cron (`noble-stack-watchdog`) remains as a backstop supervisor.

When the Python plugin is enabled, the agent gains these native tools:

| Tool | Description |
|---|---|
| `noble_balance` | Live equity across MT4/MT5 bridge + venues |
| `noble_assets` | Held assets with NT regime + renko ladders |
| `noble_status` | Stack health: watchdog, processes, Redis, venues |

## Troubleshooting

| Problem | Solution |
|---|---|
| Plugin tab doesn't appear | Press ⌘K → "Reload desktop plugins"; check `manifest.json` is valid JSON |
| "Agent not reachable" | The agent backend is down. The **watchdog auto-starts on Hermes session start** — if you just launched Hermes, wait ~10s and Retry. If still down, run `bash scripts/watchdog.sh` and check logs. |
| Plugin tools not in chat | Run `hermes config set plugins.enabled '[..., "noble-trader-desktop"]'` |
| Auth errors | The plugin inherits the dashboard's session-token auth — log in to the dashboard first |

## License

Proprietary — part of the Noble Trader quant trading stack.