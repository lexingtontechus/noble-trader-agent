/**
 * Talaria — Desktop Runtime Plugin (Electron app surface)
 *
 * CLIENT-FACING product dashboard for the Noble Trader signal service.
 * Separate from `noble-trader-admin` (internal superset) and `noble-trader`
 * (agent setup wizard) — this is the paywalled subscriber surface.
 *
 * Data path — STANDALONE, direct Supabase, no backend/agent/proxy hop:
 *   - Claim validation: POST {supabase_url}/functions/v1/talaria-check with
 *     {token} → {ok, plan_slug, plan_uuid, sub_status, period_end, grace_end,
 *     next_charge_url}. The plan_uuid from the SERVER response drives the
 *     symbol list + channel selection — never client-derived.
 *   - Symbol list: GET /rest/v1/nt_symbol?select=symbol&plan_ids=cs.{plan_uuid}
 *     (PostgREST `cs.` contains-filter on the UUID[] column — the array-literal
 *     braces form is required; bare `cs.<uuid>` fails 22P02 on Postgres).
 *   - Data poll: GET /rest/v1/nt_sweep_result + nt_renko_bricks +
 *     nt_paper_positions + v_paper_equity via the PUBLIC anon key
 *     (read-only RLS, migration 107).
 *   - Live push: native WebSocket to the Supabase Realtime endpoint
 *     (Phoenix protocol) — joins `realtime:signals` (all plans) and
 *     `realtime:paper` (Precision Pro only). Falls back to the 60s REST poll
 *     on socket error/close — never blanks the dashboard.
 *
 * Claim check cadence: on mount + every 24h. Data refresh: every 60s.
 * Runtime disk plugins are plain ESM — no JSX. Uses React.createElement.
 * Only `react` + `@hermes/plugin-sdk` imports are allowed.
 */
import React from 'react'
import { cn, ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'

// ---------------------------------------------------------------------------
// Plugin config (localStorage-backed — same pattern as noble-trader-admin)
// ---------------------------------------------------------------------------
const CONFIG_FILE = 'talaria-config.json'
const CLAIM_CHECK_MS = 24 * 60 * 60 * 1000 // 24h subscription re-check
const DATA_POLL_MS = 60 * 1000 // 60s REST data fallback poll

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
// Direct Supabase REST fetch (PostgREST, anon key headers)
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
// Claim validation — Supabase Edge Function `talaria-check`
// Throws { kind: 'not-deployed'|'bad-token'|'error', message } on failure so
// the caller can route the UI (404 = function not deployed yet → Connect tab
// shows a clear 'claim service not deployed' state, never crashes).
// ---------------------------------------------------------------------------
async function claimCheck(config) {
  const base = (config.supabase_url || '').replace(/\/+$/, '')
  if (!base || !config.supabase_key || !config.claim_token) {
    throw { kind: 'error', message: 'Enter Supabase URL, anon key and claim token' }
  }
  let resp
  try {
    resp = await fetch(`${base}/functions/v1/talaria-check`, {
      method: 'POST',
      headers: {
        'apikey': config.supabase_key,
        'Authorization': `Bearer ${config.supabase_key}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ token: config.claim_token }),
    })
  } catch (err) {
    throw { kind: 'error', message: 'Claim service unreachable — ' + String(err.message || err) }
  }
  if (resp.status === 404) {
    throw { kind: 'not-deployed', message: 'talaria-check Edge Function not deployed on this project (404)' }
  }
  if (resp.status === 401) {
    let body = {}
    try { body = await resp.json() } catch (e) {}
    throw { kind: 'bad-token', message: `Claim token rejected (${body.error || 'invalid_claim'})` }
  }
  if (!resp.ok) {
    throw { kind: 'error', message: `${resp.status} ${resp.statusText}` }
  }
  let json
  try { json = await resp.json() } catch (e) {
    throw { kind: 'error', message: 'Unexpected claim response (not JSON)' }
  }
  if (!json || json.ok !== true) {
    const err = (json && json.error) || 'invalid_claim'
    throw { kind: err === 'invalid_claim' || err === 'revoked' || err === 'expired' ? 'bad-token' : 'error', message: `Claim rejected (${err})` }
  }
  return json
}

// ---------------------------------------------------------------------------
// Remote data hook — polls Supabase REST every 60s (the data fallback that
// keeps the dashboard alive when the Realtime socket is down)
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
    const timer = setInterval(load, DATA_POLL_MS)
    return () => clearInterval(timer)
  }, [load])

  return { data, loading, error, reload: load }
}

// ---------------------------------------------------------------------------
// Native WebSocket Realtime client (Phoenix protocol — no packages)
// ---------------------------------------------------------------------------
function realtimeWsUrl(config) {
  const base = (config.supabase_url || '').replace(/\/+$/, '')
  const host = base.replace(/^https?:\/\//i, '')
  return `wss://${host}/realtime/v1/websocket?apikey=${encodeURIComponent(config.supabase_key || '')}&vsn=1.0.0`
}

function parseRealtimeMessage(raw) {
  let msg
  try { msg = JSON.parse(raw) } catch (e) { return { type: 'other' } }
  if (!msg || typeof msg !== 'object') return { type: 'other' }
  if (msg.event === 'phx_reply') {
    return { type: 'reply', topic: msg.topic, ref: msg.ref, status: msg.payload && msg.payload.status }
  }
  if (msg.event === 'phx_error' || msg.event === 'phx_close') {
    return { type: 'socket_error', topic: msg.topic }
  }
  if (msg.event === 'broadcast' && msg.payload && msg.payload.type === 'broadcast' && msg.payload.event) {
    return { type: 'broadcast', event: msg.payload.event, payload: msg.payload.payload || {} }
  }
  return { type: 'other' }
}

// Open-tab-only socket: opened while the dashboard is mounted, closed on
// unmount. On error/close it schedules an exponential-backoff reconnect; the
// 60s REST polls in useSupabaseData keep rendering data in the meantime.
// `handlers` is kept in a ref so changing callbacks never reconnects.
function useRealtime(config, enabled, planSlug, handlers) {
  const [state, setState] = React.useState('idle')
  const handlersRef = React.useRef(handlers)
  handlersRef.current = handlers

  React.useEffect(() => {
    if (!enabled || !config.supabase_url || !config.supabase_key) return undefined
    const wsUrl = realtimeWsUrl(config)
    let ws = null
    let disposed = false
    let retryTimer = null
    let attempts = 0

    const scheduleRetry = () => {
      if (disposed) return
      const delay = Math.min(30000, 5000 * Math.pow(2, attempts))
      attempts += 1
      retryTimer = setTimeout(connect, delay)
    }
    const connect = () => {
      if (disposed) return
      setState('connecting')
      try {
        ws = new WebSocket(wsUrl)
      } catch (err) {
        setState('error')
        scheduleRetry()
        return
      }
      ws.onopen = () => {
        if (disposed) { try { ws.close() } catch (e) {} return }
        setState('open')
        attempts = 0
        ws.send(JSON.stringify({
          topic: 'realtime:signals',
          event: 'phx_join',
          payload: { config: { broadcast: { self: false, ack: false } } },
          ref: '1',
        }))
        if (planSlug === 'precision_pro') {
          ws.send(JSON.stringify({
            topic: 'realtime:paper',
            event: 'phx_join',
            payload: { config: { broadcast: { self: false, ack: false } } },
            ref: '2',
          }))
        }
      }
      ws.onmessage = (evt) => {
        const msg = parseRealtimeMessage(evt.data)
        if (msg.type === 'broadcast') {
          if (msg.event === 'signal' && handlersRef.current.onSignal) {
            handlersRef.current.onSignal(msg.payload)
          } else if (msg.event === 'paper' && handlersRef.current.onPaper) {
            handlersRef.current.onPaper(msg.payload)
          }
        }
      }
      ws.onerror = () => { setState('error') }
      ws.onclose = () => {
        if (disposed) return
        setState('closed')
        scheduleRetry()
      }
    }
    connect()
    return () => {
      disposed = true
      if (retryTimer) clearTimeout(retryTimer)
      if (ws) {
        try { ws.onclose = null; ws.close() } catch (e) {}
      }
    }
  }, [enabled, config.supabase_url, config.supabase_key, planSlug])

  return state
}

// ---------------------------------------------------------------------------
// Styles — theme variables only (no hardcoded colors). `tla-` prefix keeps
// this plugin's classes from colliding with noble-trader-admin's `nta-`.
// ---------------------------------------------------------------------------
const STYLE_ID = 'talaria-style'
const CSS = [
  '.tla-root{display:flex;flex-direction:column;height:100%;gap:12px;padding:16px;overflow:auto;}',
  '.tla-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px;}',
  '.tla-card{background:var(--ui-panel,#161616);border:1px solid var(--ui-stroke-secondary,#2a2a2a);border-radius:8px;padding:12px;display:flex;flex-direction:column;gap:8px;}',
  '.tla-card h3{margin:0;font-size:12px;font-weight:600;color:var(--ui-text-secondary,#999);text-transform:uppercase;letter-spacing:0.04em;}',
  '.tla-card .tla-value{font-size:26px;font-weight:700;color:var(--ui-text-primary,#eee);}',
  '.tla-card .tla-sub{font-size:11px;color:var(--ui-text-quaternary,#777);}',
  '.tla-row{display:flex;justify-content:space-between;align-items:baseline;padding:3px 0;font-size:12px;}',
  '.tla-row .tla-k{color:var(--ui-text-tertiary,#888);}',
  '.tla-row .tla-v{color:var(--ui-text-primary,#eee);font-variant-numeric:tabular-nums;}',
  '.tla-pos{color:var(--ui-accent,#4c9aff);}',
  '.tla-neg{color:var(--ui-danger,#ff5c5c);}',
  '.tla-table{width:100%;border-collapse:collapse;font-size:11px;}',
  '.tla-table th,.tla-table td{border-bottom:1px solid var(--ui-stroke-secondary,#2a2a2a);padding:5px 6px;text-align:left;white-space:nowrap;}',
  '.tla-table th{color:var(--ui-text-tertiary,#888);font-weight:600;}',
  '.tla-table .tla-sm{font-size:9px;color:var(--ui-text-secondary,#aaa);font-variant-numeric:tabular-nums;white-space:nowrap;}',
  '.tla-table tbody tr:hover{background:rgba(255,255,255,0.02);}',
  '.tla-badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:10px;font-weight:600;text-transform:uppercase;}',
  '.tla-badge.open{background:rgba(120,220,120,0.15);color:#78dc78;}',
  '.tla-badge.closed{background:rgba(76,154,255,0.15);color:var(--ui-accent,#4c9aff);}',
  '.tla-badge.opened{background:rgba(76,154,255,0.15);color:var(--ui-accent,#4c9aff);}',
  '.tla-badge.equity{background:rgba(153,153,153,0.15);color:var(--ui-text-tertiary,#888);}',
  '.tla-badge.active{background:rgba(120,220,120,0.15);color:#78dc78;}',
  '.tla-badge.grace{background:rgba(240,180,60,0.15);color:#f0b43c;}',
  '.tla-hot{display:flex;flex-wrap:wrap;gap:8px;align-items:stretch;margin-top:8px;}',
  '.tla-hot-card h3{margin-bottom:2px;}',
  '.tla-hot-ts{display:block;font-size:10px;color:var(--ui-text-quaternary,#777);margin-bottom:2px;}',
  '.tla-hot-chip{display:flex;flex-direction:row;align-items:center;gap:6px;padding:6px 10px;border-radius:8px;border:1px solid var(--ui-stroke-secondary,#2a2a2a);}',
  '.tla-hot-chip .tla-hot-sym{font-size:13px;font-weight:700;color:var(--ui-text-primary,#eee);}',
  '.tla-hot-chip .tla-hot-kelly{font-size:11px;font-variant-numeric:tabular-nums;color:var(--ui-text-secondary,#aaa);margin-left:4px;}',
  '.tla-hot-chip .tla-hot-dir{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:0.06em;padding:2px 5px;border-radius:4px;}',
  '.tla-hot-buy{background:rgba(76,154,255,0.10);border-color:rgba(76,154,255,0.35);}',
  '.tla-hot-buy .tla-hot-dir{color:var(--ui-accent,#4c9aff);background:rgba(76,154,255,0.15);}',
  '.tla-hot-sell{background:rgba(255,92,92,0.10);border-color:rgba(255,92,92,0.35);}',
  '.tla-hot-sell .tla-hot-dir{color:var(--ui-danger,#ff5c5c);background:rgba(255,92,92,0.15);}',
  '.tla-err{color:var(--ui-danger,#ff5c5c);font-size:12px;padding:8px;}',
  '.tla-ok{color:#78dc78;font-size:12px;}',
  '.tla-hint{color:var(--ui-text-quaternary,#666);font-size:11px;}',
  '.tla-field{display:flex;flex-direction:column;gap:4px;margin-bottom:10px;}',
  '.tla-field label{font-size:11px;color:var(--ui-text-tertiary,#888);}',
  '.tla-field input{background:var(--ui-panel,#101010);border:1px solid var(--ui-stroke-secondary,#2a2a2a);color:var(--ui-text-primary,#eee);border-radius:6px;padding:7px 10px;font-size:12px;font-family:inherit;}',
  '.tla-btn{background:var(--ui-accent,#4c9aff);color:#fff;border:none;border-radius:6px;padding:8px 16px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;text-decoration:none;display:inline-block;}',
  '.tla-btn:hover{opacity:0.9;}',
  '.tla-btn-secondary{background:transparent;border:1px solid var(--ui-stroke-secondary,#2a2a2a);color:var(--ui-text-secondary,#aaa);}',
  '.tla-btn-secondary:hover{border-color:var(--ui-accent,#4c9aff);color:var(--ui-accent,#4c9aff);opacity:1;}',
  '.tla-banner{display:flex;align-items:center;gap:8px;padding:8px 12px;border-radius:8px;font-size:12px;background:rgba(240,180,60,0.10);border:1px solid rgba(240,180,60,0.35);color:#f0b43c;}',
  '.tla-banner-paywall{background:rgba(255,92,92,0.10);border-color:rgba(255,92,92,0.35);color:var(--ui-danger,#ff5c5c);}',
  '.tla-center{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:14px;padding:24px;text-align:center;}',
  '.tla-title{font-size:18px;font-weight:700;color:var(--ui-text-primary,#eee);}',
  '.tla-brick-picker{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 10px;}',
  '.tla-brick-btn{background:transparent;border:1px solid var(--ui-stroke-secondary,#2a2a2a);border-radius:8px;color:var(--ui-text-secondary,#aaa);padding:6px 14px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;letter-spacing:0.03em;}',
  '.tla-brick-btn:hover{border-color:var(--ui-accent,#4c9aff);color:var(--ui-accent,#4c9aff);}',
  '.tla-brick-btn-active{background:rgba(76,154,255,0.18);border-color:var(--ui-accent,#4c9aff);color:var(--ui-accent,#4c9aff);}',
].join('')

function ensureStyle() {
  let style = document.getElementById(STYLE_ID)
  if (!style) {
    style = document.createElement('style')
    style.id = STYLE_ID
    document.head.appendChild(style)
  }
  // Always refresh textContent — hot-reloads keep the OLD css otherwise and
  // new classes silently never apply.
  style.textContent = CSS
}

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------
// Adaptive price formatter: fewer decimals for large prices (XAU ~4095 → 2dp),
// more for small prices (FX ~1.08 → 5dp).
function fmtPrice(v) {
  if (v == null || isNaN(Number(v))) return '—'
  const n = Number(v)
  const abs = Math.abs(n)
  if (abs >= 1000) return n.toFixed(1)
  if (abs >= 100) return n.toFixed(3)
  if (abs >= 10) return n.toFixed(4)
  return n.toFixed(5)
}

function StatCard({ title, value, sub, tone }) {
  return React.createElement('div', { className: 'tla-card' },
    React.createElement('h3', null, title),
    React.createElement('div', {
      className: cn('tla-value', tone === 'pos' ? 'tla-pos' : tone === 'neg' ? 'tla-neg' : ''),
    }, value),
    sub ? React.createElement('div', { className: 'tla-sub' }, sub) : null,
  )
}

// ---------------------------------------------------------------------------
// Renko brick chart — SVG bricks (up green / down red), price axis on the
// right, brick-index axis on the bottom. Ported from the admin plugin.
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
    return React.createElement('div', { className: 'tla-hint' }, 'No bricks yet')
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
    React.createElement('div', { className: 'tla-hint', style: { marginTop: 4 } },
      `${bricks.length} bricks (last ${bricks.length} of series) · up = buy (blue) · down = sell (red) · last brick index ${bricks[bricks.length - 1].brick_index != null ? bricks[bricks.length - 1].brick_index : bricks.length - 1}`),
  )
}

// ---------------------------------------------------------------------------
// Kelly histogram — horizontal bars, value labels INSIDE bars when wide
// enough, 0 → max scale axis, regime/sub text after the bar. Ported from the
// admin plugin (same UX preferences).
// ---------------------------------------------------------------------------
function HBar({ data, height = 140, width = 640, format }) {
  if (!data || !data.length) {
    return React.createElement('div', { className: 'tla-hint' }, 'No data yet')
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
    // after the bar end in the bar's color.
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
    React.createElement('div', { className: 'tla-hint', style: { display: 'flex', gap: 16, marginBottom: 10, fontSize: 11 } },
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
// Hot signals banner — live 'signal' broadcasts + seed from nt_sweep_result
// (qualified, non-neutral, kelly present). 10-min TTL vs the newest signal,
// sorted by kelly desc, ~5 shown. Hidden entirely when empty (returns null).
// ---------------------------------------------------------------------------
const HOT_TTL_MS = 10 * 60 * 1000 // 10 min window vs the newest signal
const HOT_MAX = 5

function HotSignalsBanner({ signals }) {
  const rows = signals || []
  const newest = Math.max(...rows.map((s) => Date.parse(s.ts) || 0))
  const cutoff = newest ? newest - HOT_TTL_MS : 0
  const hot = rows
    .filter((s) => newest && (Date.parse(s.ts) || 0) >= cutoff)
    .sort((a, b) => Number(b.kelly || 0) - Number(a.kelly || 0))
    .slice(0, HOT_MAX)

  if (!hot.length) return null

  return React.createElement('div', { className: 'tla-card tla-hot-card' },
    React.createElement('h3', null, 'Hot signals'),
    React.createElement('span', { className: 'tla-hot-ts' },
      `as of ${new Date(newest).toISOString().slice(0, 19).replace('T', ' ')} UTC · ${hot.length} in 10m window`),
    React.createElement('div', { className: 'tla-hot' },
      hot.map((h) => {
        const sell = String(h.direction || '').toLowerCase() === 'sell'
        return React.createElement('div', {
          key: h.symbol + (h.ts || ''),
          className: cn('tla-hot-chip', sell ? 'tla-hot-sell' : 'tla-hot-buy'),
        },
          React.createElement('span', { className: 'tla-hot-sym' }, h.symbol),
          React.createElement('span', { className: 'tla-hot-dir' }, sell ? 'Sell' : 'Buy'),
          React.createElement('span', { className: 'tla-hot-kelly' }, `kelly ${Number(h.kelly || 0).toFixed(3)}`),
        )
      }),
    ),
  )
}

// ---------------------------------------------------------------------------
// Paper section — Precision Pro only. Live 'paper' broadcast events
// (opened/closed/equity) appended to a list (cap 50) + seed from
// nt_paper_positions + latest v_paper_equity row. Renders an empty state
// gracefully when the tables don't exist yet (PGRST205 / 404).
// ---------------------------------------------------------------------------
function PaperSection({ positions, equity, events }) {
  const eqRow = (equity.data || [])[0]
  const seedRows = (positions.data || []).map((p) => ({
    type: p.status === 'closed' || p.status === 'expired' ? 'closed' : 'opened',
    symbol: p.symbol,
    direction: p.direction,
    realized_pnl: p.realized_pnl,
    r_multiple: p.r_multiple,
    ts: p.open_ts,
  }))
  const rows = [...(events || []), ...seedRows].slice(0, 50)
  const missingTable = !!(positions.error || equity.error)

  return React.createElement('div', { className: 'tla-card' },
    React.createElement('h3', null, 'Paper portfolio — Precision Pro'),
    React.createElement('div', { className: 'tla-grid' },
      React.createElement(StatCard, {
        title: 'Paper equity',
        value: eqRow ? `$${Number(eqRow.cumulative_pnl || 0).toFixed(2)}` : '—',
        sub: eqRow ? `as of ${String(eqRow.day || '').slice(0, 10)} · realized today $${Number(eqRow.realized_pnl || 0).toFixed(2)}` : 'no equity ticks yet',
        tone: eqRow && Number(eqRow.cumulative_pnl) >= 0 ? 'pos' : 'neg',
      }),
    ),
    missingTable && rows.length === 0
      ? React.createElement('div', { className: 'tla-hint' },
          'Paper portfolio data not available yet — the nt_paper_positions table or v_paper_equity view is not deployed (PGRST205). Live paper events will still appear here once the backend publishes them.')
      : null,
    React.createElement('table', { className: 'tla-table' },
      React.createElement('thead', null,
        React.createElement('tr', null,
          React.createElement('th', null, 'Type'),
          React.createElement('th', null, 'Symbol'),
          React.createElement('th', null, 'Dir'),
          React.createElement('th', null, 'Realized PnL'),
          React.createElement('th', null, 'R-multiple'),
          React.createElement('th', null, 'Ts'))),
      React.createElement('tbody', null,
        rows.length
          ? rows.map((r, i) => (
            React.createElement('tr', { key: (r.symbol || 'evt') + (r.ts || '') + i },
              React.createElement('td', null,
                React.createElement('span', { className: cn('tla-badge', r.type === 'closed' ? 'closed' : r.type === 'opened' ? 'opened' : 'equity') }, r.type || 'event')),
              React.createElement('td', null, r.symbol || '—'),
              React.createElement('td', null, r.direction || '—'),
              React.createElement('td', {
                className: (r.realized_pnl || 0) >= 0 ? 'tla-pos' : 'tla-neg',
              }, r.realized_pnl != null ? `$${Number(r.realized_pnl).toFixed(2)}` : '—'),
              React.createElement('td', { className: 'tla-sm' }, r.r_multiple != null ? Number(r.r_multiple).toFixed(2) : '—'),
              React.createElement('td', { className: 'tla-sm' }, String(r.ts || '').slice(0, 16)),
            )
          ))
          : React.createElement('tr', null,
            React.createElement('td', { colSpan: 6 },
              React.createElement('div', { className: 'tla-hint' }, missingTable ? 'Waiting for paper tables…' : 'No paper activity yet — positions appear when the backend opens/closes them.')),
          ),
      ),
    ),
    React.createElement('div', { className: 'tla-hint' },
      `${rows.length} rows · live broadcast + REST seed · 60s poll`),
  )
}

// ---------------------------------------------------------------------------
// Connect tab — Supabase URL + public anon key + claim token.
// Save triggers the talaria-check; inline result shows ok / 401 / 404
// ('claim service not deployed') states.
// ---------------------------------------------------------------------------
function ConnectTab({ config, onSave, checkPhase, checkMsg }) {
  const [url, setUrl] = React.useState(config.supabase_url || '')
  const [key, setKey] = React.useState(config.supabase_key || '')
  const [token, setToken] = React.useState(config.claim_token || '')
  const [testing, setTesting] = React.useState(false)
  const [testResult, setTestResult] = React.useState(null)

  const testConnection = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const base = url.replace(/\/+$/, '')
      const resp = await fetch(`${base}/rest/v1/nt_symbol?select=symbol&limit=1`, {
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
    onSave({ supabase_url: url.trim(), supabase_key: key.trim(), claim_token: token.trim() })
  }

  const statusEls = []
  if (checkPhase === 'running') {
    statusEls.push(React.createElement('div', { key: 's', className: 'tla-hint', style: { marginTop: 8 } },
      'Validating claim token against talaria-check…'))
  } else if (checkPhase === 'not-deployed') {
    statusEls.push(React.createElement('div', { key: 's', className: 'tla-err', style: { marginTop: 8 } },
      'Claim service not deployed — the talaria-check Edge Function is not live on this project yet (404). The dashboard will unlock once the backend deploys it.'))
  } else if (checkPhase === 'bad-token') {
    statusEls.push(React.createElement('div', { key: 's', className: 'tla-err', style: { marginTop: 8 } },
      `Claim token rejected — ${checkMsg || 'invalid, revoked or expired token'}. Re-mint a token from the Talaria portal.`))
  } else if (checkPhase === 'error') {
    statusEls.push(React.createElement('div', { key: 's', className: 'tla-err', style: { marginTop: 8 } },
      checkMsg || 'Claim check failed'))
  }

  return React.createElement('div', { className: 'tla-root' },
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Talaria — Connect'),
      React.createElement('div', { className: 'tla-hint' },
        'Enter the Supabase project URL, the PUBLIC anon key and your claim token. The token is validated against the talaria-check Edge Function (live subscription status, re-checked every 24h). The plugin reads signals DIRECTLY from Supabase — no backend needed on your machine.'),
      React.createElement('div', { className: 'tla-field' },
        React.createElement('label', null, 'Supabase URL'),
        React.createElement('input', {
          value: url,
          placeholder: 'https://<project>.supabase.co',
          onChange: (e) => setUrl(e.target.value),
        })),
      React.createElement('div', { className: 'tla-field' },
        React.createElement('label', null, 'Supabase anon/public key'),
        React.createElement('input', {
          value: key,
          type: 'password',
          placeholder: 'sb_publishable_...',
          onChange: (e) => setKey(e.target.value),
        })),
      React.createElement('div', { className: 'tla-field' },
        React.createElement('label', null, 'Claim token'),
        React.createElement('input', {
          value: token,
          type: 'password',
          placeholder: 'paste claim token from the Talaria portal',
          onChange: (e) => setToken(e.target.value),
        })),
      React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' } },
        React.createElement('button', { className: 'tla-btn', onClick: save }, 'Save & Validate'),
        React.createElement('button', {
          className: cn('tla-btn', 'tla-btn-secondary'), onClick: testConnection, disabled: testing,
        }, testing ? 'Testing…' : 'Test connection'),
      ),
      testResult && React.createElement('div', {
        className: testResult.ok ? 'tla-ok' : 'tla-err',
        style: { marginTop: 8 },
      }, testResult.msg),
      statusEls,
    ),
  )
}

// ---------------------------------------------------------------------------
// Status screens — subscription routing
// ---------------------------------------------------------------------------
function SubscribeScreen({ claim, onRetry }) {
  const url = claim.next_charge_url || ''
  return React.createElement('div', { className: 'tla-center' },
    React.createElement('div', { className: 'tla-title' }, 'Talaria'),
    React.createElement('div', { className: 'tla-card', style: { maxWidth: 420, alignItems: 'center' } },
      React.createElement('h3', null, 'No active subscription'),
      React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
        `Your claim token is valid, but there is no active subscription for ${claim.plan_slug || 'your plan'}.`),
      url
        ? React.createElement('a', { className: 'tla-btn', href: url, target: '_blank', rel: 'noreferrer' },
            'Subscribe / pay')
        : React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
            'No payment link available — subscribe from the Talaria portal.'),
      React.createElement('button', { className: cn('tla-btn', 'tla-btn-secondary'), onClick: onRetry },
        'Re-check'),
      React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
        'Subscription status re-checks automatically every 24h.'),
    ),
  )
}

function WaitingScreen({ claim, onRetry }) {
  return React.createElement('div', { className: 'tla-center' },
    React.createElement('div', { className: 'tla-title' }, 'Talaria'),
    React.createElement('div', { className: 'tla-card', style: { maxWidth: 420, alignItems: 'center' } },
      React.createElement('h3', null, 'Waiting for payment confirmation'),
      React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
        `Your ${claim.plan_slug || ''} subscription is pending. Once the payment webhook confirms it, this screen unlocks automatically (re-checked every 24h).`),
      React.createElement('button', { className: 'tla-btn', onClick: onRetry },
        'Re-check now'),
    ),
  )
}

function PaywallScreen({ claim, onRetry }) {
  const url = claim.next_charge_url || ''
  const status = claim.sub_status || 'expired'
  return React.createElement('div', { className: 'tla-center' },
    React.createElement('div', { className: 'tla-title' }, 'Talaria'),
    React.createElement('div', { className: 'tla-card', style: { maxWidth: 420, alignItems: 'center' } },
      React.createElement('h3', null, `Subscription ${status}`),
      React.createElement('div', { className: 'tla-banner tla-banner-paywall', style: { width: '100%' } },
        `Your ${claim.plan_slug || ''} subscription is ${status} — renew to keep receiving signals.`),
      url
        ? React.createElement('a', { className: 'tla-btn', href: url, target: '_blank', rel: 'noreferrer' },
            'Renew / pay')
        : React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
            'No payment link available — renew from the Talaria portal.'),
      React.createElement('button', { className: cn('tla-btn', 'tla-btn-secondary'), onClick: onRetry },
        'Re-check'),
    ),
  )
}

// ---------------------------------------------------------------------------
// Talaria dashboard — hot-signal banner, kelly histogram, 10-brick renko
// chart (with ENTRY/SL/TP levels), Pro-only paper section.
// ---------------------------------------------------------------------------
function TalariaDashboard({ config, claim }) {
  const connected = !!(config.supabase_url && config.supabase_key)
  const isPro = claim.plan_slug === 'precision_pro'
  const [liveSignals, setLiveSignals] = React.useState([])
  const [paperEvents, setPaperEvents] = React.useState([])
  const [brickSym, setBrickSym] = React.useState(null)

  // Symbol list — plan-gated via nt_symbol.plan_ids cs. filter (UUID from the
  // server claim response, never client-derived).
  const hasPlanUuid = !!claim.plan_uuid
  const symbols = useSupabaseData(config, 'nt_symbol',
    { select: 'symbol', plan_ids: hasPlanUuid ? 'cs.{' + claim.plan_uuid + '}' : undefined },
    connected && hasPlanUuid)
  const symbolList = (symbols.data || []).map((r) => r.symbol).filter(Boolean)

  // Sweep data — powers the kelly histogram, the hot-signal seed and the
  // renko ENTRY/SL/TP levels. NOTE: nt_sweep_result's direction column is
  // `signal` (buy/sell/neutral) — the broadcast contract calls it `direction`,
  // so both are normalized client-side.
  const sweeps = useSupabaseData(config, 'nt_sweep_result',
    { select: 'symbol,signal,effective_kelly,kelly_f,entry_price,stop_loss,take_profit,sweep_timestamp,regime,qualified', order: 'sweep_timestamp.desc', limit: '200' },
    connected)

  // 10-brick renko window for the selected symbol (default = first symbol).
  const activeBrickSym = brickSym || (symbolList[0] || '')
  const bricks = useSupabaseData(config, 'nt_renko_bricks',
    { select: 'symbol,direction,brick_size,open_price,close_price,high,low,brick_index,ts', order: 'brick_index.desc', limit: '10', symbol: 'eq.' + activeBrickSym },
    connected && !!activeBrickSym)

  // Paper portfolio (Precision Pro only) — REST seed + live events.
  const paperPositions = useSupabaseData(config, 'nt_paper_positions',
    { select: 'symbol,direction,status,realized_pnl,r_multiple,open_ts', order: 'open_ts.desc', limit: '20' },
    connected && isPro)
  const paperEquity = useSupabaseData(config, 'v_paper_equity',
    { select: 'day,realized_pnl,cumulative_pnl', order: 'day.desc', limit: '1' },
    connected && isPro)

  // Live Realtime socket (open-tab-only — closed on unmount; the REST polls
  // above keep the dashboard alive on socket error/close).
  const wsState = useRealtime(config, connected, claim.plan_slug, {
    onSignal: (s) => setLiveSignals((prev) => [s, ...prev].slice(0, 50)),
    onPaper: (p) => setPaperEvents((prev) => [p, ...prev].slice(0, 50)),
  })

  // Hot-signal banner: live broadcasts + seed rows (qualified, non-neutral,
  // kelly present), deduped by symbol+ts, live first.
  const seedSignals = []
  for (const r of (sweeps.data || [])) {
    if (r.qualified && String(r.signal || '').toLowerCase() !== 'neutral' && r.kelly_f != null && !isNaN(Number(r.kelly_f))) {
      seedSignals.push({
        symbol: r.symbol,
        direction: r.signal,
        kelly: Number(r.effective_kelly != null ? r.effective_kelly : r.kelly_f) || 0,
        ts: r.sweep_timestamp,
      })
    }
  }
  const bannerSignals = []
  {
    const seen = {}
    for (const s of [...liveSignals, ...seedSignals]) {
      const k = s.symbol + '|' + (s.ts || '')
      if (!seen[k]) { seen[k] = true; bannerSignals.push(s) }
    }
  }

  // Kelly histogram — latest per symbol (fetch is sweep_timestamp desc, so
  // first occurrence per symbol is the newest).
  const histData = []
  {
    const seen = {}
    for (const r of (sweeps.data || [])) {
      if (!seen[r.symbol]) {
        seen[r.symbol] = true
        histData.push({
          label: r.symbol,
          value: Number(r.effective_kelly != null ? r.effective_kelly : r.kelly_f) || 0,
          badge: String(r.signal || '').toLowerCase() === 'sell' ? 'SELL' : 'BUY',
          color: String(r.signal || '').toLowerCase() === 'sell' ? 'var(--ui-danger, #ff5c5c)' : 'var(--ui-accent, #4c9aff)',
          sub: String(r.regime || '').replace(/^high_vol_/, 'hv-').replace(/^low_vol_/, 'lv-').replace(/strong_/, 'str-'),
        })
      }
    }
  }

  // Renko chart window: fetch is brick_index desc limit 10 → reverse for the
  // ascending order the chart expects.
  const brickWindow = ((bricks.data || []).filter((b) => b.symbol === activeBrickSym)).reverse()
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

  const graceDate = claim.grace_end || claim.period_end || ''

  return React.createElement('div', { className: 'tla-root' },
    claim.sub_status === 'grace'
      ? React.createElement('div', { className: 'tla-banner' },
          `Subscription in grace period — renews ${String(graceDate).slice(0, 10) || 'soon'} · still entitled to signals.`)
      : null,
    React.createElement(HotSignalsBanner, { signals: bannerSignals }),
    React.createElement('div', { className: 'tla-grid' },
      React.createElement(StatCard, {
        title: 'Plan',
        value: claim.plan_slug === 'precision_pro' ? 'Precision Pro' : 'Signal Scout',
        sub: `status ${claim.sub_status} · claim re-check 24h`,
      }),
      React.createElement(StatCard, {
        title: 'Symbols',
        value: String(symbolList.length || '—'),
        sub: symbols.error ? `symbol list unavailable (${symbols.error.message})` : 'from nt_symbol plan_ids',
      }),
      React.createElement(StatCard, {
        title: 'Realtime',
        value: wsState === 'open' ? 'Live' : wsState === 'connecting' ? 'Connecting' : wsState === 'idle' ? '—' : 'Poll fallback',
        sub: 'signals' + (isPro ? ' + paper' : '') + ' channels · REST poll 60s',
        tone: wsState === 'open' ? 'pos' : undefined,
      }),
      React.createElement(StatCard, {
        title: 'Hot signals',
        value: String(bannerSignals.length || 0),
        sub: 'qualified · 10m TTL · top 5',
      }),
    ),
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Kelly by symbol (latest sweep)'),
      React.createElement(HBar, { data: histData, format: (v) => v.toFixed(3) }),
      React.createElement('div', { className: 'tla-hint' },
        `${histData.length} symbols · bar = kelly (blue buy / red sell) · as of ${histData.length ? String(sweeps.data[0].sweep_timestamp).slice(0, 19).replace('T', ' ') : '—'} UTC`),
    ),
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Renko bricks — last 10 (per symbol)'),
      React.createElement('div', { className: 'tla-brick-picker' },
        symbolList.map((s) =>
          React.createElement('button', {
            key: s,
            className: cn('tla-brick-btn', s === activeBrickSym ? 'tla-brick-btn-active' : ''),
            onClick: () => setBrickSym(s),
          }, s),
        ),
      ),
      React.createElement(RenkoBrickChart, { bricks: brickWindow, levels }),
      React.createElement('div', { className: 'tla-hint' },
        'ENTRY / SL / TP reference lines from the latest sweep · window = last 10 bricks'),
    ),
    isPro ? React.createElement(PaperSection, {
      positions: paperPositions,
      equity: paperEquity,
      events: paperEvents,
    }) : null,
    React.createElement('div', { className: 'tla-hint' },
      'Direct Supabase · auto-refresh 60s · realtime ' + wsState + ' · claim re-check 24h · ' + (config.supabase_url || '')),
  )
}

// ---------------------------------------------------------------------------
// Main component — claim check + status routing
//   invalid/expired/revoked token  → Connect (re-enter token)
//   sub_status 'none'              → Subscribe CTA (next_charge_url / pricing)
//   'pending'                      → waiting screen + retry
//   'active'                       → dashboard
//   'grace'                        → dashboard + renews-date banner
//   'expired'/'cancelled'          → paywall + payment link
// ---------------------------------------------------------------------------
function Talaria() {
  const [config, updateConfig] = useConfig()
  const [claim, setClaim] = React.useState(null)
  const [checkPhase, setCheckPhase] = React.useState('idle') // idle|running|ok|bad-token|not-deployed|error
  const [checkMsg, setCheckMsg] = React.useState('')

  const runCheck = React.useCallback(async () => {
    if (!config.supabase_url || !config.supabase_key || !config.claim_token) {
      setClaim(null)
      setCheckPhase('idle')
      setCheckMsg('')
      return
    }
    setCheckPhase('running')
    setCheckMsg('')
    try {
      const res = await claimCheck(config)
      setClaim(res)
      setCheckPhase('ok')
    } catch (err) {
      setClaim(null)
      setCheckPhase(
        err && err.kind === 'not-deployed' ? 'not-deployed'
          : err && err.kind === 'bad-token' ? 'bad-token'
            : 'error')
      setCheckMsg((err && err.message) || String(err))
    }
  }, [config.supabase_url, config.supabase_key, config.claim_token])

  // Claim check on mount + every 24h. Saving config in the Connect tab
  // changes the callback identity → effect re-runs → immediate re-check.
  React.useEffect(() => {
    runCheck()
    const timer = setInterval(runCheck, CLAIM_CHECK_MS)
    return () => clearInterval(timer)
  }, [runCheck])

  const hasConfig = !!(config.supabase_url && config.supabase_key && config.claim_token)

  if (!hasConfig) {
    return React.createElement(ConnectTab, {
      config, onSave: updateConfig, checkPhase: 'idle', checkMsg: '',
    })
  }
  if (checkPhase === 'running' || checkPhase === 'idle') {
    return React.createElement('div', { className: 'tla-root' },
      React.createElement('div', { className: 'tla-card' },
        React.createElement('h3', null, 'Checking claim…'),
        React.createElement('div', { className: 'tla-hint' },
          'Validating claim token against talaria-check (live subscription status)…'),
      ),
    )
  }
  if (checkPhase !== 'ok') {
    // bad-token / not-deployed / error — back to Connect with inline status
    return React.createElement(ConnectTab, {
      config, onSave: updateConfig, checkPhase, checkMsg,
    })
  }

  const status = String((claim && claim.sub_status) || 'none').toLowerCase()
  if (status === 'none') {
    return React.createElement(SubscribeScreen, { claim, onRetry: runCheck })
  }
  if (status === 'pending') {
    return React.createElement(WaitingScreen, { claim, onRetry: runCheck })
  }
  if (status === 'expired' || status === 'cancelled') {
    return React.createElement(PaywallScreen, { claim, onRetry: runCheck })
  }
  // active | grace
  return React.createElement(TalariaDashboard, { config, claim })
}

// ---------------------------------------------------------------------------
const plugin = {
  id: 'talaria',
  name: 'Talaria',
  defaultEnabled: true,
  register(ctx) {
    ensureStyle()
    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/talaria' },
        render: () => React.createElement(Talaria, null),
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        order: 50,
        data: { codicon: 'graph-line', label: 'Talaria', path: '/talaria' },
      },
    ])
  },
}

export default plugin
