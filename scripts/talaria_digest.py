#!/usr/bin/env python3
"""Talaria daily digest — standalone script for a Hermes cron job.

Fetches the Talaria Supabase analytics views (signal health, portfolio stats,
calibration bias) and prints a markdown daily digest to stdout. Intended to be
run under `hermes cron` in no_agent mode, where stdout is delivered verbatim —
so this script prints ONLY the digest (no logging/debug noise) and always
exits 0, even when Talaria is not configured.

Config (environment variables):
    TALARIA_SUPABASE_URL  — Supabase project REST URL
    TALARIA_SUPABASE_KEY  — public anon key
    TALARIA_CLAIM_TOKEN   — Talaria claim token
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional

_TIMEOUT = 10.0
_ENV_VARS = ("TALARIA_SUPABASE_URL", "TALARIA_SUPABASE_KEY", "TALARIA_CLAIM_TOKEN")


# ---------------------------------------------------------------------------
# Config resolution (mirrors .hermes/plugins/talaria-tools/__init__.py)
# ---------------------------------------------------------------------------
def _get_config() -> Optional[dict[str, str]]:
    """Resolve Talaria config from env vars; None when URL or anon key is missing.

    TALARIA_CLAIM_TOKEN is OPTIONAL for view reads (the v_talaria_* / v_eod_*
    views are anon-granted) — it is only required by talaria-check claim
    validation, which these tools do not call.
    """
    required = ("TALARIA_SUPABASE_URL", "TALARIA_SUPABASE_KEY")
    missing = [v for v in required if not os.environ.get(v)]
    if missing:
        return None
    cfg: dict[str, str] = {
        "supabase_url": os.environ["TALARIA_SUPABASE_URL"].rstrip("/"),
        "supabase_key": os.environ["TALARIA_SUPABASE_KEY"],
        "claim_token": os.environ.get("TALARIA_CLAIM_TOKEN", ""),
    }
    return cfg


def _rest_url(base_url: str, view: str, params: Optional[dict[str, str]] = None) -> str:
    """Build a Supabase PostgREST URL for ``view`` under ``/rest/v1``."""
    base = base_url.rstrip("/")
    if not base.endswith("/rest/v1"):
        base = base + "/rest/v1"
    query = ""
    if params:
        query = "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    return f"{base}/{view}{query}"


def _fetch_json(url: str, headers: dict[str, str]) -> Any:
    """GET ``url`` and return the parsed JSON body. Raises on network/HTTP/JSON errors."""
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return json.loads(body)


def _fetch_view(cfg: dict[str, str], view: str, params: Optional[dict[str, str]] = None) -> list:
    """Fetch one Supabase view, raising on failure."""
    headers = {"apikey": cfg["supabase_key"], "Authorization": f"Bearer {cfg['supabase_key']}"}
    url = _rest_url(cfg["supabase_url"], view, params)
    rows = _fetch_json(url, headers)
    if not isinstance(rows, list):
        raise ValueError(f"unexpected response shape for {view}")
    return rows


def _error_text(exc: Exception) -> str:
    """One-line human-readable error message for a view fetch failure."""
    if isinstance(exc, urllib.error.HTTPError):
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover - defensive
            pass
        if exc.code == 404 or "does not exist" in detail:
            return "view not deployed (migration 110) — run Supabase migration 110"
        return f"HTTP {exc.code}"
    if isinstance(exc, TimeoutError):
        return f"timed out after {_TIMEOUT}s"
    if isinstance(exc, urllib.error.URLError):
        return f"unreachable: {exc.reason}"
    return str(exc)


def _field(row: dict, *candidates: str, default: Any = None) -> Any:
    """Look up the first present key among ``candidates`` (case-insensitive)."""
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
# Digest sections
# ---------------------------------------------------------------------------
def _portfolio_stats_section(cfg: dict[str, str]) -> str:
    """One-line portfolio stats summary."""
    rows = _fetch_view(cfg, "v_talaria_portfolio_stats", {"select": "*", "limit": "1"})
    if not rows:
        return "no portfolio stats yet"
    row = rows[0]
    trades = _field(row, "trades", "trade_count", "n", "num_trades")
    win_rate = _field(row, "win_rate", "winrate", "win_ratio")
    sharpe = _field(row, "sharpe", "sharpe_ratio")
    sortino = _field(row, "sortino", "sortino_ratio")
    calmar = _field(row, "calmar", "calmar_ratio")
    max_dd = _field(row, "max_dd", "max_drawdown", "maxdd")
    profit_factor = _field(row, "profit_factor", "pf", "profitfactor")
    total_pnl = _field(row, "total_pnl", "total_pn_l", "pnl", "net_pnl")
    return (
        f"trades={_fmt_cell(trades)} win_rate={_fmt_cell(win_rate)} "
        f"sharpe={_fmt_cell(sharpe)} sortino={_fmt_cell(sortino)} "
        f"calmar={_fmt_cell(calmar)} max_dd={_fmt_cell(max_dd)} "
        f"profit_factor={_fmt_cell(profit_factor)} total_pnl={_fmt_cell(total_pnl)}"
    )


def _signal_health_section(cfg: dict[str, str]) -> str:
    """Markdown table of per-symbol signal health."""
    rows = _fetch_view(cfg, "v_talaria_signal_health", {"select": "*", "limit": "20"})
    if not rows:
        return "_no rows returned_"
    display = [
        {
            "symbol": _field(r, "symbol", "ticker", "asset"),
            "n": _field(r, "n_resolved", "n", "trades", "trade_count", "count", "num_trades"),
            "win rate": _field(r, "win_rate", "winrate", "win_ratio"),
            "bias": _field(r, "bias", "signal_bias"),
            "profit factor": _field(r, "profit_factor", "pf", "profitfactor"),
            "total PnL": _field(r, "total_pnl", "total_pn_l", "pnl", "net_pnl"),
        }
        for r in rows
    ]
    return _markdown_table(["symbol", "n", "win rate", "bias", "profit factor", "total PnL"], display)


def _calibration_section(cfg: dict[str, str]) -> str:
    """Markdown table of EOD calibration bias (last 7 days)."""
    rows = _fetch_view(cfg, "v_eod_calibration_bias", {"select": "*", "order": "day.desc", "limit": "7"})
    if not rows:
        return "_no rows returned_"
    display = [
        {
            "day": _field(r, "day", "date", "eod_date"),
            "symbol": _field(r, "symbol", "ticker", "asset"),
            "predicted": _field(r, "avg_predicted_p_win", "predicted", "pred", "predicted_bias"),
            "realized": _field(r, "realized_win_rate", "realized", "actual", "realized_bias"),
            "bias": _field(r, "bias", "calibration_bias"),
            "status": _field(r, "status", "calibration_status"),
        }
        for r in rows
    ]
    return _markdown_table(["day", "symbol", "predicted", "realized", "bias", "status"], display)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    """Build and print the digest. Always exits 0."""
    cfg = _get_config()
    if cfg is None:
        print(
            "Talaria daily digest — not configured: set TALARIA_SUPABASE_URL, "
            "TALARIA_SUPABASE_KEY, TALARIA_CLAIM_TOKEN env vars"
        )
        return 0

    print("# Talaria daily digest")
    print()
    print("## Portfolio stats")
    try:
        print(_portfolio_stats_section(cfg))
    except Exception as e:  # noqa: BLE001 - digest must never crash
        print(f"_unavailable: {_error_text(e)}_")
    print()
    print("## Signal health")
    try:
        print(_signal_health_section(cfg))
    except Exception as e:  # noqa: BLE001 - digest must never crash
        print(f"_unavailable: {_error_text(e)}_")
    print()
    print("## Calibration bias")
    try:
        print(_calibration_section(cfg))
    except Exception as e:  # noqa: BLE001 - digest must never crash
        print(f"_unavailable: {_error_text(e)}_")
    return 0


if __name__ == "__main__":
    sys.exit(main())
