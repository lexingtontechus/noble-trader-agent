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
const DAISY_CSS = `.dui-badge{display:inline-flex;align-items:center;justify-content:center;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,-webkit-backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter,-webkit-backdrop-filter;transition-timing-function:cubic-bezier(.4,0,.2,1);transition-timing-function:cubic-bezier(0,0,.2,1);transition-duration:.2s;height:1.25rem;font-size:.875rem;line-height:1.25rem;width:-moz-fit-content;width:fit-content;padding-left:.563rem;padding-right:.563rem;border-radius:var(--rounded-badge,1.9rem);border-width:1px;--tw-border-opacity:1;border-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)))}@media (hover:hover){.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn).dui-active,.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn):active,.dui-menu li>details>summary:active{--tw-bg-opacity:1;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-nc,oklch(var(--nc)/var(--tw-text-opacity)))}.dui-table tr.dui-hover:hover,.dui-table tr.dui-hover:nth-child(2n):hover{--tw-bg-opacity:1;background-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-bg-opacity)))}.dui-table-zebra tr.dui-hover:hover,.dui-table-zebra tr.dui-hover:nth-child(2n):hover{--tw-bg-opacity:1;background-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-bg-opacity)))}}.dui-btn{display:inline-flex;height:3rem;min-height:3rem;flex-shrink:0;cursor:pointer;-webkit-user-select:none;-moz-user-select:none;user-select:none;flex-wrap:wrap;align-items:center;justify-content:center;border-radius:var(--rounded-btn,.5rem);border-color:transparent;border-color:oklch(var(--btn-color,var(--b2))/var(--tw-border-opacity));padding-left:1rem;padding-right:1rem;text-align:center;font-size:.875rem;line-height:1em;gap:.5rem;font-weight:600;text-decoration-line:none;transition-duration:.2s;transition-timing-function:cubic-bezier(0,0,.2,1);border-width:var(--border-btn,1px);transition-property:color,background-color,border-color,opacity,box-shadow,transform;--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-shadow:0 1px 2px 0 rgba(0,0,0,.05);--tw-shadow-colored:0 1px 2px 0 var(--tw-shadow-color);box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow);outline-color:var(--fallback-bc,oklch(var(--bc)/1));background-color:oklch(var(--btn-color,var(--b2))/var(--tw-bg-opacity));--tw-bg-opacity:1;--tw-border-opacity:1}.dui-btn-disabled,.dui-btn:disabled,.dui-btn[disabled]{pointer-events:none}:where(.dui-btn:is(input[type=checkbox])),:where(.dui-btn:is(input[type=radio])){width:auto;-webkit-appearance:none;-moz-appearance:none;appearance:none}.dui-btn:is(input[type=checkbox]):after,.dui-btn:is(input[type=radio]):after{--tw-content:attr(aria-label);content:var(--tw-content)}@media (hover:hover){.dui-btn:hover{--tw-border-opacity:1;border-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-bg-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn:hover{background-color:color-mix(in oklab,oklch(var(--btn-color,var(--b2))/var(--tw-bg-opacity,1)) 90%,#000);border-color:color-mix(in oklab,oklch(var(--btn-color,var(--b2))/var(--tw-border-opacity,1)) 90%,#000)}}@supports not (color:oklch(0% 0 0)){.dui-btn:hover{background-color:var(--btn-color,var(--fallback-b2));border-color:var(--btn-color,var(--fallback-b2))}}.dui-btn.dui-glass:hover{--glass-opacity:25%;--glass-border-opacity:15%}.dui-btn-ghost:hover{border-color:transparent}@supports (color:oklch(0% 0 0)){.dui-btn-ghost:hover{background-color:var(--fallback-bc,oklch(var(--bc)/.2))}}.dui-btn-outline:hover{--tw-border-opacity:1;border-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-b1,oklch(var(--b1)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-primary:hover{--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-primary:hover{background-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-secondary:hover{--tw-text-opacity:1;color:var(--fallback-sc,oklch(var(--sc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-secondary:hover{background-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-accent:hover{--tw-text-opacity:1;color:var(--fallback-ac,oklch(var(--ac)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-accent:hover{background-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-success:hover{--tw-text-opacity:1;color:var(--fallback-suc,oklch(var(--suc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-success:hover{background-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-info:hover{--tw-text-opacity:1;color:var(--fallback-inc,oklch(var(--inc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-info:hover{background-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-warning:hover{--tw-text-opacity:1;color:var(--fallback-wac,oklch(var(--wac)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-warning:hover{background-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-error:hover{--tw-text-opacity:1;color:var(--fallback-erc,oklch(var(--erc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-error:hover{background-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000)}}.dui-btn-disabled:hover,.dui-btn:disabled:hover,.dui-btn[disabled]:hover{--tw-border-opacity:0;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-bg-opacity:0.2;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-text-opacity:0.2}@supports (color:color-mix(in oklab,black,black)){.dui-btn:is(input[type=checkbox]:checked):hover,.dui-btn:is(input[type=radio]:checked):hover{background-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000)}}:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(.dui-active,.dui-btn):hover,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(.dui-active,.dui-btn):hover{cursor:pointer;outline:2px solid transparent;outline-offset:2px}@supports (color:oklch(0% 0 0)){:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(.dui-active,.dui-btn):hover,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(.dui-active,.dui-btn):hover{background-color:var(--fallback-bc,oklch(var(--bc)/.1))}}}.dui-join{display:inline-flex;align-items:stretch;border-radius:var(--rounded-btn,.5rem)}.dui-join :where(.dui-join-item){border-start-end-radius:0;border-end-end-radius:0;border-end-start-radius:0;border-start-start-radius:0}.dui-join .dui-join-item:not(:first-child):not(:last-child),.dui-join :not(:first-child):not(:last-child) .dui-join-item{border-start-end-radius:0;border-end-end-radius:0;border-end-start-radius:0;border-start-start-radius:0}.dui-join .dui-join-item:first-child:not(:last-child),.dui-join :first-child:not(:last-child) .dui-join-item{border-start-end-radius:0;border-end-end-radius:0}.dui-join .dui-dropdown .dui-join-item:first-child:not(:last-child),.dui-join :first-child:not(:last-child) .dui-dropdown .dui-join-item{border-start-end-radius:inherit;border-end-end-radius:inherit}.dui-join :where(.dui-join-item:first-child:not(:last-child)),.dui-join :where(:first-child:not(:last-child) .dui-join-item){border-end-start-radius:inherit;border-start-start-radius:inherit}.dui-join .dui-join-item:last-child:not(:first-child),.dui-join :last-child:not(:first-child) .dui-join-item{border-end-start-radius:0;border-start-start-radius:0}.dui-join :where(.dui-join-item:last-child:not(:first-child)),.dui-join :where(:last-child:not(:first-child) .dui-join-item){border-start-end-radius:inherit;border-end-end-radius:inherit}@supports not selector(:has(*)){:where(.dui-join *){border-radius:inherit}}@supports selector(:has(*)){:where(.dui-join :has(.dui-join-item)){border-radius:inherit}}.dui-menu{display:flex;flex-direction:column;flex-wrap:wrap;font-size:.875rem;line-height:1.25rem;padding:.5rem}.dui-menu :where(li ul){position:relative;white-space:nowrap;margin-inline-start:1rem;padding-inline-start:.5rem}.dui-menu :where(li:not(.dui-menu-title)>:not(ul,details,.dui-menu-title,.dui-btn)),.dui-menu :where(li:not(.dui-menu-title)>details>summary:not(.dui-menu-title)){display:grid;grid-auto-flow:column;align-content:flex-start;align-items:center;gap:.5rem;grid-auto-columns:minmax(auto,max-content) auto max-content;-webkit-user-select:none;-moz-user-select:none;user-select:none}.dui-menu li.dui-disabled{cursor:not-allowed;-webkit-user-select:none;-moz-user-select:none;user-select:none;color:var(--fallback-bc,oklch(var(--bc)/.3))}.dui-menu :where(li>.dui-menu-dropdown:not(.dui-menu-dropdown-show)){display:none}:where(.dui-menu li){position:relative;display:flex;flex-shrink:0;flex-direction:column;flex-wrap:wrap;align-items:stretch}:where(.dui-menu li) .dui-badge{justify-self:end}.dui-table{position:relative;width:100%;border-radius:var(--rounded-box,1rem);text-align:left;font-size:.875rem;line-height:1.25rem}.dui-table :where(.dui-table-pin-rows thead tr){position:sticky;top:0;z-index:1;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}.dui-table :where(.dui-table-pin-rows tfoot tr){position:sticky;bottom:0;z-index:1;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}.dui-table :where(.dui-table-pin-cols tr th){position:sticky;left:0;right:0;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}.dui-table-zebra tbody tr:nth-child(2n) :where(.dui-table-pin-cols tr th){--tw-bg-opacity:1;background-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-bg-opacity)))}.dui-btm-nav>:where(.dui-active){border-top-width:2px;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}@media (prefers-reduced-motion:no-preference){.dui-btn{animation:button-pop var(--animation-btn,.25s) ease-out}}.dui-btn:active:focus,.dui-btn:active:hover{animation:button-pop 0s ease-out;transform:scale(var(--btn-focus-scale,.97))}@supports not (color:oklch(0% 0 0)){.dui-btn{background-color:var(--btn-color,var(--fallback-b2));border-color:var(--btn-color,var(--fallback-b2))}.dui-btn-primary{--btn-color:var(--fallback-p)}}@supports (color:color-mix(in oklab,black,black)){.dui-btn-active{background-color:color-mix(in oklab,oklch(var(--btn-color,var(--b3))/var(--tw-bg-opacity,1)) 90%,#000);border-color:color-mix(in oklab,oklch(var(--btn-color,var(--b3))/var(--tw-border-opacity,1)) 90%,#000)}.dui-btn-outline.dui-btn-primary.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000)}.dui-btn-outline.dui-btn-secondary.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000)}.dui-btn-outline.dui-btn-accent.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000)}.dui-btn-outline.dui-btn-success.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000)}.dui-btn-outline.dui-btn-info.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000)}.dui-btn-outline.dui-btn-warning.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000)}.dui-btn-outline.dui-btn-error.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000)}}.dui-btn:focus-visible{outline-style:solid;outline-width:2px;outline-offset:2px}.dui-btn-primary{--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)));outline-color:var(--fallback-p,oklch(var(--p)/1))}@supports (color:oklch(0% 0 0)){.dui-btn-primary{--btn-color:var(--p)}}.dui-btn.dui-glass{--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow);outline-color:currentColor}.dui-btn.dui-glass.dui-btn-active{--glass-opacity:25%;--glass-border-opacity:15%}.dui-btn-ghost{border-width:1px;border-color:transparent;background-color:transparent;color:currentColor;--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow);outline-color:currentColor}.dui-btn-ghost.dui-btn-active{border-color:transparent;background-color:var(--fallback-bc,oklch(var(--bc)/.2))}.dui-btn-link.dui-btn-active{border-color:transparent;background-color:transparent;text-decoration-line:underline}.dui-btn-outline{border-color:currentColor;background-color:transparent;--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow)}.dui-btn-outline.dui-btn-active{--tw-border-opacity:1;border-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-b1,oklch(var(--b1)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-primary{--tw-text-opacity:1;color:var(--fallback-p,oklch(var(--p)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-primary.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-secondary{--tw-text-opacity:1;color:var(--fallback-s,oklch(var(--s)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-secondary.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-sc,oklch(var(--sc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-accent{--tw-text-opacity:1;color:var(--fallback-a,oklch(var(--a)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-accent.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-ac,oklch(var(--ac)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-success{--tw-text-opacity:1;color:var(--fallback-su,oklch(var(--su)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-success.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-suc,oklch(var(--suc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-info{--tw-text-opacity:1;color:var(--fallback-in,oklch(var(--in)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-info.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-inc,oklch(var(--inc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-warning{--tw-text-opacity:1;color:var(--fallback-wa,oklch(var(--wa)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-warning.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-wac,oklch(var(--wac)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-error{--tw-text-opacity:1;color:var(--fallback-er,oklch(var(--er)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-error.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-erc,oklch(var(--erc)/var(--tw-text-opacity)))}.dui-btn.dui-btn-disabled,.dui-btn:disabled,.dui-btn[disabled]{--tw-border-opacity:0;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-bg-opacity:0.2;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-text-opacity:0.2}.dui-btn:is(input[type=checkbox]:checked),.dui-btn:is(input[type=radio]:checked){--tw-border-opacity:1;border-color:var(--fallback-p,oklch(var(--p)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-p,oklch(var(--p)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)))}.dui-btn:is(input[type=checkbox]:checked):focus-visible,.dui-btn:is(input[type=radio]:checked):focus-visible{outline-color:var(--fallback-p,oklch(var(--p)/1))}@keyframes button-pop{0%{transform:scale(var(--btn-focus-scale,.98))}40%{transform:scale(1.02)}to{transform:scale(1)}}@keyframes checkmark{0%{background-position-y:5px}50%{background-position-y:-2px}to{background-position-y:0}}.dui-join>:where(:not(:first-child)){margin-top:0;margin-bottom:0;margin-inline-start:-1px}.dui-join>:where(:not(:first-child)):is(.dui-btn){margin-inline-start:calc(var(--border-btn)*-1)}.dui-join-item:focus{isolation:isolate}:where(.dui-menu li:empty){--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));opacity:.1;margin:.5rem 1rem;height:1px}.dui-menu :where(li ul):before{position:absolute;bottom:.75rem;inset-inline-start:0;top:.75rem;width:1px;--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));opacity:.1;content:""}.dui-menu :where(li:not(.dui-menu-title)>:not(ul,details,.dui-menu-title,.dui-btn)),.dui-menu :where(li:not(.dui-menu-title)>details>summary:not(.dui-menu-title)){border-radius:var(--rounded-btn,.5rem);padding:.5rem 1rem;text-align:start;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,-webkit-backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter,-webkit-backdrop-filter;transition-timing-function:cubic-bezier(.4,0,.2,1);transition-timing-function:cubic-bezier(0,0,.2,1);transition-duration:.2s;text-wrap:balance}:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):is(summary):not(.dui-active,.dui-btn):focus-visible,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(summary,.dui-active,.dui-btn).dui-focus,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(summary,.dui-active,.dui-btn):focus,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):is(summary):not(.dui-active,.dui-btn):focus-visible,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(summary,.dui-active,.dui-btn).dui-focus,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(summary,.dui-active,.dui-btn):focus{cursor:pointer;background-color:var(--fallback-bc,oklch(var(--bc)/.1));--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));outline:2px solid transparent;outline-offset:2px}.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn).dui-active,.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn):active,.dui-menu li>details>summary:active{--tw-bg-opacity:1;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-nc,oklch(var(--nc)/var(--tw-text-opacity)))}.dui-menu :where(li>details>summary)::-webkit-details-marker{display:none}.dui-menu :where(li>.dui-menu-dropdown-toggle):after,.dui-menu :where(li>details>summary):after{justify-self:end;display:block;margin-top:-.5rem;height:.5rem;width:.5rem;transform:rotate(45deg);transition-property:transform,margin-top;transition-duration:.3s;transition-timing-function:cubic-bezier(.4,0,.2,1);content:"";transform-origin:75% 75%;box-shadow:2px 2px;pointer-events:none}.dui-menu :where(li>.dui-menu-dropdown-toggle.dui-menu-dropdown-show):after,.dui-menu :where(li>details[open]>summary):after{transform:rotate(225deg);margin-top:0}@keyframes modal-pop{0%{opacity:0}}@keyframes progress-loading{50%{background-position-x:-115%}}@keyframes radiomark{0%{box-shadow:0 0 0 12px var(--fallback-b1,oklch(var(--b1)/1)) inset,0 0 0 12px var(--fallback-b1,oklch(var(--b1)/1)) inset}50%{box-shadow:0 0 0 3px var(--fallback-b1,oklch(var(--b1)/1)) inset,0 0 0 3px var(--fallback-b1,oklch(var(--b1)/1)) inset}to{box-shadow:0 0 0 4px var(--fallback-b1,oklch(var(--b1)/1)) inset,0 0 0 4px var(--fallback-b1,oklch(var(--b1)/1)) inset}}@keyframes rating-pop{0%{transform:translateY(-.125em)}40%{transform:translateY(-.125em)}to{transform:translateY(0)}}@keyframes skeleton{0%{background-position:150%}to{background-position:-50%}}.dui-table:where([dir=rtl],[dir=rtl] *){text-align:right}.dui-table :where(th,td){padding:.75rem 1rem;vertical-align:middle}.dui-table tr.dui-active,.dui-table tr.dui-active:nth-child(2n),.dui-table-zebra tbody tr:nth-child(2n){--tw-bg-opacity:1;background-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-bg-opacity)))}.dui-table-zebra tr.dui-active,.dui-table-zebra tr.dui-active:nth-child(2n),.dui-table-zebra-zebra tbody tr:nth-child(2n){--tw-bg-opacity:1;background-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-bg-opacity)))}.dui-table :where(thead tr,tbody tr:not(:last-child),tbody tr:first-child:last-child){border-bottom-width:1px;--tw-border-opacity:1;border-bottom-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-border-opacity)))}.dui-table :where(thead,tfoot){white-space:nowrap;font-size:.75rem;line-height:1rem;font-weight:700;color:var(--fallback-bc,oklch(var(--bc)/.6))}.dui-table :where(tfoot){border-top-width:1px;--tw-border-opacity:1;border-top-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-border-opacity)))}@keyframes toast-pop{0%{transform:scale(.9);opacity:0}to{transform:scale(1);opacity:1}}.dui-glass,.dui-glass.dui-btn-active{border:none;-webkit-backdrop-filter:blur(var(--glass-blur,40px));backdrop-filter:blur(var(--glass-blur,40px));background-color:transparent;background-image:linear-gradient(135deg,rgb(255 255 255/var(--glass-opacity,30%)) 0,transparent 100%),linear-gradient(var(--glass-reflex-degree,100deg),rgb(255 255 255/var(--glass-reflex-opacity,10%)) 25%,transparent 25%);box-shadow:0 0 0 1px rgb(255 255 255/var(--glass-border-opacity,10%)) inset,0 0 0 2px rgb(0 0 0/5%);text-shadow:0 1px rgb(0 0 0/var(--glass-text-shadow-opacity,5%))}@media (hover:hover){.dui-glass.dui-btn-active{border:none;-webkit-backdrop-filter:blur(var(--glass-blur,40px));backdrop-filter:blur(var(--glass-blur,40px));background-color:transparent;background-image:linear-gradient(135deg,rgb(255 255 255/var(--glass-opacity,30%)) 0,transparent 100%),linear-gradient(var(--glass-reflex-degree,100deg),rgb(255 255 255/var(--glass-reflex-opacity,10%)) 25%,transparent 25%);box-shadow:0 0 0 1px rgb(255 255 255/var(--glass-border-opacity,10%)) inset,0 0 0 2px rgb(0 0 0/5%);text-shadow:0 1px rgb(0 0 0/var(--glass-text-shadow-opacity,5%))}}.dui-badge-sm{height:1rem;font-size:.75rem;line-height:1rem;padding-left:.438rem;padding-right:.438rem}.dui-btm-nav-xs>:where(.dui-active){border-top-width:1px}.dui-btm-nav-sm>:where(.dui-active){border-top-width:2px}.dui-btm-nav-md>:where(.dui-active){border-top-width:2px}.dui-btm-nav-lg>:where(.dui-active){border-top-width:4px}.dui-btn-sm{height:2rem;min-height:2rem;padding-left:.75rem;padding-right:.75rem;font-size:.875rem}.dui-btn-square:where(.dui-btn-sm){height:2rem;width:2rem;padding:0}.dui-btn-circle:where(.dui-btn-sm){height:2rem;width:2rem;border-radius:9999px;padding:0}.dui-join.dui-join-vertical{flex-direction:column}.dui-join.dui-join-vertical .dui-join-item:first-child:not(:last-child),.dui-join.dui-join-vertical :first-child:not(:last-child) .dui-join-item{border-end-start-radius:0;border-end-end-radius:0;border-start-start-radius:inherit;border-start-end-radius:inherit}.dui-join.dui-join-vertical .dui-join-item:last-child:not(:first-child),.dui-join.dui-join-vertical :last-child:not(:first-child) .dui-join-item{border-start-start-radius:0;border-start-end-radius:0;border-end-start-radius:inherit;border-end-end-radius:inherit}.dui-join.dui-join-horizontal{flex-direction:row}.dui-join.dui-join-horizontal .dui-join-item:first-child:not(:last-child),.dui-join.dui-join-horizontal :first-child:not(:last-child) .dui-join-item{border-end-end-radius:0;border-start-end-radius:0;border-end-start-radius:inherit;border-start-start-radius:inherit}.dui-join.dui-join-horizontal .dui-join-item:last-child:not(:first-child),.dui-join.dui-join-horizontal :last-child:not(:first-child) .dui-join-item{border-end-start-radius:0;border-start-start-radius:0;border-end-end-radius:inherit;border-start-end-radius:inherit}.dui-join.dui-join-vertical>:where(:not(:first-child)){margin-left:0;margin-right:0;margin-top:-1px}.dui-join.dui-join-vertical>:where(:not(:first-child)):is(.dui-btn){margin-top:calc(var(--border-btn)*-1)}.dui-join.dui-join-horizontal>:where(:not(:first-child)){margin-top:0;margin-bottom:0;margin-inline-start:-1px}.dui-join.dui-join-horizontal>:where(:not(:first-child)):is(.dui-btn){margin-inline-start:calc(var(--border-btn)*-1);margin-top:0}.dui-table-sm :not(thead):not(tfoot) tr{font-size:.875rem;line-height:1.25rem}.dui-table-sm :where(th,td){padding:.5rem .75rem}.dui-table{display:table}`
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
  '.tla-explainer{color:var(--ui-text-secondary,#bbb);font-size:11px;line-height:1.55;background:rgba(127,127,127,0.07);border-left:3px solid var(--ui-accent,#4c9aff);padding:7px 10px;margin:8px 0 10px;border-radius:0 6px 6px 0;}',
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
  // Phase 2 analytics — signal health / calibration / markov / sizing cards
  '.tla-mini-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;}',
  '.tla-inline{display:flex;flex-wrap:wrap;gap:12px;align-items:center;font-size:12px;}',
  '.tla-badge.overconfident{background:rgba(255,92,92,0.15);color:var(--ui-danger,#ff5c5c);}',
  '.tla-badge.underconfident{background:rgba(120,220,120,0.15);color:#78dc78;}',
  '.tla-badge.calibrated{background:rgba(153,153,153,0.15);color:var(--ui-text-tertiary,#888);}',
  '.tla-badge.sig{background:rgba(120,220,120,0.15);color:#78dc78;}',
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
  if (globalThis.__DAISY_INJECTED__ !== "talaria-style") {
    const ds = document.getElementById('daisy-talaria-style')
    if (!ds) { const d = document.createElement('style'); d.id = 'daisy-talaria-style'; d.textContent = DAISY_CSS; document.head.appendChild(d) }
    globalThis.__DAISY_INJECTED__ = "talaria-style"
  }
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
  const n = Number(p)
  if (n == null || isNaN(n)) return '—'
  // Full-value format (6dp → rstrip trailing zeros → keep ≥2dp): BTC 64900 →
  // $64900.00, XAU 4072.5 → $4072.50, FX 1.137 → $1.137, XAG 57.0568 →
  // $57.0568 (no magnitude-based truncation).
  let s = n.toFixed(6).replace(/\.?0+$/, '')
  if (!s.includes('.')) s += '.00'
  else if (s.split('.')[1].length < 2) s = n.toFixed(2)
  return '$' + s
}

// bricks: [{ open_price, close_price, direction }] ordered by brick_index asc.
// levels: [{ label, price, color }] — horizontal reference lines (entry/sl/tp).
function RenkoBrickChart({ bricks, height = 300, levels }) {
  if (!bricks || !bricks.length) {
    return React.createElement('div', { className: 'tla-hint' }, 'No bricks yet')
  }
  // Y-scale from BRICKS ONLY — levels no longer stretch the price range, so a
  // far-away ENTRY/SL/TP can't crush the bricks into a flat band (the BTCUSD
  // 6420-6468 mess). Level lines are drawn only when inside the visible range,
  // and all pricing lives in the legend row BELOW the chart (2026-08-08).
  let minP = Infinity
  let maxP = -Infinity
  for (const b of bricks) {
    const lo = Math.min(b.open_price, b.close_price)
    const hi = Math.max(b.open_price, b.close_price)
    if (lo < minP) minP = lo
    if (hi > maxP) maxP = hi
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

  // Level lines — dashed horizontal, drawn ONLY when inside the visible brick
  // range. No on-chart labels (pricing is in the legend row below).
  const levelEls = (levels || [])
    .filter((lv) => lv.price != null && Number(lv.price) > 0)
    .filter((lv) => Number(lv.price) >= pMin && Number(lv.price) <= pMax)
    .map((lv) => {
      const y = priceToY(Number(lv.price))
      const color = lv.color || 'var(--ui-text-tertiary,#888)'
      return React.createElement('g', { key: 'lv' + lv.label },
        React.createElement('line', {
          x1: BRICK_LEFT_PAD, x2: BRICK_LEFT_PAD + bricks.length * BRICK_STEP,
          y1: y, y2: y,
          stroke: color, strokeWidth: 1, strokeDasharray: '5 3', strokeOpacity: 0.8,
        }),
      )
    })

  // Pricing legend (for ALL symbols) — colored ENTRY/SL/TP with full-value
  // prices, rendered below the chart so nothing overlaps the bricks.
  const levelLegend = (levels || [])
    .filter((lv) => lv.price != null && Number(lv.price) > 0)
    .map((lv) => {
      const color = lv.color || 'var(--ui-text-tertiary,#888)'
      return React.createElement('span', {
        key: 'lg' + lv.label,
        style: { color, fontWeight: 600, marginRight: 14, fontSize: 11, fontFamily: 'monospace', whiteSpace: 'nowrap' },
      }, `${lv.label} ${fmtBrickPrice(Number(lv.price))}`)
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
    levelLegend.length
      ? React.createElement('div', { style: { marginTop: 6, display: 'flex', flexWrap: 'wrap', alignItems: 'center' } },
          React.createElement('span', { style: { marginRight: 4, fontSize: 11, color: 'var(--ui-text-quaternary,#666)' } }, 'levels:'),
          levelLegend,
        )
      : null,
    React.createElement('div', { className: 'tla-hint', style: { marginTop: 4 } },
      `${bricks.length} bricks (last ${bricks.length} of series) · up = buy (blue) · down = sell (red) · last brick index ${bricks[bricks.length - 1].brick_index != null ? bricks[bricks.length - 1].brick_index : bricks.length - 1}`),
  )
}

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
// Pager — daisyUI join pagination with active button (2026-08-08)
// ---------------------------------------------------------------------------
const PAGE_SIZE = 8

function Pager({ page, pages, onChange }) {
  if (!pages || pages <= 1) return null
  const btns = []
  for (let i = 1; i <= pages; i++) {
    btns.push(React.createElement('button', {
      key: 'pg' + i,
      className: cn('dui-join-item', 'dui-btn', 'dui-btn-sm', i === page ? 'dui-btn-active' : ''),
      onClick: () => onChange(i),
    }, String(i)))
  }
  return React.createElement('div', { className: cn('dui-join', 'dui-join-horizontal'), style: { marginTop: 8, flexWrap: 'wrap' } },
    React.createElement('button', {
      className: cn('dui-join-item', 'dui-btn', 'dui-btn-sm'),
      onClick: () => onChange(Math.max(1, page - 1)),
    }, '«'),
    ...btns,
    React.createElement('button', {
      className: cn('dui-join-item', 'dui-btn', 'dui-btn-sm'),
      onClick: () => onChange(Math.min(pages, page + 1)),
    }, '»'),
  )
}

// ---------------------------------------------------------------------------
// Paper section — Precision Pro only. Live 'paper' broadcast events
// (opened/closed/equity) appended to a list (cap 50) + seed from
// nt_paper_positions + latest v_paper_equity row. Renders an empty state
// gracefully when the tables don't exist yet (PGRST205 / 404).
// ---------------------------------------------------------------------------
function PaperSection({ positions, equity, events }) {
  const [paperPage, setPaperPage] = React.useState(1)
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
  const paperPages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE))
  const paperPageRows = rows.slice((paperPage - 1) * PAGE_SIZE, paperPage * PAGE_SIZE)
  const missingTable = !!(positions.error || equity.error)

  return React.createElement('div', { className: 'tla-card' },
    React.createElement('h3', null, 'Paper portfolio — Precision Pro'),
    React.createElement('div', { className: 'tla-explainer' },
      'What this is: the SIMULATED (paper) trade book — the trades the engine WOULD have taken, at real Kelly-sized notional. PnL here is only BOOKED when the backend CLOSES a position: open positions show — / $0.00, and the paper equity curve adds each day\'s closed-trade PnL. This is the ACTUAL sized result — it is NOT the same as the Signal health scoreboard, which shows the THEORETICAL unit-size PnL of every signal. So $0.00 early on just means no trades have closed yet, not that the strategy is flat.'),
    React.createElement('div', { className: 'tla-grid' },
      React.createElement(StatCard, {
        title: 'Paper equity',
        value: eqRow ? `$${Number(eqRow.cumulative_pnl || 0).toFixed(2)}` : '—',
        sub: eqRow ? `as of ${String(eqRow.day || '').slice(0, 10)} · realized today $${Number(eqRow.realized_pnl || 0).toFixed(2)}` : 'no equity ticks yet',
        tone: eqRow && Number(eqRow.cumulative_pnl) >= 0 ? 'pos' : 'neg',
      }),
    ),
    React.createElement('div', { className: 'tla-hint' },
      'Paper equity = cumulative realized PnL of the SIMULATED portfolio (no real money). Each day adds the closed-trade PnL for that day.'),
    missingTable && rows.length === 0
      ? React.createElement('div', { className: 'tla-hint' },
          'Paper portfolio data not available yet — the nt_paper_positions table or v_paper_equity view is not deployed (PGRST205). Live paper events will still appear here once the backend publishes them.')
      : null,
    React.createElement('table', { className: cn('tla-table', 'dui-table', 'dui-table-sm') },
      React.createElement('thead', null,
        React.createElement('tr', null,
          React.createElement('th', null, 'Type'),
          React.createElement('th', null, 'Symbol'),
          React.createElement('th', null, 'Dir'),
          React.createElement('th', null, 'Realized PnL'),
          React.createElement('th', null, 'R-multiple'),
          React.createElement('th', null, 'Ts'))),
      React.createElement('tbody', null,
        paperPageRows.length
          ? paperPageRows.map((r, i) => (
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
    React.createElement(Pager, { page: paperPage, pages: paperPages, onChange: setPaperPage }),
    React.createElement('div', { className: 'tla-hint' },
      `${rows.length} rows · live broadcast + REST seed · 60s poll · PnL — for OPEN positions (realized only on close), so $0/blank is expected until the backend closes a trade · R-multiple = PnL ÷ risk per trade (R=1 means you made exactly one unit of risk)`),
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
        React.createElement('button', { className: cn('tla-btn', 'dui-btn', 'dui-btn-primary', 'dui-btn-sm'), onClick: save }, 'Save & Validate'),
        React.createElement('button', {
          className: cn('tla-btn', 'tla-btn-secondary', 'dui-btn', 'dui-btn-ghost', 'dui-btn-sm'), onClick: testConnection, disabled: testing,
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
        ? React.createElement('a', { className: cn('tla-btn', 'dui-btn', 'dui-btn-primary', 'dui-btn-sm'), href: url, target: '_blank', rel: 'noreferrer' },
            'Subscribe / pay')
        : React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
            'No payment link available — subscribe from the Talaria portal.'),
      React.createElement('button', { className: cn('tla-btn', 'tla-btn-secondary', 'dui-btn', 'dui-btn-ghost', 'dui-btn-sm'), onClick: onRetry },
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
      React.createElement('button', { className: cn('tla-btn', 'dui-btn', 'dui-btn-primary', 'dui-btn-sm'), onClick: onRetry },
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
        ? React.createElement('a', { className: cn('tla-btn', 'dui-btn', 'dui-btn-primary', 'dui-btn-sm'), href: url, target: '_blank', rel: 'noreferrer' },
            'Renew / pay')
        : React.createElement('div', { className: 'tla-hint', style: { textAlign: 'center' } },
            'No payment link available — renew from the Talaria portal.'),
      React.createElement('button', { className: cn('tla-btn', 'tla-btn-secondary', 'dui-btn', 'dui-btn-ghost', 'dui-btn-sm'), onClick: onRetry },
        'Re-check'),
    ),
  )
}

// ---------------------------------------------------------------------------
// Phase 2 — light client-side analytics math (pure JS, no imports).
// Ports of BrickPatternAnalyzer (agent signals/renko_engine.py),
// MetaRegimeClassifier display logic, MarkovChain (backend markov_chain.py),
// wilson_confidence (agent pattern_learning.py), multiple_testing.py
// (Benjamini-Hochberg FDR) and the SizingEngine what-if arithmetic.
// All functions are defined ABOVE TalariaDashboard (ESM order).
// ---------------------------------------------------------------------------

// Classify the last-10 brick window into a short pattern label from the
// up/down directions. Rule table (per the portability spec):
//   3 consecutive same-direction → '3-push'
//   2 up then 1 down             → 'pullback'
//   strictly alternating         → 'chop'
//   otherwise                    → 'neutral'
function brickPattern(bricks) {
  const dirs = (bricks || [])
    .map((b) => String(b.direction || '').toLowerCase())
    .filter((d) => d === 'up' || d === 'down')
  const win = dirs.slice(-10)
  if (win.length < 3) return 'neutral'
  // 3 consecutive same-direction (any position in the window)
  for (let i = 0; i + 2 < win.length; i++) {
    if (win[i] === win[i + 1] && win[i + 1] === win[i + 2]) return '3-push'
  }
  // 2 up then 1 down (pullback after an up-push)
  if (win.length >= 3 && win[win.length - 3] === 'up' && win[win.length - 2] === 'up' && win[win.length - 1] === 'down') {
    return 'pullback'
  }
  // Strictly alternating = chop
  let alt = true
  for (let i = 1; i < win.length; i++) {
    if (win[i] === win[i - 1]) { alt = false; break }
  }
  if (alt) return 'chop'
  return 'neutral'
}

// Map a backend regime label to the sizing rule table. Mirrors the
// MetaRegimeClassifier display logic (sizing_multiplier + aggressiveness).
// Returns { mult, aggressiveness, tone } with tone 'pos'|'neg'|'warn'|undefined.
const META_REGIME_TABLE = {
  calm_trend: { mult: 1.0, aggressiveness: 'normal' },
  choppy_range: { mult: 0.5, aggressiveness: 'patient' },
  high_vol_breakout: { mult: 1.5, aggressiveness: 'aggressive' },
  regime_transition: { mult: 0.3, aggressiveness: 'standby' },
  risk_off: { mult: -1.0, aggressiveness: 'standby' },
  funding_stress: { mult: -0.5, aggressiveness: 'standby' },
  liquidity_drained: { mult: -0.3, aggressiveness: 'standby' },
  strong_trend: { mult: 1.2, aggressiveness: 'normal' },
  low_vol_range: { mult: 0.8, aggressiveness: 'patient' },
  high_vol_chop: { mult: 0.6, aggressiveness: 'patient' },
}
function metaRegimeInfo(regimeLabel) {
  const r = META_REGIME_TABLE[String(regimeLabel || '').trim()] || { mult: 1.0, aggressiveness: 'normal' }
  let tone
  if (r.mult <= 0) tone = 'neg'
  else if (r.mult >= 1.5) tone = 'pos'
  else if (r.mult < 1.0) tone = 'warn'
  return { mult: r.mult, aggressiveness: r.aggressiveness, tone }
}

// Fit a 3-state (UP/DOWN/FLAT) Markov chain on the brick close prices and
// compute P(UP after 3 steps) from the last state's row of the transition
// matrix raised to the 3rd power (hand-rolled matrix multiply — no libs).
// FLAT is a real state (|delta| <= 1e-4), not dropped. Returns
// { pUp, pDown, n } or null when fewer than 10 closes.
function markovUpProbability(closes) {
  const cs = (closes || []).map(Number).filter((v) => isFinite(v))
  if (cs.length < 10) return null
  const EPS = 0.0001
  const stateOf = (a, b) => {
    const d = a - b
    return Math.abs(d) <= EPS ? 2 : d > 0 ? 0 : 1
  }
  // Transition counts: T[from][to], states 0=UP 1=DOWN 2=FLAT
  const T = [[0, 0, 0], [0, 0, 0], [0, 0, 0]]
  for (let i = 2; i < cs.length; i++) {
    T[stateOf(cs[i - 1], cs[i - 2])][stateOf(cs[i], cs[i - 1])] += 1
  }
  // Normalize rows; an unvisited state falls back to uniform transitions.
  const P = T.map((row) => {
    const s = row[0] + row[1] + row[2]
    return s > 0 ? [row[0] / s, row[1] / s, row[2] / s] : [1 / 3, 1 / 3, 1 / 3]
  })
  const last = stateOf(cs[cs.length - 1], cs[cs.length - 2])
  // v = e_last · P³  (row-vector × P, three times)
  let v = P[last]
  for (let step = 0; step < 3; step++) {
    const nv = [0, 0, 0]
    for (let j = 0; j < 3; j++) {
      for (let k = 0; k < 3; k++) nv[k] += v[j] * P[j][k]
    }
    v = nv
  }
  return { pUp: v[0], pDown: v[1], n: cs.length }
}

// Wilson score lower bound at 95% confidence: clamp(centre − half, 0..1).
function wilsonLower(n, wins) {
  n = Number(n)
  wins = Number(wins)
  if (!isFinite(n) || n <= 0) return 0
  const z = 1.96
  const phat = Math.min(1, Math.max(0, wins / n))
  const denom = 1 + (z * z) / n
  const centre = (phat + (z * z) / (2 * n)) / denom
  const half = (z * Math.sqrt((phat * (1 - phat) + (z * z) / (4 * n)) / n)) / denom
  return Math.min(1, Math.max(0, centre - half))
}

// Benjamini-Hochberg FDR at 0.05: sort p ascending, rank i=1..k, mark
// significant where p <= (i/k)*0.05 (all rows up to the last passing rank).
// Returns [{ symbol, p_value, significant }].
function benjaminiHochberg(rows) {
  const items = (rows || [])
    .filter((r) => r && r.symbol != null)
    .map((r) => ({ symbol: r.symbol, p_value: Number(r.p_value) }))
  const sorted = items.slice().sort((a, b) => a.p_value - b.p_value)
  const k = sorted.length
  let lastSig = -1
  for (let i = 0; i < k; i++) {
    if (sorted[i].p_value <= ((i + 1) / k) * 0.05) lastSig = i
  }
  return sorted.map((r, i) => ({ symbol: r.symbol, p_value: r.p_value, significant: i <= lastSig }))
}

// erf via the Abramowitz-Stegun 7.1.26 rational approximation (|error| <= 1.5e-7).
function _erfAS(x) {
  const t = 1 / (1 + 0.3275911 * x)
  const a1 = 0.254829592, a2 = -0.284496736, a3 = 1.421413741, a4 = -1.453152027, a5 = 1.061405429
  const poly = a1 * t + a2 * t * t + a3 * t * t * t + a4 * t * t * t * t + a5 * t * t * t * t * t
  return 1 - poly * Math.exp(-x * x)
}
function _phiStd(z) {
  return 0.5 * (1 + _erfAS(z / Math.SQRT2))
}

// Per-symbol two-sided binomial p-value (normal approx) testing the null that
// the true win rate is 50%: z = |phat − 0.5| / sqrt(0.25/n), p = 2·(1 − Φ(|z|)).
// Rows with n_resolved <= 0 get p = 1 (no evidence).
function computePValues(signalHealthRows) {
  return (signalHealthRows || [])
    .filter((r) => r && r.symbol)
    .map((r) => {
      const n = Number(r.n_resolved)
      const wins = Number(r.n_tp)
      let p = 1
      if (isFinite(n) && n > 0 && isFinite(wins) && wins >= 0) {
        const phat = Math.min(1, wins / n)
        const z = Math.abs(phat - 0.5) / Math.sqrt(0.25 / n)
        p = Math.min(1, Math.max(0, 2 * (1 - _phiStd(z))))
      }
      return { symbol: r.symbol, p_value: p }
    })
}

// SizingEngine what-if: baseline = equity × kelly × regime mult, then a
// drawdown clip ddClip = clamp(1 − dd/max_dd, 0.25, 1.0), capped at 5% of
// equity. Returns { baseline, final, capHit, cap, ddClip }.
function sizingWhatIf(equityUsd, effectiveKelly, regimeLabel, dd = 0.15) {
  const eq = Number(equityUsd) > 0 ? Number(equityUsd) : 1000
  const kelly = isFinite(Number(effectiveKelly)) && Number(effectiveKelly) > 0 ? Number(effectiveKelly) : 0
  const reg = metaRegimeInfo(regimeLabel)
  const baseline = eq * kelly * reg.mult
  const maxDd = 0.15
  const ddClip = Math.min(1, Math.max(0.25, 1 - dd / maxDd))
  let final = baseline * ddClip
  const cap = eq * 0.05
  let capHit = false
  if (final > cap) { final = cap; capHit = true }
  return { baseline, final, capHit, cap, ddClip }
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
  const [sigHealthPage, setSigHealthPage] = React.useState(1)

  // Symbol list — plan-gated via nt_symbol.plan_ids cs. filter (UUID from the
  // server claim response, never client-derived). Intersected with the latest
  // sweep rows so ONLY ACTIVE symbols (those with a recent sweep) show in the
  // picker; falls back to the full plan list while sweeps are still loading.
  // Ordering is STABLE: grouped by asset_class (commodities → forex → crypto
  // → stocks) then symbol ASC — the raw sweep order changes every refresh.
  const hasPlanUuid = !!claim.plan_uuid
  const symbols = useSupabaseData(config, 'nt_symbol',
    { select: 'symbol,asset_class', plan_ids: hasPlanUuid ? 'cs.{' + claim.plan_uuid + '}' : undefined },
    connected && hasPlanUuid)
  const planSymbols = (symbols.data || []).map((r) => r.symbol).filter(Boolean)
  const assetClassOf = {}
  for (const r of (symbols.data || [])) assetClassOf[r.symbol] = r.asset_class || 'other'
  const CLASS_RANK = { commodities: 0, forex: 1, crypto: 2, stocks: 3 }
  const sortByClassThenSymbol = (a, b) => {
    const ra = CLASS_RANK[assetClassOf[a]] != null ? CLASS_RANK[assetClassOf[a]] : 9
    const rb = CLASS_RANK[assetClassOf[b]] != null ? CLASS_RANK[assetClassOf[b]] : 9
    return (ra - rb) || a.localeCompare(b)
  }

  // Sweep data — powers the kelly histogram, the hot-signal seed and the
  // renko ENTRY/SL/TP levels. NOTE: nt_sweep_result's direction column is
  // `signal` (buy/sell/neutral) — the broadcast contract calls it `direction`,
  // so both are normalized client-side.
  const sweeps = useSupabaseData(config, 'nt_sweep_result',
    { select: 'symbol,signal,effective_kelly,kelly_f,entry_price,stop_loss,take_profit,sweep_timestamp,regime,qualified', order: 'sweep_timestamp.desc', limit: '200' },
    connected)

  // Active symbols = plan symbols present in the latest sweep stream
  // (same derivation as noble-trader-admin). Fallback to plan list when
  // sweeps have not loaded yet (loading / empty → picker stays usable).
  const sweepSyms = []
  {
    const seen = {}
    for (const r of (sweeps.data || [])) {
      if (!seen[r.symbol]) { seen[r.symbol] = true; sweepSyms.push(r.symbol) }
    }
  }
  const symbolList = (sweepSyms.length ? planSymbols.filter((s) => sweepSyms.includes(s)) : planSymbols)
    .slice()
    .sort(sortByClassThenSymbol)

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

  // Phase 1 — analytics reads (migration 110 views may not exist yet; the
  // fetch helper already degrades to .error on PGRST205, cards render hints).
  // Signal health validates the paid signal stream — ALL plans see it.
  const signalHealth = useSupabaseData(config, 'v_talaria_signal_health',
    { select: '*', limit: '20' },
    connected)
  // Portfolio tear-sheet — Precision Pro only.
  const portStats = useSupabaseData(config, 'v_talaria_portfolio_stats',
    { select: '*', limit: '1' },
    connected && isPro)
  // EOD calibration bias — all plans (already anon-granted, migration 103).
  const calib = useSupabaseData(config, 'v_eod_calibration_bias',
    { select: 'day,symbol,avg_predicted_p_win,realized_win_rate,bias,status', order: 'day.desc', limit: '10' },
    connected)
  // Paper book vs equal-weight baseline — Precision Pro only (migration 106).
  const vsOpt = useSupabaseData(config, 'v_paper_vs_optimized_daily',
    { select: 'day,paper_pnl,equal_wt_pnl,paper_minus_equal_wt', order: 'day.desc', limit: '14' },
    connected && isPro)
  // Long brick series (up to 200) for the Markov card — same symbol as the
  // 10-brick chart window, kept as a separate fetch.
  const brickSeries = useSupabaseData(config, 'nt_renko_bricks',
    { select: 'symbol,direction,open_price,close_price,high,low,brick_index,ts', order: 'brick_index.desc', limit: '200', symbol: 'eq.' + activeBrickSym },
    connected && !!activeBrickSym)

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

    // --- Phase 2 derived analytics -------------------------------------------
    // Markov + pattern: pattern from the 10-brick window, Markov fit on the
    // longer (≤200) brick close series (desc fetch → reverse to ascending).
    const brickSeriesAsc = (brickSeries.data || []).reverse()
    const pattern = brickPattern(brickWindow)
    const markov = markovUpProbability(brickSeriesAsc.map((b) => Number(b.close_price)))

    // Signal health scoreboard: per-symbol Wilson lower bound + BH-FDR over
    // the two-sided binomial p-values (null: true win rate = 50%).
    const sigHealthRows = (signalHealth.data || []) || []
    const sigHealthPages = Math.max(1, Math.ceil(sigHealthRows.length / PAGE_SIZE))
    const sigHealthPageRows = sigHealthRows.slice((sigHealthPage - 1) * PAGE_SIZE, sigHealthPage * PAGE_SIZE)
    const fdrBySym = {}
    for (const f of benjaminiHochberg(computePValues(sigHealthRows))) {
      fdrBySym[f.symbol] = f
    }

    // Calibration + paper-vs-optimized rows (already day.desc ordered).
    const calibRows = calib.data || []
    const vsOptRows = vsOpt.data || []

    // Sizing what-if — follows the SELECTED renko symbol (Opt 1, 2026-08-08):
    // kelly + regime come from that symbol's newest sweep row, NOT the
    // globally newest sweep. Paper equity + portfolio drawdown as before.
    const kellyIn = sweepRow && sweepRow.effective_kelly != null
      ? Number(sweepRow.effective_kelly)
      : (sweepRow && sweepRow.kelly_f != null ? Number(sweepRow.kelly_f) : null)
    const regimeLabel = (sweepRow && sweepRow.regime) || ''
    const eqRowSizing = (paperEquity.data || [])[0]
    const eqUsd = eqRowSizing && Number(eqRowSizing.cumulative_pnl) > 0 ? Number(eqRowSizing.cumulative_pnl) : 1000
    const portRow = (portStats.data || [])[0]
    const portDd = portRow && Number(portRow.max_dd_pct) > 0 ? Number(portRow.max_dd_pct) / 100 : 0.15
    const sizing = sizingWhatIf(eqUsd, kellyIn, regimeLabel, portDd)
    const regInfo = metaRegimeInfo(regimeLabel)

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
            className: cn('tla-brick-btn', 'dui-btn', 'dui-btn-sm', s === activeBrickSym ? 'tla-brick-btn-active' : ''),
            onClick: () => setBrickSym(s),
          }, s),
        ),
      ),
      React.createElement(RenkoBrickChart, { bricks: brickWindow, levels }),
      React.createElement('div', { className: 'tla-hint' },
        'ENTRY / SL / TP reference lines from the latest sweep · window = last 10 bricks'),
    ),
    // Markov + brick-pattern — ALL plans (client-side light math only). Placed
    // right below the renko chart because both analyze the SAME selected symbol
    // (activeBrickSym): pattern = last 10 bricks (short-term shape), Markov =
    // up to 200 brick closes (longer statistical fit).
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, `Markov + pattern — ${activeBrickSym || 'select a symbol'}`),
      React.createElement('div', { className: 'tla-grid' },
        React.createElement(StatCard, {
          title: 'Brick pattern',
          value: pattern || '—',
          sub: `last ${brickWindow.length} bricks · ${(sweepRow && sweepRow.regime) || 'regime n/a'}`,
        }),
        React.createElement(StatCard, {
          title: 'Markov P(up in 3)',
          value: markov ? (markov.pUp * 100).toFixed(1) + '%' : '—',
          sub: markov ? `P(down) ${(markov.pDown * 100).toFixed(1)}% · ${markov.n} bricks` : 'needs ≥10 bricks',
          tone: markov && markov.pUp > 0.5 ? 'pos' : markov && markov.pUp < 0.5 ? 'neg' : undefined,
        }),
      ),
      React.createElement('div', { className: 'tla-hint' },
        `Analyzes the symbol selected in the chart above (${activeBrickSym || 'none'}). Nuance: Brick pattern = the last 10 bricks only (short-term shape: 3-push / pullback / chop). Markov P(up in 3) = a 3-state UP/DOWN/FLAT Markov chain fitted on up to 200 brick closes (longer statistical fit) — the probability the next 3-brick move is UP. A 50% value means no edge; >50% leans bullish, <50% leans bearish.`),
    ),
    isPro ? React.createElement(PaperSection, {
      positions: paperPositions,
      equity: paperEquity,
      events: paperEvents,
    }) : null,

    // --- Phase 2/3 analytics cards (data computed above) ---

    // Signal health scoreboard — ALL plans. Wilson lower bound + BH-FDR over
    // two-sided binomial p-values (null: true win rate = 50%).
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Signal health scoreboard'),
      signalHealth.error
        ? React.createElement('div', { className: 'tla-hint' },
            'Signal health view not deployed yet (migration 110) — ' + signalHealth.error.message)
        : sigHealthRows.length === 0
          ? React.createElement('div', { className: 'tla-hint' }, 'No resolved signals yet — rows appear once the EOD resolver closes signals.')
          : React.createElement('table', { className: cn('tla-table', 'dui-table', 'dui-table-sm') },
              React.createElement('thead', null,
                React.createElement('tr', null,
                  React.createElement('th', null, 'Symbol'),
                  React.createElement('th', null, 'Resolved'),
                  React.createElement('th', null, 'Win rate'),
                  React.createElement('th', null, 'WR LB'),
                  React.createElement('th', null, 'Bias'),
                  React.createElement('th', null, 'Profit factor'),
                  React.createElement('th', null, 'Total PnL'))),
              React.createElement('tbody', null,
                sigHealthPageRows.map((r, i) => {
                  const fdr = fdrBySym[r.symbol]
                  const lb = (r.n_resolved > 0 && r.n_tp != null)
                    ? wilsonLower(Number(r.n_resolved), Number(r.n_tp))
                    : null
                  return React.createElement('tr', { key: r.symbol + i },
                    React.createElement('td', null,
                      r.symbol,
                      fdr && fdr.significant
                        ? React.createElement('span', { className: cn('tla-badge', 'tla-hot-chip'), title: 'survives BH-FDR at 0.05' }, 'sig')
                        : null),
                    React.createElement('td', null, r.n_resolved != null ? String(r.n_resolved) : '—'),
                    React.createElement('td', null, r.win_rate != null ? (Number(r.win_rate) * 100).toFixed(1) + '%' : '—'),
                    React.createElement('td', { className: 'tla-sm' }, lb != null ? (lb * 100).toFixed(1) + '%' : '—'),
                    React.createElement('td', {
                      className: r.bias != null && Number(r.bias) > 0.10 ? 'tla-neg' : r.bias != null && Number(r.bias) < -0.10 ? 'tla-pos' : '',
                    }, r.bias != null ? Number(r.bias).toFixed(3) : '—'),
                    React.createElement('td', null, r.profit_factor != null ? Number(r.profit_factor).toFixed(2) : '—'),
                    React.createElement('td', {
                      className: r.total_pnl != null && Number(r.total_pnl) >= 0 ? 'tla-pos' : 'tla-neg',
                    }, r.total_pnl != null ? `$${Number(r.total_pnl).toFixed(2)}` : '—'),
                  )
                }),
              ),
            ),
      React.createElement(Pager, { page: sigHealthPage, pages: sigHealthPages, onChange: setSigHealthPage }),
      React.createElement('div', { className: 'tla-hint' },
        '30-day window · Wilson lower bound (95%) · sig = survives BH-FDR at 0.05 · bias = predicted − realized win rate'),
    ),

    // Calibration bias — ALL plans (migration 103).
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Calibration bias (7d)'),
      calib.error
        ? React.createElement('div', { className: 'tla-hint' }, 'Calibration view not deployed yet — ' + calib.error.message)
        : calibRows.length === 0
          ? React.createElement('div', { className: 'tla-hint' }, 'No calibration rows yet — resolved signals needed.')
          : React.createElement('table', { className: cn('tla-table', 'dui-table', 'dui-table-sm') },
              React.createElement('thead', null,
                React.createElement('tr', null,
                  React.createElement('th', null, 'Day'),
                  React.createElement('th', null, 'Symbol'),
                  React.createElement('th', null, 'Predicted'),
                  React.createElement('th', null, 'Realized'),
                  React.createElement('th', null, 'Bias'),
                  React.createElement('th', null, 'Status'))),
              React.createElement('tbody', null,
                calibRows.map((r, i) => (
                  React.createElement('tr', { key: (r.day || '') + (r.symbol || '') + i },
                    React.createElement('td', { className: 'tla-sm' }, String(r.day || '').slice(0, 10)),
                    React.createElement('td', null, r.symbol || '—'),
                    React.createElement('td', null, r.avg_predicted_p_win != null ? (Number(r.avg_predicted_p_win) * 100).toFixed(1) + '%' : '—'),
                    React.createElement('td', null, r.realized_win_rate != null ? (Number(r.realized_win_rate) * 100).toFixed(1) + '%' : '—'),
                    React.createElement('td', {
                      className: r.bias != null && Number(r.bias) > 0.10 ? 'tla-neg' : r.bias != null && Number(r.bias) < -0.10 ? 'tla-pos' : '',
                    }, r.bias != null ? Number(r.bias).toFixed(3) : '—'),
                    React.createElement('td', null,
                      React.createElement('span', {
                        className: cn('tla-badge',
                          r.status === 'OVERCONFIDENT' ? 'closed' : r.status === 'UNDERCONFIDENT' ? 'opened' : ''),
                      }, r.status || '—')),
                  )
                )),
              ),
            ),
      React.createElement('div', { className: 'tla-hint' },
        'What it means: OVERCONFIDENT = the model predicted a HIGHER win rate than it actually delivered (it thinks it wins more than it does — be cautious). UNDERCONFIDENT = it wins MORE than predicted (predictions are too pessimistic). Close to 0 = well calibrated. · last 10 rows'),
    ),

    // Sizing what-if — ALL plans (arithmetic on sweep kelly + equity).
    React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, `Sizing what-if — ${activeBrickSym || 'select a symbol'}`),
      React.createElement('div', { className: 'tla-grid' },
        React.createElement(StatCard, {
          title: 'Baseline size',
          value: kellyIn != null ? `$${Number(sizing.baseline).toFixed(2)}` : '—',
          sub: `equity $${Number(eqUsd).toFixed(2)} × kelly ${kellyIn != null ? Number(kellyIn).toFixed(3) : 'n/a'} × regime ${regInfo.mult.toFixed(2)}`,
        }),
        React.createElement(StatCard, {
          title: 'Final size (capped)',
          value: kellyIn != null ? `$${Number(sizing.final).toFixed(2)}` : '—',
          sub: sizing.capHit ? '5% equity cap hit' : `regime ${regInfo.aggressiveness} · dd clip ${(portDd * 100).toFixed(1)}%`,
          tone: kellyIn != null ? (sizing.final > 0 ? 'pos' : 'neg') : undefined,
        }),
      ),
      React.createElement('div', { className: 'tla-hint' },
        `Sizing for the symbol selected above (${activeBrickSym || 'none'}) — SizingEngine arithmetic: baseline = equity × effective_kelly × regime multiplier, clipped by drawdown, capped at 5% of equity`),
    ),

    // Portfolio tear-sheet — Precision Pro only (SQL view, migration 110).
    isPro ? React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Portfolio stats — Precision Pro'),
      React.createElement('div', { className: 'tla-explainer' },
        'What this is: risk-adjusted performance of the CLOSED paper trades in the Paper portfolio section above (Sharpe, Sortino, Calmar, drawdown, profit factor, total PnL). These are computed from the ACTUAL sized paper book — the same +$0.xx number you see in Paper equity — NOT the theoretical Signal health scoreboard. Dash (—) = not enough CLOSED trades yet for a meaningful number, which is expected early on.'),
      portStats.error
        ? React.createElement('div', { className: 'tla-hint' }, 'Portfolio stats view not deployed yet (migration 110) — ' + portStats.error.message)
        : portRow
          ? React.createElement('div', { className: 'tla-grid' },
              React.createElement(StatCard, { title: 'Sharpe', value: portRow.sharpe != null ? Number(portRow.sharpe).toFixed(2) : '—', sub: 'annualized daily' }),
              React.createElement(StatCard, { title: 'Sortino', value: portRow.sortino != null ? Number(portRow.sortino).toFixed(2) : '—', sub: 'downside-only' }),
              React.createElement(StatCard, { title: 'Calmar', value: portRow.calmar != null ? Number(portRow.calmar).toFixed(2) : '—', sub: 'return / max DD' }),
              React.createElement(StatCard, { title: 'Max DD %', value: portRow.max_dd_pct != null ? (Number(portRow.max_dd_pct) * 100).toFixed(1) + '%' : '—', sub: 'peak-to-trough', tone: portRow.max_dd_pct != null && Number(portRow.max_dd_pct) > 0.10 ? 'neg' : undefined }),
              React.createElement(StatCard, { title: 'Vol ann %', value: portRow.vol_annual_pct != null ? (Number(portRow.vol_annual_pct) * 100).toFixed(1) + '%' : '—', sub: 'daily σ annualized' }),
              React.createElement(StatCard, { title: 'Profit factor', value: portRow.profit_factor != null ? Number(portRow.profit_factor).toFixed(2) : '—', sub: 'gross wins / gross losses', tone: portRow.profit_factor != null && Number(portRow.profit_factor) >= 1 ? 'pos' : 'neg' }),
              React.createElement(StatCard, { title: 'Total PnL', value: portRow.total_pnl != null ? `$${Number(portRow.total_pnl).toFixed(2)}` : '—', sub: portRow.n_trades != null ? `${portRow.n_trades} trades · win rate ${portRow.win_rate != null ? (Number(portRow.win_rate) * 100).toFixed(1) + '%' : '—'}` : '', tone: portRow.total_pnl != null && Number(portRow.total_pnl) >= 0 ? 'pos' : 'neg' }),
            )
          : React.createElement('div', { className: 'tla-hint' }, 'No portfolio stats yet — the paper book needs resolved positions.'),
      React.createElement('div', { className: 'tla-hint' },
        'Risk-adjusted performance of the paper book over its trading history. Sharpe = reward per unit of volatility (higher is better); Sortino = same but only counts downside; Calmar = annualized return ÷ max drawdown; Max DD = worst peak-to-trough; Vol = how jumpy returns are; Profit factor = gross wins ÷ gross losses (above 1.0 = profitable); Total PnL = cumulative paper profit. Dash (—) = not enough closed trades yet for a meaningful number.'),
    ) : null,

    // Paper vs equal-weight — Precision Pro only (migration 106).
    isPro ? React.createElement('div', { className: 'tla-card' },
      React.createElement('h3', null, 'Paper vs equal-weight'),
      vsOpt.error
        ? React.createElement('div', { className: 'tla-hint' }, 'Comparison view not deployed yet — ' + vsOpt.error.message)
        : vsOptRows.length === 0
          ? React.createElement('div', { className: 'tla-hint' }, 'No comparison rows yet.')
          : React.createElement('table', { className: cn('tla-table', 'dui-table', 'dui-table-sm') },
              React.createElement('thead', null,
                React.createElement('tr', null,
                  React.createElement('th', null, 'Day'),
                  React.createElement('th', null, 'Paper PnL'),
                  React.createElement('th', null, 'Equal-wt PnL'),
                  React.createElement('th', null, 'Delta'))),
              React.createElement('tbody', null,
                vsOptRows.map((r, i) => (
                  React.createElement('tr', { key: (r.day || '') + i },
                    React.createElement('td', { className: 'tla-sm' }, String(r.day || '').slice(0, 10)),
                    React.createElement('td', { className: Number(r.paper_pnl || 0) >= 0 ? 'tla-pos' : 'tla-neg' }, `$${Number(r.paper_pnl || 0).toFixed(2)}`),
                    React.createElement('td', null, `$${Number(r.equal_wt_pnl || 0).toFixed(2)}`),
                    React.createElement('td', {
                      className: Number(r.paper_minus_equal_wt || 0) >= 0 ? 'tla-pos' : 'tla-neg',
                    }, `$${Number(r.paper_minus_equal_wt || 0).toFixed(2)}`),
                  )
                )),
              ),
            ),
      React.createElement('div', { className: 'tla-hint' },
        'Is the strategy beating the benchmark? Paper PnL = the signal engine\'s Kelly/regime-sized trades. Equal-wt PnL = what you would have made betting the same amount on every symbol with no regime filter. Delta > $0 (green) = the engine beat the equal-weight benchmark that day; Delta < $0 (red) = the benchmark won. · last 14 days'),
    ) : null,

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
