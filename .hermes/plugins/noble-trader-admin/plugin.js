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
const DAISY_CSS = `.dui-badge{display:inline-flex;align-items:center;justify-content:center;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,-webkit-backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter,-webkit-backdrop-filter;transition-timing-function:cubic-bezier(.4,0,.2,1);transition-timing-function:cubic-bezier(0,0,.2,1);transition-duration:.2s;height:1.25rem;font-size:.875rem;line-height:1.25rem;width:-moz-fit-content;width:fit-content;padding-left:.563rem;padding-right:.563rem;border-radius:var(--rounded-badge,1.9rem);border-width:1px;--tw-border-opacity:1;border-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)))}@media (hover:hover){.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn).dui-active,.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn):active,.dui-menu li>details>summary:active{--tw-bg-opacity:1;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-nc,oklch(var(--nc)/var(--tw-text-opacity)))}.dui-table tr.dui-hover:hover,.dui-table tr.dui-hover:nth-child(2n):hover{--tw-bg-opacity:1;background-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-bg-opacity)))}.dui-table-zebra tr.dui-hover:hover,.dui-table-zebra tr.dui-hover:nth-child(2n):hover{--tw-bg-opacity:1;background-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-bg-opacity)))}}.dui-btn{display:inline-flex;height:3rem;min-height:3rem;flex-shrink:0;cursor:pointer;-webkit-user-select:none;-moz-user-select:none;user-select:none;flex-wrap:wrap;align-items:center;justify-content:center;border-radius:var(--rounded-btn,.5rem);border-color:transparent;border-color:oklch(var(--btn-color,var(--b2))/var(--tw-border-opacity));padding-left:1rem;padding-right:1rem;text-align:center;font-size:.875rem;line-height:1em;gap:.5rem;font-weight:600;text-decoration-line:none;transition-duration:.2s;transition-timing-function:cubic-bezier(0,0,.2,1);border-width:var(--border-btn,1px);transition-property:color,background-color,border-color,opacity,box-shadow,transform;--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-shadow:0 1px 2px 0 rgba(0,0,0,.05);--tw-shadow-colored:0 1px 2px 0 var(--tw-shadow-color);box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow);outline-color:var(--fallback-bc,oklch(var(--bc)/1));background-color:oklch(var(--btn-color,var(--b2))/var(--tw-bg-opacity));--tw-bg-opacity:1;--tw-border-opacity:1}.dui-btn-disabled,.dui-btn:disabled,.dui-btn[disabled]{pointer-events:none}:where(.dui-btn:is(input[type=checkbox])),:where(.dui-btn:is(input[type=radio])){width:auto;-webkit-appearance:none;-moz-appearance:none;appearance:none}.dui-btn:is(input[type=checkbox]):after,.dui-btn:is(input[type=radio]):after{--tw-content:attr(aria-label);content:var(--tw-content)}@media (hover:hover){.dui-btn:hover{--tw-border-opacity:1;border-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-bg-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn:hover{background-color:color-mix(in oklab,oklch(var(--btn-color,var(--b2))/var(--tw-bg-opacity,1)) 90%,#000);border-color:color-mix(in oklab,oklch(var(--btn-color,var(--b2))/var(--tw-border-opacity,1)) 90%,#000)}}@supports not (color:oklch(0% 0 0)){.dui-btn:hover{background-color:var(--btn-color,var(--fallback-b2));border-color:var(--btn-color,var(--fallback-b2))}}.dui-btn.dui-glass:hover{--glass-opacity:25%;--glass-border-opacity:15%}.dui-btn-ghost:hover{border-color:transparent}@supports (color:oklch(0% 0 0)){.dui-btn-ghost:hover{background-color:var(--fallback-bc,oklch(var(--bc)/.2))}}.dui-btn-outline:hover{--tw-border-opacity:1;border-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-b1,oklch(var(--b1)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-primary:hover{--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-primary:hover{background-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-secondary:hover{--tw-text-opacity:1;color:var(--fallback-sc,oklch(var(--sc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-secondary:hover{background-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-accent:hover{--tw-text-opacity:1;color:var(--fallback-ac,oklch(var(--ac)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-accent:hover{background-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-success:hover{--tw-text-opacity:1;color:var(--fallback-suc,oklch(var(--suc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-success:hover{background-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-info:hover{--tw-text-opacity:1;color:var(--fallback-inc,oklch(var(--inc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-info:hover{background-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-warning:hover{--tw-text-opacity:1;color:var(--fallback-wac,oklch(var(--wac)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-warning:hover{background-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000)}}.dui-btn-outline.dui-btn-error:hover{--tw-text-opacity:1;color:var(--fallback-erc,oklch(var(--erc)/var(--tw-text-opacity)))}@supports (color:color-mix(in oklab,black,black)){.dui-btn-outline.dui-btn-error:hover{background-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000)}}.dui-btn-disabled:hover,.dui-btn:disabled:hover,.dui-btn[disabled]:hover{--tw-border-opacity:0;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-bg-opacity:0.2;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-text-opacity:0.2}@supports (color:color-mix(in oklab,black,black)){.dui-btn:is(input[type=checkbox]:checked):hover,.dui-btn:is(input[type=radio]:checked):hover{background-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000)}}:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(.dui-active,.dui-btn):hover,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(.dui-active,.dui-btn):hover{cursor:pointer;outline:2px solid transparent;outline-offset:2px}@supports (color:oklch(0% 0 0)){:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(.dui-active,.dui-btn):hover,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(.dui-active,.dui-btn):hover{background-color:var(--fallback-bc,oklch(var(--bc)/.1))}}}.dui-join{display:inline-flex;align-items:stretch;border-radius:var(--rounded-btn,.5rem)}.dui-join :where(.dui-join-item){border-start-end-radius:0;border-end-end-radius:0;border-end-start-radius:0;border-start-start-radius:0}.dui-join .dui-join-item:not(:first-child):not(:last-child),.dui-join :not(:first-child):not(:last-child) .dui-join-item{border-start-end-radius:0;border-end-end-radius:0;border-end-start-radius:0;border-start-start-radius:0}.dui-join .dui-join-item:first-child:not(:last-child),.dui-join :first-child:not(:last-child) .dui-join-item{border-start-end-radius:0;border-end-end-radius:0}.dui-join .dui-dropdown .dui-join-item:first-child:not(:last-child),.dui-join :first-child:not(:last-child) .dui-dropdown .dui-join-item{border-start-end-radius:inherit;border-end-end-radius:inherit}.dui-join :where(.dui-join-item:first-child:not(:last-child)),.dui-join :where(:first-child:not(:last-child) .dui-join-item){border-end-start-radius:inherit;border-start-start-radius:inherit}.dui-join .dui-join-item:last-child:not(:first-child),.dui-join :last-child:not(:first-child) .dui-join-item{border-end-start-radius:0;border-start-start-radius:0}.dui-join :where(.dui-join-item:last-child:not(:first-child)),.dui-join :where(:last-child:not(:first-child) .dui-join-item){border-start-end-radius:inherit;border-end-end-radius:inherit}@supports not selector(:has(*)){:where(.dui-join *){border-radius:inherit}}@supports selector(:has(*)){:where(.dui-join :has(.dui-join-item)){border-radius:inherit}}.dui-menu{display:flex;flex-direction:column;flex-wrap:wrap;font-size:.875rem;line-height:1.25rem;padding:.5rem}.dui-menu :where(li ul){position:relative;white-space:nowrap;margin-inline-start:1rem;padding-inline-start:.5rem}.dui-menu :where(li:not(.dui-menu-title)>:not(ul,details,.dui-menu-title,.dui-btn)),.dui-menu :where(li:not(.dui-menu-title)>details>summary:not(.dui-menu-title)){display:grid;grid-auto-flow:column;align-content:flex-start;align-items:center;gap:.5rem;grid-auto-columns:minmax(auto,max-content) auto max-content;-webkit-user-select:none;-moz-user-select:none;user-select:none}.dui-menu li.dui-disabled{cursor:not-allowed;-webkit-user-select:none;-moz-user-select:none;user-select:none;color:var(--fallback-bc,oklch(var(--bc)/.3))}.dui-menu :where(li>.dui-menu-dropdown:not(.dui-menu-dropdown-show)){display:none}:where(.dui-menu li){position:relative;display:flex;flex-shrink:0;flex-direction:column;flex-wrap:wrap;align-items:stretch}:where(.dui-menu li) .dui-badge{justify-self:end}.dui-table{position:relative;width:100%;border-radius:var(--rounded-box,1rem);text-align:left;font-size:.875rem;line-height:1.25rem}.dui-table :where(.dui-table-pin-rows thead tr){position:sticky;top:0;z-index:1;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}.dui-table :where(.dui-table-pin-rows tfoot tr){position:sticky;bottom:0;z-index:1;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}.dui-table :where(.dui-table-pin-cols tr th){position:sticky;left:0;right:0;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}.dui-table-zebra tbody tr:nth-child(2n) :where(.dui-table-pin-cols tr th){--tw-bg-opacity:1;background-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-bg-opacity)))}.dui-btm-nav>:where(.dui-active){border-top-width:2px;--tw-bg-opacity:1;background-color:var(--fallback-b1,oklch(var(--b1)/var(--tw-bg-opacity)))}@media (prefers-reduced-motion:no-preference){.dui-btn{animation:button-pop var(--animation-btn,.25s) ease-out}}.dui-btn:active:focus,.dui-btn:active:hover{animation:button-pop 0s ease-out;transform:scale(var(--btn-focus-scale,.97))}@supports not (color:oklch(0% 0 0)){.dui-btn{background-color:var(--btn-color,var(--fallback-b2));border-color:var(--btn-color,var(--fallback-b2))}.dui-btn-primary{--btn-color:var(--fallback-p)}}@supports (color:color-mix(in oklab,black,black)){.dui-btn-active{background-color:color-mix(in oklab,oklch(var(--btn-color,var(--b3))/var(--tw-bg-opacity,1)) 90%,#000);border-color:color-mix(in oklab,oklch(var(--btn-color,var(--b3))/var(--tw-border-opacity,1)) 90%,#000)}.dui-btn-outline.dui-btn-primary.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-p,oklch(var(--p)/1)) 90%,#000)}.dui-btn-outline.dui-btn-secondary.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-s,oklch(var(--s)/1)) 90%,#000)}.dui-btn-outline.dui-btn-accent.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-a,oklch(var(--a)/1)) 90%,#000)}.dui-btn-outline.dui-btn-success.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-su,oklch(var(--su)/1)) 90%,#000)}.dui-btn-outline.dui-btn-info.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-in,oklch(var(--in)/1)) 90%,#000)}.dui-btn-outline.dui-btn-warning.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-wa,oklch(var(--wa)/1)) 90%,#000)}.dui-btn-outline.dui-btn-error.dui-btn-active{background-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000);border-color:color-mix(in oklab,var(--fallback-er,oklch(var(--er)/1)) 90%,#000)}}.dui-btn:focus-visible{outline-style:solid;outline-width:2px;outline-offset:2px}.dui-btn-primary{--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)));outline-color:var(--fallback-p,oklch(var(--p)/1))}@supports (color:oklch(0% 0 0)){.dui-btn-primary{--btn-color:var(--p)}}.dui-btn.dui-glass{--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow);outline-color:currentColor}.dui-btn.dui-glass.dui-btn-active{--glass-opacity:25%;--glass-border-opacity:15%}.dui-btn-ghost{border-width:1px;border-color:transparent;background-color:transparent;color:currentColor;--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow);outline-color:currentColor}.dui-btn-ghost.dui-btn-active{border-color:transparent;background-color:var(--fallback-bc,oklch(var(--bc)/.2))}.dui-btn-link.dui-btn-active{border-color:transparent;background-color:transparent;text-decoration-line:underline}.dui-btn-outline{border-color:currentColor;background-color:transparent;--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-shadow:0 0 #0000;--tw-shadow-colored:0 0 #0000;box-shadow:var(--tw-ring-offset-shadow,0 0 #0000),var(--tw-ring-shadow,0 0 #0000),var(--tw-shadow)}.dui-btn-outline.dui-btn-active{--tw-border-opacity:1;border-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-b1,oklch(var(--b1)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-primary{--tw-text-opacity:1;color:var(--fallback-p,oklch(var(--p)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-primary.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-secondary{--tw-text-opacity:1;color:var(--fallback-s,oklch(var(--s)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-secondary.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-sc,oklch(var(--sc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-accent{--tw-text-opacity:1;color:var(--fallback-a,oklch(var(--a)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-accent.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-ac,oklch(var(--ac)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-success{--tw-text-opacity:1;color:var(--fallback-su,oklch(var(--su)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-success.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-suc,oklch(var(--suc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-info{--tw-text-opacity:1;color:var(--fallback-in,oklch(var(--in)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-info.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-inc,oklch(var(--inc)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-warning{--tw-text-opacity:1;color:var(--fallback-wa,oklch(var(--wa)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-warning.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-wac,oklch(var(--wac)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-error{--tw-text-opacity:1;color:var(--fallback-er,oklch(var(--er)/var(--tw-text-opacity)))}.dui-btn-outline.dui-btn-error.dui-btn-active{--tw-text-opacity:1;color:var(--fallback-erc,oklch(var(--erc)/var(--tw-text-opacity)))}.dui-btn.dui-btn-disabled,.dui-btn:disabled,.dui-btn[disabled]{--tw-border-opacity:0;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-bg-opacity:0.2;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));--tw-text-opacity:0.2}.dui-btn:is(input[type=checkbox]:checked),.dui-btn:is(input[type=radio]:checked){--tw-border-opacity:1;border-color:var(--fallback-p,oklch(var(--p)/var(--tw-border-opacity)));--tw-bg-opacity:1;background-color:var(--fallback-p,oklch(var(--p)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-pc,oklch(var(--pc)/var(--tw-text-opacity)))}.dui-btn:is(input[type=checkbox]:checked):focus-visible,.dui-btn:is(input[type=radio]:checked):focus-visible{outline-color:var(--fallback-p,oklch(var(--p)/1))}@keyframes button-pop{0%{transform:scale(var(--btn-focus-scale,.98))}40%{transform:scale(1.02)}to{transform:scale(1)}}@keyframes checkmark{0%{background-position-y:5px}50%{background-position-y:-2px}to{background-position-y:0}}.dui-join>:where(:not(:first-child)){margin-top:0;margin-bottom:0;margin-inline-start:-1px}.dui-join>:where(:not(:first-child)):is(.dui-btn){margin-inline-start:calc(var(--border-btn)*-1)}.dui-join-item:focus{isolation:isolate}:where(.dui-menu li:empty){--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));opacity:.1;margin:.5rem 1rem;height:1px}.dui-menu :where(li ul):before{position:absolute;bottom:.75rem;inset-inline-start:0;top:.75rem;width:1px;--tw-bg-opacity:1;background-color:var(--fallback-bc,oklch(var(--bc)/var(--tw-bg-opacity)));opacity:.1;content:""}.dui-menu :where(li:not(.dui-menu-title)>:not(ul,details,.dui-menu-title,.dui-btn)),.dui-menu :where(li:not(.dui-menu-title)>details>summary:not(.dui-menu-title)){border-radius:var(--rounded-btn,.5rem);padding:.5rem 1rem;text-align:start;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,-webkit-backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter;transition-property:color,background-color,border-color,text-decoration-color,fill,stroke,opacity,box-shadow,transform,filter,backdrop-filter,-webkit-backdrop-filter;transition-timing-function:cubic-bezier(.4,0,.2,1);transition-timing-function:cubic-bezier(0,0,.2,1);transition-duration:.2s;text-wrap:balance}:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):is(summary):not(.dui-active,.dui-btn):focus-visible,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(summary,.dui-active,.dui-btn).dui-focus,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>:not(ul,details,.dui-menu-title)):not(summary,.dui-active,.dui-btn):focus,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):is(summary):not(.dui-active,.dui-btn):focus-visible,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(summary,.dui-active,.dui-btn).dui-focus,:where(.dui-menu li:not(.dui-menu-title,.dui-disabled)>details>summary:not(.dui-menu-title)):not(summary,.dui-active,.dui-btn):focus{cursor:pointer;background-color:var(--fallback-bc,oklch(var(--bc)/.1));--tw-text-opacity:1;color:var(--fallback-bc,oklch(var(--bc)/var(--tw-text-opacity)));outline:2px solid transparent;outline-offset:2px}.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn).dui-active,.dui-menu li>:not(ul,.dui-menu-title,details,.dui-btn):active,.dui-menu li>details>summary:active{--tw-bg-opacity:1;background-color:var(--fallback-n,oklch(var(--n)/var(--tw-bg-opacity)));--tw-text-opacity:1;color:var(--fallback-nc,oklch(var(--nc)/var(--tw-text-opacity)))}.dui-menu :where(li>details>summary)::-webkit-details-marker{display:none}.dui-menu :where(li>.dui-menu-dropdown-toggle):after,.dui-menu :where(li>details>summary):after{justify-self:end;display:block;margin-top:-.5rem;height:.5rem;width:.5rem;transform:rotate(45deg);transition-property:transform,margin-top;transition-duration:.3s;transition-timing-function:cubic-bezier(.4,0,.2,1);content:"";transform-origin:75% 75%;box-shadow:2px 2px;pointer-events:none}.dui-menu :where(li>.dui-menu-dropdown-toggle.dui-menu-dropdown-show):after,.dui-menu :where(li>details[open]>summary):after{transform:rotate(225deg);margin-top:0}@keyframes modal-pop{0%{opacity:0}}@keyframes progress-loading{50%{background-position-x:-115%}}@keyframes radiomark{0%{box-shadow:0 0 0 12px var(--fallback-b1,oklch(var(--b1)/1)) inset,0 0 0 12px var(--fallback-b1,oklch(var(--b1)/1)) inset}50%{box-shadow:0 0 0 3px var(--fallback-b1,oklch(var(--b1)/1)) inset,0 0 0 3px var(--fallback-b1,oklch(var(--b1)/1)) inset}to{box-shadow:0 0 0 4px var(--fallback-b1,oklch(var(--b1)/1)) inset,0 0 0 4px var(--fallback-b1,oklch(var(--b1)/1)) inset}}@keyframes rating-pop{0%{transform:translateY(-.125em)}40%{transform:translateY(-.125em)}to{transform:translateY(0)}}@keyframes skeleton{0%{background-position:150%}to{background-position:-50%}}.dui-table:where([dir=rtl],[dir=rtl] *){text-align:right}.dui-table :where(th,td){padding:.75rem 1rem;vertical-align:middle}.dui-table tr.dui-active,.dui-table tr.dui-active:nth-child(2n),.dui-table-zebra tbody tr:nth-child(2n){--tw-bg-opacity:1;background-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-bg-opacity)))}.dui-table-zebra tr.dui-active,.dui-table-zebra tr.dui-active:nth-child(2n),.dui-table-zebra-zebra tbody tr:nth-child(2n){--tw-bg-opacity:1;background-color:var(--fallback-b3,oklch(var(--b3)/var(--tw-bg-opacity)))}.dui-table :where(thead tr,tbody tr:not(:last-child),tbody tr:first-child:last-child){border-bottom-width:1px;--tw-border-opacity:1;border-bottom-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-border-opacity)))}.dui-table :where(thead,tfoot){white-space:nowrap;font-size:.75rem;line-height:1rem;font-weight:700;color:var(--fallback-bc,oklch(var(--bc)/.6))}.dui-table :where(tfoot){border-top-width:1px;--tw-border-opacity:1;border-top-color:var(--fallback-b2,oklch(var(--b2)/var(--tw-border-opacity)))}@keyframes toast-pop{0%{transform:scale(.9);opacity:0}to{transform:scale(1);opacity:1}}.dui-glass,.dui-glass.dui-btn-active{border:none;-webkit-backdrop-filter:blur(var(--glass-blur,40px));backdrop-filter:blur(var(--glass-blur,40px));background-color:transparent;background-image:linear-gradient(135deg,rgb(255 255 255/var(--glass-opacity,30%)) 0,transparent 100%),linear-gradient(var(--glass-reflex-degree,100deg),rgb(255 255 255/var(--glass-reflex-opacity,10%)) 25%,transparent 25%);box-shadow:0 0 0 1px rgb(255 255 255/var(--glass-border-opacity,10%)) inset,0 0 0 2px rgb(0 0 0/5%);text-shadow:0 1px rgb(0 0 0/var(--glass-text-shadow-opacity,5%))}@media (hover:hover){.dui-glass.dui-btn-active{border:none;-webkit-backdrop-filter:blur(var(--glass-blur,40px));backdrop-filter:blur(var(--glass-blur,40px));background-color:transparent;background-image:linear-gradient(135deg,rgb(255 255 255/var(--glass-opacity,30%)) 0,transparent 100%),linear-gradient(var(--glass-reflex-degree,100deg),rgb(255 255 255/var(--glass-reflex-opacity,10%)) 25%,transparent 25%);box-shadow:0 0 0 1px rgb(255 255 255/var(--glass-border-opacity,10%)) inset,0 0 0 2px rgb(0 0 0/5%);text-shadow:0 1px rgb(0 0 0/var(--glass-text-shadow-opacity,5%))}}.dui-badge-sm{height:1rem;font-size:.75rem;line-height:1rem;padding-left:.438rem;padding-right:.438rem}.dui-btm-nav-xs>:where(.dui-active){border-top-width:1px}.dui-btm-nav-sm>:where(.dui-active){border-top-width:2px}.dui-btm-nav-md>:where(.dui-active){border-top-width:2px}.dui-btm-nav-lg>:where(.dui-active){border-top-width:4px}.dui-btn-sm{height:2rem;min-height:2rem;padding-left:.75rem;padding-right:.75rem;font-size:.875rem}.dui-btn-square:where(.dui-btn-sm){height:2rem;width:2rem;padding:0}.dui-btn-circle:where(.dui-btn-sm){height:2rem;width:2rem;border-radius:9999px;padding:0}.dui-join.dui-join-vertical{flex-direction:column}.dui-join.dui-join-vertical .dui-join-item:first-child:not(:last-child),.dui-join.dui-join-vertical :first-child:not(:last-child) .dui-join-item{border-end-start-radius:0;border-end-end-radius:0;border-start-start-radius:inherit;border-start-end-radius:inherit}.dui-join.dui-join-vertical .dui-join-item:last-child:not(:first-child),.dui-join.dui-join-vertical :last-child:not(:first-child) .dui-join-item{border-start-start-radius:0;border-start-end-radius:0;border-end-start-radius:inherit;border-end-end-radius:inherit}.dui-join.dui-join-horizontal{flex-direction:row}.dui-join.dui-join-horizontal .dui-join-item:first-child:not(:last-child),.dui-join.dui-join-horizontal :first-child:not(:last-child) .dui-join-item{border-end-end-radius:0;border-start-end-radius:0;border-end-start-radius:inherit;border-start-start-radius:inherit}.dui-join.dui-join-horizontal .dui-join-item:last-child:not(:first-child),.dui-join.dui-join-horizontal :last-child:not(:first-child) .dui-join-item{border-end-start-radius:0;border-start-start-radius:0;border-end-end-radius:inherit;border-start-end-radius:inherit}.dui-join.dui-join-vertical>:where(:not(:first-child)){margin-left:0;margin-right:0;margin-top:-1px}.dui-join.dui-join-vertical>:where(:not(:first-child)):is(.dui-btn){margin-top:calc(var(--border-btn)*-1)}.dui-join.dui-join-horizontal>:where(:not(:first-child)){margin-top:0;margin-bottom:0;margin-inline-start:-1px}.dui-join.dui-join-horizontal>:where(:not(:first-child)):is(.dui-btn){margin-inline-start:calc(var(--border-btn)*-1);margin-top:0}.dui-table-sm :not(thead):not(tfoot) tr{font-size:.875rem;line-height:1.25rem}.dui-table-sm :where(th,td){padding:.5rem .75rem}.dui-table{display:table}`
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
  if (globalThis.__DAISY_INJECTED__ !== "noble-trader-admin-style") {
    const ds = document.getElementById('daisy-noble-trader-admin-style')
    if (!ds) { const d = document.createElement('style'); d.id = 'daisy-noble-trader-admin-style'; d.textContent = DAISY_CSS; document.head.appendChild(d) }
    globalThis.__DAISY_INJECTED__ = "noble-trader-admin-style"
  }
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
  return React.createElement('span', { className: cn('nta-badge', 'dui-badge', 'dui-badge-sm', cls) }, label)
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

// Full-decimal USD price formatter (full-value rule): 6dp → rstrip trailing
// zeros → keep ≥2dp → "$" prefix. XAU 4032.2 → $4032.20, XAG 57.0568 →
// $57.0568, FX 1.13896 → $1.13896 (no 1dp truncation for large prices).
function fmtUsd(v) {
  if (v == null || isNaN(Number(v))) return '—'
  const n = Number(v)
  let s = n.toFixed(6).replace(/\.?0+$/, '')
  if (!s.includes('.')) s += '.00'
  else if (s.split('.')[1].length < 2) s = n.toFixed(2)
  return '$' + s
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
        React.createElement('button', { className: cn('nta-btn', 'dui-btn', 'dui-btn-primary', 'dui-btn-sm'), onClick: save }, 'Save & Connect'),
        React.createElement('button', {
          className: cn('nta-btn', 'dui-btn', 'dui-btn-ghost', 'dui-btn-sm'), onClick: testConnection, disabled: testing,
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
    return React.createElement('div', { className: 'nta-hint' }, 'No bricks yet')
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
    React.createElement('div', { className: 'nta-hint', style: { marginTop: 4 } },
      `${bricks.length} bricks (last ${bricks.length} of series) · up = buy (blue) · down = sell (red) · last brick index ${bricks[bricks.length - 1].brick_index != null ? bricks[bricks.length - 1].brick_index : bricks.length - 1}`),
  )
}

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
// Markov + pattern + sizing helpers (ported from talaria, 2026-08-08)
// ---------------------------------------------------------------------------
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
  // Stable ordering source: nt_symbol asset_class (sweep order changes every
  // refresh → the picker must be sorted by asset_class then symbol, not by
  // sweep recency). All-plan symbols; inactive rows are filtered out by the
  // active-symbol intersection below.
  const symMeta = useSupabaseData(config, 'nt_symbol', { select: 'symbol,asset_class' }, connected)

  // Renko brick chart: pick a symbol from the latest sweep, fetch its bricks.
  const [brickSym, setBrickSym] = React.useState(null)
  // Pagination state for the recent tables (daisyUI join pager).
  const [posPage, setPosPage] = React.useState(1)
  const [sigPage, setSigPage] = React.useState(1)
  const latestSyms = []
  {
    const seen = {}
    for (const r of (sweeps.data || [])) {
      if (!seen[r.symbol]) { seen[r.symbol] = true; latestSyms.push(r.symbol) }
    }
  }
  // Stable order: asset_class (commodities → forex → crypto → stocks) then
  // symbol ASC. Symbol order must NOT change between refreshes.
  {
    const assetClassOf = {}
    for (const r of (symMeta.data || [])) assetClassOf[r.symbol] = r.asset_class || 'other'
    const CLASS_RANK = { commodities: 0, forex: 1, crypto: 2, stocks: 3 }
    latestSyms.sort((a, b) => {
      const ra = CLASS_RANK[assetClassOf[a]] != null ? CLASS_RANK[assetClassOf[a]] : 9
      const rb = CLASS_RANK[assetClassOf[b]] != null ? CLASS_RANK[assetClassOf[b]] : 9
      return (ra - rb) || a.localeCompare(b)
    })
  }
  const activeBrickSym = brickSym || (latestSyms[0] || '')
  // Fetch ONLY when a symbol is known (default = 1st latest-sweep symbol).
  // Fetch the LAST 10 bricks (brick_index DESC limit 10, reversed to ascending
  // for the chart) — the previous asc+limit-200+slice(-10) returned bricks
  // 190-199 for series with >200 bricks (XAUUSD 2350, BTCUSD 17351) instead
  // of the latest 10 (2026-08-08 fix).
  const bricks = useSupabaseData(config, 'nt_renko_bricks',
    { select: 'symbol,direction,brick_size,open_price,close_price,high,low,brick_index,ts', order: 'brick_index.desc', limit: '10', symbol: 'eq.' + activeBrickSym },
    connected && !!activeBrickSym)
  // Longer brick series (≤200) for the Markov fit on the selected symbol.
  const brickSeries = useSupabaseData(config, 'nt_renko_bricks',
    { select: 'symbol,direction,brick_size,open_price,close_price,high,low,brick_index,ts', order: 'brick_index.desc', limit: '200', symbol: 'eq.' + activeBrickSym },
    connected && !!activeBrickSym)

  const loading = signals.loading || positions.loading || recentPositions.loading
  const err = signals.error || positions.error || recentPositions.error

  if (err) {
    return React.createElement('div', { className: 'nta-root' },
      React.createElement('div', { className: 'nta-err' },
        `Supabase read failed: ${err.message}`),
      React.createElement('button', { className: cn('nta-btn', 'dui-btn', 'dui-btn-ghost', 'dui-btn-sm'), onClick: () => updateConfig({ supabase_url: '', supabase_key: '' }) },
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
            className: cn('nta-brick-btn', 'dui-btn', 'dui-btn-sm', s === activeBrickSym ? 'nta-brick-btn-active' : ''),
            onClick: () => setBrickSym(s),
          }, s),
        ),
      ),
      (() => {
        // Last ~10 bricks of the active symbol's series (the dashboard window)
        const all = (bricks.data || []).filter((b) => b.symbol === activeBrickSym)
        // Fetch is brick_index DESC limit 10 → reverse to ascending for the chart.
        const window = all.slice().reverse()
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

    // Markov + pattern — same layout as talaria: right below the renko chart,
    // analyzing the SAME selected symbol (activeBrickSym).
    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, `Markov + pattern — ${activeBrickSym || 'select a symbol'}`),
      (() => {
        const sweepRow = (sweeps.data || []).find((r) => r.symbol === activeBrickSym)
        const brickWindow = ((bricks.data || []).filter((b) => b.symbol === activeBrickSym)).reverse()
        const brickSeriesAsc = (brickSeries.data || []).slice().reverse()
        const pattern = brickPattern(brickWindow)
        const markov = markovUpProbability(brickSeriesAsc.map((b) => Number(b.close_price)))
        return React.createElement('div', null,
          React.createElement('div', { className: 'nta-grid' },
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
          React.createElement('div', { className: 'nta-hint' },
            `Analyzes the symbol selected in the chart above (${activeBrickSym || 'none'}). Nuance: Brick pattern = the last 10 bricks only (short-term shape: 3-push / pullback / chop). Markov P(up in 3) = a 3-state UP/DOWN/FLAT Markov chain fitted on up to 200 brick closes (longer statistical fit) — the probability the next 3-brick move is UP. A 50% value means no edge; >50% leans bullish, <50% leans bearish.`),
        )
      })(),
    ),

    // Sizing what-if — same layout as talaria: follows the SELECTED symbol's
    // newest sweep row (kelly + regime), paper equity, portfolio drawdown.
    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, `Sizing what-if — ${activeBrickSym || 'select a symbol'}`),
      (() => {
        const sweepRow = (sweeps.data || []).find((r) => r.symbol === activeBrickSym)
        const kellyIn = sweepRow && sweepRow.effective_kelly != null
          ? Number(sweepRow.effective_kelly)
          : (sweepRow && sweepRow.kelly_f != null ? Number(sweepRow.kelly_f) : null)
        const regimeLabel = (sweepRow && sweepRow.regime) || ''
        const eqRowSizing = (equity.data || [])[0]
        const eqUsd = eqRowSizing && Number(eqRowSizing.cumulative_pnl) > 0 ? Number(eqRowSizing.cumulative_pnl) : 1000
        const portRow = (optimized.data || [])[0]
        const portDd = portRow && Number(portRow.paper_minus_equal_wt) > 0 ? 0.05 : 0.15
        const sizing = sizingWhatIf(eqUsd, kellyIn, regimeLabel, portDd)
        const regInfo = metaRegimeInfo(regimeLabel)
        return React.createElement('div', null,
          React.createElement('div', { className: 'nta-grid' },
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
          React.createElement('div', { className: 'nta-hint' },
            `Sizing for the symbol selected above (${activeBrickSym || 'none'}) — SizingEngine arithmetic: baseline = equity × effective_kelly × regime multiplier, clipped by drawdown, capped at 5% of equity`),
        )
      })(),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Latest sweep — HMM × Renko detail'),
      React.createElement('table', { className: cn('nta-table', 'dui-table', 'dui-table-sm') },
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
                React.createElement('td', { className: 'nta-sm' }, fmtUsd(r.entry_price)),
                React.createElement('td', { className: 'nta-sm' }, fmtUsd(r.stop_loss)),
                React.createElement('td', { className: 'nta-sm' }, fmtUsd(r.take_profit)),
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
      React.createElement('table', { className: cn('nta-table', 'dui-table', 'dui-table-sm') },
        React.createElement('thead', null,
          React.createElement('tr', null,
            React.createElement('th', null, 'Symbol'),
            React.createElement('th', null, 'Dir'),
            React.createElement('th', null, 'Notional'),
            React.createElement('th', null, 'Status'),
            React.createElement('th', null, 'PnL'),
            React.createElement('th', null, 'Opened'))),
        React.createElement('tbody', null,
          (recentPositions.data || []).slice((posPage - 1) * PAGE_SIZE, posPage * PAGE_SIZE).map((p) => (
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
      React.createElement(Pager, {
        page: posPage,
        pages: Math.max(1, Math.ceil((recentPositions.data || []).length / PAGE_SIZE)),
        onChange: (p) => setPosPage(p),
      }),
    ),

    React.createElement('div', { className: 'nta-card' },
      React.createElement('h3', null, 'Recent signals'),
      React.createElement('table', { className: cn('nta-table', 'dui-table', 'dui-table-sm') },
        React.createElement('thead', null,
          React.createElement('tr', null,
            React.createElement('th', null, 'Symbol'),
            React.createElement('th', null, 'Signal'),
            React.createElement('th', null, 'Outcome'),
            React.createElement('th', null, 'Q'),
            React.createElement('th', null, 'Entry'),
            React.createElement('th', null, 'SL'),
            React.createElement('th', null, 'TP'),
            React.createElement('th', null, 'Ts'))),
        React.createElement('tbody', null,
          (recentSignals.data || []).slice((sigPage - 1) * PAGE_SIZE, sigPage * PAGE_SIZE).map((sig) => (
            React.createElement('tr', { key: sig.signal_id || (sig.symbol + sig.ts) },
              React.createElement('td', null, sig.symbol),
              React.createElement('td', null, sig.signal),
              React.createElement('td', null,
                React.createElement(OutcomeBadge, { outcome: sig.outcome })),
              React.createElement('td', null, sig.qualified ? '✓' : ''),
              React.createElement('td', null, sig.entry_price != null ? fmtUsd(sig.entry_price) : '—'),
              React.createElement('td', null, sig.stop_loss != null ? fmtUsd(sig.stop_loss) : '—'),
              React.createElement('td', null, sig.take_profit != null ? fmtUsd(sig.take_profit) : '—'),
              React.createElement('td', null, String(sig.ts || '').slice(0, 16)),
            )
          ))),
      ),
      React.createElement(Pager, {
        page: sigPage,
        pages: Math.max(1, Math.ceil((recentSignals.data || []).length / PAGE_SIZE)),
        onChange: (p) => setSigPage(p),
      }),
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
