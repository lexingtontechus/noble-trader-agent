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
| **Portfolio** | `/portfolio` | Stub — see Dashboard for portfolio metrics |
| **Backtest** | `/backtest` | Backtest runs table (Sharpe, win rate, max DD, net P&L) |

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

| Phase | Page | Why migrate |
|-------|------|-------------|
| ✅ Done | Dashboard | Main view — account + PnL, was the worst meta-refresh offender |
| ✅ Done | Status | Replaced 10s full-page reload with TanStack Query polling |
| ✅ Done | Monitor | First page to actually consume the WebSocket infrastructure |
| ✅ Done | Symbols | Reactive CRUD with optimistic updates |
| ✅ Done | PnL | Charts + by-regime breakdown |
| Next | Backtest | Add equity curve with trade markers, regime shading |
| Next | Portfolio | Allocation pie, exposure bars, VaR distribution |
| Skip | Heartbeats, Orders, Agent, Optimize, Config | Server-rendered tables — fine as Jinja2 |

## Development notes

- **HMR**: Vite hot-reloads on save. No build step needed during dev.
- **Type safety**: Run `npm run typecheck` to verify TypeScript.
- **Bundle splitting**: React, Recharts, and TanStack Query are split into
  separate vendor chunks for better caching.
- **API types**: `src/lib/types.ts` is hand-maintained. If the FastAPI
  schema changes, update the types here. (Optional: generate types from
  `openapi.yaml` via `openapi-typescript`.)
