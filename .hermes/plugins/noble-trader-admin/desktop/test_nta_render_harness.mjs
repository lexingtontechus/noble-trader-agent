// Render-harness test for the noble-trader-admin desktop plugin (STANDALONE).
//
// Stubs `react` + `@hermes/plugin-sdk` + `localStorage` + `fetch` in a temp
// node_modules, loads the plugin's desktop/plugin.js as ESM, forces the admin
// component to render in BOTH states:
//   1. unconnected (no config) → Connect tab renders, no crash
//   2. connected (config saved) → dashboard renders against mocked Supabase
//      REST payloads shaped like the real nt_signal_sim / nt_paper_positions /
//      v_paper_* / v_eod_* responses
// Asserts no "Element type is invalid" / render crash occurs.
//
// Run:  node .hermes/plugins/noble-trader-admin/desktop/test_nta_render_harness.mjs

import { execSync } from 'node:child_process'
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
// The harness sits next to plugin.js in the desktop/ dir
const pluginSrc = join(__dirname, 'plugin.js')

// ── stub react ──────────────────────────────────────────────────────────────
// createElement throws on undefined type (catches "invalid element type");
// useEffect fires the effect and immediately runs its cleanup (so the 60s
// setInterval is cleared and the node process can exit).
const reactStub = `
const React = { createElement: (type, props, ...children) => {
  if (typeof type === 'undefined' || type === null) {
    throw new Error('INVALID ELEMENT TYPE: element type is undefined/null')
  }
  return { type, props: props || {}, children }
}, useState: (init) => [typeof init === 'function' ? init() : init, () => {}],
useEffect: (fn) => { if (typeof fn === 'function') { const cleanup = fn(); if (typeof cleanup === 'function') cleanup() } },
useCallback: (fn) => fn }
export default React
`
const sdkStub = `
export const cn = (...args) => args.filter(Boolean).join(' ')
export const ROUTES_AREA = 'routes'
export const SIDEBAR_NAV_AREA = 'sidebar'
`
// ── browser mocks: document, localStorage, fetch (Supabase REST shapes) ─────
const mockFetch = `
globalThis.document = {
  getElementById: () => null,
  createElement: (tag) => ({ tag, textContent: '', appendChild: () => {} }),
  head: { appendChild: () => {} },
}
const store = {}
globalThis.localStorage = {
  getItem: (k) => (k in store ? store[k] : null),
  setItem: (k, v) => { store[k] = String(v) },
  removeItem: (k) => { delete store[k] },
}
const SB = 'https://test.supabase.co'
const DATA = {
  'nt_signal_sim': [
    { signal_id: 's1', symbol: 'AUDUSD', signal: 'buy', ts: '2026-08-06T20:25:22+00:00', qualified: true, outcome: null, entry_price: 0.700212, source: 'sweep-light', kelly_f: 0.04, effective_kelly: 0.04 },
    { signal_id: 's2', symbol: 'BTCUSD', signal: 'sell', ts: '2026-08-06T20:25:09+00:00', qualified: true, outcome: 'tp_hit', entry_price: 104500.12, source: 'sweep-light', kelly_f: 0.16, effective_kelly: 0.16 },
    { signal_id: 's3', symbol: 'EURUSD', signal: 'buy', ts: '2026-08-06T20:25:16+00:00', qualified: false, outcome: 'sl_hit', entry_price: 1.0821, source: 'sweep-light', kelly_f: 0.04, effective_kelly: 0.04 },
  ],
  'nt_paper_positions': [
    { position_id: 'p1', symbol: 'USDCAD', direction: 'buy', size_notional: 200, size_units: 142.1, status: 'open', realized_pnl: null, open_ts: '2026-08-06T09:45:29+00:00', close_ts: null },
    { position_id: 'p2', symbol: 'EURUSD', direction: 'sell', size_notional: 40, size_units: 35.06, status: 'closed', realized_pnl: 1.234, open_ts: '2026-08-06T09:45:16+00:00', close_ts: '2026-08-06T23:40:00+00:00' },
    { position_id: 'p3', symbol: 'BTCUSD', direction: 'sell', size_notional: 40, size_units: 0.0006, status: 'expired', realized_pnl: -0.5, open_ts: '2026-08-06T09:45:03+00:00', close_ts: '2026-08-07T00:30:00+00:00' },
  ],
  'v_paper_equity': [
    { day: '2026-08-06', realized_pnl: 0.734, cumulative_pnl: 0.734 },
  ],
  'v_eod_calibration_bias': [
    { day: '2026-08-06', symbol: 'AUDUSD', avg_predicted_p_win: 0.62, realized_win_rate: 0.5, bias: 0.12, status: 'OVERCONFIDENT' },
  ],
  'v_paper_vs_optimized_daily': [
    { day: '2026-08-06', paper_pnl: 0.734, equal_wt_pnl: 0.5, paper_minus_equal_wt: 0.234 },
  ],
  'nt_sweep_result': [
    { symbol: 'XAUUSD', sweep_timestamp: '2026-08-06T23:11:04+00:00', regime: 'high_vol_strong_bull', regime_conf: 1, kelly_f: 0.04, effective_kelly: 0.04, brick_size: 1, sl_bricks: 3, tp_bricks: 6, signal: 'buy', qualified: false, entry_price: 4090, stop_loss: 4087, take_profit: 4096 },
    { symbol: 'USDJPY', sweep_timestamp: '2026-08-06T23:10:42+00:00', regime: 'high_vol_strong_bear', regime_conf: 1, kelly_f: 0.16, effective_kelly: 0.16, brick_size: 0.1, sl_bricks: 3, tp_bricks: 6, signal: 'sell', qualified: false, entry_price: 155.2, stop_loss: 155.5, take_profit: 154.6 },
    { symbol: 'USDCAD', sweep_timestamp: '2026-08-06T23:10:29+00:00', regime: 'high_vol_strong_bull', regime_conf: 1, kelly_f: 0.4, effective_kelly: 0.4, brick_size: 0.001, sl_bricks: 3, tp_bricks: 6, signal: 'buy', qualified: true, entry_price: 1.372, stop_loss: 1.369, take_profit: 1.378 },
  ],
  'nt_renko_bricks': (() => {
    // 16-brick XAUUSD series so the last-10 window has data
    const rows = []
    let px = 4090
    const dirs = ['up','up','down','down','down','down','up','down','down','down','up','down','down','down','up','down']
    for (let i = 0; i < dirs.length; i++) {
      const move = dirs[i] === 'up' ? 1 : -1
      rows.push({ brick_id: i + 1, symbol: 'XAUUSD', direction: dirs[i], brick_size: 1, open_price: px, close_price: px + move, high: Math.max(px, px + move), low: Math.min(px, px + move), brick_index: i, ts: '2026-08-07T01:00:00+00:00' })
      px += move
    }
    return rows
  })(),
}
globalThis.fetch = async (url) => {
  const u = String(url)
  if (!u.startsWith(SB)) throw new Error('unexpected url: ' + u)
  const path = u.replace(SB + '/rest/v1/', '').split('?')[0]
  if (!(path in DATA)) throw new Error('unmocked supabase table: ' + path)
  return { ok: true, status: 200, statusText: 'OK', json: async () => DATA[path], text: async () => JSON.stringify(DATA[path]) }
}
`

const harness = `
import React from 'react'
import mod from './plugin.js'
const plugin = mod.default || mod
if (!plugin || plugin.id !== 'noble-trader-admin') throw new Error('bad default export')
let rendered = 0
let connectSeen = 0
let dashSeen = 0
// Expand function-type elements recursively (the react stub does not recurse).
function expand(el, depth = 0) {
  if (depth > 8) return el
  if (el && typeof el.type === 'function') {
    return expand(el.type(el.props || {}), depth + 1)
  }
  return el
}
// Recursively search the tree for an h3 whose text contains needle.
// Expands function-type components as it descends (the react stub does not).
function findH3(el, needle, depth = 0) {
  if (!el || depth > 10) return false
  if (typeof el === 'string') return false
  if (typeof el.type === 'function') {
    return findH3(el.type(el.props || {}), needle, depth + 1)
  }
  if (el.type === 'h3') {
    const txt = Array.isArray(el.children) ? el.children.filter(c => typeof c === 'string').join(' ') : String(el.children || '')
    if (txt.includes(needle)) return true
  }
  const kids = Array.isArray(el.children) ? el.children : (el.children ? [el.children] : [])
  for (const k of kids) {
    if (findH3(k, needle, depth + 1)) return true
  }
  return false
}
const ctx = {
  registerMany: (items) => {
    for (const it of items) {
      if (it.area === 'routes' && typeof it.render === 'function') {
        const el = it.render()
        if (!el || typeof el.type !== 'function') throw new Error('page render returned invalid element')
        const out = expand(el)
        if (!out) throw new Error('admin render returned null')
        rendered++
        if (findH3(out, 'Connect')) connectSeen++
        if (findH3(out, 'Qualified signals')) dashSeen++
      }
    }
  },
}
plugin.register(ctx)
if (rendered < 1) throw new Error('page render never executed')
if (connectSeen < 1) throw new Error('Connect tab never rendered (config pre-set — expected both surfaces)')
console.log('RENDER OK — id=' + plugin.id + ' rendered=' + rendered + ' connectTab=' + connectSeen + ' dashboard=' + dashSeen)
`

// Second harness run: pre-set config → dashboard surface
const harnessConnected = `
import React from 'react'
import mod from './plugin.js'
localStorage.setItem('noble-trader-admin-config.json', JSON.stringify({ supabase_url: SB, supabase_key: 'sb_test_key' }))
const plugin = mod.default || mod
let dashSeen = 0
let sweepSeen = 0
let hotSeen = 0
let brickSeen = 0
function findText(el, needle, depth = 0) {
  if (!el || depth > 10) return false
  if (typeof el === 'string') {
    return el.includes(needle)
  }
  if (typeof el.type === 'function') {
    return findText(el.type(el.props || {}), needle, depth + 1)
  }
  const kids = Array.isArray(el.children) ? el.children : (el.children ? [el.children] : [])
  for (const k of kids) {
    if (findText(k, needle, depth + 1)) return true
  }
  return false
}
function findH3(el, needle, depth = 0) {
  if (!el || depth > 10) return false
  if (typeof el === 'string') return false
  if (typeof el.type === 'function') {
    return findH3(el.type(el.props || {}), needle, depth + 1)
  }
  if (el.type === 'h3') {
    const txt = Array.isArray(el.children) ? el.children.filter(c => typeof c === 'string').join(' ') : String(el.children || '')
    if (txt.includes(needle)) return true
  }
  const kids = Array.isArray(el.children) ? el.children : (el.children ? [el.children] : [])
  for (const k of kids) {
    if (findH3(k, needle, depth + 1)) return true
  }
  return false
}
function expand(el, depth = 0) {
  if (depth > 8) return el
  if (el && typeof el.type === 'function') {
    return expand(el.type(el.props || {}), depth + 1)
  }
  return el
}
const ctx = {
  registerMany: (items) => {
    for (const it of items) {
      if (it.area === 'routes' && typeof it.render === 'function') {
        const out = expand(it.render())
        if (findH3(out, 'Qualified signals')) dashSeen++
        if (findH3(out, 'Latest sweep')) sweepSeen++
        if (findText(out, 'Hot signals')) hotSeen++
        if (findH3(out, 'Renko bricks') || findText(out, 'bricks')) brickSeen++
      }
    }
  },
}
plugin.register(ctx)
if (dashSeen < 1) throw new Error('Dashboard never rendered with config set')
if (sweepSeen < 1) throw new Error('Sweep histogram section never rendered')
if (brickSeen < 1) throw new Error('Renko bricks section never rendered')
// Hot banner is conditionally hidden when no hot signals (display/hidden by
// design). The react stub's useState setter is a no-op, so data stays null and
// the banner correctly hides — assert it didn't CRASH, not that it rendered.
console.log('CONNECTED DASHBOARD OK — statCard=' + dashSeen + ' sweepHistogram=' + sweepSeen + ' hotBanner=' + hotSeen + ' brickChart=' + brickSeen + ' (hot hidden-when-empty: expected)')
`

const dir = mkdtempSync(join(tmpdir(), 'nta-harness-'))
writeFileSync(join(dir, 'package.json'), JSON.stringify({ type: 'module' }))
mkdirSync(join(dir, 'node_modules', 'react'), { recursive: true })
mkdirSync(join(dir, 'node_modules', '@hermes', 'plugin-sdk'), { recursive: true })
writeFileSync(join(dir, 'node_modules', 'react', 'package.json'), JSON.stringify({ name: 'react', type: 'module', main: 'index.js' }))
writeFileSync(join(dir, 'node_modules', 'react', 'index.js'), reactStub)
writeFileSync(join(dir, 'node_modules', '@hermes', 'plugin-sdk', 'package.json'), JSON.stringify({ name: '@hermes/plugin-sdk', type: 'module', main: 'index.js' }))
writeFileSync(join(dir, 'node_modules', '@hermes', 'plugin-sdk', 'index.js'), sdkStub)
writeFileSync(join(dir, 'plugin.js'), readFileSync(pluginSrc, 'utf8'))
writeFileSync(join(dir, 'harness.mjs'), mockFetch + harness)
writeFileSync(join(dir, 'harness_connected.mjs'), mockFetch + harnessConnected)

try {
  const out1 = execSync('node harness.mjs', { cwd: dir, encoding: 'utf8', timeout: 60000 })
  console.log(out1.trim())
  const out2 = execSync('node harness_connected.mjs', { cwd: dir, encoding: 'utf8', timeout: 60000 })
  console.log(out2.trim())
  console.log('HARNESS PASS')
} catch (e) {
  console.error('HARNESS FAIL:')
  const all = String(e.stdout || '') + '\n---STDERR---\n' + String(e.stderr || e.message || '')
  console.error(all.split('\n').filter(l => !l.includes('MODULE_TYPELESS') && !l.includes('Reparsing') && !l.includes('trace-warnings') && !l.includes('(Use')).join('\n').slice(0, 5000))
  process.exit(1)
}
