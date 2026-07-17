/* Hermes Dashboard — client-side charting.
 *
 * Self-hosted uPlot (vendored in static/uplot.iife.min.js). No CDN, works offline,
 * passes the strict CSP in app.py (script-src 'self').
 *
 * The browser already has a signed session cookie (set by /auth/login), so
 * fetch() to the /api/* endpoints is authenticated with no extra token handling.
 * If a request 401s, we surface a "log in" hint instead of a blank box.
 *
 * Public API:
 *   HermesChart.equityCurve(el, '/api/pnl/...')      — equity + drawdown
 *   HermesChart.pnlHistory(el, '/api/pnl/history')   — cumulative net PnL area
 *   HermesChart.varHistory(el, '/api/portfolio/var_history') — VaR / CVaR / DD lines
 *   HermesChart.exposure(el, '/api/portfolio/exposure')      — horizontal bars (venue)
 *   HermesChart.line(el, {label, x, y, points})      — generic time-series
 */

(function () {
  "use strict";

  // Palette aligned with daisyUI dark themes.
  const C = {
    equity: "#4ade80",   // success green
    drawdown: "#f87171", // error red
    pnl: "#60a5fa",      // info blue
    var: "#fbbf24",      // warning amber
    cvar: "#f97316",     // orange
    grid: "rgba(148,163,184,0.12)",
    axis: "rgba(148,163,184,0.55)",
  };

  function fmtTime(ms) {
    const d = new Date(ms);
    return d.toLocaleString(undefined, { hour12: false });
  }

  function authError(el, msg) {
    el.innerHTML =
      '<div class="alert alert-warning text-sm py-3">Session expired or not ' +
      'authenticated. <a class="link" href="/">Log in</a>' +
      (msg ? " — " + msg : "") + "</div>";
  }

  function noData(el, msg) {
    el.innerHTML =
      '<div class="text-xs opacity-50 text-center py-6">' +
      (msg || "No data yet — run the loops to accumulate snapshots.") +
      "</div>";
  }

  async function getJSON(url) {
    const r = await fetch(url, { credentials: "same-origin" });
    if (r.status === 401) {
      const e = new Error("unauthorized");
      e.status = 401;
      throw e;
    }
    if (!r.ok) {
      const e = new Error("http " + r.status);
      e.status = r.status;
      throw e;
    }
    return r.json();
  }

  // Convert ["2026-07-10T12:00:00", 12345.6, ...] rows to uPlot [xs, ys...]
  function toSeries(rows, getX, series) {
    const xs = rows.map((r) => getX(r));
    const out = [xs];
    for (const s of series) {
      out.push(rows.map((r) => s.pick(r)));
    }
    return out;
  }

  function baseOpts(el, seriesDefs, extra) {
    const opts = {
      title: "",
      width: el.clientWidth || 720,
      height: el.clientHeight || 280,
      scales: { x: { time: false } },
      axes: [
        {
          stroke: C.axis,
          grid: { stroke: C.grid },
          values: (u, vals) => vals.map((v) => fmtTime(v)),
        },
        { stroke: C.axis, grid: { stroke: C.grid } },
      ],
      legend: { show: true },
      series: [{ label: "Time" }].concat(
        seriesDefs.map((s) => ({
          label: s.label,
          stroke: s.color,
          width: s.width || 1.5,
          fill: s.fill || null,
          points: { show: !!s.points },
        }))
      ),
    };
    return Object.assign(opts, extra || {});
  }

  function mount(el, opts, data) {
    el.innerHTML = "";
    if (typeof uPlot === "undefined") {
      el.innerHTML =
        '<div class="alert alert-error text-sm">uPlot failed to load (static/uplot.iife.min.js).</div>';
      return;
    }
    try {
      new uPlot(opts, data, el);
    } catch (e) {
      el.innerHTML =
        '<div class="alert alert-error text-sm">Chart render error: ' +
        (e && e.message ? e.message : e) + "</div>";
    }
  }

  function handleErr(el, e, ctx) {
    if (e && e.status === 401) return authError(el, ctx);
    if (e && e.status) return noData(el, "Endpoint error: " + e.status);
    return noData(el, ctx || "Failed to load chart data.");
  }

  // ---- Public chart builders ----

  async function equityCurve(el, apiUrl) {
    try {
      const data = await getJSON(apiUrl);
      const runs = data.history || data.equity_curve || [];
      if (!runs.length) return noData(el, "No equity curve yet.");
      renderEquity(el, runs);
    } catch (e) {
      handleErr(el, e, "equity curve");
    }
  }

  // Same as equityCurve but from in-page SSR data (PnL page passes equity_curve
  // server-side since /api/pnl/tear_sheet does not include the curve).
  function equityCurveFromData(el, runs) {
    if (!runs || !runs.length) return noData(el, "No equity curve yet.");
    try {
      renderEquity(el, runs);
    } catch (e) {
      noData(el, "Failed to render equity curve.");
    }
    return Promise.resolve();
  }

  function renderEquity(el, runs) {
    const rows = runs.map((p) => ({
      ts: Date.parse(p.ts || p.timestamp),
      eq: parseFloat(p.equity_total ?? p.equity ?? 0),
      dd: parseFloat((p.drawdown_pct ?? 0) * 100),
    }));
    const series = [
      { label: "Equity", color: C.equity, width: 2 },
      { label: "Drawdown %", color: C.drawdown, width: 1, fill: "rgba(248,113,113,0.12)" },
    ];
    const opts = baseOpts(el, series);
    mount(el, opts, toSeries(rows, (r) => r.ts, [
      { pick: (r) => r.eq },
      { pick: (r) => r.dd },
    ]));
  }

  async function pnlHistory(el, apiUrl) {
    try {
      const data = await getJSON(apiUrl);
      const runs = data.history || [];
      if (!runs.length) return noData(el, "No realized PnL yet.");
      let cum = 0;
      const rows = runs.map((p) => {
        cum += parseFloat(p.net_pnl ?? 0);
        return { ts: Date.parse(p.ts || p.timestamp), cum };
      });
      const opts = baseOpts(el, [
        { label: "Cumulative Net PnL", color: C.pnl, width: 2, fill: "rgba(96,165,250,0.12)" },
      ]);
      mount(el, opts, toSeries(rows, (r) => r.ts, [{ pick: (r) => r.cum }]));
    } catch (e) {
      handleErr(el, e, "pnl history");
    }
  }

  async function varHistory(el, apiUrl) {
    try {
      const data = await getJSON(apiUrl);
      const rows = data.history || [];
      if (!rows.length) return noData(el, "No VaR history yet.");
      const norm = rows.map((r) => ({
        ts: Date.parse(r.ts || r.timestamp),
        var: parseFloat(r.var_95_pct ?? 0) * 100,
        cvar: parseFloat(r.cvar_95_pct ?? 0) * 100,
        dd: parseFloat(r.drawdown_pct ?? 0) * 100,
      }));
      const opts = baseOpts(el, [
        { label: "VaR 95%", color: C.var, width: 1.5 },
        { label: "CVaR 95%", color: C.cvar, width: 1.5 },
        { label: "Drawdown %", color: C.drawdown, width: 1, fill: "rgba(248,113,113,0.10)" },
      ]);
      mount(el, opts, toSeries(norm, (r) => r.ts, [
        { pick: (r) => r.var },
        { pick: (r) => r.cvar },
        { pick: (r) => r.dd },
      ]));
    } catch (e) {
      handleErr(el, e, "var history");
    }
  }

  async function exposure(el, apiUrl) {
    try {
      const data = await getJSON(apiUrl);
      const by = data.by_venue || data.venues || data.exposure || {};
      const labels = Object.keys(by);
      if (!labels.length) return noData(el, "No exposure breakdown yet.");
      const vals = labels.map((k) => {
        const v = by[k];
        return Math.abs(parseFloat(v.notional_usd ?? v.exposure_usd ?? v.gross_usd ?? v ?? 0));
      });
      el.innerHTML =
        '<div class="space-y-2">' +
        labels
          .map((k, i) => {
            const pct = vals[i] / (Math.max.apply(null, vals) || 1);
            return (
              '<div class="flex items-center gap-2 text-xs">' +
              '<div class="w-28 truncate opacity-70">' + k + "</div>" +
              '<div class="flex-1 bg-base-300 rounded h-4 overflow-hidden">' +
              '<div class="h-4 bg-primary" style="width:' + (pct * 100).toFixed(1) + '%"></div></div>' +
              '<div class="w-24 text-right font-mono">$' + vals[i].toLocaleString(undefined, { maximumFractionDigits: 0 }) + "</div>" +
              "</div>"
            );
          })
          .join("") +
        "</div>";
    } catch (e) {
      handleErr(el, e, "exposure");
    }
  }

  async function line(el, cfg) {
    try {
      const data = await getJSON(cfg.url);
      const rows = cfg.rows(data) || [];
      if (!rows.length) return noData(el, cfg.empty || "No data yet.");
      const opts = baseOpts(el, cfg.series);
      mount(el, opts, toSeries(rows, cfg.x, cfg.series.map((s) => ({ pick: s.pick }))));
    } catch (e) {
      handleErr(el, e, cfg.label || "series");
    }
  }

  function resizeAll() {
    // uPlot instances are not auto-responsive; re-render on resize is out of scope
    // for the initial pass. Pages that want live-resize can re-call their builder.
  }
  window.addEventListener("resize", resizeAll);

  window.HermesChart = {
    equityCurve,
    equityCurveFromData,
    pnlHistory,
    varHistory,
    exposure,
    line,
  };
})();
