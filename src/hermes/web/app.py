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

import hmac
from pathlib import Path
from typing import Any

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from hermes.web.rate_limit_middleware import RateLimitMiddleware
from hermes.web.csrf_middleware import CSRFMiddleware
from hermes.web.csrf import get_csrf_token

from hermes import __version__
from hermes.core.config import (
    HermesConfig,
    get_config_hash,
    redact_config_for_display,
)
from hermes.core.secrets import get_secret_or_none
from hermes.web.status import check_all, get_ingest_stats, get_recent_heartbeats

log = structlog.get_logger(__name__)

# Security headers middleware
async def security_headers_middleware(request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    
    # Prevent clickjacking
    response.headers["X-Frame-Options"] = "DENY"
    
    # Prevent MIME type sniffing
    response.headers["X-Content-Type-Options"] = "nosniff"
    
    # Enable XSS protection
    response.headers["X-XSS-Protection"] = "1; mode=block"
    
    # Referrer policy
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    
    # Content Security Policy
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
    
    # Strict Transport Security (HSTS) - only for HTTPS
    if request.url.scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
    
    return response

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

# Mount React dashboard assets
DIST_DIR = Path(__file__).parent.parent.parent.parent / "dashboard" / "dist"
if DIST_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST_DIR / "assets")), name="assets")
    # Add /app route for React dashboard
    @app.get("/app", response_class=HTMLResponse)
    async def react_dashboard(request: Request) -> HTMLResponse:
        """Serve React dashboard."""
        index_path = DIST_DIR / "index.html"
        if index_path.exists():
            content = index_path.read_text()
            # Update CSP to allow inline styles and scripts
            content = content.replace(
                'default-src \'self\'; script-src \'self\' \'unsafe-inline\' https://cdn.tailwindcss.com; style-src \'self\' \'unsafe-inline\' https://cdn.tailwindcss.com;',
                'default-src \'self\'; script-src \'self\' \'unsafe-inline\' \'unsafe-eval\' https://cdn.tailwindcss.com; style-src \'self\' \'unsafe-inline\' https://cdn.tailwindcss.com;'
            )
            return HTMLResponse(content=content)
        else:
            return HTMLResponse(content="Dashboard not built. Run 'npm run build' in dashboard directory.", status_code=503)

    # Catch-all route for React app routing
    @app.get("/app/{path:path}", response_class=HTMLResponse)
    async def react_app_catchall(request: Request, path: str) -> HTMLResponse:
        """Serve React app for client-side routing."""
        index_path = DIST_DIR / "index.html"
        if index_path.exists():
            content = index_path.read_text()
            # Update CSP to allow inline styles and scripts
            content = content.replace(
                'default-src \'self\'; script-src \'self\' \'unsafe-inline\' https://cdn.tailwindcss.com; style-src \'self\' \'unsafe-inline\' https://cdn.tailwindcss.com;',
                'default-src \'self\'; script-src \'self\' \'unsafe-inline\' \'unsafe-eval\' https://cdn.tailwindcss.com; style-src \'self\' \'unsafe-inline\' https://cdn.tailwindcss.com;'
            )
            return HTMLResponse(content=content)
        else:
            return HTMLResponse(content="Dashboard not built. Run 'npm run build' in dashboard directory.", status_code=503)

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Global config + optional monitor reference
_config: HermesConfig | None = None
_monitor = None  # Set by dashboard if monitor is running in same process

# Flag to indicate React dashboard is available
_react_dashboard_available = (DIST_DIR / "index.html").exists()


def create_app(config: HermesConfig, monitor=None) -> FastAPI:
    """Configure the app with a loaded config (called by CLI)."""
    global _config, _monitor
    _config = config
    _monitor = monitor

    # Add session middleware for browser auth (signed cookies).
    # The session_secret is read from .env; fall back to a dev-only secret
    # if missing so `platform dashboard` still starts for first-time users.
    auth_cfg = config.auth if hasattr(config, "auth") else {}
    auth_enabled = auth_cfg.get("enabled", True) if isinstance(auth_cfg, dict) else True
    session_secret = (
        auth_cfg.get("session_secret") if isinstance(auth_cfg, dict) else None
    ) or get_secret_or_none("hermes.session_secret") or "dev-only-secret-change-me"
    if session_secret == "dev-only-secret-change-me":
        log.warning("auth_using_dev_session_secret", note="set HERMES_SESSION_SECRET in .env")

    # SessionMiddleware must be added BEFORE any route that uses request.session.
    # Max age defaults to 24h; can be overridden via config.
    max_age = auth_cfg.get("session_max_age_sec", 86400) if isinstance(auth_cfg, dict) else 86400
    
    # Validate session secret is configured
    if not session_secret or session_secret == "dev-only-secret-change-me":
        raise RuntimeError(
            "HERMES_SESSION_SECRET must be configured. "
            "Set it in .env or config/default.yaml → auth.session_secret. "
            "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(48))\""
        )
    
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        max_age=max_age,
        same_site="strict",     # cookie only sent on same-site requests
        https_only=True,        # Always require HTTPS for session cookies in production
    )
    
    # Add security headers middleware
    app.add_middleware(
        BaseHTTPMiddleware,
        dispatch=security_headers_middleware
    )
    
    # Add rate limiting middleware
    # Rate limits are enforced per-endpoint with venue-specific limits from config
    app.add_middleware(
        RateLimitMiddleware,
        config=config
    )
    
    # Add CSRF protection middleware
    # CSRF tokens are required for all state-changing requests (POST, PUT, DELETE, PATCH)
    # Tokens are validated against the session and must match
    app.add_middleware(
        CSRFMiddleware,
        exempt_paths=['/health', '/api/health', '/api/status'],  # Health endpoints don't need CSRF
    )

    log.info("auth_middleware_added", enabled=auth_enabled, max_age_sec=max_age)
    return app


def get_config() -> HermesConfig:
    if _config is None:
        raise RuntimeError(
            "App not configured. Call create_app(config) before serving requests."
        )
    return _config


# ============================================================
# Auth — dual-path: session cookie (browser) OR bearer token (agent)
# ============================================================


def _get_auth_settings() -> dict[str, Any]:
    """Read auth settings from config + .env. Cached per-request via lru not used
    because settings can change between requests in dev."""
    cfg = get_config()
    auth_cfg = getattr(cfg, "auth", {}) or {}
    if not isinstance(auth_cfg, dict):
        auth_cfg = {}
    
    # Extract credentials from config or secrets
    admin_username = (
        auth_cfg.get("admin_username")
        or get_secret_or_none("hermes.admin_username")
    )
    admin_password = (
        auth_cfg.get("admin_password")
        or get_secret_or_none("hermes.admin_password")
    )
    agent_token = (
        auth_cfg.get("agent_token")
        or get_secret_or_none("hermes.agent_token")
        or ""
    )
    
    # Validate required credentials
    if not admin_username:
        raise RuntimeError(
            "HERMES_ADMIN_USERNAME must be configured. "
            "Set it in .env or config/default.yaml → auth.admin_username"
        )
    if not admin_password:
        raise RuntimeError(
            "HERMES_ADMIN_PASSWORD must be configured. "
            "Set it in .env or config/default.yaml → auth.admin_password. "
            "Generate with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    
    # Validate password strength
    if len(admin_password) < 8:
        raise RuntimeError(
            f"HERMES_ADMIN_PASSWORD must be at least 8 characters (got {len(admin_password)})"
        )
    
    # Prevent default credentials
    if admin_username.lower() == "admin" and admin_password == "change-me":
        raise RuntimeError(
            "Default credentials detected. Please set unique HERMES_ADMIN_USERNAME and HERMES_ADMIN_PASSWORD"
        )
    
    return {
        "enabled": auth_cfg.get("enabled", True),
        "admin_username": admin_username,
        "admin_password": admin_password,
        "agent_token": agent_token,
    }


async def require_auth(request: Request, authorization: str | None = Header(None)) -> dict[str, Any]:
    """FastAPI dependency — authenticates every protected route.

    Two paths:
      1. Browser: reads signed session cookie set by /auth/login.
      2. Agent: reads `Authorization: Bearer <token>` header.

    Returns the authenticated principal as {"username": str, "role": "admin"|"agent"}.
    Raises HTTPException(401) if neither path succeeds.
    """
    settings = _get_auth_settings()
    if not settings["enabled"]:
        return {"username": "anonymous", "role": "admin"}

    # Path 1: session cookie
    user = request.session.get("user") if hasattr(request, "session") else None
    if user:
        return user

    # Path 2: bearer token (agent)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        # Use constant-time comparison to prevent timing attacks
        if settings["agent_token"] and hmac.compare_digest(token, settings["agent_token"]):
            return {"username": "agent", "role": "agent"}

    raise HTTPException(
        status_code=401,
        detail="Not authenticated — log in via /auth/login or send a valid Bearer token.",
        headers={"WWW-Authenticate": 'Bearer realm="hermes"'},
    )


# === Auth routes ===


@app.post("/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    """Log in with username + password. Sets a session cookie on success.

    Body: {"username": "...", "password": "..."}
    """
    settings = _get_auth_settings()
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    username = body.get("username", "")
    password = body.get("password", "")

    # Constant-time comparison on both fields to prevent username enumeration via timing
    user_ok = hmac.compare_digest(username, settings["admin_username"])
    pass_ok = hmac.compare_digest(password, settings["admin_password"])

    if not (user_ok and pass_ok):
        log.warning("auth_login_failed", username=username, ip=request.client.host if request.client else "?")
        return JSONResponse({"error": "invalid username or password"}, status_code=401)

    request.session["user"] = {"username": username, "role": "admin"}
    log.info("auth_login_ok", username=username, ip=request.client.host if request.client else "?")
    return JSONResponse({"ok": True, "user": {"username": username, "role": "admin"}})


@app.post("/auth/logout")
async def auth_logout(request: Request) -> JSONResponse:
    """Clear the session cookie."""
    user = request.session.get("user") if hasattr(request, "session") else None
    request.session.clear()
    log.info("auth_logout", username=user.get("username") if user else "?")
    return JSONResponse({"ok": True})


@app.get("/auth/me")
async def auth_me(request: Request, authorization: str | None = Header(None)) -> JSONResponse:
    """Return the current authenticated principal, or 401.

    Used by the SPA on app load to check if the user is already logged in
    (cookie sent automatically by the browser).
    """
    settings = _get_auth_settings()
    if not settings["enabled"]:
        return JSONResponse({"username": "anonymous", "role": "admin"})

    # Try session cookie
    user = request.session.get("user") if hasattr(request, "session") else None
    if user:
        return JSONResponse(user)

    # Try bearer token (agent)
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:]
        if settings["agent_token"] and hmac.compare_digest(token, settings["agent_token"]):
            return JSONResponse({"username": "agent", "role": "agent"})

    return JSONResponse({"error": "not authenticated"}, status_code=401)


# === Routes ===


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    # Redirect to React dashboard if available
    if _react_dashboard_available:
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/app/")
    
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
    """Config viewer page (secrets redacted).

    Only the operator-tunable sections are surfaced in the form layout —
    the rest (venues, upstream, duckdb, hermes_redis, notifications, logging)
    stay in the raw JSON block at the bottom for completeness.
    """
    config = get_config()
    redacted = redact_config_for_display(config)

    # Sections shown as editable-style form cards (filtered list).
    FORM_SECTIONS = [
        "portfolio",
        "account",
        "asset",
        "signal",
        "entry",
        "execution",
        "position_management",
        "circuit_breakers",
        "autonomy",
        "meta_regime",
        "renko",
    ]

    import json

    config_json = json.dumps(redacted, indent=2, default=str)

    # Build (section_name, fields) pairs where fields is a list of
    # {key, value, is_secret, is_nested} dicts. We flatten one level deep
    # so nested dicts (e.g. portfolio.target_allocation) render as labelled
    # sub-forms rather than opaque JSON blobs.
    form_sections: list[dict[str, Any]] = []
    for section_name in FORM_SECTIONS:
        section_data = redacted.get(section_name)
        if section_data is None:
            continue
        form_sections.append({
            "name": section_name,
            "data": section_data,
        })

    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "config_json": config_json,
            "form_sections": form_sections,
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
async def api_status(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_heartbeats(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_monitor_events(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_portfolio(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_hypotheses(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_simulations(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_backtest_runs(limit: int = 20, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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


@app.get("/api/backtest/runs/{run_id}")
async def api_backtest_run_detail(
    run_id: str, _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Get a single backtest run with its tear_sheet (equity curve + per-trade stats)."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_backtest_run_detail

    run = get_backtest_run_detail(config, run_id)
    if run is None:
        return JSONResponse({"error": f"Backtest run not found: {run_id}"}, status_code=404)
    return JSONResponse(content=_json.loads(_json.dumps(run, default=str)))


@app.get("/api/portfolio/var_history")
async def api_portfolio_var_history(
    limit: int = 500, _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Historical VaR + drawdown + leverage time series (from account_snapshots)."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_portfolio_var_history

    rows = get_portfolio_var_history(config, limit=limit)
    return JSONResponse(content=_json.loads(_json.dumps(
        {"count": len(rows), "history": rows}, default=str,
    )))


@app.get("/api/portfolio/exposure")
async def api_portfolio_exposure(
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Current exposure breakdown by venue + direction + asset class."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_portfolio_exposure_breakdown

    breakdown = get_portfolio_exposure_breakdown(config)
    return JSONResponse(content=_json.loads(_json.dumps(breakdown, default=str)))


@app.get("/api/agent/decision_tree")
async def api_agent_decision_tree(
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Static decision tree definition (interactive tree viz source)."""
    from hermes.web.status import get_decision_tree_definition
    return JSONResponse(get_decision_tree_definition())


@app.get("/api/agent/trade_journal")
async def api_agent_trade_journal(
    limit: int = 50, _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Trade journal entries (with postmortems + lessons)."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_trade_journal_entries

    entries = get_trade_journal_entries(config, limit=limit)
    return JSONResponse(content=_json.loads(_json.dumps(
        {"count": len(entries), "entries": entries}, default=str,
    )))


@app.get("/api/pnl/tear_sheet")
async def api_pnl_tear_sheet(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON tear sheet endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_pnl_tear_sheet

    ts = get_pnl_tear_sheet(config)
    return JSONResponse(
        content=_json.loads(_json.dumps(ts, default=str))
    )


@app.get("/api/pnl/history")
async def api_pnl_history(limit: int = 100, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_orders(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_fills(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_risk_decisions(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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
async def api_signals(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
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


# ============================================================
# Symbol Registry — /symbols page + /api/symbols CRUD
# ============================================================


@app.get("/symbols", response_class=HTMLResponse)
async def symbols_page(request: Request) -> HTMLResponse:
    """Symbol registry page — list symbols with active toggles and add form."""
    config = get_config()
    from hermes.db.symbol_registry import list_symbols

    rows = list_symbols(config)
    venues = {name: v.asset_classes for name, v in config.venues.items() if v.enabled}

    return templates.TemplateResponse(
        request,
        "symbols.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "symbols": [r.to_dict() for r in rows],
            "venues": venues,
        },
    )


@app.get("/api/symbols")
async def api_symbols_list(
    active_only: bool = False,
    venue: str | None = None,
    asset_class: str | None = None,
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """List symbols in the registry (JSON)."""
    import json as _json

    config = get_config()
    from hermes.db.symbol_registry import list_symbols

    rows = list_symbols(
        config, active_only=active_only, venue=venue, asset_class=asset_class,
    )
    return JSONResponse(
        content=_json.loads(_json.dumps(
            {"count": len(rows), "symbols": [r.to_dict() for r in rows]},
            default=str,
        ))
    )


@app.get("/api/symbols/{symbol}")
async def api_symbols_get(symbol: str, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Fetch a single symbol by name."""
    import json as _json

    config = get_config()
    from hermes.db.symbol_registry import get_symbol

    row = get_symbol(config, symbol)
    if row is None:
        return JSONResponse(
            {"error": f"Symbol not found: {symbol}"}, status_code=404,
        )
    return JSONResponse(content=_json.loads(_json.dumps(row.to_dict(), default=str)))


@app.post("/api/symbols")
async def api_symbols_add(request: Request, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Add a new symbol. Body: {symbol, venue, asset_class, base_ccy?, ...}."""
    config = get_config()
    from hermes.db.symbol_registry import add_symbol

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    required = ("symbol", "venue", "asset_class")
    missing = [f for f in required if not body.get(f)]
    if missing:
        return JSONResponse(
            {"error": f"missing required field(s): {missing}"}, status_code=400,
        )

    try:
        row = add_symbol(
            config,
            body["symbol"],
            body["venue"],
            body["asset_class"],
            base_ccy=body.get("base_ccy"),
            quote_ccy=body.get("quote_ccy", "USD"),
            tick_size=body.get("tick_size"),
            min_notional=body.get("min_notional"),
            max_leverage=body.get("max_leverage"),
            added_by="dashboard",
            rationale=body.get("rationale"),
            activate=not body.get("inactive", False),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    return JSONResponse(row.to_dict(), status_code=201)


@app.post("/api/symbols/{symbol}/activate")
async def api_symbols_activate(symbol: str, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Activate a previously deactivated symbol."""
    config = get_config()
    from hermes.db.symbol_registry import activate_symbol

    try:
        row = activate_symbol(config, symbol, activated_by="dashboard")
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(row.to_dict())


@app.post("/api/symbols/{symbol}/deactivate")
async def api_symbols_deactivate(request: Request, symbol: str, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Deactivate a symbol (soft-delete). Optional body: {reason: ...}."""
    config = get_config()
    from hermes.db.symbol_registry import deactivate_symbol

    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = body.get("reason") if isinstance(body, dict) else None

    try:
        row = deactivate_symbol(
            config, symbol, deactivated_by="dashboard", rationale=reason,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(row.to_dict())


@app.post("/api/symbols/{symbol}/validate")
async def api_symbols_validate(symbol: str, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Live-test that the venue can fetch a price for this symbol."""
    config = get_config()
    from hermes.db.symbol_registry import validate_symbol

    try:
        row = validate_symbol(config, symbol)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return JSONResponse(row.to_dict())


@app.post("/api/symbols/sync")
async def api_symbols_sync(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """Seed the symbols table from config/default.yaml.initial_symbols."""
    config = get_config()
    from hermes.db.symbol_registry import seed_from_config

    inserted = seed_from_config(config, added_by="dashboard")
    return JSONResponse({"inserted": inserted})


# === CSRF Token Endpoint ===


@app.get("/api/csrf/token")
async def get_csrf_token_endpoint(
    request: Request,
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Get a fresh CSRF token for form submissions.
    
    Returns a new CSRF token that should be included in POST/PUT/DELETE requests.
    The token is tied to the current session and expires after 1 hour.
    """
    from hermes.web.csrf import get_csrf_token as csrf_generate
    
    # Get session ID from request
    session_id = _get_session_id(request)
    if not session_id:
        return JSONResponse({"error": "No session found"}, status_code=401)
    
    token = csrf_generate(session_id)
    return JSONResponse({"csrf_token": token})


def _get_session_id(request: Request) -> str | None:
    """Extract session ID from request for CSRF token generation."""
    if hasattr(request, 'session'):
        session = request.session
        if isinstance(session, dict) and 'user' in session:
            user = session.get('user')
            if isinstance(user, dict) and 'username' in user:
                return f"user:{user['username']}"
    
    # Check for session cookie
    session_id = request.cookies.get('session')
    if session_id:
        return f"session:{session_id}"
    
    # Check for bearer token (agent auth)
    auth_header = request.headers.get('Authorization')
    if auth_header and auth_header.startswith('Bearer '):
        import hashlib
        token = auth_header[7:]
        token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
        return f"agent:{token_hash}"
    
    return None
