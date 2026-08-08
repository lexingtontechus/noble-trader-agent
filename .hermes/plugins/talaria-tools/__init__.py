"""
Talaria tools — Python plugin (in-chat analytics tools)

Registers talaria_* tools that read the Talaria Supabase analytics views via
the Supabase REST API (public anon key + claim token), mirroring the
noble-trader plugin conventions. The Talaria desktop plugin (Electron) stores
its config in localStorage, which a Python plugin cannot read — so this plugin
sources config from environment variables:

    TALARIA_SUPABASE_URL  — Supabase project REST URL
    TALARIA_SUPABASE_KEY  — public anon key
    TALARIA_CLAIM_TOKEN   — Talaria claim token

Stdlib-only (urllib.request); no external dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

log = logging.getLogger(__name__)

_TIMEOUT = 10.0
_ENV_VARS = ("TALARIA_SUPABASE_URL", "TALARIA_SUPABASE_KEY", "TALARIA_CLAIM_TOKEN")


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------
def _get_config() -> Optional[dict[str, str]]:
    """Resolve Talaria config from env vars.

    Requires TALARIA_SUPABASE_URL + TALARIA_SUPABASE_KEY; TALARIA_CLAIM_TOKEN
    is OPTIONAL (the v_talaria_* / v_eod_* views are anon-granted — the claim
    token is only needed for talaria-check claim validation, which these
    tools do not call). Returns a dict with keys ``supabase_url``,
    ``supabase_key``, ``claim_token`` (may be ""), or ``None`` (after logging
    ``talaria_tools_not_configured``) when a required var is missing. Never
    logs the key or claim token values — only their presence.
    """
    required = ("TALARIA_SUPABASE_URL", "TALARIA_SUPABASE_KEY")
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        log.warning(
            "talaria_tools_not_configured missing=%s",
            ",".join(missing),
        )
        return None
    return {
        "supabase_url": os.environ["TALARIA_SUPABASE_URL"].rstrip("/"),
        "supabase_key": os.environ["TALARIA_SUPABASE_KEY"],
        "claim_token": os.environ.get("TALARIA_CLAIM_TOKEN", ""),
    }


def _rest_url(base_url: str, view: str, params: Optional[dict[str, str]] = None) -> str:
    """Build a Supabase PostgREST URL for ``view`` under ``/rest/v1``.

    Tolerates a base URL that already ends in ``/rest/v1`` so callers can set
    TALARIA_SUPABASE_URL to either the project root or the REST root.
    """
    base = base_url.rstrip("/")
    if not base.endswith("/rest/v1"):
        base = base + "/rest/v1"
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    return f"{base}/{view}{query}"


# ---------------------------------------------------------------------------
# HTTP + formatting helpers
# ---------------------------------------------------------------------------
def _fetch_json(url: str, headers: dict[str, str]) -> Any:
    """GET ``url`` and return the parsed JSON body. Raises on network/HTTP/JSON errors."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _http_error(tool: str, exc: Exception) -> dict[str, Any]:
    """Translate an exception from ``_fetch_json`` into a friendly error dict."""
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            pass
        if exc.code == 404 or "does not exist" in detail:
            log.warning("%s_view_not_deployed code=%s", tool, exc.code)
            return {"error": "view not deployed (migration 110) — run Supabase migration 110 and retry"}
        log.error("%s_http_error code=%s detail=%s", tool, exc.code, detail[:300])
        return {"error": f"talaria request failed (HTTP {exc.code})"}
    if isinstance(exc, TimeoutError):
        log.error("%s_timeout timeout=%ss", tool, _TIMEOUT)
        return {"error": f"talaria request timed out after {_TIMEOUT}s"}
    if isinstance(exc, urllib.error.URLError):
        log.error("%s_network_error reason=%s", tool, str(exc.reason))
        return {"error": f"talaria unreachable: {exc.reason}"}
    if isinstance(exc, json.JSONDecodeError):
        log.error("%s_bad_json error=%s", tool, str(exc))
        return {"error": "talaria returned invalid JSON"}
    log.error("%s_failed error=%s", tool, str(exc))
    return {"error": str(exc)}


def _field(row: dict, *candidates: str, default: Any = None) -> Any:
    """Look up the first present key among ``candidates`` (case-insensitive).

    View column names are not guaranteed, so each tool maps rows defensively.
    """
    lower = {str(k).lower(): v for k, v in row.items()}
    for c in candidates:
        if c.lower() in lower:
            return lower[c.lower()]
    return default


def _fmt_cell(value: Any) -> str:
    """Format a cell value for a markdown table."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-") else "0"
    return str(value)


def _markdown_table(headers: list[str], rows: list[dict[str, Any]]) -> str:
    """Render a compact markdown table from display dicts."""
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_fmt_cell(row.get(h)) for h in headers) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------
def _talaria_health() -> dict[str, Any]:
    """Talaria signal health: per-symbol win rate, bias, profit factor, PnL.

    Reads ``v_talaria_signal_health`` (limit 20) via the Supabase REST API and
    returns a compact markdown table.
    """
    cfg = _get_config()
    if cfg is None:
        return {
            "error": "talaria not configured — set TALARIA_SUPABASE_URL, TALARIA_SUPABASE_KEY, TALARIA_CLAIM_TOKEN env vars"
        }
    headers = {"apikey": cfg["supabase_key"], "Authorization": f"Bearer {cfg['supabase_key']}"}
    url = _rest_url(
        cfg["supabase_url"],
        "v_talaria_signal_health",
        {"select": "*", "limit": "20"},
    )
    try:
        rows = _fetch_json(url, headers)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        return _http_error("talaria_health", e)
    if not isinstance(rows, list) or not rows:
        return {"view": "v_talaria_signal_health", "rows": 0, "markdown": "_no rows returned_"}
    display = []
    for row in rows:
        display.append(
            {
                "symbol": _field(row, "symbol", "ticker", "asset"),
                "n": _field(row, "n", "trades", "trade_count", "count", "num_trades"),
                "win rate": _field(row, "win_rate", "winrate", "win_ratio"),
                "bias": _field(row, "bias", "signal_bias"),
                "profit factor": _field(row, "profit_factor", "pf", "profitfactor"),
                "total PnL": _field(row, "total_pnl", "total_pn_l", "pnl", "net_pnl"),
            }
        )
    table = _markdown_table(["symbol", "n", "win rate", "bias", "profit factor", "total PnL"], display)
    return {"view": "v_talaria_signal_health", "rows": len(display), "markdown": table}


def _talaria_stats() -> dict[str, Any]:
    """Talaria portfolio stats: trades, win rate, sharpe, sortino, calmar, max_dd, PF, PnL.

    Reads ``v_talaria_portfolio_stats`` (limit 1) and returns a one-line summary.
    """
    cfg = _get_config()
    if cfg is None:
        return {
            "error": "talaria not configured — set TALARIA_SUPABASE_URL, TALARIA_SUPABASE_KEY, TALARIA_CLAIM_TOKEN env vars"
        }
    headers = {"apikey": cfg["supabase_key"], "Authorization": f"Bearer {cfg['supabase_key']}"}
    url = _rest_url(cfg["supabase_url"], "v_talaria_portfolio_stats", {"select": "*", "limit": "1"})
    try:
        rows = _fetch_json(url, headers)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        return _http_error("talaria_stats", e)
    if not isinstance(rows, list) or not rows:
        return {"view": "v_talaria_portfolio_stats", "rows": 0, "summary": "no portfolio stats yet"}
    row = rows[0]
    trades = _field(row, "trades", "trade_count", "n", "num_trades")
    win_rate = _field(row, "win_rate", "winrate", "win_ratio")
    sharpe = _field(row, "sharpe", "sharpe_ratio")
    sortino = _field(row, "sortino", "sortino_ratio")
    calmar = _field(row, "calmar", "calmar_ratio")
    max_dd = _field(row, "max_dd", "max_drawdown", "maxdd")
    profit_factor = _field(row, "profit_factor", "pf", "profitfactor")
    total_pnl = _field(row, "total_pnl", "total_pn_l", "pnl", "net_pnl")
    summary = (
        f"trades={_fmt_cell(trades)} win_rate={_fmt_cell(win_rate)} "
        f"sharpe={_fmt_cell(sharpe)} sortino={_fmt_cell(sortino)} "
        f"calmar={_fmt_cell(calmar)} max_dd={_fmt_cell(max_dd)} "
        f"profit_factor={_fmt_cell(profit_factor)} total_pnl={_fmt_cell(total_pnl)}"
    )
    return {"view": "v_talaria_portfolio_stats", "rows": 1, "summary": summary}


def _talaria_calibration() -> dict[str, Any]:
    """Talaria EOD calibration bias: last 7 days of predicted vs realized.

    Reads ``v_eod_calibration_bias`` (order day.desc limit 7) and returns a
    markdown table of day | symbol | predicted | realized | bias | status.
    """
    cfg = _get_config()
    if cfg is None:
        return {
            "error": "talaria not configured — set TALARIA_SUPABASE_URL, TALARIA_SUPABASE_KEY, TALARIA_CLAIM_TOKEN env vars"
        }
    headers = {"apikey": cfg["supabase_key"], "Authorization": f"Bearer {cfg['supabase_key']}"}
    url = _rest_url(
        cfg["supabase_url"],
        "v_eod_calibration_bias",
        {"select": "*", "order": "day.desc", "limit": "7"},
    )
    try:
        rows = _fetch_json(url, headers)
    except Exception as e:  # noqa: BLE001 - tools must never raise
        return _http_error("talaria_calibration", e)
    if not isinstance(rows, list) or not rows:
        return {"view": "v_eod_calibration_bias", "rows": 0, "markdown": "_no rows returned_"}
    display = []
    for row in rows:
        display.append(
            {
                "day": _field(row, "day", "date", "eod_date"),
                "symbol": _field(row, "symbol", "ticker", "asset"),
                "predicted": _field(row, "predicted", "pred", "predicted_bias"),
                "realized": _field(row, "realized", "actual", "realized_bias"),
                "bias": _field(row, "bias", "calibration_bias"),
                "status": _field(row, "status", "calibration_status"),
            }
        )
    table = _markdown_table(["day", "symbol", "predicted", "realized", "bias", "status"], display)
    return {"view": "v_eod_calibration_bias", "rows": len(display), "markdown": table}


# ---------------------------------------------------------------------------
# Plugin entry point
# ---------------------------------------------------------------------------
def register_tools(ctx: Any) -> None:
    """Register talaria-* tools with the Hermes agent.

    Args:
        ctx: PluginContext from hermes_cli.plugins — provides register_tool().
    """

    # --- talaria_health ---
    ctx.register_tool(
        name="talaria_health",
        toolset="trading",
        schema={
            "description": "Talaria signal health: per-symbol n, win rate, bias, profit factor, total PnL (reads Supabase view v_talaria_signal_health; env config).",
            "name": "talaria_health",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_talaria_health,
    )

    # --- talaria_stats ---
    ctx.register_tool(
        name="talaria_stats",
        toolset="trading",
        schema={
            "description": "Talaria portfolio stats: trades, win rate, sharpe, sortino, calmar, max_dd, profit factor, total PnL (reads Supabase view v_talaria_portfolio_stats; env config).",
            "name": "talaria_stats",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_talaria_stats,
    )

    # --- talaria_calibration ---
    ctx.register_tool(
        name="talaria_calibration",
        toolset="trading",
        schema={
            "description": "Talaria EOD calibration bias: last 7 days of predicted vs realized bias and status (reads Supabase view v_eod_calibration_bias; env config).",
            "name": "talaria_calibration",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_talaria_calibration,
    )


def register(ctx: Any) -> None:
    """Entry point called by the Hermes plugin loader.

    Runs once at plugin load time — registers the talaria_* in-chat tools.
    No hooks (unlike the noble-trader plugin which launches a watchdog).
    """
    log.info("talaria_tools_plugin_loaded")
    register_tools(ctx)
