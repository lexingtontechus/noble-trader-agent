# Noble Trading — Risk Manager (Next.js)

Dynamic Masaniello + Markov Regime Engine, built with Next.js 15 App Router,
Clerk authentication, and Supabase persistence.

## Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 15 (App Router) |
| Auth | @clerk/nextjs — middleware, server-side `auth()`, `UserButton` |
| Database | Supabase (PostgreSQL + RLS) |
| Charts | Chart.js via react-chartjs-2 |
| Fonts | next/font/google (Syne + IBM Plex Mono, no layout shift) |
| Styling | CSS Modules + CSS custom properties |

---

## Quick Start

```bash
npm install
cp .env.local.example .env.local
# Fill in your Clerk keys (Supabase keys are pre-filled)
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

---

## Environment Variables

```env
# Clerk — https://dashboard.clerk.com → Your App → API Keys
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
CLERK_SECRET_KEY=sk_test_...

# Clerk redirect URLs (leave as-is)
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/sign-up
NEXT_PUBLIC_CLERK_AFTER_SIGN_IN_URL=/
NEXT_PUBLIC_CLERK_AFTER_SIGN_UP_URL=/

# Supabase — pre-filled for NobleTradingApp project
NEXT_PUBLIC_SUPABASE_URL=https://pcvscowltlrxzgxjurcr.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=eyJ...
```

### Clerk JWT Template (required for Supabase RLS)

1. Clerk Dashboard → **JWT Templates** → **New template** → select **Supabase**
2. Name it exactly: `supabase`
3. Save — this allows RLS policies (`auth.jwt() ->> 'sub'`) to resolve to the Clerk user ID

---

## Project Structure

```
noble-risk-nextjs/
├── middleware.js                  # Clerk route protection — all routes except /sign-in
│
├── app/                           # Next.js App Router
│   ├── layout.js                  # Root layout: ClerkProvider + next/font + globals.css
│   ├── page.js                    # / — server component, auth() guard, renders RiskManagerClient
│   ├── sign-in/[[...sign-in]]/    # Clerk catch-all sign-in route
│   └── sign-up/[[...sign-up]]/    # Clerk catch-all sign-up route
│
├── components/
│   ├── RiskManagerClient.js       # 'use client' — main dashboard, owns all state
│   ├── AppHeader.js               # 'use client' — logo + Clerk UserButton
│   ├── AuthScreen.js              # 'use client' — branded Clerk SignIn
│   ├── TradeForm.js               # 'use client' — all 20+ input fields
│   ├── ResultsPanel.js            # 'use client' — hero metrics + factor bars + verdict
│   ├── MarkovStates.js            # 'use client' — HMM state probability tiles
│   ├── BatchProgressChart.js      # 'use client' — Chart.js stacked bar
│   ├── RiskSweepChart.js          # 'use client' — Chart.js p_win sensitivity line
│   ├── SessionLog.js              # 'use client' — paginated 3-day Supabase log
│   └── ui/
│       ├── Panel.js / Badge.js
│       ├── Field.js / Toggle.js / FactorBar.js
│
├── hooks/
│   ├── useSizer.js                # sizeTrade, computeMarkovProbs, computeRiskSweep, useSizer()
│   └── useSupabase.js             # saveCalculation, fetchSessionLog (paginated), fetchFactorById
│
├── lib/
│   ├── constants.js               # DEFAULT_PARAMS, REGIME_LABELS, STATE_COLORS, STATE_LABELS
│   └── supabase.js                # createClient + getAuthClient(clerkToken)
│
└── styles/
    └── globals.css                # CSS variables, reset, Clerk overrides
```

---

## Key Next.js Differences from the Vite version

| Vite / React | Next.js |
|---|---|
| `@clerk/clerk-react` | `@clerk/nextjs` |
| `ClerkProvider` in `main.jsx` | `ClerkProvider` in `app/layout.js` |
| `SignedIn` / `SignedOut` in `App.jsx` | `middleware.js` + server `auth()` in `app/page.js` |
| `import.meta.env.VITE_*` | `process.env.NEXT_PUBLIC_*` |
| `vite.config.js` | `next.config.js` |
| `.jsx` extensions | `.js` extensions |
| No `'use client'` needed | Every interactive component has `'use client'` |
| `src/` directory | Root-level `app/`, `components/`, `hooks/`, `lib/` |
| Google Fonts `<link>` tag | `next/font/google` (zero layout shift) |
