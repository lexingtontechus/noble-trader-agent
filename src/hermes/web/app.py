"""
Hermes Dashboard — FastAPI web UI for monitoring the trading platform.

Pages:
  /              — Status overview (connections + recent heartbeats + ingest stats)
  /config        — Loaded config (secrets redacted)
  /heartbeats    — Recent heartbeats table
  /health        — JSON health endpoint (for monitoring/CI)
  /api/status    — JSON status (for programmatic access)

Run with:
    platform dashboard
    platform dashboard --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from hermes import __version__
from hermes.core.config import (
    HermesConfig,
    get_config_hash,
    redact_config_for_display,
)
from hermes.web.status import check_all, get_ingest_stats, get_recent_heartbeats

log = structlog.get_logger(__name__)

# Paths
WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

# Create FastAPI app
app = FastAPI(
    title="Hermes Trading Platform Dashboard",
    description="Status & monitoring for the Hermes entry/execution optimization layer",
    version=__version__,
)

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Global config + optional monitor reference
_config: HermesConfig | None = None
_monitor = None  # Set by dashboard if monitor is running in same process


def create_app(config: HermesConfig, monitor=None) -> FastAPI:
    """Configure the app with a loaded config (called by CLI)."""
    global _config, _monitor
    _config = config
    _monitor = monitor
    return app


def get_config() -> HermesConfig:
    if _config is None:
        raise RuntimeError(
            "App not configured. Call create_app(config) before serving requests."
        )
    return _config


# === Routes ===


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    """Status overview page."""
    config = get_config()
    status = await check_all(config)
    stats = get_ingest_stats(config)
    recent = get_recent_heartbeats(config, limit=20)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "status": status,
            "stats": stats,
            "recent_heartbeats": recent,
        },
    )


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """Config viewer page (secrets redacted)."""
    config = get_config()
    redacted = redact_config_for_display(config)

    # Pretty-print the JSON for display
    import json

    config_json = json.dumps(redacted, indent=2, default=str)

    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "config_json": config_json,
        },
    )


@app.get("/heartbeats", response_class=HTMLResponse)
async def heartbeats_page(
    request: Request,
    symbol: str | None = None,
    limit: int = 100,
) -> HTMLResponse:
    """Recent heartbeats table with optional symbol filter."""
    config = get_config()

    # Get more heartbeats for this page
    from hermes.web.status import get_recent_heartbeats as _get_recent

    # If symbol filter, we need a custom query — for now just get more and filter
    heartbeats = _get_recent(config, limit=max(limit, 500))
    if symbol:
        heartbeats = [h for h in heartbeats if h.get("symbol") == symbol]

    return templates.TemplateResponse(
        request,
        "heartbeats.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "heartbeats": heartbeats[:limit],
            "filter_symbol": symbol,
            "limit": limit,
            "total_shown": len(heartbeats[:limit]),
        },
    )


@app.get("/health")
async def health() -> JSONResponse:
    """JSON health endpoint (for monitoring/CI)."""
    config = get_config()
    status = await check_all(config)
    overall_ok = status["overall_ok"] == status["overall_total"]
    return JSONResponse(
        {
            "status": "healthy" if overall_ok else "degraded",
            "version": __version__,
            "checked_at": status["checked_at"],
            "subsystems": {
                s["name"]: s["status"] for s in status["subsystems"]
            },
        },
        status_code=200 if overall_ok else 503,
    )


@app.get("/api/status")
async def api_status() -> JSONResponse:
    """JSON status endpoint (for programmatic access)."""
    config = get_config()
    status = await check_all(config)
    stats = get_ingest_stats(config)
    return JSONResponse(
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "checked_at": status["checked_at"],
            "overall": status["overall"],
            "subsystems": status["subsystems"],
            "ingest_stats": stats,
        }
    )


@app.get("/api/heartbeats")
async def api_heartbeats(limit: int = 50) -> JSONResponse:
    """JSON heartbeats endpoint (for programmatic access)."""
    config = get_config()
    heartbeats = get_recent_heartbeats(config, limit=limit)
    return JSONResponse(
        {
            "count": len(heartbeats),
            "heartbeats": heartbeats,
        }
    )


@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request) -> HTMLResponse:
    """Active Price Monitor page — shows live prices, indicators, positions, events."""
    config = get_config()

    # Get recent monitor events from DuckDB
    from hermes.web.status import get_recent_monitor_events

    events = get_recent_monitor_events(config, limit=50)

    # Get live data from monitor if running in-process
    live_data = {}
    if _monitor is not None:
        live_data = {
            "stats": _monitor.get_stats(),
            "positions": [
                {
                    "position_id": p.position_id,
                    "symbol": p.symbol,
                    "direction": p.direction,
                    "qty": p.qty,
                    "entry_price": p.entry_price,
                    "stop_price": p.trailing_stop or p.stop_price,
                    "target_price": p.target_price,
                    "opened_at": str(p.opened_at),
                }
                for p in _monitor.get_positions()
            ],
            "correlation_matrix": _monitor.get_correlation_matrix(),
        }

    return templates.TemplateResponse(
        request,
        "monitor.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "events": events,
            "live_data": live_data,
            "monitor_running": _monitor is not None,
        },
    )


@app.get("/api/monitor/events")
async def api_monitor_events(limit: int = 50) -> JSONResponse:
    """JSON monitor events endpoint."""
    config = get_config()
    from hermes.web.status import get_recent_monitor_events

    events = get_recent_monitor_events(config, limit=limit)
    return JSONResponse(
        {
            "count": len(events),
            "events": events,
        }
    )


@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request) -> HTMLResponse:
    """Blended signals page — shows L4 output (entry/execution decisions)."""
    config = get_config()
    from hermes.web.status import get_recent_blended_signals

    signals = get_recent_blended_signals(config, limit=100)

    return templates.TemplateResponse(
        request,
        "signals.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "signals": signals,
        },
    )


@app.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request) -> HTMLResponse:
    """Portfolio page — shows account metrics, positions, risk decisions."""
    config = get_config()
    from hermes.web.status import get_portfolio_metrics, get_recent_risk_decisions

    metrics = get_portfolio_metrics(config)
    decisions = get_recent_risk_decisions(config, limit=50)

    return templates.TemplateResponse(
        request,
        "portfolio.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "metrics": metrics,
            "decisions": decisions,
        },
    )


@app.get("/api/portfolio")
async def api_portfolio() -> JSONResponse:
    """JSON portfolio metrics endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_portfolio_metrics

    metrics = get_portfolio_metrics(config)
    return JSONResponse(
        content=_json.loads(_json.dumps({"metrics": metrics}, default=str))
    )


@app.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request) -> HTMLResponse:
    """Orders page — shows order lifecycle + fills."""
    config = get_config()
    from hermes.web.status import get_recent_fills, get_recent_orders

    orders = get_recent_orders(config, limit=100)
    fills = get_recent_fills(config, limit=100)

    return templates.TemplateResponse(
        request,
        "orders.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "orders": orders,
            "fills": fills,
        },
    )


@app.get("/pnl", response_class=HTMLResponse)
async def pnl_page(request: Request) -> HTMLResponse:
    """PnL analytics page — tear sheet + equity curve + trade history."""
    config = get_config()
    from hermes.web.status import get_equity_curve, get_pnl_history, get_pnl_tear_sheet

    tear_sheet = get_pnl_tear_sheet(config)
    equity_curve = get_equity_curve(config, limit=500)
    pnl_history = get_pnl_history(config, limit=100)

    return templates.TemplateResponse(
        request,
        "pnl.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "tear_sheet": tear_sheet,
            "equity_curve": equity_curve,
            "pnl_history": pnl_history,
        },
    )


@app.get("/backtest", response_class=HTMLResponse)
async def backtest_page(request: Request) -> HTMLResponse:
    """Backtest results page."""
    config = get_config()
    from hermes.web.status import get_backtest_runs

    runs = get_backtest_runs(config, limit=20)

    return templates.TemplateResponse(
        request,
        "backtest.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "runs": runs,
        },
    )


@app.get("/optimize", response_class=HTMLResponse)
async def optimize_page(request: Request) -> HTMLResponse:
    """Optimization results page — shows simulation runs."""
    config = get_config()
    from hermes.web.status import get_simulation_runs

    runs = get_simulation_runs(config, limit=50)

    return templates.TemplateResponse(
        request,
        "optimize.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "runs": runs,
        },
    )


@app.get("/agent", response_class=HTMLResponse)
async def agent_page(request: Request) -> HTMLResponse:
    """Agent page — shows hypotheses + trade journal + decision tree."""
    config = get_config()
    from hermes.web.status import get_hypotheses, get_trade_journal_entries

    hypotheses = get_hypotheses(config, limit=50)
    journal = get_trade_journal_entries(config, limit=50)

    return templates.TemplateResponse(
        request,
        "agent.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "hypotheses": hypotheses,
            "journal": journal,
        },
    )


@app.get("/api/hypotheses")
async def api_hypotheses(limit: int = 50) -> JSONResponse:
    """JSON hypotheses endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_hypotheses

    hyps = get_hypotheses(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(hyps), "hypotheses": hyps},
            default=str,
        ))
    )


@app.get("/api/simulations")
async def api_simulations(limit: int = 50) -> JSONResponse:
    """JSON simulation runs endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_simulation_runs

    runs = get_simulation_runs(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(runs), "runs": runs},
            default=str,
        ))
    )


@app.get("/api/backtest/runs")
async def api_backtest_runs(limit: int = 20) -> JSONResponse:
    """JSON backtest runs endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_backtest_runs

    runs = get_backtest_runs(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(runs), "runs": runs},
            default=str,
        ))
    )


@app.get("/api/pnl/tear_sheet")
async def api_pnl_tear_sheet() -> JSONResponse:
    """JSON tear sheet endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_pnl_tear_sheet

    ts = get_pnl_tear_sheet(config)
    return JSONResponse(
        content=_json.loads(_json.dumps(ts, default=str))
    )


@app.get("/api/pnl/history")
async def api_pnl_history(limit: int = 100) -> JSONResponse:
    """JSON PnL history endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_pnl_history

    history = get_pnl_history(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(history), "history": history},
            default=str,
        ))
    )


@app.get("/api/orders")
async def api_orders(limit: int = 50) -> JSONResponse:
    """JSON orders endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_orders

    orders = get_recent_orders(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(orders), "orders": orders},
            default=str,
        ))
    )


@app.get("/api/fills")
async def api_fills(limit: int = 50) -> JSONResponse:
    """JSON fills endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_fills

    fills = get_recent_fills(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(fills), "fills": fills},
            default=str,
        ))
    )


@app.get("/api/risk/decisions")
async def api_risk_decisions(limit: int = 50) -> JSONResponse:
    """JSON risk decisions endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_risk_decisions

    decisions = get_recent_risk_decisions(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(decisions), "decisions": decisions},
            default=str,
        ))
    )


@app.get("/api/signals")
async def api_signals(limit: int = 50) -> JSONResponse:
    """JSON blended signals endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_blended_signals

    signals = get_recent_blended_signals(config, limit=limit)
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(signals), "signals": signals},
            default=str,
        ))
    )
