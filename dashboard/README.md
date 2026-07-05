# Hermes Dashboard — Vite + React + DaisyUI SPA

Companion dashboard for the Hermes / Noble Trader platform. Replaces the
Jinja2 dashboard for chart-heavy pages (Dashboard, PnL, Monitor) and
provides a modern, reactive UI on top of the existing FastAPI backend.

## Stack

- **Vite 6** + **React 18** + **TypeScript 5**
- **Tailwind CSS 3** + **DaisyUI 4** (same themes as the Jinja2 dashboard)
- **TanStack Query 5** for server state (polling, caching, refetch)
- **React Router 7** for client-side routing
- **Recharts** for charts (equity curve, distribution, drawdown)
- **Axios** for HTTP, native WebSocket / EventSource for streaming

## Quick start

```bash
cd dashboard
npm install
cp .env.example .env   # set dev API key if you want auth
npm run dev            # starts at http://localhost:5173
```

In a separate terminal, start the FastAPI backend:

```bash
platform dashboard     # serves at http://localhost:8080
```

Vite proxies `/api`, `/ws`, `/sse`, `/health` to `http://localhost:8080`
during dev (see `vite.config.ts`). No CORS configuration needed locally.

## Project structure

```
dashboard/
├── index.html
├── package.json
├── vite.config.ts          # dev server + proxy config
├── tailwind.config.js      # DaisyUI themes (dark, retro, cyberpunk, ...)
├── tsconfig.json
├── .env.example
└── src/
    ├── main.tsx            # React entry
    ├── App.tsx             # Router + auth gate
    ├── styles/globals.css
    ├── lib/
    │   ├── api.ts          # Axios client + typed API functions
    │   ├── auth.tsx        # Auth context (dev: API key, prod: Clerk)
    │   ├── format.ts       # fmtUSD, fmtPct, fmtTs, pnlColor
    │   ├── query.ts        # TanStack Query client
    │   ├── types.ts        # API response types
    │   └── ws.ts           # useTickStream + useSSEStream hooks
    ├── components/
    │   ├── layout/         # Navbar, Footer, Card, ThemeSwitcher
    │   └── charts/         # EquityCurve, StatsGrid, PositionTable
    └── pages/
        ├── Dashboard.tsx   # Main view: account + PnL + positions + risk
        ├── Status.tsx      # Subsystem connections + ingest stats
        ├── Monitor.tsx     # Live ticks via WebSocket + monitor events
        ├── Symbols.tsx     # Symbol registry CRUD (mirrors Jinja2 page)
        ├── PnL.tsx         # Full tear sheet — Sharpe, Sortino, by-regime
        ├── Portfolio.tsx   # Stub (use Dashboard for now)
        ├── Backtest.tsx    # Backtest runs table
        └── Login.tsx       # Dev login (API key)
```

## Pages

| Page | URL | What it shows |
|------|-----|---------------|
| **Dashboard** | `/` | Account overview (equity, cash, leverage, daily P&L, exposure) + equity curve + performance stats grid + open positions + VaR + recent risk decisions |
| **Status** | `/status` | Subsystem connection badges + heartbeat ingest stats + per-symbol counts (auto-refresh 10s via TanStack Query, no full-page reload) |
| **Monitor** | `/monitor` | Live tick stream via `/ws/{symbol}` WebSocket — pick a symbol, see real-time price updates + recent monitor events |
| **Symbols** | `/symbols` | Symbol registry CRUD — list, add, activate, deactivate, validate (same operations as the Jinja2 page, but reactive) |
| **PnL** | `/pnl` | Full tear sheet — equity curve, returns (best/worst trade, profit factor), risk-adjusted (Sharpe/Sortino/Calmar/Ulcer), trade stats, by-regime breakdown |
| **Portfolio** | `/portfolio` | Allocation pie + exposure bars + VaR distribution histogram + risk decisions table |
| **Backtest** | `/backtest` | Runs table (clickable) + equity curve + trade list drill-down |
| **Agent** | `/agent` | Interactive decision tree + hypotheses table + trade journal with postmortems |

## Auth model

This dashboard is for **single user + admin + agent** usage. Auth uses
**server-side session cookies** for browsers + a **long-lived bearer token**
for programmatic agent access. No third-party auth service (Clerk, Auth0,
etc.) required.

### How it works

- **Browser**: POST `/auth/login` with `{username, password}` → server sets
  a signed session cookie (24h expiry). Browser sends the cookie
  automatically on every subsequent request. No token in localStorage, no
  `Authorization` header, no XSS risk.
- **Agent**: Send `Authorization: Bearer <HERMES_AGENT_TOKEN>` with every
  request. Token is a long random string stored in the backend's `.env`.
- **Logout**: POST `/auth/logout` → server clears the cookie.

### Configuration (backend `.env`)

```bash
HERMES_ADMIN_USERNAME=admin
HERMES_ADMIN_PASSWORD=<strong-password>
HERMES_SESSION_SECRET=<64-char-random-string>
HERMES_AGENT_TOKEN=<long-random-string>
```

Generate strong values with:
```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Disable auth (dev only)

Set `auth.enabled: false` in `config/default.yaml` to skip auth entirely.
**Never do this in production.**

### Why not Clerk / JWT / API keys in localStorage?

- **Clerk / Auth0**: Overkill for single-user. They exist for user management
  at scale (password reset, email verification, social login, multi-tenancy).
- **JWT in browser**: The "stateless" benefit is wasted on a single server.
  You end up needing a revocation list anyway, which is just sessions with
  extra steps.
- **API key in localStorage**: Vulnerable to XSS. Session cookies with
  `HttpOnly` + `SameSite=Strict` flags are safer.

## Build & deploy

```bash
npm run build       # → dist/
npm run preview     # serve the built bundle locally
```

### Recommended: single-host deploy (FastAPI serves the SPA bundle)

For single-user usage, the simplest path is to let FastAPI serve the SPA
bundle itself — one host, one deploy, no CORS configuration, no separate
auth domain. The SPA's session cookie is same-origin, so `withCredentials:
true` just works.

Add this to the bottom of `src/hermes/web/app.py` (after all route
definitions):

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

DIST_DIR = Path(__file__).resolve().parents[4] / "dashboard" / "dist"
if DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="spa")
```

Then:
```bash
cd dashboard && npm run build
cd .. && platform dashboard    # serves SPA at /, API at /api/*, WS at /ws/*
```

Open `http://localhost:8080` — FastAPI serves the SPA for `/`, and the
SPA's calls to `/api/*` and `/ws/*` hit FastAPI routes on the same origin.

### Alternative: external static host (Vercel / Netlify / Cloudflare Pages)

Only worth it if you want CDN caching for the SPA bundle or you're scaling
to many users. The `dist/` folder is static:

- **Vercel**: `vercel --prod` from this directory
- **Netlify**: drag-and-drop `dist/` or connect the repo
- **Cloudflare Pages**: `wrangler pages deploy dist`

For external deploys:
1. Set `VITE_API_BASE_URL` to your FastAPI host (e.g. `https://api.nobletrader.io`) before building.
2. Enable CORS on FastAPI for the SPA origin.
3. Set `https_only=True` in the `SessionMiddleware` call in `src/hermes/web/app.py`
   so the session cookie is only sent over HTTPS.
4. Same-origin becomes cross-origin — `withCredentials: true` in `api.ts`
   handles this, but the cookie's `SameSite=Strict` flag may need to be
   relaxed to `SameSite=Lax` for cross-site requests.

## Theme system

Same 7 DaisyUI themes as the Jinja2 dashboard:
`dark`, `retro`, `cyberpunk`, `nord`, `dracula`, `synthwave`, `light`.

Theme switcher is in the navbar (top-right). Selection persists to
`localStorage` under the key `hermes-theme`.

## Migration roadmap

This scaffold is the foundation. The Jinja2 dashboard at `src/hermes/web/`
stays untouched and continues to serve the admin pages. Migration order:

### Migrated to SPA (8 pages — all chart-heavy / interactive pages)

| Page | Route | What the SPA version adds over Jinja2 |
|------|-------|----------------------------------------|
| Dashboard | `/` | Live account overview — equity curve (Recharts area chart) + 18-stat grid + open positions table + risk card + recent risk decisions. Replaces 10s meta-refresh with TanStack Query polling. |
| Status | `/status` | Subsystem connection badges + ingest stats. Replaces meta-refresh with 10s TanStack Query polling — no full-page reload. |
| Monitor | `/monitor` | Live tick stream via `/ws/{symbol}` WebSocket — first page to actually consume the WS infrastructure. Symbol picker + real-time price updates + monitor events. |
| Symbols | `/symbols` | Symbol registry CRUD — reactive mutations, Add Symbol modal, per-row activate/deactivate/validate buttons, active-only toggle, sync-from-config. |
| PnL | `/pnl` | Full tear sheet — equity curve + returns card + risk-adjusted card + trade stats card + by-regime breakdown table. |
| Backtest | `/backtest` | 14-column runs table with clickable rows → drill-down panel showing 12-stat grid + equity curve + trade list (up to 100 trades). |
| Portfolio | `/portfolio` | Allocation pie + exposure bars + VaR distribution histogram + 4 stat grids (account, PnL, risk) + 12-column risk decisions table. |
| Agent | `/agent` | Collapsible decision tree viz (10 leaf nodes, threshold annotations) + action legend + hypotheses table + trade journal with postmortem indicators. |

### Intentionally skipped (4 pages — pure server-rendered tables)

These pages stay on Jinja2 indefinitely. Migrating them would produce a
dashboard that looks identical but loads slightly faster — no value-add.
The `/api/*` endpoints already exist for all four, so they can be
migrated later if a specific need arises.

| Page | Route | Jinja2 LOC | API endpoint exists? | Skip reason |
|------|-------|-----------|----------------------|-------------|
| Heartbeats | `/heartbeats` | 115 | ✅ `GET /api/heartbeats` | **Pure table + symbol filter.** No chart, no real-time updates (heartbeats are historical, not live), no interactivity beyond filtering. Server-side pagination works fine. Migrating = ~1 day of work for zero UX gain. |
| Signals | `/signals` | 59 | ✅ `GET /api/signals` | **Single table.** Smallest Jinja2 page (59 lines). Shows blended signals (L4 output) — a flat list with no drill-down or chart. Filter by symbol is the only interactivity. Already shown in compact form on the Dashboard's recent heartbeats table. |
| Orders | `/orders` | 112 | ✅ `GET /api/orders` + `GET /api/fills` | **Two tables side by side.** Order lifecycle (DRAFT→SUBMITTED→PARTIAL→FILLED) is already represented via status badges. A timeline visualization would be nice but is luxury for a single-user dashboard. |
| Optimize | `/optimize` | 84 | ✅ `GET /api/simulations` | **Simulation runs table.** A parallel-coordinates plot showing which parameter combinations won would add value, but only if you're doing serious parameter analysis. The table is sufficient for "did this run beat the baseline?" |
| Config | `/config` | 82 | ❌ (only `GET /config` HTML route) | **Read-only form rendering the YAML.** We just rebuilt this as a form in the Jinja2 dashboard (the 11-section recursive `field()` macro). All inputs are `disabled`. No chart, no interactivity, data doesn't change without a restart. Migrating = re-rendering the same disabled form in React. |

### Outstanding items (not pages — feature gaps)

| Item | Where | Status | Reason / Next step |
|------|-------|--------|--------------------|
| **Single-host deploy** (StaticFiles mount) | `src/hermes/web/app.py` | Not wired | 5-line change documented in [Build & deploy](#build--deploy) section. Recommended for single-user — no CORS, same-origin cookies. Add `app.mount("/", StaticFiles(directory="dashboard/dist", html=True))` after all route definitions. |
| **HTTPS cookie flag** | `src/hermes/web/app.py` → `SessionMiddleware` | `https_only=False` | Set to `True` when deploying over HTTPS. Currently `False` so localhost dev works. Documented in `config/default.yaml` comment. |
| **CORS middleware** | `src/hermes/web/app.py` | Not added | Only needed if SPA is hosted externally (Vercel/Netlify). For single-host deploy, same-origin means no CORS needed. |
| **SPA bundle served by FastAPI** | `dashboard/dist/` | Built but not mounted | Run `cd dashboard && npm run build` then add the StaticFiles mount (see above). The `dist/` folder is gitignored. |
| **WebSocket `/ws/{symbol}`** | `src/hermes/web/app.py` | Backend exists, SPA uses it on Monitor page | Working. Could add more live-updating pages (e.g., live P&L on Dashboard) but current 30s TanStack Query polling is sufficient. |
| **SSE `/sse/alerts`** | `src/hermes/web/app.py` | Backend exists, SPA has `useSSEStream` hook but no page uses it | Could add a toast/notification system on the Dashboard. Low priority — alerts are also logged to DuckDB and visible in risk decisions table. |
| **Generate types from OpenAPI** | `dashboard/src/lib/types.ts` | Hand-maintained | Optional: use `openapi-typescript` to generate types from `docs/openapi.yaml`. Currently types are hand-maintained and stay in sync manually. Worth it only if the API schema changes frequently. |
| **Clerk auth swap (prod)** | `dashboard/src/lib/auth.tsx` | Dev API key works, prod needs Clerk | For single-user single-host, the current session cookie auth is sufficient. If you ever go multi-user, swap `auth.tsx` for `@clerk/clerk-react` — the API client already uses `withCredentials: true` so no other changes needed. |

## Development notes

- **HMR**: Vite hot-reloads on save. No build step needed during dev.
- **Type safety**: Run `npm run typecheck` to verify TypeScript.
- **Bundle splitting**: React, Recharts, and TanStack Query are split into
  separate vendor chunks for better caching.
- **API types**: `src/lib/types.ts` is hand-maintained. If the FastAPI
  schema changes, update the types here. (Optional: generate types from
  `openapi.yaml` via `openapi-typescript`.)
