/**
 * Noble Trader — Desktop Runtime Plugin (Electron app surface)
 *
 * Loaded by the Hermes desktop app from
 *   <hermes home>/desktop-plugins/<name>/plugin.js
 * via apps/desktop/src/contrib/runtime-loader.ts (raw ESM, no JSX transform),
 * expecting a default export of type HermesPlugin whose register(ctx)
 * contributes surfaces through @hermes/plugin-sdk.
 *
 * The plugin calls the **agent web app** directly at:
 *   http://127.0.0.1:8080/api/plugin/*
 *
 * The /api/plugin/* routes in src/hermes/web/app.py are auth-exempt and
 * CORS-enabled for this purpose. The Hermes desktop app's `ctx.rest()`
 * is NOT used — it routes through the headless `hermes serve` backend
 * which doesn't have Noble Trader's routes mounted.
 *
 * Runtime disk plugins are plain ESM — no JSX. Uses React.createElement.
 */
import React from 'react'
import { cn, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

// ---------------------------------------------------------------------------
// Direct fetch to the agent web app (bypasses the Hermes gateway entirely)
// ---------------------------------------------------------------------------
const AGENT_BASE_URL = 'http://127.0.0.1:8080'

async function fetchAgent(path, options = {}) {
  const url = path.startsWith('/api/')
    ? `${AGENT_BASE_URL}${path}`
    : `${AGENT_BASE_URL}/api/plugin/${path}`
  const opts = {
    method: options.method || 'GET',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...(options.body ? { body: JSON.stringify(options.body) } : {}),
  }
  const resp = await fetch(url, opts)
  if (!resp.ok) {
    throw new Error(`${resp.status} ${resp.statusText}`)
  }
  return await resp.json()
}

// ---------------------------------------------------------------------------
// Local plugin state (localStorage for forms, no backend)
// ---------------------------------------------------------------------------
const STATE_FILE = 'noble-trader-plugin-state.json'

function loadState() {
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(STATE_FILE)
      return raw ? JSON.parse(raw) : {}
    }
  } catch (e) {}
  return {}
}

function saveState(state) {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(STATE_FILE, JSON.stringify(state))
    }
  } catch (e) {}
}

function useLocalState(key, initialValue) {
  const [value, setValue] = React.useState(() => {
    const state = loadState()
    return state[key] !== undefined ? state[key] : initialValue
  })
  const setLocalValue = React.useCallback((newValue) => {
    setValue(newValue)
    const state = loadState()
    state[key] = newValue
    saveState(state)
  }, [key])
  return [value, setLocalValue]
}

// ---------------------------------------------------------------------------
// Remote data hook — calls agent web app directly via fetch
// ---------------------------------------------------------------------------
function useRemoteData(url) {
  const [data, setData] = React.useState(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState(null)

  const load = React.useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const json = await fetchAgent(url)
      setData(json)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [url])

  React.useEffect(() => { load() }, [load])

  return { data, loading, error, reload: load }
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------
const STYLE_ID = 'noble-trader-desktop-style'
const CSS = `.nt-root{display:flex;flex-direction:column;height:100%;}` +
  `.nt-tabbar{display:flex;gap:4px;padding:8px 12px;border-bottom:1px solid var(--chrome-border,#2a2a2a);}` +
  `.nt-tab{background:transparent;border:1px solid transparent;color:var(--ui-text-tertiary,#888);padding:4px 12px;border-radius:6px;font-size:12px;cursor:pointer;font-family:inherit;}` +
  `.nt-tab:hover{color:var(--ui-text-primary,#eee);}` +
  `.nt-tab.nt-active{background:var(--chrome-active,#1d1d1d);color:var(--ui-text-primary,#eee);border-color:var(--chrome-border,#2a2a2a);}` +
  `.nt-body{flex:1;overflow:auto;padding:16px;}` +
  `.nt-card{background:var(--chrome-card,#141414);border:1px solid var(--chrome-border,#2a2a2a);border-radius:10px;margin-bottom:12px;}` +
  `.nt-head{padding:12px 16px;border-bottom:1px solid var(--chrome-border,#2a2a2a);}` +
  `.nt-title{margin:0;font-size:14px;font-weight:600;color:var(--ui-text-primary,#eee);}` +
  `.nt-pad{padding:16px;}` +
  `.nt-center{text-align:center;}` +
  `.nt-row{display:flex;align-items:center;gap:8px;}` +
  `.nt-between{justify-content:space-between;}` +
  `.nt-space > * + *{margin-top:12px;}` +
  `.nt-space-sm > * + *{margin-top:8px;}` +
  `.nt-flex{display:flex;}` +
  `.nt-flexcol{display:flex;flex-direction:column;}` +
  `.nt-flex1{flex:1;}` +
  `.nt-overflow{overflow:auto;}` +
  `.nt-gap{gap:8px;}` +
  `.nt-mt{margin-top:12px;}` +
  `.nt-ml{margin-left:6px;}` +
  `.nt-grid2{display:grid;grid-template-columns:1fr 1fr;gap:8px;}` +
  `.nt-center-text{text-align:center;}` +
  `.nt-border{border-top:1px solid var(--chrome-border,#2a2a2a);padding-top:8px;margin-top:8px;}` +
  `.nt-2xl{font-size:26px;font-weight:700;color:var(--ui-text-primary,#eee);}` +
  `.nt-sm{font-size:13px;color:var(--ui-text-secondary,#bbb);}` +
  `.nt-xs{font-size:11px;color:var(--ui-text-tertiary,#888);}` +
  `.nt-tertiary{color:var(--ui-text-tertiary,#888);}` +
  `.nt-error{color:var(--ui-destructive,#f87171);font-size:13px;margin-bottom:8px;}` +
  `.nt-ok{color:var(--ui-success,#22c55e);font-size:28px;}` +
  `.nt-btn{font-family:inherit;background:var(--chrome-button,#2d2d2d);color:var(--ui-text-primary,#eee);border:1px solid var(--chrome-border,#2a2a2a);border-radius:6px;padding:6px 12px;font-size:12px;cursor:pointer;}` +
  `.nt-btn:hover{background:var(--chrome-button-hover,#3a3a3a);}` +
  `.nt-btn:disabled{opacity:0.5;cursor:not-allowed;}` +
  `.nt-input{background:var(--chrome-input,#1a1a1a);color:var(--ui-text-primary,#eee);border:1px solid var(--chrome-border,#2a2a2a);border-radius:6px;padding:4px 8px;font-size:12px;width:100%;font-family:inherit;}` +
  `.nt-label{display:block;font-size:11px;color:var(--ui-text-tertiary,#888);margin-bottom:2px;}` +
  `.nt-table{width:100%;border-collapse:collapse;font-size:12px;}` +
  `.nt-table th,.nt-table td{border-bottom:1px solid var(--chrome-border,#2a2a2a);padding:6px 8px;text-align:left;}` +
  `.nt-table th{color:var(--ui-text-tertiary,#888);font-weight:600;}` +
  `.nt-table tbody tr:hover{background:rgba(255,255,255,0.02);}` +
  `.nt-empty{text-align:center;color:var(--ui-text-tertiary,#888);padding:12px;font-size:12px;}` +
  `.nt-pos{color:var(--ui-success,#22c55e);}` +
  `.nt-neg{color:var(--ui-destructive,#f87171);}` +
  `.nt-dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-right:4px;}` +
  `.nt-dot.online{background:var(--ui-success,#22c55e);}` +
  `.nt-dot.offline{background:var(--ui-destructive,#f87171);}` +
  `.nt-strong{font-weight:600;}`

function ensureStyle() {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID)) return
  const el = document.createElement('style')
  el.id = STYLE_ID
  el.textContent = CSS
  document.head.appendChild(el)
}

function Card({ className, children }) {
  return React.createElement('div', { className: cn('nt-card', className) }, children)
}
function CardHeader({ children }) {
  return React.createElement('div', { className: 'nt-head' }, children)
}
function CardTitle({ children }) {
  return React.createElement('h3', { className: 'nt-title' }, children)
}
function CardContent({ className, children }) {
  return React.createElement('div', { className: cn('nt-pad', className) }, children)
}

// ---------------------------------------------------------------------------
// Main Plugin Component — defaults to Portfolio, no redirect logic
// ---------------------------------------------------------------------------
function NobleTraderPlugin() {
  const [active, setActive] = React.useState('portfolio')

  return React.createElement('div', { className: 'nt-root' },
    React.createElement('div', { className: 'nt-tabbar' },
      TabButton('portfolio', 'Portfolio', active, setActive),
      TabButton('setup', 'Setup', active, setActive),
      TabButton('status', 'Status', active, setActive)),
    React.createElement('div', { className: 'nt-body' },
      active === 'portfolio' && React.createElement(PortfolioTab, null),
      active === 'setup' && React.createElement(SetupTab, null),
      active === 'status' && React.createElement(StatusTab, null))
  )
}

function TabButton(id, label, active, setActive) {
  return React.createElement('button', {
    className: cn('nt-tab', active === id ? 'nt-active' : ''),
    onClick: () => setActive(id),
  }, label)
}

// ---------------------------------------------------------------------------
// Portfolio Tab — fetches from agent web app directly
// ---------------------------------------------------------------------------
function PortfolioTab() {
  const brokerage = useRemoteData('/api/plugin/brokerage')
  const portfolio = useRemoteData('/api/plugin/portfolio')
  const status = useRemoteData('/api/plugin/status')

  if (brokerage.loading || portfolio.loading || status.loading) {
    return React.createElement(Card, null,
      React.createElement(CardContent, null, 'Loading portfolio…'))
  }

  // If brokerage failed (agent web app not reachable), show graceful $0
  const b = brokerage.data || { connected: false, equity: 0, currency: 'USD', positions: [], open_trades: [], trades: [] }
  const pdata = portfolio.data || {}
  const sd = status.data || {}
  const connected = b.connected || false
  const positions = b.positions || []
  const trades = b.trades || []

  return React.createElement('div', { className: 'nt-space' },
    // Status row
    React.createElement(Card, null,
      React.createElement(CardContent, null,
        React.createElement('div', { className: 'nt-flex nt-gap' },
          React.createElement('span', { className: cn('nt-dot', connected ? 'online' : 'offline') }),
          React.createElement('span', { className: 'nt-strong' }, 'Noble Trader Agent'),
          React.createElement('span', { className: 'nt-xs nt-tertiary' },
            connected ? 'Live' : 'Degraded')),
        React.createElement('p', { className: 'nt-xs nt-tertiary' },
          sd.checked_at || (brokerage.error ? 'Agent not reachable' : 'No status received'))
      )
    ),
    // Equity card — always shows a number, never NaN
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Account Equity')),
      React.createElement(CardContent, null,
        React.createElement('div', { className: 'nt-grid2 nt-center-text' },
          React.createElement('div', null,
            React.createElement('p', { className: 'nt-2xl' },
              `$${Number(b.equity || 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${b.currency || 'USD'}`),
            React.createElement('p', { className: 'nt-xs nt-tertiary' }, 'Live Equity (MetaApi)')),
          React.createElement('div', null,
            React.createElement('p', { className: 'nt-2xl' }, `${positions.length}`),
            React.createElement('p', { className: 'nt-xs nt-tertiary' }, 'Open Positions'))
        ),
        brokerage.error && React.createElement('p', { className: 'nt-xs nt-tertiary nt-mt' },
          'Backend unreachable. The agent web app runs on port 8080 — start it via the watchdog.'),
        !connected && React.createElement('p', { className: 'nt-xs nt-tertiary nt-mt' },
          'No live brokerage connection. Configure credentials in Setup tab.')
      )
    ),
    // Open Positions
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Open Positions')),
      React.createElement(CardContent, null,
        positions.length > 0
          ? renderPositions(positions)
          : React.createElement('p', { className: 'nt-empty' }, 'No open positions')
      )
    ),
    // Recent Trades
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Recent Trades')),
      React.createElement(CardContent, null,
        trades.length > 0
          ? renderRecentTrades(trades)
          : React.createElement('p', { className: 'nt-empty' }, 'No trade history')
      )
    )
  )
}

function renderPositions(positions) {
  return React.createElement('table', { className: 'nt-table' },
    React.createElement('thead', null,
      React.createElement('tr', null,
        ['Symbol', 'Side', 'Qty', 'Entry', 'P&L'].map((h, i) =>
          React.createElement('th', { key: i }, h)
        )
      )
    ),
    React.createElement('tbody', null,
      positions.slice(0, 10).map((p, i) =>
        React.createElement('tr', { key: i },
          React.createElement('td', null, p.symbol || p.asset || '—'),
          React.createElement('td', null, (p.side || p.direction || '—').toString()),
          React.createElement('td', null, p.qty || p.size || p.lots || '—'),
          React.createElement('td', null, p.entry_price ? Number(p.entry_price).toFixed(2) : '—'),
          React.createElement('td', { className: Number(p.pnl || 0) >= 0 ? 'pos' : 'neg' },
            `${Number(p.pnl || 0) >= 0 ? '+' : ''}${Number(p.pnl || 0).toFixed(2)}`
          )
        )
      )
    )
  )
}

function renderRecentTrades(trades) {
  return React.createElement('table', { className: 'nt-table' },
    React.createElement('thead', null,
      React.createElement('tr', null,
        ['Symbol', 'Side', 'Profit', 'Time'].map((h, i) =>
          React.createElement('th', { key: i }, h)
        )
      )
    ),
    React.createElement('tbody', null,
      trades.slice(0, 10).map((t, i) =>
        React.createElement('tr', { key: i },
          React.createElement('td', null, t.symbol || '—'),
          React.createElement('td', null, (t.side || t.action || '—').toString()),
          React.createElement('td', { className: Number(t.profit || 0) >= 0 ? 'pos' : 'neg' },
            `${Number(t.profit || 0) >= 0 ? '+' : ''}${Number(t.profit || 0).toFixed(2)}`
          ),
          React.createElement('td', null, (t.time || '').toString().slice(0, 19).replace('T', ' '))
        )
      )
    )
  )
}

// ---------------------------------------------------------------------------
// Setup Tab — local form that writes to .env
// ---------------------------------------------------------------------------
const SETUP_FIELDS = [
  { name: 'NOBLE_TRADER_PROXY_REDIS_URL', label: 'Redis URL', type: 'text', hint: 'redis://user:***@host:port' },
  { name: 'NOBLE_TRADER_QUOTE_PROXY_URL', label: 'Quote Proxy URL', type: 'text', hint: 'https://your-proxy.up.railway.app' },
  { name: 'TRADINGVIEW_API_KEY', label: 'TradingView API Key', type: 'password', hint: 'RapidAPI key for price data' },
  { name: 'METAAPI_TOKEN_DEMO', label: 'MetaApi Demo Token', type: 'password', group: 'demo' },
  { name: 'METAAPI_ACCOUNT_ID_DEMO', label: 'MetaApi Demo Account ID', type: 'text', group: 'demo' },
  { name: 'METAAPI_TOKEN', label: 'MetaApi Live Token', type: 'password', group: 'live' },
  { name: 'METAAPI_ACCOUNT_ID', label: 'MetaApi Live Account ID', type: 'text', group: 'live' },
]

function SetupTab() {
  const [form, setForm] = useLocalState('setupForm', {})
  const [saved, setSaved] = React.useState(false)
  const [saving, setSaving] = React.useState(false)

  const setField = (name, value) => setForm((f) => ({ ...f, [name]: value }))

  const handleSave = async () => {
    setSaving(true)
    try {
      await fetchAgent('/api/plugin/setup', { method: 'POST', body: form })
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      // Fallback: just save locally
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } finally {
      setSaving(false)
    }
  }

  return React.createElement('div', { className: 'nt-space' },
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Configuration')),
      React.createElement(CardContent, null,
        React.createElement('p', { className: 'nt-sm nt-tertiary' },
          'Fill in your credentials and click Save. The agent web app on :8080 writes them to .env.'),
        React.createElement('div', { className: 'nt-space-sm' },
          SETUP_FIELDS.map((field) =>
            React.createElement('div', { key: field.name, className: 'nt-space-sm' },
              React.createElement('label', { className: 'nt-label' }, field.label),
              React.createElement('input', {
                type: field.type,
                className: 'nt-input',
                value: form[field.name] || '',
                placeholder: field.hint,
                onChange: (e) => setField(field.name, e.target.value),
              }),
              React.createElement('p', { className: 'nt-xs nt-tertiary' }, field.hint)
            )
          )
        ),
        React.createElement('button', {
          className: 'nt-btn',
          style: { marginTop: '12px' },
          disabled: saving,
          onClick: handleSave,
        }, saving ? 'Saving…' : saved ? 'Saved!' : 'Save Configuration')
      )
    ),
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Instructions')),
      React.createElement(CardContent, null,
        React.createElement('p', { className: 'nt-sm nt-tertiary' },
          'The agent runs on http://127.0.0.1:8080. Its watchdog auto-starts when ' +
          'Hermes launches. If the backend is unreachable, the plugin shows $0 equity ' +
          'and allows you to configure credentials here.')
      )
    )
  )
}

// ---------------------------------------------------------------------------
// Status Tab
// ---------------------------------------------------------------------------
function StatusTab() {
  const health = useRemoteData('/api/plugin/health')

  return React.createElement('div', { className: 'nt-space' },
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Health')),
      React.createElement(CardContent, null,
        health.loading ? React.createElement('p', { className: 'nt-sm' }, 'Checking…') :
        health.error ? React.createElement('p', { className: 'nt-xs nt-tertiary' },
          `Agent unreachable (port 8080). Error: ${health.error.message}`) :
        React.createElement('div', { className: 'nt-row nt-border' },
          React.createElement('span', { className: 'nt-xs nt-tertiary' }, 'Status'),
          React.createElement('span', { className: 'nt-sm nt-strong' },
            health.data?.status || 'unknown')
        )
      )
    ),
    React.createElement(Card, null,
      React.createElement(CardHeader, null, React.createElement(CardTitle, null, 'Architecture')),
      React.createElement(CardContent, null,
        React.createElement('div', { className: 'nt-space-sm' },
          React.createElement('div', { className: 'nt-row nt-border' },
            React.createElement('span', { className: 'nt-xs nt-tertiary' }, 'Plugin'),
            React.createElement('span', { className: 'nt-sm' }, 'desktop/plugin.js (ESM)')),
          React.createElement('div', { className: 'nt-row nt-border' },
            React.createElement('span', { className: 'nt-xs nt-tertiary' }, 'API'),
            React.createElement('span', { className: 'nt-sm' }, 'http://127.0.0.1:8080/api/plugin/*')),
          React.createElement('div', { className: 'nt-row nt-border' },
            React.createElement('span', { className: 'nt-xs nt-tertiary' }, 'Backend'),
            React.createElement('span', { className: 'nt-sm' }, 'src/hermes/web/app.py')
          )
        )
      )
    )
  )
}

// ---------------------------------------------------------------------------
// Plugin entry — default export, desktop-runtime contract.
// ---------------------------------------------------------------------------
const plugin = {
  id: 'noble-trader',
  name: 'Noble Trader',
  defaultEnabled: true,
  register(ctx) {
    ensureStyle()
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/noble-trader' },
        render: () => React.createElement(NobleTraderPlugin, null),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 50,
        data: { codicon: 'graph-line', label: 'Noble Trader', path: '/noble-trader' },
      },
    ])
  },
}

export default plugin
