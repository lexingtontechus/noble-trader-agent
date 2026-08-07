/**
 * Noble Trader Admin — Desktop Runtime Plugin (Electron app surface)
 *
 * ADMIN-ONLY dashboard for the EOD signal lookback + paper portfolio.
 * Separate from the `noble-trader` plugin — this one renders the
 * validation/optimization data that lives in Supabase.
 *
 * STANDALONE — no backend, no agent web app, no proxy. Talks DIRECTLY to
 * Supabase REST (PostgREST) using the PUBLIC anon key (safe to embed in a
 * distributed plugin) + scoped read-only RLS policies (migration 107). The
 * service-role key stays on the backend — never ships in the plugin.
 * PostgREST sends `Access-Control-Allow-Origin: *` so the Electron renderer
 * can fetch it cross-origin like any other endpoint.
 *
 * Data path:
 *   plugin.js → https://<project>.supabase.co/rest/v1/<table|view>?select=...
 *   (anon key in the apikey + Authorization headers; RLS grants SELECT only)
 *
 * Runtime disk plugins are plain ESM — no JSX. Uses React.createElement.
 * Only `react` + `@hermes/plugin-sdk` imports are allowed.
 */
import React from 'react'
import { cn, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

// ---------------------------------------------------------------------------
// Plugin config (own config, localStorage-backed — same pattern as noble-trader)
// ---------------------------------------------------------------------------
const CONFIG_FILE = 'noble-trader-admin-config.json'

function loadConfig() {
  try {
    if (typeof localStorage !== 'undefined') {
      const raw = localStorage.getItem(CONFIG_FILE)
      if (raw) return JSON.parse(raw)
    }
  } catch (e) {}
  return {}
}

function saveConfig(cfg) {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(CONFIG_FILE, JSON.stringify(cfg))
    }
  } catch (e) {}
}

function useConfig() {
  const [config, setConfig] = React.useState(loadConfig)
  const update = React.useCallback((patch) => {
    setConfig((prev) => {
      const next = { ...prev, ...patch }
      saveConfig(next)
      return next
    })
  }, [])
  return [config, update]
}

// ---------------------------------------------------------------------------
// Direct Supabase REST fetch (no proxy)
// ---------------------------------------------------------------------------
async function fetchSupabase(config, path, params = {}) {
  const base = (config.supabase_url || '').replace(/\/+$/, '')
  if (!base || !config.supabase_key) {
    throw new Error('Not connected — enter Supabase URL + key in the Connect tab')
  }
  const qs = new URLSearchParams(params).toString()
  const url = `${base}/rest/v1/${path}${qs ? '?' + qs : ''}`
  const resp = await fetch(url, {
    method: 'GET',
    headers: {
      'apikey': config.supabase_key,
      'Authorization': `Bearer ${config.supabase_key}`,
      'Accept': 'application/json',
    },
  })
  if (!resp.ok) {
    const body = await resp.text().catch(() => '')
    throw new Error(`${resp.status} ${resp.statusText}${body ? ' — ' + body.slice(0, 120) : ''}`)
  }
  return await resp.json()
}

// ---------------------------------------------------------------------------
// Remote data hook — calls Supabase directly via fetch
// ---------------------------------------------------------------------------
function useSupabaseData(config, table, params, enabled) {
  const [data, setData] = React.useState(null)
  const [loading, setLoading] = React.useState(true)
  const [error, setError] = React.useState(null)

  const load = React.useCallback(async () => {
    if (!enabled || !config.supabase_url || !config.supabase_key) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const json = await fetchSupabase(config, table, params)
      setData(json)
    } catch (err) {
      setError(err)
    } finally {
      setLoading(false)
    }
  }, [config.supabase_url, config.supabase_key, table, JSON.stringify(params), enabled])

  React.useEffect(() => {
    load()
    const timer = setInterval(load, 60000) // auto-refresh every 60s
    return () => clearInterval(timer)
  }, [load])

  return { data, loading, error, reload: load }
}

// ---------------------------------------------------------------------------
// Styles — theme variables only (no hardcoded colors)
// ---------------------------------------------------------------------------
const STYLE_ID = 'noble-trader-admin-style'
const CSS = [
  '.nta-root{display:flex;flex-direction:column;height:100%;gap:12px;padding:16px;overflow:auto;}',
  '.nta-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;}',
  '.nta-card{background:var(--ui-panel,#161616);border:1px solid var(--ui-stroke-secondary,#2a2a2a);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px;}',
  '.nta-card h3{margin:0;font-size:12px;font-weight:600;color:var(--ui-text-secondary,#999);text-transform:uppercase;letter-spacing:0.04em;}',
  '.nta-card .nta-value{font-size:26px;font-weight:700;color:var(--ui-text-primary,#eee);}',
  '.nta-card .nta-sub{font-size:11px;color:var(--ui-text-quaternary,#777);}',
  '.nta-row{display:flex;justify-content:space-between;align-items:baseline;padding:3px 0;font-size:12px;}',
  '.nta-row .nta-k{color:var(--ui-text-tertiary,#888);}',
  '.nta-row .nta-v{color:var(--ui-text-primary,#eee);font-variant-numeric:tabular-nums;}',
  '.nta-pos{color:var(--ui-accent,#4c9aff);}',
  '.nta-neg{color:var(--ui-danger,#ff5c5c);}',
  '.nta-table{width:100%;border-collapse:collapse;font-size:11px;}',
  '.nta-table th,.nta-table td{border-bottom:1px solid var(--ui-stroke-secondary,#2a2a2a);padding:5px 6px;text-align:left;white-space:nowrap;}',
  '.nta-table th{color:var(--ui-text-tertiary,#888);font-weight:600;}',
  '.nta-table .nta-sm{font-size:9px;color:var(--ui-text-secondary,#aaa);font-variant-numeric:tabular-nums;white-space:nowrap;}',
  '.nta-table tbody tr:hover{background:rgba(255,255,255,0.02);}',
  '.nta-badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;text-transform:uppercase;}',
  '.nta-hot{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin-top:8px;}',
  '.nta-hot-card h3{margin-bottom:2px;}',
  '.nta-hot-ts{display:block;font-size:10px;color:var(--ui-text-quaternary,#777);margin-bottom:2px;}',
  '.nta-hot-chip{display:flex;flex-direction:row;align-items:center;gap:6px;padding:6px 10px;border-radius:8px;border:1px solid var(--ui-stroke-secondary,#2a2a2a);}',
  '.nta-hot-chip .nta-hot-sym{font-size:13px;font-weight:700;color:var(--ui-text-primary,#eee);}',
  '.nta-hot-chip .nta-hot-kelly{font-size:11px;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary,#aaa);margin-left:4px;}',
  '.nta-hot-chip .nta-hot-dir{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;padding:2px 5px;border-radius:4px;}',
  '.nta-hot-buy{background:rgba(76,154,255,0.10);border-color:rgba(76,154,255,0.35);}',
  '.nta-hot-buy .nta-hot-dir{color:var(--ui-accent,#4c9aff);background:rgba(76,154,255,0.15);}',
  '.nta-hot-sell{background:rgba(255,92,92,0.10);border-color:rgba(255,92,92,0.35);}',
  '.nta-hot-sell .nta-hot-dir{color:var(--ui-danger,#ff5c5c);background:rgba(255,92,92,0.15);}',
  '.nta-badge.tp{background:rgba(76,154,255,0.15);color:var(--ui-accent,#4c9aff);}',
  '.nta-badge.sl{background:rgba(255,92,92,0.15);color:var(--ui-danger,#ff5c5c);}',
  '.nta-badge.exp{background:rgba(153,153,153,0.15);color:var(--ui-text-tertiary,#888);}',
  '.nta-badge.open{background:rgba(120,220,120,0.15);color:#78dc78;}',
  '.nta-badge.closed{background:rgba(76,154,255,0.15);color:var(--ui-accent,#4c9aff);}',
  '.nta-err{color:var(--ui-danger,#ff5c5c);font-size:12px;padding:8px;}',
  '.nta-hint{color:var(--ui-text-quaternary,#666);font-size:11px;}',
  '.nta-field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px;}',
  '.nta-field label{font-size:11px;color:var(--ui-text-tertiary,#888);}',
  '.nta-field input{background:var(--ui-panel,#101010);border:1px solid var(--ui-stroke-secondary,#2a2a2a);color:var(--ui-text-primary,#eee);border-radius:6px;padding:7px 10px;font-size:12px;font-family:inherit;}',
  '.nta-btn{background:var(--ui-accent,#4c9aff);color:#fff;border:none;border-radius:6px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;}',
  '.nta-btn:hover{opacity:0.9;}',
  '.nta-ok{color:#78dc78;font-size:12px;}',
  '.nta-brick-picker{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 10px;}',
  '.nta-brick-btn{background:transparent;border:1px solid var(--ui-stroke-secondary,#2a2a2a);border-radius:8px;color:var(--ui-text-secondary,#aaa);padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;letter-spacing:0.03em;}',
  '.nta-brick-btn:hover{border-color:var(--ui-accent,#4c9aff);color:var(--ui-accent,#4c9aff);}',
  '.nta-brick-btn-active{background:rgba(76,154,255,0.18);border-color:var(--ui-accent,#4c9aff);color:var(--ui-accent,#4c9aff);}',
].join('')

function ensureStyle() {
  let style = document.getElementById(STYLE_ID)
  if (!style) {
    style = document.createElement('style')
    style.id = STYLE_ID
    document.head.appendChild(style)
  }
  // Always refresh textContent — hot-reloads keep the OLD css otherwise and
  // new classes (brick tabs etc.) silently never apply.
  style.textContent = CSS
}

// ---------------------------------------------------------------------------
// Small SVG chart helpers (no chart library — only react + sdk imports)
// ---------------------------------------------------------------------------
function LineChart({ points, width = 300, height = 90, color }) {
  if (!points || points.length < 2) {
    return React.createElement('div', { className: 'nta-hint' }, 'No data yet')
  }
  const min = Math.min(...points, 0)
  const max = Math.max(...points, 0)
  const span = (max - min) || 1
  const step = width / (points.length - 1)
  const coords = points.map((v, i) => {
    const x = i * step
    const y = height - ((v - min) / span) * (height - 8) - 4
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })
  const line = coords.join(' ')
  const zeroY = height - ((0 - min) / span) * (height - 8) - 4
  const fill = `${line} ${width},${height} 0,${height}`
  return React.createElement('svg', {
    viewBox: `0 0 ${width} ${height}`,
    width: '100%',
    height: height,
    preserveAspectRatio: 'none',
  },
    React.createElement('line', {
      x1: 0, y1: zeroY, x2: width, y2: zeroY,
      stroke: 'var(--ui-stroke-secondary)',
      strokeWidth: 1, strokeDasharray: '3 3',
    }),
    React.createElement('polygon', {
      points: fill,
      fill: (color || 'var(--ui-accent)'),
      opacity: 0.12,
    }),
    React.createElement('polyline', {
      points: line,
      fill: 'none',
      stroke: (color || 'var(--ui-accent)'),
      strokeWidth: 2,
      strokeLinejoin: 'round',
    }),
  )
}

function Donut({ data, size = 110, thickness = 16 }) {
  const total = data.reduce((s, d) => s + (d.value || 0), 0)
  if (!total) {
    return React.createElement('div', { className: 'nta-hint' }, 'No resolved outcomes')
  }
  const r = (size - thickness) / 2
  const cx = size / 2
  const cy = size / 2
  const circ = 2 * Math.PI * r
  let offset = 0
  const arcs = data.map((d) => {
    const frac = (d.value || 0) / total
    const seg = (
      React.createElement('circle', {
        key: d.label,
        cx, cy, r,
        fill: 'none',
        stroke: d.color || 'var(--ui-accent)',
        strokeWidth: thickness,
        strokeDasharray: `${(frac * circ).toFixed(2)} ${(circ - frac * circ).toFixed(2)}`,
        strokeDashoffset: (-offset).toFixed(2),
        transform: `rotate(-90 ${cx} ${cy})`,
      })
    )
    offset += frac * circ
    return seg
  })
  return React.createElement('svg', {
    viewBox: `0 0 ${size} ${size}`,
    width: size,
    height: size,
  }, arcs)
}

function Bars({ data, height = 80 }) {
  if (!data || !data.length) {
    return React.createElement('div', { className: 'nta-hint' }, 'No data yet')
  }
  const max = Math.max(...data.map((d) => Math.abs(d.value || 0)), 1)
  const barW = 26
  const gap = 6
  const totalW = data.length * (barW + gap)
  const padTop = 14
  const bars = data.map((d, i) => {
    const h = Math.max(2, (Math.abs(d.value || 0) / max) * (height - padTop - 14))
    const y = height - h - 14
    const x = i * (barW + gap)
    const color = (d.value || 0) >= 0 ? 'var(--ui-accent,#4c9aff)' : 'var(--ui-danger,#ff5c5c)'
    return React.createElement('g', { key: d.label },
    // Value label above the bar (with $ sign)
    React.createElement('text', {
    x: x + barW / 2, y: y - 3,
    textAnchor: 'middle', fontSize: 8,
    fill: (d.value || 0) >= 0 ? 'var(--ui-accent,#4c9aff)' : 'var(--ui-danger,#ff5c5c)',
    fontVariantNumeric: 'tabular-nums',
    }, `${(d.value || 0) >= 0 ? '+' : '−'}$${Math.abs(Number(d.value || 0)).toFixed(2)}`),
      React.createElement('rect', {
        x, y, width: barW, height: h,
        fill: color, rx: 2, opacity: 0.85,
      }),
      React.createElement('text', {
        x: x + barW / 2, y: height - 2,
        textAnchor: 'middle', fontSize: 8,
        fill: 'var(--ui-text-tertiary)',
      }, d.label.length > 6 ? d.label.slice(0, 5) : d.label),
    )
  })
  return React.createElement('div', null,
    React.createElement('svg', {
      viewBox: `0 0 ${totalW} ${height}`,
      width: '100%',
      height: height,
    },
      // Zero baseline
      React.createElement('line', {
        x1: 0, x2: totalW,
        y1: height - 14, y2: height - 14,
        stroke: 'var(--ui-stroke-secondary,#2a2a2a)',
        strokeWidth: 1,
      }),
      bars,
    ),
    React.createElement('div', { className: 'nta-hint', style: { marginTop: 4 } },
      'Paper PnL minus equal-weight PnL (relative) · −$ = paper underperformed benchmark that day · Realized PnL card = absolute result'),
  )
}

// Horizontal bar histogram — rows = labels, bar length = value (0..max).
// Each row can carry: `color` (bar), `badge` (BUY/SELL text), `sub` (regime
// context). Colors use theme vars WITH literal fallbacks — an undefined
// custom property makes SVG fill invalid (renders black), so never omit
// the fallback.
function HBar({ data, height = 140, width = 640, format }) {
  if (!data || !data.length) {
    return React.createElement('div', { className: 'nta-hint' }, 'No data yet')
  }
  const rowH = 26
  const gap = 8
  const labelW = 56
  const badgeW = 40
  const valW = 50
  const barMaxW = width - labelW - badgeW - valW - 16
  const max = Math.max(...data.map((d) => Math.abs(d.value || 0)), 1)
  const svgH = data.length * (rowH + gap) + 16
  const rows = data.map((d, i) => {
    const y = i * (rowH + gap)
    const w = (Math.abs(d.value || 0) / max) * barMaxW
    const color = d.color || ((d.value || 0) >= 0 ? 'var(--ui-accent, #4c9aff)' : 'var(--ui-danger, #ff5c5c)')
    const badge = d.badge || ''
    const badgeColor = badge === 'SELL' ? 'var(--ui-danger, #ff5c5c)' : 'var(--ui-accent, #4c9aff)'
    const label = format ? format(d.value) : String(d.value)
    // Put the value INSIDE the bar when it's wide enough; otherwise right
    // after the bar end in the bar's color (brighter than the old grey tail).
    const inside = w >= 46
    return React.createElement('g', { key: d.label },
      React.createElement('text', {
        x: 0, y: y + rowH - 10,
        fontSize: 12,
        fill: 'var(--ui-text-primary, #eee)',
        fontWeight: 600,
      }, d.label.length > 7 ? d.label.slice(0, 6) : d.label),
      React.createElement('text', {
        x: labelW, y: y + rowH - 10,
        fontSize: 9,
        fill: badgeColor,
        fontWeight: 700,
      }, badge),
      React.createElement('rect', {
        x: labelW + badgeW, y: y + 4, width: Math.max(w, 2), height: rowH - 8,
        fill: color, rx: 2, opacity: 0.9,
      }),
      inside
        ? React.createElement('text', {
            x: labelW + badgeW + Math.max(w, 2) - 6, y: y + rowH - 10,
            fontSize: 11,
            fill: '#fff',
            fontWeight: 700,
            textAnchor: 'end',
          }, label)
        : React.createElement('text', {
            x: labelW + badgeW + Math.max(w, 2) + 6, y: y + rowH - 10,
            fontSize: 11,
            fill: color,
            fontWeight: 700,
          }, label),
      d.sub ? React.createElement('text', {
        x: labelW + badgeW + barMaxW, y: y + rowH - 2,
        fontSize: 9,
        fill: 'var(--ui-text-quaternary, #666)',
        textAnchor: 'end',
      }, d.sub.length > 18 ? d.sub.slice(0, 17) : d.sub) : null,
    )
  })
  // Scale context: 0 → max axis + faint max reference line.
  const axisY = svgH - 5
  const scaleEls = [
    React.createElement('line', {
      key: 'ref',
      x1: labelW + badgeW, x2: labelW + badgeW + barMaxW,
      y1: axisY - 5, y2: axisY - 5,
      stroke: 'var(--ui-stroke-secondary, #2a2a2a)',
      strokeWidth: 1,
    }),
    React.createElement('text', {
      key: 'z',
      x: labelW + badgeW, y: axisY,
      fontSize: 9,
      fill: 'var(--ui-text-tertiary, #888)',
      textAnchor: 'start',
    }, '0'),
    React.createElement('text', {
      key: 'mx',
      x: labelW + badgeW + barMaxW, y: axisY,
      fontSize: 9,
      fill: 'var(--ui-text-tertiary, #888)',
      textAnchor: 'end',
    }, format ? format(max) : String(max)),
  ]
  return React.createElement('div', null,
    React.createElement('div', { className: 'nta-hint', style: { display: 'flex', gap: 16, marginBottom: 10, fontSize: 11 } },
      React.createElement('span', null,
        React.createElement('span', { style: { display: 'inline-block', width: 10, height: 10, background: 'var(--ui-accent, #4c9aff)', borderRadius: 2, marginRight: 5 } }),
        'BUY'),
      React.createElement('span', null,
        React.createElement('span', { style: { display: 'inline-block', width: 10, height: 10, background: 'var(--ui-danger, #ff5c5c)', borderRadius: 2, marginRight: 5 } }),
        'SELL'),
    ),
    React.createElement('svg', {
      viewBox: `0 0 ${width} ${svgH}`,
      width: '100%',
      height: 'auto',
      style: { display: 'block', maxHeight: 460 },
    }, rows, scaleEls),
  )
}

// ---------------------------------------------------------------------------
// Section components
// ---------------------------------------------------------------------------
function StatCard({ title, value, sub, tone }) {
  return React.createElement('div', { className: 'nta-card' },
    React.createElement('h3', null, title),
    React.createElement('div', {
      className: cn('nta-value', tone === 'pos' ? 'nta-pos' : tone === 'neg' ? 'nta-neg' : ''),
    }, value),
    sub ? React.createElement('div', { className: 'nta-sub' }, sub) : null,
  )
}

function OutcomeBadge({ outcome }) {
  const cls = outcome === 'tp_hit' ? 'tp' : outcome === 'sl_hit' ? 'sl' : outcome === 'expired' ? 'exp' : 'open'
  const label = outcome || 'open'
  return React.createElement('span', { className: cn('nta-badge', cls) }, label)
}

// Adaptive price formatter: fewer decimals for large prices (XAU ~4095 → 2dp),
// more for small prices (FX ~1.08 → 5dp). Keeps the table compact.
function fmtPrice(v) {
  if (v == null || isNaN(Number(v))) return '—'
  const n = Number(v)
  const abs = Math.abs(n)
  if (abs >= 1000) return n.toFixed(1)
  if (abs >= 100) return n.toFixed(3)
  if (abs >= 10) return n.toFixed(4)
  return n.toFixed(5)
}

// ---------------------------------------------------------------------------
// Connect tab — own config (Supabase URL + public anon key)
// ---------------------------------------------------------------------------
function ConnectTab({ config, onSave }) {
  const [url, setUrl] = React.useState(config.supabase_url || '')
  const [key, setKey] = React.useState(config.supabase_key || '')
  const [testing, setTesting] = React.useState(false)
  const [testResult, setTestResult] = React.useState(null)

  const testConnection = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const base = url.replace(/\/+$/, '')
      const resp = await fetch(`${base}/rest/v1/nt_signal_sim?select=signal_id&limit=1`, {
        headers: {
          'apikey': key,
          'Authorization': `Bearer ${key}`,
          'Accept': 'application/json',
        },
      })
      if (!resp.ok) {
        setTestResult({ ok: false, msg: `${resp.status} ${resp.statusText}` })
      } else {
        setTestResult({ ok: true, msg: 'Connected — Supabase reachable' })
      }
    } catch (err) {
      setTestResult({ ok: false, msg: String(err.message || err) })
    } finally {
      setTesting(false)
    }
  }

  const save = () => {
    onSave({ supabase_url: url.trim(), supabase_key: key.trim() })
  }

  return React.createElement('div', { className: 'nta-root' },
    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Noble Trader Admin — Connect'),
      React.createElement('div', { className: 'nta-hint' },
        'Enter the Supabase project URL and the PUBLIC anon key. The plugin reads EOD signal-lookback + paper-portfolio data DIRECTLY from Supabase via scoped read-only RLS (migration 107) — no backend, no service key, safe for multi-user install.'),
      React.createElement('div', { className: 'nta-field' },
        React.createElement('label', null, 'Supabase URL'),
        React.createElement('input', {
          value: url,
          placeholder: 'https://<project>.supabase.co',
          onChange: (e) => setUrl(e.target.value),
        })),
      React.createElement('div', { className: 'nta-field' },
        React.createElement('label', null, 'Supabase anon/public key'),
        React.createElement('input', {
          value: key,
          type: 'password',
          placeholder: 'sb_publishable_...',
          onChange: (e) => setKey(e.target.value),
        })),
      React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
        React.createElement('button', { className: 'nta-btn', onClick: save }, 'Save & Connect'),
        React.createElement('button', {
          className: 'nta-btn', onClick: testConnection, disabled: testing,
        }, testing ? 'Testing…' : 'Test connection'),
      ),
      testResult && React.createElement('div', {
        className: testResult.ok ? 'nta-ok' : 'nta-err',
        style: { marginTop: 8 },
      }, testResult.msg),
    ),
  )
}

// ---------------------------------------------------------------------------
// Renko brick chart — SVG bricks (up green / down red), price axis on the
// right, brick-index axis on the bottom. Ported from the reference TSX to
// plain React.createElement (no chart lib — only react + sdk imports).
// ---------------------------------------------------------------------------
const BRICK_W = 26
const BRICK_GAP = 4
const BRICK_STEP = BRICK_W + BRICK_GAP
const BRICK_RIGHT_MARGIN = 66
const BRICK_TOP_PAD = 18
const BRICK_BOTTOM_PAD = 26
const BRICK_LEFT_PAD = 6
const MIN_BRICK_H = 5

function brickGridLines(minP, maxP, target = 6) {
  const range = maxP - minP
  if (range <= 0) return [minP]
  const raw = range / target
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  const step = norm <= 1 ? mag : norm <= 2 ? 2 * mag : norm <= 5 ? 5 * mag : 10 * mag
  const lines = []
  for (let p = Math.ceil(minP / step) * step; p <= maxP; p += step) lines.push(parseFloat(p.toPrecision(10)))
  return lines
}

function brickStep(total, target = 8) {
  if (total <= target) return 1
  const raw = total / target
  const mag = Math.pow(10, Math.floor(Math.log10(raw)))
  const norm = raw / mag
  const step = norm <= 1 ? mag : norm <= 2 ? 2 * mag : norm <= 5 ? 5 * mag : 10 * mag
  return Math.max(1, Math.round(step))
}

function fmtBrickPrice(p) {
  if (p >= 10000) return p.toFixed(0)
  if (p >= 100) return p.toFixed(1)
  if (p >= 1) return p.toFixed(2)
  return p.toFixed(4)
}

// bricks: [{ open_price, close_price, direction }] ordered by brick_index asc.
// levels: [{ label, price, color }] — horizontal reference lines (entry/sl/tp).
function RenkoBrickChart({ bricks, height = 300, levels }) {
  if (!bricks || !bricks.length) {
    return React.createElement('div', { className: 'nta-hint' }, 'No bricks yet')
  }
  let minP = Infinity
  let maxP = -Infinity
  for (const b of bricks) {
    const lo = Math.min(b.open_price, b.close_price)
    const hi = Math.max(b.open_price, b.close_price)
    if (lo < minP) minP = lo
    if (hi > maxP) maxP = hi
  }
  for (const lv of (levels || [])) {
    if (lv.price != null && Number(lv.price) > 0) {
      if (lv.price < minP) minP = lv.price
      if (lv.price > maxP) maxP = lv.price
    }
  }
  const range = maxP - minP || 1
  const pad = range * 0.12
  const pMin = minP - pad
  const pMax = maxP + pad
  const pRange = pMax - pMin
  const chartH = height - BRICK_TOP_PAD - BRICK_BOTTOM_PAD
  const svgW = bricks.length * BRICK_STEP + BRICK_RIGHT_MARGIN + BRICK_LEFT_PAD
  const priceToY = (p) => BRICK_TOP_PAD + chartH * (1 - (p - pMin) / pRange)
  const idxStep = brickStep(bricks.length)
  const idxLabels = []
  for (let i = 0; i < bricks.length; i += idxStep) idxLabels.push(i)
  if (idxLabels[idxLabels.length - 1] !== bricks.length - 1) idxLabels.push(bricks.length - 1)
  const gridLines = brickGridLines(pMin, pMax, Math.max(4, Math.floor(height / 55)))

  const rects = bricks.map((b, i) => {
    const lo = Math.min(b.open_price, b.close_price)
    const hi = Math.max(b.open_price, b.close_price)
    let yTop = priceToY(hi)
    let yBot = priceToY(lo)
    let h = yBot - yTop
    if (h < MIN_BRICK_H) { h = MIN_BRICK_H; yTop = yBot - h }
    const up = b.direction === 'up'
    return React.createElement('rect', {
      key: i,
      x: BRICK_LEFT_PAD + i * BRICK_STEP,
      y: yTop,
      width: BRICK_W,
      height: h,
      rx: 1.5,
      fill: up ? 'var(--ui-accent,#4c9aff)' : 'var(--ui-danger,#ff5c5c)',
      fillOpacity: 0.85,
      stroke: up ? '#16a34a' : '#dc2626',
      strokeWidth: 0.5,
    })
  })

  const gridEls = gridLines.map((p) => {
    const y = priceToY(p)
    return React.createElement('g', { key: 'g' + p },
      React.createElement('line', {
        x1: BRICK_LEFT_PAD, x2: BRICK_LEFT_PAD + bricks.length * BRICK_STEP,
        y1: y, y2: y,
        stroke: 'var(--ui-text-tertiary,#888)', strokeOpacity: 0.15, strokeDasharray: '3 3',
      }),
      React.createElement('text', {
        x: BRICK_LEFT_PAD + bricks.length * BRICK_STEP + 5, y: y + 3,
        fill: 'var(--ui-text-tertiary,#888)', fontSize: 9, fontFamily: 'monospace',
      }, fmtBrickPrice(p)),
    )
  })

  const idxEls = idxLabels.map((i) =>
    React.createElement('text', {
      key: 'i' + i,
      x: BRICK_LEFT_PAD + i * BRICK_STEP + BRICK_W / 2,
      y: height - 6,
      fill: 'var(--ui-text-tertiary,#888)', fontSize: 8, fontFamily: 'monospace', textAnchor: 'middle',
    }, String(i)),
  )

  // Axis titles: "Price" (right, rotated) + "Brick index" (bottom centre)
  const chartEndX = BRICK_LEFT_PAD + bricks.length * BRICK_STEP
  const axisTitles = [
    React.createElement('text', {
      key: 'pricetitle',
      x: BRICK_LEFT_PAD + bricks.length * BRICK_STEP + 42,
      y: BRICK_TOP_PAD + chartH / 2,
      fill: 'var(--ui-text-tertiary,#888)', fontSize: 9, fontFamily: 'sans-serif',
      textAnchor: 'middle',
      transform: `rotate(-90, ${BRICK_LEFT_PAD + bricks.length * BRICK_STEP + 42}, ${BRICK_TOP_PAD + chartH / 2})`,
    }, 'Price'),
    React.createElement('text', {
      key: 'idxtitle',
      x: BRICK_LEFT_PAD + chartEndX / 2 - BRICK_LEFT_PAD / 2,
      y: height - 1,
      fill: 'var(--ui-text-tertiary,#888)', fontSize: 9, fontFamily: 'sans-serif', textAnchor: 'middle',
    }, 'Brick index'),
  ]

  // Level lines (entry/sl/tp) — dashed horizontal + right label
  const levelEls = (levels || [])
    .filter((lv) => lv.price != null && Number(lv.price) > 0)
    .map((lv) => {
      const y = priceToY(Number(lv.price))
      const color = lv.color || 'var(--ui-text-tertiary,#888)'
      return React.createElement('g', { key: 'lv' + lv.label },
        React.createElement('line', {
          x1: BRICK_LEFT_PAD, x2: BRICK_LEFT_PAD + bricks.length * BRICK_STEP,
          y1: y, y2: y,
          stroke: color, strokeWidth: 1, strokeDasharray: '5 3', strokeOpacity: 0.8,
        }),
        React.createElement('text', {
          x: BRICK_LEFT_PAD + bricks.length * BRICK_STEP + 5, y: y - 2,
          fill: color, fontSize: 9, fontFamily: 'monospace', fontWeight: 600,
        }, `${lv.label} ${fmtBrickPrice(Number(lv.price))}`),
      )
    })

  return React.createElement('div', null,
    React.createElement('svg', {
      viewBox: `0 0 ${svgW} ${height}`,
      width: '100%',
      height: 'auto',
      style: { display: 'block', maxHeight: 420 },
    },
      gridEls,
      levelEls,
      rects,
      idxEls,
      axisTitles,
    ),
    React.createElement('div', { className: 'nta-hint', style: { marginTop: 4 } },
      `${bricks.length} bricks (last ${bricks.length} of series) · up = buy (blue) · down = sell (red) · last brick index ${bricks[bricks.length - 1].brick_index != null ? bricks[bricks.length - 1].brick_index : bricks.length - 1}`),
  )
}

// ---------------------------------------------------------------------------
// Hot signals banner — qualified, most-recent signals ranked by effective kelly
// TTL: only signals with ts within HOT_TTL_MS of the newest signal are shown,
// so stale chips drop off automatically on the next poll.
// ---------------------------------------------------------------------------
const HOT_TTL_MS = 10 * 60 * 1000 // 10 min window vs the newest signal
const HOT_MAX = 5

function HotSignalsBanner({ signals }) {
  const rows = (signals.data || []).filter((s) => s.qualified)
  const newest = Math.max(...rows.map((r) => Date.parse(r.ts) || 0))
  const cutoff = newest ? newest - HOT_TTL_MS : 0
  const hot = rows
    .filter((r) => newest && (Date.parse(r.ts) || 0) >= cutoff)
    .map((r) => ({
      symbol: r.symbol,
      signal: r.signal,
      kelly: Number(r.effective_kelly != null ? r.effective_kelly : r.kelly_f) || 0,
      ts: r.ts,
    }))
    .sort((a, b) => b.kelly - a.kelly)
    .slice(0, HOT_MAX)

  // Hidden entirely when nothing is hot (display/hidden based on true|false).
  if (!hot.length) return null

  return React.createElement('div', { className: 'nta-card nta-hot-card' },
    React.createElement('h3', null, 'Hot signals'),
    React.createElement('span', { className: 'nta-hot-ts' },
      `as of ${new Date(newest).toISOString().slice(0, 19).replace('T', ' ')} UTC · ${hot.length} in 10m window`),
    React.createElement('div', { className: 'nta-hot' },
      hot.map((h) => React.createElement('div', {
        key: h.symbol + h.ts,
        className: cn('nta-hot-chip', h.signal === 'sell' ? 'nta-hot-sell' : 'nta-hot-buy'),
      },
        React.createElement('span', { className: 'nta-hot-sym' }, h.symbol),
        React.createElement('span', { className: 'nta-hot-dir' }, h.signal === 'sell' ? 'Sell' : 'Buy'),
        React.createElement('span', { className: 'nta-hot-kelly' }, `kelly ${h.kelly.toFixed(3)}`),
      )),
    ),
  )
}

// ---------------------------------------------------------------------------
// Main Admin Component
// ---------------------------------------------------------------------------
function NobleTraderAdmin() {
  const [config, updateConfig] = useConfig()
  const connected = !!(config.supabase_url && config.supabase_key)

  if (!connected) {
    return React.createElement(ConnectTab, { config, onSave: updateConfig })
  }

  const signals = useSupabaseData(config, 'nt_signal_sim', { select: 'outcome,qualified,symbol', limit: '5000' }, connected)
  const positions = useSupabaseData(config, 'nt_paper_positions', { select: 'status,realized_pnl', limit: '5000' }, connected)
  const recentPositions = useSupabaseData(config, 'nt_paper_positions', { select: 'symbol,direction,size_notional,size_units,status,realized_pnl,open_ts,close_ts', order: 'open_ts.desc', limit: '20' }, connected)
  const recentSignals = useSupabaseData(config, 'nt_signal_sim', { select: 'symbol,signal,ts,qualified,outcome,entry_price,stop_loss,take_profit,p_win,kelly_f,effective_kelly,source', order: 'ts.desc', limit: '30' }, connected)
  const equity = useSupabaseData(config, 'v_paper_equity', { select: 'day,realized_pnl,cumulative_pnl', order: 'day.desc', limit: '30' }, connected)
  const calibration = useSupabaseData(config, 'v_eod_calibration_bias', { select: 'day,symbol,avg_predicted_p_win,realized_win_rate,bias,status', order: 'day.desc', limit: '14' }, connected)
  const optimized = useSupabaseData(config, 'v_paper_vs_optimized_daily', { select: 'day,paper_pnl,equal_wt_pnl,paper_minus_equal_wt', order: 'day.desc', limit: '30' }, connected)
  const sweeps = useSupabaseData(config, 'nt_sweep_result', { select: 'symbol,sweep_timestamp,regime,regime_conf,markov_p_up,markov_p_dn,p_timesfm,kelly_f,effective_kelly,brick_size,sl_bricks,tp_bricks,signal,entry_price,stop_loss,take_profit,qualified', order: 'sweep_timestamp.desc', limit: '200' }, connected)

  // Renko brick chart: pick a symbol from the latest sweep, fetch its bricks.
  const [brickSym, setBrickSym] = React.useState(null)
  const latestSyms = []
  {
    const seen = {}
    for (const r of (sweeps.data || [])) {
      if (!seen[r.symbol]) { seen[r.symbol] = true; latestSyms.push(r.symbol) }
    }
  }
  const activeBrickSym = brickSym || (latestSyms[0] || '')
  // Fetch ONLY when a symbol is known (default = 1st latest-sweep symbol).
  // Never fetch unfiltered — a 200-row all-symbols grab renders a jumbled mix.
  const bricks = useSupabaseData(config, 'nt_renko_bricks',
    { select: 'symbol,direction,brick_size,open_price,close_price,high,low,brick_index,ts', order: 'brick_index.asc', limit: '200', symbol: 'eq.' + activeBrickSym },
    connected && !!activeBrickSym)

  const loading = signals.loading || positions.loading || recentPositions.loading
  const err = signals.error || positions.error || recentPositions.error

  if (err) {
    return React.createElement('div', { className: 'nta-root' },
      React.createElement('div', { className: 'nta-err' },
        `Supabase read failed: ${err.message}`),
      React.createElement('button', { className: 'nta-btn', onClick: () => updateConfig({ supabase_url: '', supabase_key: '' }) },
        'Disconnect / reconfigure'),
    )
  }

  const outcomeCounts = {}
  let qualifiedTotal = 0
  for (const s of (signals.data || [])) {
    const o = s.outcome || 'open'
    outcomeCounts[o] = (outcomeCounts[o] || 0) + 1
    if (s.qualified) qualifiedTotal += 1
  }
  const posStatus = {}
  let realizedPnl = 0
  for (const p of (positions.data || [])) {
    const st = p.status || 'unknown'
    posStatus[st] = (posStatus[st] || 0) + 1
    if (st === 'closed' || st === 'expired') realizedPnl += Number(p.realized_pnl || 0)
  }

  const eq = (equity.data || []).slice().reverse() // oldest → newest
  const eqVals = eq.map((r) => Number(r.cumulative_pnl) || 0)
  const cal = calibration.data || []
  const opt = optimized.data || []
  const resolved = (outcomeCounts.tp_hit || 0) + (outcomeCounts.sl_hit || 0) + (outcomeCounts.expired || 0)

  return React.createElement('div', { className: 'nta-root' },
    React.createElement(HotSignalsBanner, { signals: recentSignals }),
    React.createElement('div', { className: 'nta-grid' },
      React.createElement(StatCard, {
        title: 'Qualified signals',
        value: String(qualifiedTotal),
        sub: `${signals.data ? signals.data.length : 0} total signals captured`,
      }),
      React.createElement(StatCard, {
        title: 'Resolved outcomes',
        value: String(resolved),
        sub: `${outcomeCounts.tp_hit || 0} TP · ${outcomeCounts.sl_hit || 0} SL · ${outcomeCounts.expired || 0} expired`,
      }),
      React.createElement(StatCard, {
        title: 'Open positions',
        value: String(posStatus.open || 0),
        sub: `${posStatus.closed || 0} closed · ${posStatus.expired || 0} expired`,
      }),
      React.createElement(StatCard, {
        title: 'Realized PnL',
        value: `$${realizedPnl.toFixed(2)}`,
        sub: 'paper portfolio',
        tone: realizedPnl >= 0 ? 'pos' : 'neg',
      }),
    ),

    React.createElement('div', { className: 'nta-grid' },
      React.createElement('div', { className: 'nta-card' },
        React.createElement('h3', null, 'Paper Equity (cumulative $)'),
        React.createElement(LineChart, { points: eqVals, height: 110 }),
        React.createElement('div', { className: 'nta-hint' },
          eq.length ? `${eq[0].day} → ${eq[eq.length - 1].day}` : 'Pending first EOD resolution'),
      ),
      React.createElement('div', { className: 'nta-card' },
        React.createElement('h3', null, 'Outcome distribution'),
        React.createElement('div', { style: { display: 'flex', alignItems: 'center', gap: 12 } },
          React.createElement(Donut, {
            data: [
              { label: 'tp', value: outcomeCounts.tp_hit || 0, color: 'var(--ui-accent)' },
              { label: 'sl', value: outcomeCounts.sl_hit || 0, color: 'var(--ui-danger)' },
              { label: 'exp', value: outcomeCounts.expired || 0, color: 'var(--ui-text-tertiary)' },
            ],
          }),
          React.createElement('div', null,
            React.createElement('div', { className: 'nta-row' },
              React.createElement('span', { className: 'nta-k' }, 'TP'),
              React.createElement('span', { className: 'nta-v nta-pos' }, String(outcomeCounts.tp_hit || 0))),
            React.createElement('div', { className: 'nta-row' },
              React.createElement('span', { className: 'nta-k' }, 'SL'),
              React.createElement('span', { className: 'nta-v nta-neg' }, String(outcomeCounts.sl_hit || 0))),
            React.createElement('div', { className: 'nta-row' },
              React.createElement('span', { className: 'nta-k' }, 'Expired'),
              React.createElement('span', { className: 'nta-v' }, String(outcomeCounts.expired || 0))),
          ),
        ),
      ),
    ),

    React.createElement('div', { className: 'nta-grid' },
      React.createElement('div', { className: 'nta-card' },
        React.createElement('h3', null, 'Calibration bias by day'),
        cal.length
          ? cal.slice(0, 8).map((r) => (
            React.createElement('div', { key: r.day + r.symbol, className: 'nta-row' },
              React.createElement('span', { className: 'nta-k' }, `${r.day} ${r.symbol}`),
              React.createElement('span', {
                className: cn('nta-v',
                  r.status === 'OVERCONFIDENT' ? 'nta-pos' :
                  r.status === 'UNDERCONFIDENT' ? 'nta-neg' : ''),
              }, `${Number(r.bias || 0).toFixed(3)} ${r.status}`),
            )
          ))
          : React.createElement('div', { className: 'nta-hint' }, 'Pending calibration records'),
      ),
      React.createElement('div', { className: 'nta-card' },
        React.createElement('h3', null, 'Paper vs equal-weight (daily $)'),
        React.createElement(Bars, {
          data: opt.map((r) => ({ label: String(r.day).slice(5), value: Number(r.paper_minus_equal_wt) || 0 })),
        }),
        React.createElement('div', { className: 'nta-hint' },
          'Positive = paper book beats equal-weight'),
      ),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Latest sweep — HMM × Renko (histogram of effective kelly)'),
      (() => {
        const seen = {}
        const latest = []
        for (const r of (sweeps.data || [])) {
          if (!seen[r.symbol]) {
            seen[r.symbol] = true
            latest.push(r)
          }
        }
        const histData = latest.map((r) => ({
          label: r.symbol,
          value: Number(r.effective_kelly != null ? r.effective_kelly : r.kelly_f) || 0,
          badge: r.signal === 'sell' ? 'SELL' : 'BUY',
          color: r.signal === 'sell' ? 'var(--ui-danger, #ff5c5c)' : 'var(--ui-accent, #4c9aff)',
          sub: String(r.regime || '').replace(/^high_vol_/, 'hv-').replace(/^low_vol_/, 'lv-').replace(/strong_/, 'str-'),
        }))
        return React.createElement('div', null,
          React.createElement(HBar, {
            data: histData,
            format: (v) => v.toFixed(3),
          }),
          React.createElement('div', { className: 'nta-hint' },
            `${latest.length} symbols · bar = effective kelly (blue buy / red sell) · as of ${latest[0] ? String(latest[0].sweep_timestamp).slice(0, 19).replace('T', ' ') : '—'} UTC`),
        )
      })(),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Renko bricks — per symbol'),
      React.createElement('div', { className: 'nta-brick-picker' },
        latestSyms.map((s) =>
          React.createElement('button', {
            key: s,
            className: cn('nta-brick-btn', s === activeBrickSym ? 'nta-brick-btn-active' : ''),
            onClick: () => setBrickSym(s),
          }, s),
        ),
      ),
      (() => {
        // Last ~10 bricks of the active symbol's series (the dashboard window)
        const all = (bricks.data || []).filter((b) => b.symbol === activeBrickSym)
        const window = all.slice(-10)
        // Entry / SL / TP levels from the latest sweep row for this symbol
        const sweepRow = (sweeps.data || []).find((r) => r.symbol === activeBrickSym)
        const levels = []
        if (sweepRow) {
          if (sweepRow.entry_price != null && Number(sweepRow.entry_price) > 0)
            levels.push({ label: 'ENTRY', price: Number(sweepRow.entry_price), color: 'var(--ui-text-primary,#eee)' })
          if (sweepRow.stop_loss != null && Number(sweepRow.stop_loss) > 0)
            levels.push({ label: 'SL', price: Number(sweepRow.stop_loss), color: 'var(--ui-danger,#ff5c5c)' })
          if (sweepRow.take_profit != null && Number(sweepRow.take_profit) > 0)
            levels.push({ label: 'TP', price: Number(sweepRow.take_profit), color: 'var(--ui-accent,#4c9aff)' })
        }
        return React.createElement(RenkoBrickChart, { bricks: window, levels })
      })(),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Latest sweep — HMM × Renko detail'),
      React.createElement('table', { className: 'nta-table' },
        React.createElement('thead', null,
          React.createElement('tr', null,
            React.createElement('th', null, 'Symbol'),
            React.createElement('th', null, 'HMM regime'),
            React.createElement('th', null, 'Conf'),
            React.createElement('th', null, 'Kelly'),
            React.createElement('th', null, 'Renko brick'),
            React.createElement('th', null, 'SL/TP'),
            React.createElement('th', null, 'Entry'),
            React.createElement('th', null, 'SL'),
            React.createElement('th', null, 'TP'),
            React.createElement('th', null, 'Signal'),
            React.createElement('th', null, 'Sweep ts'))),
        React.createElement('tbody', null,
          (() => {
            const seen = {}
            const latest = []
            for (const r of (sweeps.data || [])) {
              if (!seen[r.symbol]) {
                seen[r.symbol] = true
                latest.push(r)
              }
            }
            return latest.map((r) => (
              React.createElement('tr', { key: r.symbol + r.sweep_timestamp },
                React.createElement('td', null, r.symbol),
                React.createElement('td', null, r.regime),
                React.createElement('td', null, r.regime_conf != null ? Number(r.regime_conf).toFixed(2) : '—'),
                React.createElement('td', null, (r.effective_kelly != null ? Number(r.effective_kelly) : Number(r.kelly_f || 0)).toFixed(3)),
                React.createElement('td', null, r.brick_size),
                React.createElement('td', null, `${r.sl_bricks}/${r.tp_bricks}`),
                React.createElement('td', { className: 'nta-sm' }, fmtPrice(r.entry_price)),
                React.createElement('td', { className: 'nta-sm' }, fmtPrice(r.stop_loss)),
                React.createElement('td', { className: 'nta-sm' }, fmtPrice(r.take_profit)),
                React.createElement('td', {
                  className: r.signal === 'sell' ? 'nta-neg' : 'nta-pos',
                }, r.signal || '—'),
                React.createElement('td', null, String(r.sweep_timestamp || '').slice(0, 19).replace('T', ' ')),
              )
            ))
          })()),
      ),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Recent paper positions'),
      React.createElement('table', { className: 'nta-table' },
        React.createElement('thead', null,
          React.createElement('tr', null,
            React.createElement('th', null, 'Symbol'),
            React.createElement('th', null, 'Dir'),
            React.createElement('th', null, 'Notional'),
            React.createElement('th', null, 'Status'),
            React.createElement('th', null, 'PnL'),
            React.createElement('th', null, 'Opened'))),
        React.createElement('tbody', null,
          (recentPositions.data || []).map((p) => (
            React.createElement('tr', { key: p.position_id || (p.symbol + p.open_ts) },
              React.createElement('td', null, p.symbol),
              React.createElement('td', null, p.direction),
              React.createElement('td', null, `$${Number(p.size_notional || 0).toFixed(0)}`),
              React.createElement('td', null,
                React.createElement(OutcomeBadge, { outcome: p.status })),
              React.createElement('td', {
                className: (p.realized_pnl || 0) >= 0 ? 'nta-pos' : 'nta-neg',
              }, p.realized_pnl != null ? `$${Number(p.realized_pnl).toFixed(2)}` : '—'),
              React.createElement('td', null, String(p.open_ts || '').slice(0, 16)),
            )
          ))),
      ),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Recent signals'),
      React.createElement('table', { className: 'nta-table' },
        React.createElement('thead', null,
          React.createElement('tr', null,
            React.createElement('th', null, 'Symbol'),
            React.createElement('th', null, 'Signal'),
            React.createElement('th', null, 'Outcome'),
            React.createElement('th', null, 'Q'),
            React.createElement('th', null, 'Entry'),
            React.createElement('th', null, 'Ts'))),
        React.createElement('tbody', null,
          (recentSignals.data || []).map((sig) => (
            React.createElement('tr', { key: sig.signal_id || (sig.symbol + sig.ts) },
              React.createElement('td', null, sig.symbol),
              React.createElement('td', null, sig.signal),
              React.createElement('td', null,
                React.createElement(OutcomeBadge, { outcome: sig.outcome })),
              React.createElement('td', null, sig.qualified ? '✓' : ''),
              React.createElement('td', null, sig.entry_price != null ? Number(sig.entry_price).toFixed(5) : '—'),
              React.createElement('td', null, String(sig.ts || '').slice(0, 16)),
            )
          ))),
      ),
    ),

    React.createElement('div', { className: 'nta-hint' },
      'Direct Supabase · auto-refresh 60s · connected to ' + (config.supabase_url || '')),
  )
}

// ---------------------------------------------------------------------------
const plugin = {
  id: 'noble-trader-admin',
  name: 'Noble Trader Admin',
  defaultEnabled: false,
  register(ctx) {
    ensureStyle()
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/noble-trader-admin' },
        render: () => React.createElement(NobleTraderAdmin, null),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 60,
        data: { codicon: 'graph-line', label: 'Noble Trader Admin', path: '/noble-trader-admin' },
      },
    ])
  },
}

export default plugin
