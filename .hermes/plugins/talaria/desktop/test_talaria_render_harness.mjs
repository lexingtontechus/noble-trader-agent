/**
 * Talaria plugin — node render harness
 * Mirrors the noble-trader-admin technique (test_nta_render_harness.mjs):
 * stubs `react` + `@hermes/plugin-sdk` in a temp node_modules, loads the real
 * plugin.js, forces the registerMany page render, and asserts the dashboard
 * component tree renders WITHOUT "INVALID ELEMENT TYPE" throws.
 *
 * Unlike the admin harness (no-op useState setter), this harness uses a
 * functional state stub so the claim-check promise resolves and the FULL
 * dashboard tree (banner, histogram, renko chart, paper section) renders.
 *
 * Run: node desktop/test_talaria_render_harness.mjs
 */
import { pathToFileURL } from 'node:url'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'
import fs from 'node:fs'
import os from 'node:os'

const HARNESS_DIR = dirname(fileURLToPath(import.meta.url))
const PLUGIN_SRC = resolve(HARNESS_DIR, 'plugin.js')

// ---------------------------------------------------------------------------
// Browser-global stubs (installed BEFORE importing the plugin)
// ---------------------------------------------------------------------------
function makeLocalStorage(initial) {
  const store = { ...initial }
  return {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v) },
    removeItem: (k) => { delete store[k] },
  }
}

globalThis.localStorage = makeLocalStorage({
  'talaria-config.json': JSON.stringify({
    supabase_url: 'https://pcvscowltlrxzgxjurcr.supabase.co',
    supabase_key: 'sb_publishable_TEST_ANON',
    claim_token: 'test-claim-token-32-hex-chars-0000',
  }),
})

globalThis.document = {
  getElementById: () => null,
  createElement: () => ({ appendChild: () => {} }),
  head: { appendChild: () => {} },
}

// Native-WebSocket stub: constructor only, never fires events → the
// useRealtime hook connects but no retry timers keep the loop alive.
globalThis.WebSocket = class WebSocketStub {
  constructor(url) { this.url = url }
  close() {}
}

function jsonResp(obj, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'ERR',
    json: async () => obj,
    text: async () => JSON.stringify(obj),
  }
}

const nowIso = () => new Date().toISOString()

globalThis.fetch = async (url) => {
  const u = String(url)
  if (u.includes('/functions/v1/talaria-check')) {
    return jsonResp({
      ok: true,
      plan_slug: 'precision_pro',
      plan_uuid: '1b66e78e-e8d1-46b6-9887-b36e038131c5',
      sub_status: 'active',
      period_end: '2026-09-01T00:00:00Z',
      grace_end: null,
      next_charge_url: 'https://pay.example.com/renew',
    })
  }
  if (u.includes('/rest/v1/nt_symbol')) {
    return jsonResp([
      { symbol: 'XAUUSD', asset_class: 'commodities' },
      { symbol: 'EURUSD', asset_class: 'forex' },
      { symbol: 'BTCUSD', asset_class: 'crypto' },
    ])
  }
  if (u.includes('/rest/v1/nt_sweep_result')) {
    return jsonResp([
      { symbol: 'XAUUSD', signal: 'buy', effective_kelly: 0.24, kelly_f: 0.24, entry_price: 4090.5, stop_loss: 4085.0, take_profit: 4102.0, sweep_timestamp: nowIso(), regime: 'strong_trend', qualified: true },
      { symbol: 'EURUSD', signal: 'sell', effective_kelly: 0.18, kelly_f: 0.18, entry_price: 1.0845, stop_loss: 1.0875, take_profit: 1.079, sweep_timestamp: nowIso(), regime: 'low_vol_range', qualified: true },
      { symbol: 'BTCUSD', signal: 'neutral', effective_kelly: null, kelly_f: null, entry_price: null, stop_loss: null, take_profit: null, sweep_timestamp: nowIso(), regime: 'high_vol_chop', qualified: false },
    ])
  }
  if (u.includes('/rest/v1/nt_renko_bricks')) {
    const m = u.match(/symbol=eq\.([A-Z0-9]+)/)
    const sym = m ? m[1] : 'XAUUSD'
    const bricks = []
    for (let i = 0; i < 10; i++) {
      bricks.push({
        symbol: sym,
        direction: i % 2 === 0 ? 'up' : 'down',
        brick_size: 1,
        open_price: 4085 + i,
        close_price: 4085 + i + (i % 2 === 0 ? 1 : -1),
        high: 4086 + i,
        low: 4084 + i,
        brick_index: 300 + i,
        ts: nowIso(),
      })
    }
    return jsonResp(bricks)
  }
  if (u.includes('/rest/v1/nt_paper_positions')) {
    return jsonResp([
      { symbol: 'XAUUSD', direction: 'buy', status: 'open', realized_pnl: null, r_multiple: null, open_ts: nowIso() },
      { symbol: 'EURUSD', direction: 'sell', status: 'closed', realized_pnl: 12.34, r_multiple: 1.6, open_ts: new Date(Date.now() - 3600000).toISOString() },
    ])
  }
  if (u.includes('/rest/v1/v_paper_equity')) {
    return jsonResp([{ day: '2026-08-06', realized_pnl: 12.34, cumulative_pnl: 88.4 }])
  }
  if (u.includes('/rest/v1/v_talaria_signal_health')) {
    return jsonResp([
      { symbol: 'XAUUSD', n_resolved: 42, n_tp: 27, n_sl: 15, n_expired: 3, win_rate: 0.6429, avg_predicted_p_win: 0.61, bias: -0.0329, avg_pnl_bricks: 1.24, avg_pnl_dollars: 18.5, total_pnl: 777.0, profit_factor: 1.82, avg_ev: 0.21, avg_hold_bars: 9.5, last_signal_ts: nowIso() },
      { symbol: 'EURUSD', n_resolved: 35, n_tp: 14, n_sl: 21, n_expired: 5, win_rate: 0.4, avg_predicted_p_win: 0.52, bias: 0.12, avg_pnl_bricks: -0.4, avg_pnl_dollars: -6.2, total_pnl: -217.0, profit_factor: 0.71, avg_ev: 0.08, avg_hold_bars: 11.2, last_signal_ts: nowIso() },
    ])
  }
  if (u.includes('/rest/v1/v_talaria_portfolio_stats')) {
    return jsonResp([{ n_days: 14, n_trades: 31, win_rate: 0.5484, avg_r: 0.92, profit_factor: 1.44, total_pnl: 885.2, total_return_pct: 0.0885, sharpe: 1.72, sortino: 2.41, calmar: 1.05, max_dd_pct: 0.084, vol_annual_pct: 0.184 }])
  }
  if (u.includes('/rest/v1/v_eod_calibration_bias')) {
    return jsonResp([
      { day: '2026-08-06', symbol: 'XAUUSD', avg_predicted_p_win: 0.61, realized_win_rate: 0.64, bias: -0.03, status: 'CALIBRATED' },
      { day: '2026-08-06', symbol: 'EURUSD', avg_predicted_p_win: 0.52, realized_win_rate: 0.4, bias: 0.12, status: 'OVERCONFIDENT' },
    ])
  }
  if (u.includes('/rest/v1/v_paper_vs_optimized_daily')) {
    return jsonResp([
      { day: '2026-08-06', paper_pnl: 12.34, equal_wt_pnl: 9.1, paper_minus_equal_wt: 3.24 },
      { day: '2026-08-05', paper_pnl: -4.2, equal_wt_pnl: -2.0, paper_minus_equal_wt: -2.2 },
    ])
  }
  throw new Error('Unexpected fetch URL: ' + u)
}

// ---------------------------------------------------------------------------
// Stub packages in a temp dir (bare-specifier resolution for plugin.js)
// ---------------------------------------------------------------------------
const REACT_STUB = `
const hookSlots = []
let cursor = 0
let renderFn = null
let latestRoot = null
let renders = 0
const MAX_RENDERS = 120

function scheduleRender() {
  queueMicrotask(() => {
    if (renders >= MAX_RENDERS) return
    renders++
    cursor = 0
    if (renderFn) latestRoot = renderFn()
  })
}

function useState(init) {
  const i = cursor++
  if (hookSlots[i] === undefined) {
    hookSlots[i] = { state: typeof init === 'function' ? init() : init }
  }
  const slot = hookSlots[i]
  const set = (v) => {
    const next = typeof v === 'function' ? v(slot.state) : v
    if (Object.is(next, slot.state)) return
    slot.state = next
    scheduleRender()
  }
  return [slot.state, set]
}

function useEffect(cb, deps) {
  const i = cursor++
  const slot = hookSlots[i] || (hookSlots[i] = {})
  const key = deps ? JSON.stringify(deps) : undefined
  // Function deps stringify to null → can't distinguish a changed load
  // callback. Real React compares by reference; emulate that: re-run the
  // effect whenever a function dep is present (the load cb is recreated on
  // each render pass, so this mirrors React). The gated fetches (bricks:
  // enabled only after symbols/sweeps load) depend on this.
  const hasFnDep = Array.isArray(deps) && deps.some((d) => typeof d === 'function')
  if (!slot.ran || slot.key !== key || hasFnDep) {
    slot.ran = true
    slot.key = key
    const cleanup = cb()
    // Invoke cleanup immediately — kills the 60s/24h intervals so the
    // Node event loop doesn't stay alive and hang the harness.
    if (typeof cleanup === 'function') cleanup()
  }
}

function useCallback(cb, deps) {
  const i = cursor++
  const slot = hookSlots[i] || (hookSlots[i] = {})
  const key = deps ? JSON.stringify(deps) : undefined
  if (!slot.ran || slot.key !== key) {
    slot.ran = true
    slot.key = key
    slot.cb = cb
  }
  return slot.cb
}

function useRef(init) {
  const i = cursor++
  if (hookSlots[i] === undefined) hookSlots[i] = { current: init }
  return hookSlots[i]
}

function createElement(type, props, ...children) {
  if (type == null) throw new Error('INVALID ELEMENT TYPE: ' + String(type))
  const flat = []
  for (const c of children) {
    if (Array.isArray(c)) flat.push(...c)
    else if (c !== false && c != null) flat.push(c)
  }
  return { type, props: props || {}, children: flat }
}

// Mini-reconciler: function-type elements are INVOKED (depth-first, one
// synchronous pass — this is what drives hook slot alignment), DOM elements
// keep their children.
function expand(el) {
  if (el == null || typeof el === 'string' || typeof el === 'number') return el
  if (Array.isArray(el)) return el.map(expand)
  if (typeof el === 'object' && el.type != null) {
    if (typeof el.type === 'function') {
      return expand(el.type(el.props || {}))
    }
    return { type: el.type, props: el.props || {}, children: (el.children || []).map(expand) }
  }
  return el
}

const React = { useState, useEffect, useCallback, useRef, createElement }
globalThis.__REACT_STUB__ = {
  React,
  reset: () => { hookSlots.length = 0; cursor = 0; renders = 0; latestRoot = null },
  setRenderFn: (fn) => { renderFn = () => expand(fn()) },
  getLatestRoot: () => latestRoot,
  renderOnce: () => { cursor = 0; latestRoot = renderFn(); return latestRoot },
}
export default React
`

const SDK_STUB = `
export const cn = (...a) => a.filter(Boolean).join(' ')
export const ROUTES_AREA = 'routes-area'
export const SIDEBAR_NAV_AREA = 'sidebar-nav-area'
`

const tmp = fs.mkdtempSync(resolve(os.tmpdir(), 'talaria-harness-'))
fs.mkdirSync(resolve(tmp, 'node_modules/react'), { recursive: true })
fs.mkdirSync(resolve(tmp, 'node_modules/@hermes/plugin-sdk'), { recursive: true })
fs.writeFileSync(resolve(tmp, 'package.json'), JSON.stringify({ type: 'module' }))
fs.writeFileSync(resolve(tmp, 'node_modules/react/package.json'), JSON.stringify({ name: 'react', type: 'module', main: 'index.js' }))
fs.writeFileSync(resolve(tmp, 'node_modules/react/index.js'), REACT_STUB)
fs.writeFileSync(resolve(tmp, 'node_modules/@hermes/plugin-sdk/package.json'), JSON.stringify({ name: '@hermes/plugin-sdk', type: 'module', main: 'index.js' }))
fs.writeFileSync(resolve(tmp, 'node_modules/@hermes/plugin-sdk/index.js'), SDK_STUB)
fs.copyFileSync(PLUGIN_SRC, resolve(tmp, 'plugin.js'))

const pluginPath = resolve(tmp, 'plugin.js')

// ---------------------------------------------------------------------------
// Load the real plugin and drive the render
// ---------------------------------------------------------------------------
function walk(el, acc) {
  if (el == null) return acc
  if (typeof el === 'string' || typeof el === 'number') {
    const t = String(el).trim()
    if (t) acc.texts.push(t)
    return acc
  }
  if (Array.isArray(el)) { for (const c of el) walk(c, acc); return acc }
  if (typeof el === 'object' && el.type != null) {
    if (el.props && typeof el.props.className === 'string') acc.classes.push(el.props.className)
    for (const c of el.children || []) walk(c, acc)
  }
  return acc
}

async function flush(rounds = 15) {
  for (let i = 0; i < rounds; i++) await new Promise((r) => setTimeout(r, 20))
}

const failures = []
function assert(cond, label) {
  if (cond) console.log('  PASS  ' + label)
  else { failures.push(label); console.log('  FAIL  ' + label) }
}

// --- Scenario 1: configured claim → full dashboard tree ---
const mod = await import(pathToFileURL(pluginPath).href)
const plugin = mod.default
const stub = globalThis.__REACT_STUB__

let items = null
plugin.register({ registerMany: (x) => { items = x } })
const page = items.find((i) => i.id === 'page')
const nav = items.find((i) => i.id === 'nav')
assert(!!page && !!nav, 'registerMany exposes page + nav items')
assert(page.area === 'routes-area' && nav.area === 'sidebar-nav-area', 'areas match sdk constants')
assert(page.data.path === '/talaria' && nav.data.path === '/talaria', 'route/nav path /talaria')
assert(nav.data.label === 'Talaria', 'nav label Talaria')
assert(plugin.id === 'talaria' && plugin.defaultEnabled === true, 'plugin id/defaultEnabled')

stub.setRenderFn(() => page.render())
let threw = null
try {
  stub.renderOnce() // initial mount (claim-check loading screen)
  await flush() // let claim-check + data polls resolve
  stub.renderOnce() // final dashboard tree
} catch (e) {
  threw = e
}
assert(!threw, 'render sequence threw nothing (no INVALID ELEMENT TYPE)')
if (threw) console.log('  THREW: ' + (threw.stack || threw))

const acc = { texts: [], classes: [] }
walk(stub.getLatestRoot(), acc)
const hasText = (t) => acc.texts.some((x) => x.includes(t))

assert(acc.texts.some((x) => x.includes('Direct Supabase')), 'dashboard root renders (footer hint present)')
assert(hasText('Hot signals'), 'hot-signal banner + stat render')
assert(acc.classes.some((c) => c.includes('tla-hot-card')), 'banner visible (seed signal within 10m TTL)')
assert(hasText('Kelly by symbol'), 'kelly histogram card renders')
assert(hasText('Renko bricks'), 'renko chart card renders')
assert(hasText('10 bricks'), 'renko chart window hint renders')
assert(hasText('levels:'), 'renko pricing legend row renders (all symbols)')
assert(hasText('ENTRY'), 'renko legend shows ENTRY level')
assert(hasText('Paper portfolio'), 'Pro-only paper section renders (precision_pro claim)')
assert(hasText('Precision Pro'), 'plan stat shows Precision Pro')
assert(hasText('XAUUSD'), 'symbol list / chips render symbols')
assert(hasText('Connecting') || hasText('Live'), 'realtime stat reflects socket state')

// --- NEW analytics sections (Phase 1-3) ---
assert(hasText('Signal health'), 'signal health scoreboard renders (all plans)')
assert(hasText('Calibration bias'), 'calibration bias card renders (all plans)')
assert(hasText('Markov + pattern') || hasText('Markov'), 'markov + brick-pattern card renders (all plans)')
assert(hasText('Sizing what-if'), 'sizing what-if card renders (all plans)')
assert(hasText('Portfolio stats'), 'portfolio stats card renders (Pro)')
assert(hasText('What this is'), 'paper/portfolio header explainers render (disconnect clarity)')
assert(hasText('Paper vs equal'), 'paper vs equal-weight card renders (Pro)')
assert(hasText('Sharpe'), 'tear-sheet sharpe metric renders (Pro)')
assert(hasText('profit factor') || hasText('Profit factor'), 'profit factor metric renders')

// --- Scenario 2: no config → Connect tab ---
globalThis.localStorage = makeLocalStorage({})
const mod2 = await import(pathToFileURL(pluginPath).href + '?scenario=2')
const plugin2 = mod2.default
let items2 = null
plugin2.register({ registerMany: (x) => { items2 = x } })
const page2 = items2.find((i) => i.id === 'page')
stub.reset()
stub.setRenderFn(() => page2.render())
let threw2 = null
try { stub.renderOnce() } catch (e) { threw2 = e }
assert(!threw2, 'no-config render threw nothing')
const acc2 = { texts: [], classes: [] }
walk(stub.getLatestRoot(), acc2)
assert(acc2.texts.some((x) => x.includes('Talaria — Connect')), 'Connect tab renders without config')
assert(acc2.texts.some((x) => x.includes('Claim token')), 'Connect tab has claim token field')
assert(acc2.texts.some((x) => x.includes('Save & Validate')), 'Connect tab has save button')

console.log('')
if (failures.length) {
  console.log('HARNESS FAIL — ' + failures.length + ' assertion(s) failed')
  process.exitCode = 1
} else {
  console.log('HARNESS PASS — dashboard + connect trees render clean')
}
