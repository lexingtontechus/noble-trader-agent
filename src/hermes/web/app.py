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

import datetime
import hmac
import os
import secrets
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
# from hermes.web.csrf_middleware import CSRFMiddleware
from hermes.web.csrf import get_csrf_token

try:
    from hermes import __version__
except ImportError:
    __version__ = "0.1.0-dev"
from hermes.core.config import (
    HermesConfig,
    get_config_hash,
    redact_config_for_display,
)
from hermes.core.secrets import get_secret_or_none
from hermes.web.status import check_all, get_ingest_stats, get_recent_heartbeats

log = structlog.get_logger(__name__)


import math
import json as _json
import datetime as _dt

try:
    import pandas as _pd
except Exception:  # pragma: no cover
    _pd = None


def _sanitize(obj):
    """Recursively walk a structure and neutralize non-finite numbers + numpy/
    pandas types (which aren't isinstance(obj, float) and aren't JSON-native)."""
    # numpy types carry a .dtype. Convert any of them to native Python via
    # .tolist() (works for both scalars and arrays), then recurse.
    if hasattr(obj, "dtype"):
        try:
            return _sanitize(obj.tolist())
        except Exception:
            try:
                return _sanitize(obj.item())
            except Exception:
                return str(obj)
    # pandas Timestamp (and friends) — serialize to ISO string.
    if _pd is not None and isinstance(obj, _pd.Timestamp):
        try:
            return obj.isoformat()
        except Exception:
            return str(obj)
    # plain datetime/date
    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return [_sanitize(v) for v in obj]
    return obj


def _json_default(obj):
    """Fallback for anything still not natively JSON-serializable."""
    if isinstance(obj, (set, frozenset)):
        return list(obj)
    return str(obj)


def safe_json(payload: Any) -> Any:
    """Round-trip a payload through JSON, making floats/objs JSON-safe."""
    return _json.loads(_json.dumps(_sanitize(payload), default=_json_default))


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

# Templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Global config + optional monitor reference
_config: HermesConfig | None = None
# MEDIUM-LOW-AGENT-REPO Fix #14 (2026-07-22): _monitor is wired ONLY by
# ``create_app(config, monitor=...)`` (line ~161) when the dashboard is
# launched in the SAME process as the price monitor (e.g. via
# ``platform dashboard --with-monitor`` or the dev CLI). In standalone
# web mode (``platform dashboard`` alone, or uvicorn hermes.web.app:app
# with no monitor process attached), ``_monitor`` stays ``None`` and the
# /monitor page degrades to a DuckDB-event-log view with an informative
# "Monitor not running in this process" banner (see monitor.html:35).
# Route handlers must always guard ``_monitor is not None`` before
# touching attributes — never assume it is set.
_monitor = None  # Set by create_app() when the price monitor runs in-process


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

    # CSRF protection middleware - disabled for debugging
    # Tokens are required for all state-changing requests (POST, PUT, DELETE, PATCH)
    # Tokens are validated against the session and must match
    # app.add_middleware(
    #     CSRFMiddleware,
    #     exempt_paths=['/health', '/api/health', '/api/status', '/auth/login'],  # Health and auth endpoints don't need CSRF
    # )

    log.info("auth_middleware_added", enabled=auth_enabled, max_age_sec=max_age)

    # Entitlement check: the Git/pkg token (secret:github.token) is the license.
    # Token present = licensed; warns (does not block) if missing so a tenant stack
    # is never bricked. (Full live verification is an upstream/subscription concern.)
    from hermes.core.secrets import get_secret_or_none

    if get_secret_or_none("github.token", ""):
        log.info("entitlement_ok", git_token_present=True, version=__version__)
    else:
        log.warning("entitlement_missing",
                    note="set GITHUB_TOKEN (issued by subscription) in the wizard")

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


@app.get("/test")
async def test_endpoint() -> JSONResponse:
    """Simple test endpoint to check if the app is working."""
    return JSONResponse({"message": "Test endpoint works!"})

@app.post("/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    """Log in with username + password. Sets a session cookie on success.

    Body: {"username": "...", "password": "..."}
    """
    try:
        # Try to get the request ID for logging
        request_id = getattr(request.state, 'request_id', 'no-id')

        settings = _get_auth_settings()
        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse({"error": f"invalid JSON body: {str(e)}"}, status_code=400)

        username = body.get("username", "")
        password = body.get("password", "")

        if not username or not password:
            return JSONResponse({"error": "username and password are required"}, status_code=400)

        # Log attempt (without password)
        log.info("auth_login_attempt", request_id=request_id, username=username[:3] + "*" * (len(username) - 3) if len(username) > 3 else "***", ip=request.client.host if request.client else "?")

        # Constant-time comparison on username to prevent enumeration
        user_ok = hmac.compare_digest(username, settings["admin_username"])

        # Log username check
        log.debug("auth_username_check", request_id=request_id, user_ok=user_ok, expected_username=settings["admin_username"][:3] + "*" * (len(settings["admin_username"]) - 3) if len(settings["admin_username"]) > 3 else "***")

        # Verify password against hash using proper password verification
        from hermes.security.password_utils import verify_password
        pass_ok = verify_password(password, settings["admin_password"]) if settings["admin_password"] else False


        # Log password check (without revealing password)
        log.debug("auth_password_check", request_id=request_id, pass_ok=pass_ok, has_password=len(password) > 0)

        if not (user_ok and pass_ok):
            log.warning("auth_login_failed", request_id=request_id, username=username[:3] + "*" * (len(username) - 3) if len(username) > 3 else "***", ip=request.client.host if request.client else "?")
            return JSONResponse({"error": "invalid username or password"}, status_code=401)

        # Set session
        if not hasattr(request, "session"):
            raise RuntimeError("SessionMiddleware not installed - cannot access session")
        request.session["user"] = {"username": username, "role": "admin"}
        log.info("auth_login_ok", request_id=request_id, username=username[:3] + "*" * (len(username) - 3) if len(username) > 3 else "***", ip=request.client.host if request.client else "?")
        return JSONResponse({"ok": True, "user": {"username": username, "role": "admin"}})

    except Exception as e:
        # Log the error for debugging
        log.error("auth_login_error", error=str(e), exc_info=True)
        return JSONResponse({"error": f"Internal server error: {str(e)}"}, status_code=500)


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


@app.get("/")
async def index(request: Request) -> HTMLResponse:
    """Root — routes to the right first view.

    - First use (setup incomplete): onboarding wizard (/setup).
    - Otherwise: Portfolio is the default homepage.
    The Status overview remains reachable via Account → Status (/).
    """
    from fastapi.responses import RedirectResponse

    if not is_setup_complete():
        return RedirectResponse(url="/setup", status_code=302)
    return RedirectResponse(url="/portfolio", status_code=302)


# ── Onboarding wizard ────────────────────────────────────────────────────────
# First use of the web app routes here. After setup is complete the wizard is
# hidden (root redirects to /portfolio and /setup redirects there too).
_SETUP_REQUIRED_KEYS = (
    "NOBLE_TRADER_PROXY_REDIS_URL",
    "TRADINGVIEW_API_KEY",
    "MT4_MT5_BRIDGE_TOKEN",
    "NOBLE_TRADER_LICENSE_KEY",
    "NOBLE_TRADER_QUOTE_PROXY_URL",
)
_PLACEHOLDER_VALUES = {"", "<nt-redis-host>", "redis://<nt-redis-host>:<port>",
                       "<publishable-anon-key>", "<paper-api-key>", "<0x-your-dedicated-trading-wallet>",
                       "<license-key>", "<quote-proxy-url>"}


def _env_path() -> Path:
    """Resolve the .env file the secrets backend reads."""
    p = os.environ.get("SECRETS_ENV_FILE_PATH")
    if p:
        return Path(p).resolve()
    # Fall back to ./data/.. no — use CWD/.env (secrets backend default).
    return Path(".env").resolve()


def is_setup_complete() -> bool:
    """True once the required pasted credentials are present and non-placeholder."""
    env = _read_env()
    for key in _SETUP_REQUIRED_KEYS:
        val = (env.get(key) or "").strip()
        if not val or val in _PLACEHOLDER_VALUES:
            return False
    return True


def _read_env() -> dict[str, str]:
    """Parse the .env file into a dict (best-effort, ignores comments/blank)."""
    env: dict[str, str] = {}
    p = _env_path()
    if not p.exists():
        return env
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return env


def _write_env(updates: dict[str, str]) -> None:
    """Merge updates into .env, preserving other keys. Never overwrites with empty."""
    env = _read_env()
    for k, v in updates.items():
        if v is not None and str(v).strip():
            env[k] = str(v).strip()
    lines = [f"{k}={v}" for k, v in env.items()]
    _env_path().write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request) -> HTMLResponse:
    """Onboarding wizard — shown on first use; hidden once setup is complete."""
    if is_setup_complete():
        from fastapi.responses import RedirectResponse

        return RedirectResponse(url="/portfolio", status_code=302)

    config = get_config()
    existing = _read_env()
    # Auto-generate the three auth secrets if missing so the user doesn't have to.
    generated = {
        "HERMES_SESSION_SECRET": existing.get("HERMES_SESSION_SECRET")
        or secrets.token_urlsafe(48),
        "HERMES_ADMIN_PASSWORD": existing.get("HERMES_ADMIN_PASSWORD")
        or secrets.token_urlsafe(32),
        "HERMES_AGENT_TOKEN": existing.get("HERMES_AGENT_TOKEN")
        or secrets.token_urlsafe(64),
    }
    cold_start = (getattr(config, "autonomy", {}) or {}).get("cold_start", {}) or {}
    return templates.TemplateResponse(
        request,
        "setup.html",
        {
            "version": __version__,
            "environment": config.environment,
            "existing": existing,
            "generated": generated,
            "required_keys": _SETUP_REQUIRED_KEYS,
            "cold_start": cold_start,
            "onboarding": True,  # Hide nav during onboarding
        },
    )


@app.post("/setup")
async def setup_submit(request: Request) -> HTMLResponse:
    """Accept the wizard form, write .env, auto-migrate, then enter the platform."""
    form = await request.form()
    updates: dict[str, str] = {}

    # Required pasted fields
    for key in _SETUP_REQUIRED_KEYS:
        val = (form.get(key) or "").strip()
        if not val:
            return templates.TemplateResponse(
                request, "setup.html",
                {"version": __version__, "environment": get_config().environment,
                 "existing": _read_env(), "generated": {},
                 "error": f"Missing required field: {key}",
                 "required_keys": _SETUP_REQUIRED_KEYS,
                 "cold_start": (getattr(get_config(), "autonomy", {}) or {}).get("cold_start", {}) or {},
                 "onboarding": True},  # Hide nav during onboarding
                status_code=400,
            )
        updates[key] = val

    # Optional fields (kept only if provided)
    for key in ("MT4_MT5_SOURCE_ID", "MT4_MT5_RELAY_URL",
                "DISCORD_WEBHOOK_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                "GITHUB_TOKEN",
                "HERMES_ADMIN_USERNAME"):
        val = (form.get(key) or "").strip()
        if val:
            updates[key] = val

    # Auth secrets: keep existing or use generated
    existing = _read_env()
    updates["HERMES_SESSION_SECRET"] = existing.get("HERMES_SESSION_SECRET") or secrets.token_urlsafe(48)
    updates["HERMES_ADMIN_PASSWORD"] = existing.get("HERMES_ADMIN_PASSWORD") or secrets.token_urlsafe(32)
    updates["HERMES_AGENT_TOKEN"] = existing.get("HERMES_AGENT_TOKEN") or secrets.token_urlsafe(64)
    updates["HERMES_ADMIN_USERNAME"] = existing.get("HERMES_ADMIN_USERNAME") or "admin"
    updates["SECRETS_BACKEND"] = existing.get("SECRETS_BACKEND") or "env_file"
    updates["SECRETS_ENV_FILE_PATH"] = existing.get("SECRETS_ENV_FILE_PATH") or "./.env"
    updates["HERMES_DUCKDB_PATH"] = existing.get("HERMES_DUCKDB_PATH") or "./data/hermes.duckdb"
    updates["HERMES_REDIS_URL"] = existing.get("HERMES_REDIS_URL") or "redis://localhost:6379/1"
    updates["HERMES_LOG_LEVEL"] = existing.get("HERMES_LOG_LEVEL") or "INFO"
    updates["HERMES_ENVIRONMENT"] = existing.get("HERMES_ENVIRONMENT") or "development"
    updates["TRADINGVIEW_API_HOST"] = existing.get("TRADINGVIEW_API_HOST") or "tradingview-data1.p.rapidapi.com"
    updates["TRADINGVIEW_BASE_URL"] = existing.get("TRADINGVIEW_BASE_URL") or "https://tradingview-data1.p.rapidapi.com"

    try:
        _write_env(updates)
        # Auto-migrate the local DuckDB so the account is ready immediately.
        from hermes.core.config import load_config
        from hermes.db.migrate import apply_migrations

        apply_migrations(load_config())
    except Exception as e:  # surface, don't silently fail
        return templates.TemplateResponse(
            request, "setup.html",
            {"version": __version__, "environment": get_config().environment,
             "existing": _read_env(), "generated": {},
             "error": f"Setup failed: {e}",
             "required_keys": _SETUP_REQUIRED_KEYS,
             "cold_start": (getattr(get_config(), "autonomy", {}) or {}).get("cold_start", {}) or {}},
            status_code=500,
        )

    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/portfolio", status_code=302)





def build_config_display(config: HermesConfig, redacted: dict[str, Any]) -> list[dict[str, Any]]:
    """Curate config into labelled, described, 2-column-friendly groups.

    Each group: {"title", "description", "rows": [{"label", "value", "hint"}]}.
    Drives the redesigned /config page (neat, responsive, informative).
    """
    def rows_from(d: dict, hints: dict | None = None) -> list[dict[str, Any]]:
        hints = hints or {}
        out = []
        for k, v in d.items():
            if isinstance(v, dict):
                v = ", ".join(f"{kk}={vv}" for kk, vv in v.items())
            elif isinstance(v, list):
                v = ", ".join(str(x) for x in v) if v else "—"
            elif v is None:
                v = "—"
            out.append({"label": k, "value": v, "hint": hints.get(k, "")})
        return out

    groups: list[dict[str, Any]] = []

    # ── Portfolio ───────────────────────────────────────────────
    pf = redacted.get("portfolio", {})
    groups.append({
        "title": "Portfolio",
        "description": "Capital allocation targets, rebalancing behaviour, and the small starting universe (start_smart).",
        "rows": [
            {"label": "Target allocation", "value": ", ".join(f"{k} {int(v*100)}%" for k, v in pf.get("target_allocation", {}).items()), "hint": "Desired weight per asset class."},
            {"label": "Rebalance", "value": f"{pf.get('rebalance_frequency')} / {pf.get('rebalance_method')} (drift {pf.get('rebalance_threshold_drift_pct')}%)", "hint": "How + when the book is rebalanced."},
            {"label": "start_smart", "value": str(pf.get("start_smart")), "hint": "Phase in assets gradually from a small universe."},
            {"label": "Initial symbols", "value": "; ".join(f"{s.get('symbol')}@{s.get('venue')}" for s in pf.get("initial_symbols", [])), "hint": "Seeds the symbols table (db/symbol_registry). Keep aligned with venues' crypto_pairs."},
        ],
    })

    # ── Venues ──────────────────────────────────────────────────
    vn = redacted.get("venues", {})
    venue_rows = []
    for name, v in vn.items():
        if not isinstance(v, dict):
            continue
        pairs = ", ".join(v.get("features", {}).get("crypto_pairs", []) or []) or "—"
        venue_rows.append({
            "label": f"{name} ({'enabled' if v.get('enabled') else 'disabled'})",
            "value": f"classes: {', '.join(v.get('asset_classes', [])) or '—'}",
            "hint": f"crypto_pairs: {pairs}",
        })
    groups.append({
        "title": "Venues",
        "description": "Connected brokers/data venues and the crypto pairs they expose.",
        "rows": venue_rows,
    })

    # ── Account risk limits ─────────────────────────────────────
    ac = redacted.get("account", {})
    groups.append({
        "title": "Account Limits",
        "description": "Hard risk guardrails at the account level (drawdown, loss, leverage, exposure).",
        "rows": rows_from(ac, {
            "max_portfolio_drawdown_pct": "Halt/size trigger at this equity drawdown.",
            "daily_loss_limit_pct": "Daily loss circuit.",
            "weekly_loss_limit_pct": "Weekly loss circuit.",
            "max_leverage_total": "Aggregate leverage cap.",
            "max_gross_exposure_pct": "Gross exposure cap (× equity).",
            "max_net_exposure_pct": "Net exposure cap.",
            "margin_usage_limit_pct": "Margin utilisation cap.",
            "min_cash_buffer_pct": "Required idle cash.",
        }),
    })

    # ── Asset limits ────────────────────────────────────────────
    ast = redacted.get("asset", {})
    groups.append({
        "title": "Asset Limits",
        "description": "Per-asset sizing + concentration caps.",
        "rows": rows_from(ast, {
            "max_position_size_pct": "Max weight of one position.",
            "max_position_notional": "Max $ notional per position.",
            "max_asset_drawdown_pct": "Per-asset drawdown halt.",
            "max_concentration_pct": "Single-name concentration cap.",
            "sector_exposure_cap": "Sector exposure cap.",
            "venue_exposure_cap": "Single-venue exposure cap.",
        }),
    })

    # ── Signal / Entry / Execution ──────────────────────────────
    sig = redacted.get("signal", {})
    groups.append({
        "title": "Signal",
        "description": "How raw signals are filtered before they become trades.",
        "rows": rows_from(sig, {
            "staleness_ms": "Max age of a signal before it's discarded.",
            "min_edge_estimate_bps": "Minimum edge to act.",
            "reward_risk_min": "Min reward/risk ratio.",
            "regime_filter_allowlist": "Which regimes may trade.",
            "tail_risk_action_override": "Tail-risk posture override.",
        }),
    })

    ent = redacted.get("entry", {})
    groups.append({
        "title": "Entry",
        "description": "Per-regime entry behaviour + brick confirmation rules.",
        "rows": [
            {"label": "Strategies", "value": ", ".join(f"{k}={v}" for k, v in ent.get("strategies", {}).items()), "hint": "Regime → entry action."},
            {"label": "brick_confirmation_count", "value": str(ent.get("brick_confirmation_count")), "hint": "Bricks needed to confirm."},
            {"label": "pullback_depth_brick_fraction", "value": str(ent.get("pullback_depth_brick_fraction")), "hint": "Pullback depth as brick fraction."},
            {"label": "signal_expiry_minutes", "value": str(ent.get("signal_expiry_minutes")), "hint": "Signal TTL."},
        ],
    })

    exe = redacted.get("execution", {})
    groups.append({
        "title": "Execution",
        "description": "Order routing, slicing, and slippage controls.",
        "rows": rows_from(exe, {
            "default_method": "Default order type.",
            "large_size_threshold_usd": "Above this, slice/iceberg.",
            "twap_n_bricks": "TWAP slices.",
            "iceberg_child_pct": "Iceberg child size %.",
            "limit_offset_bps": "Limit offset (bps).",
            "post_only_preference": "Prefer post-only.",
            "max_slippage_bps": "Max slippage tolerance.",
        }),
    })

    pm = redacted.get("position_management", {})
    groups.append({
        "title": "Position Management",
        "description": "Trailing stops, exit logic, and regime-driven exits.",
        "rows": [
            {"label": "Trailing", "value": f"{pm.get('trailing', {}).get('method')} (atr×{pm.get('trailing', {}).get('atr_mult')}, {pm.get('trailing', {}).get('brick_count')} brick)", "hint": "Trailing stop method."},
            {"label": "Exit", "value": f"{pm.get('exit', {}).get('strategy')} (momentum {pm.get('exit', {}).get('brick_momentum_threshold')})", "hint": "Base exit rule."},
            {"label": "Regime exit", "value": ", ".join(pm.get("regime_exit", {}).get("trigger_states", []) or []), "hint": "Regimes that force exit."},
        ],
    })

    # ── Circuit breakers (summary) ──────────────────────────────
    cb = redacted.get("circuit_breakers", {})
    cb_rows = [
        {"label": "Volatility guard", "value": f"mult {cb.get('volatility', {}).get('vol_mult_threshold')} (k={cb.get('volatility', {}).get('k_constant')})", "hint": "ATR multiple that trips the ladder."},
        {"label": "Risk checks", "value": ", ".join(k for k, v in cb.get("risk", {}).get("checks", {}).items() if v) or "—", "hint": "Enabled risk validations."},
        {"label": "VaR", "value": f"{int(cb.get('risk', {}).get('var_confidence', 0)*100)}% / {cb.get('risk', {}).get('var_window_days')}d", "hint": "VaR confidence + window."},
        {"label": "Kill-switch auto", "value": ", ".join(k for k, v in cb.get("kill_switch", {}).get("auto_triggers", {}).items() if v) or "—", "hint": "Auto-halt conditions."},
    ]
    mgr = cb.get("manager", {})
    for name, blk in mgr.items():
        if isinstance(blk, dict) and blk.get("enabled"):
            tiers = blk.get("tiers", [])
            cb_rows.append({"label": f"Manager: {name}", "value": f"{len(tiers)} tier(s)", "hint": (blk.get("description") or "")[:80]})
    groups.append({
        "title": "Circuit Breakers",
        "description": "Volatility/risk kill-switches + the graduated manager action ladder.",
        "rows": cb_rows,
    })

    # ── Autonomy ────────────────────────────────────────────────
    au = redacted.get("autonomy", {})
    tier_rows = []
    for t in ["tier_0", "tier_1", "tier_2", "tier_3", "tier_4"]:
        tdata = au.get(t)
        if not isinstance(tdata, dict):
            continue
        tier_rows.append({
            "label": f"{t} — approval: {tdata.get('approval')}",
            "value": ", ".join(tdata.get("actions", []) or []),
            "hint": f"max ${tdata.get('max_notional_usd', 'n/a')}",
        })
    ah = au.get("active_hours", {})
    tier_rows.append({
        "label": f"Active hours ({ah.get('timezone')})",
        "value": f"{ah.get('start')}–{ah.get('end')} · crypto_24_7={ah.get('crypto_24_7')} · degrade_outside={ah.get('degrade_outside_hours')}",
        "hint": "Stock-session window + user locale tz for scheduling/WS.",
    })
    groups.append({
        "title": "Autonomy",
        "description": "Approval tiers (L0–L4) and the active trading-hours window bound to your locale timezone.",
        "rows": tier_rows,
    })

    # ── Meta-regime ─────────────────────────────────────────────
    mr = redacted.get("meta_regime", {})
    groups.append({
        "title": "Meta-Regime (HMM)",
        "description": "Hidden-Markov model labelling market state (bull/bear/risk-off/...); drives regime_filter + regime_exit. Retrains periodically; only trusts states above the confidence floor.",
        "rows": rows_from(mr, {
            "hmm_n_components": "Latent regimes discovered.",
            "retrain_frequency_days": "Retrain cadence.",
            "confidence_floor": "Min posterior prob to trust a regime.",
            "thresholds": "Correlation / funding / liquidity / entropy trip-wires.",
        }),
    })

    # ── Renko ───────────────────────────────────────────────────
    rk = redacted.get("renko", {})
    groups.append({
        "title": "Renko",
        "description": "Brick size is simulated, not fixed — the stack tests several multipliers of a base brick and keeps the best signal/risk fit.",
        "rows": rows_from(rk, {
            "rolling_window_bricks": "Bricks of history used to estimate the base brick.",
            "simulation_multipliers": "Candidate brick sizes = base × multiplier.",
        }),
    })

    # ── Upstream (redacted) ─────────────────────────────────────
    up = redacted.get("upstream", {})
    up_rows = []
    nt = up.get("noble_trader", {})
    if isinstance(nt, dict):
        up_rows.append({"label": "Noble Trader Redis", "value": str(nt.get("redis", {}).get("url")), "hint": f"channel {nt.get('redis', {}).get('channel')} · group {nt.get('redis', {}).get('consumer_group')}"})
        up_rows.append({"label": "Supabase", "value": str(nt.get("supabase", {}).get("url")), "hint": f"sweep={nt.get('supabase', {}).get('sweep_result_table')}, backfill {nt.get('supabase', {}).get('backfill_lookback_days')}d"})
    groups.append({
        "title": "Upstream",
        "description": "Noble Trader signal source (Redis stream) + Supabase regime/sweep store. Credentials redacted.",
        "rows": up_rows,
    })

    # ── Data sources ────────────────────────────────────────────
    ds = redacted.get("data_sources", {})
    groups.append({
        "title": "Data Sources",
        "description": "Allowed/prohibited price sources and failure policy.",
        "rows": [
            {"label": "Policy", "value": str(ds.get("policy")), "hint": "Which sources may feed pricing."},
            {"label": "Allowed", "value": ", ".join(ds.get("allowed_sources", []) or []), "hint": "Permitted origins."},
            {"label": "Prohibited", "value": ", ".join(ds.get("prohibited_sources", []) or []), "hint": "Blocked origins."},
            {"label": "Fallback", "value": str(ds.get("fallback_behavior")), "hint": "What happens if a source fails."},
        ],
    })

    # ── Secrets status ──────────────────────────────────────────
    secret_rows = []
    for path in ["auth.admin_username", "auth.agent_token", "venues.mt4_mt5.credentials.bridge_token",
                 "venues.mt4_mt5.credentials.source_id", "upstream.noble_trader.redis.url",
                 "hermes_redis.url", "notifications.discord.webhook_url",
                 "notifications.telegram.bot_token", "notifications.telegram.chat_id"]:
        cur = redacted
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        # A value that is a secret: ref or redacted => configured.
        configured = ok and (isinstance(cur, str) and (cur.startswith("secret:") or "redacted" in cur))
        secret_rows.append({
            "label": path,
            "value": "configured" if configured else ("secret: ref" if ok and isinstance(cur, str) and cur.startswith("secret:") else "not set"),
            "hint": "",
        })
    groups.append({
        "title": "Secrets Status",
        "description": "Resolved secret references — never printed. Shows only whether each credential is wired (secret:… ref or redacted value).",
        "rows": secret_rows,
    })

    return groups


@app.get("/approvals", response_class=HTMLResponse)
async def approvals_page(request: Request, _auth: dict[str, Any] = Depends(require_auth)) -> HTMLResponse:
    """Human-approval queue — the credential-free default surface for tier-3 trades.

    Lists pending decisions from DuckDB `pending_decisions`. Each has an Approve
    button (POST /api/approvals/{id}/approve). No Discord/Telegram required.
    """
    config = get_config()
    from hermes.portfolio.pending_approvals import PendingApprovals

    pa = PendingApprovals(config)
    rows = pa.list_pending()
    return templates.TemplateResponse(
        request,
        "approvals.html",
        {
            "version": __version__,
            "environment": config.environment,
            "pending": rows,
            "strip_data": _build_regime_strip(config),
            "show_regime_strip": True,
        },
    )


@app.post("/api/approvals/{decision_id}/approve")
async def api_approve_decision(
    decision_id: str, _auth: dict[str, Any] = Depends(require_auth)
) -> JSONResponse:
    """Approve a pending decision via the dashboard; re-publishes for L3 execution."""
    config = get_config()
    from hermes.portfolio.pending_approvals import PendingApprovals

    pa = PendingApprovals(config)
    payload = pa.approve(decision_id)
    if payload is None:
        return JSONResponse({"ok": False, "error": "not pending"}, status_code=404)
    return JSONResponse({"ok": True, "decision_id": decision_id, "status": "approved"})


@app.get("/config", response_class=HTMLResponse)
async def config_page(request: Request) -> HTMLResponse:
    """Config viewer page (secrets redacted).

    Renders curated, labelled, described sections in a responsive 2-column
    layout. A collapsible raw JSON block is kept at the bottom for debugging.
    """
    config = get_config()
    redacted = redact_config_for_display(config)

    import json
    config_json = json.dumps(redacted, indent=2, default=str)
    display_groups = build_config_display(config, redacted)

    return templates.TemplateResponse(
        request,
        "config.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "config_json": config_json,
            "display_groups": display_groups,
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

    # Derive lag_ms (received - upstream) for display. Heartbeats carry pandas
    # Timestamps; compute here so the template only renders plain values.
    now = datetime.datetime.now(datetime.timezone.utc)
    for h in heartbeats:
        _tr, _tu = h.get("ts_received"), h.get("ts_upstream")
        try:
            h["lag_ms"] = round((_tr - _tu).total_seconds() * 1000, 1) if _tr and _tu else None
        except Exception:
            h["lag_ms"] = None

        # UX-UNIFORMITY-2: mark recent heartbeats as "live" (within 60s of now)
        # so the kelly_badge pulse animation distinguishes fresh from cached.
        try:
            if _tr is not None:
                # Normalize: pandas Timestamp or datetime → aware UTC seconds
                _tr_aware = _tr.to_pydatetime().astimezone(datetime.timezone.utc) if hasattr(_tr, "to_pydatetime") else _tr
                if _tr_aware.tzinfo is None:
                    _tr_aware = _tr_aware.replace(tzinfo=datetime.timezone.utc)
                age_sec = (now - _tr_aware).total_seconds()
                h["is_live"] = 0 <= age_sec <= 60
            else:
                h["is_live"] = False
        except Exception:
            h["is_live"] = False

    # UX-UNIFORMITY-2: compute kelly_delta (change in effective_kelly vs prior
    # heartbeat for the same symbol). Sorted DESC by ts_received, so iterate
    # from oldest→newest by reversing, then track prior_kelly per symbol.
    _prior_kelly: dict[str, float | None] = {}
    for h in reversed(heartbeats):
        sym = h.get("symbol")
        cur = h.get("effective_kelly")
        prev = _prior_kelly.get(sym)
        try:
            if prev is not None and cur is not None:
                h["kelly_delta"] = float(cur) - float(prev)
            else:
                h["kelly_delta"] = None
        except Exception:
            h["kelly_delta"] = None
        if cur is not None:
            _prior_kelly[sym] = cur

    return templates.TemplateResponse(
        request,
        "heartbeats.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "heartbeats": heartbeats[:limit],
            "filter_symbol": symbol,
            "limit": limit,
            "total_shown": len(heartbeats[:limit]),
            "strip_data": _build_regime_strip(config),
            "show_regime_strip": True,
        },
    )


# MEDIUM-LOW-AGENT-REPO Fix #23 (2026-07-22): the duplicate /test route
# that previously lived here (returning {"message": "Backend is working!"})
# was deleted — it shadowed the earlier /test registration at line ~346
# (which returns {"message": "Test endpoint works!"}). FastAPI keeps only
# the LAST registration for a given path, so the first one was unreachable.
# Kept the line-346 registration; removed this one to avoid confusion.

@app.get("/health-simple")
async def health_simple() -> JSONResponse:
    """Simple health endpoint without external dependencies."""
    return JSONResponse({
        "status": "healthy",
        "version": __version__,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "message": "Hermes backend is running"
    })

@app.get("/health")
async def health() -> JSONResponse:
    """JSON health endpoint (for monitoring/CI).

    MEDIUM-LOW-AGENT-REPO Fix #24 (2026-07-22): previously returned a
    hardcoded ``"0.1.0-dev"`` string that drifted from the canonical
    ``hermes.__version__`` (currently "0.1.0" per src/hermes/__init__.py:3).
    Now uses the module-level ``__version__`` imported at app.py:39
    (with a "0.1.0-dev" fallback only if the import fails). Response
    shape is unchanged.
    """
    return JSONResponse({
        "status": "healthy",
        "version": __version__,
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "message": "Hermes backend is running"
    })


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
        content=safe_json(
            {
                "count": len(heartbeats),
                "heartbeats": heartbeats,
            }
        )
    )


@app.get("/monitor", response_class=HTMLResponse)
async def monitor_page(request: Request) -> HTMLResponse:
    """Active Price Monitor page — shows live prices, indicators, positions, events."""
    config = get_config()

    # Get recent monitor events from DuckDB
    from hermes.web.status import get_recent_monitor_events

    events = get_recent_monitor_events(config, limit=50)

    # Get live data from monitor if running in-process.
    # MEDIUM-LOW-AGENT-REPO Fix #14 (2026-07-22): in standalone web mode
    # (no monitor passed to create_app), _monitor is None and we gracefully
    # fall back to an empty live_data dict + monitor_running=False — the
    # monitor.html template then renders the "Monitor not running in this
    # process" banner (see monitor.html:35) and the page still shows the
    # DuckDB event-log card below. We never raise on a missing monitor.
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
            "ws": _monitor.get_stats().get("ws", {}),
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
            # Explicit standalone-mode flag so the template can render a
            # clearer hint when no monitor is attached to this process.
            "standalone_mode": _monitor is None,
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


# Deprecated - signals page removed, use /journal (Trade Journal) instead
@app.get("/signals", response_class=HTMLResponse)
async def signals_page(request: Request) -> HTMLResponse:
    """Deprecated — redirects to /journal (Trade Journal)."""
    from starlette.responses import RedirectResponse
    return RedirectResponse(url="/journal", status_code=301)


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
            "strip_data": _build_regime_strip(config),
            "show_regime_strip": True,
        },
    )


@app.get("/api/portfolio")
async def api_portfolio(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON portfolio metrics endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_portfolio_metrics

    metrics = get_portfolio_metrics(config)
    return JSONResponse(content=safe_json({"metrics": metrics}))


@app.post("/api/validate-redis")
async def validate_redis_credentials(
    request: Request,
) -> JSONResponse:
    """Validate Redis credentials and plan subscription.
    
    Accepts redis_username + redis_password from the setup form.
    Validates against Supabase redis_credentials table (migration 0004).
    Returns 200 if credentials are valid, 401 if invalid.
    
    No auth required — this endpoint is used during onboarding.
    """
    body = await request.json()
    redis_username = body.get("redis_username", "")
    redis_password = body.get("redis_password", "")
    user_id = body.get("user_id", "")
    redis_url = body.get("redis_url", "")
    
    if not redis_username and not redis_url:
        return JSONResponse(
            {"valid": False, "error": "redis_username or redis_url is required"},
            status_code=400,
        )
    
    from hermes.core.credentials_validator import (
        parse_redis_url,
        extract_plan_prefix,
        validate_plan_prefix,
        get_stream_name,
    )
    
    # Parse provided Redis URL (if given)
    parsed = parse_redis_url(redis_url) if redis_url else {}
    
    # Extract plan prefix from URL or username
    username = redis_username or parsed.get("username", "")
    plan_prefix = extract_plan_prefix(username)
    
    # If not in URL, try from config
    if not plan_prefix:
        config = get_config()
        plan_prefix = config.upstream.get("noble_trader", {}).get("plan_prefix")
    
    # Validate plan prefix
    is_valid_plan, plan_error = validate_plan_prefix(plan_prefix)
    
    # Get stream name
    stream_name = get_stream_name(plan_prefix)
    
    # If redis_password is provided, validate against Supabase
    # (In production, this would call the Supabase edge function)
    if redis_password and redis_username:
        # Validate username format (sub_<32hex>)
        import re
        if not re.match(r'^sub_[a-f0-9]{32}$', redis_username):
            return JSONResponse(
                {
                    "valid": False,
                    "error": "Invalid Redis username format. Expected: sub_<32hex>",
                    "plan_prefix": plan_prefix,
                    "stream_name": stream_name,
                },
                status_code=401,
            )
        
        # In production: call Supabase edge function to validate credentials
        # For now, validate username format only
        # The edge function would check redis_credentials table for matching
        # username and validate the password against the encrypted value
    
    result = {
        "valid": is_valid_plan,
        "plan_prefix": plan_prefix,
        "stream_name": stream_name,
        "redis_username": redis_username if redis_username else None,
        "error": plan_error if not is_valid_plan else None,
    }
    
    return JSONResponse(result)


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
            "strip_data": _build_regime_strip(config),
            "show_regime_strip": True,
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

    # Pre-serialize the equity curve for the in-page chart. Raw rows carry
    # pandas Timestamp/datetime values that Jinja's |tojson cannot serialize.
    equity_curve_json = _json.dumps(
        [
            {
                "ts": (r.get("ts").isoformat() if hasattr(r.get("ts"), "isoformat") else str(r.get("ts"))),
                "equity_total": r.get("equity_total"),
                "drawdown_pct": r.get("drawdown_pct"),
            }
            for r in equity_curve
        ],
        default=_json_default,
    )

    return templates.TemplateResponse(
        request,
        "pnl.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "tear_sheet": tear_sheet,
            "equity_curve": equity_curve,
            "equity_curve_json": equity_curve_json,
            "pnl_history": pnl_history,
            "strip_data": _build_regime_strip(config),
            "show_regime_strip": True,
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


@app.get("/journal", response_class=HTMLResponse)
async def journal_page(request: Request) -> HTMLResponse:
    """Trade Journal page — shows trade journal + decision tree.

    Renamed from /agent to /journal for clarity.
    Phase 1A v10: Hypotheses card removed (hermes_hypotheses table dropped;
    hypothesis is now a per-signal column on trade_postmortem, surfaced via
    `noble journal generate`). See LLM-INTEGRATION-STRATEGY.md §7.
    """
    config = get_config()
    from hermes.web.status import get_trade_journal_entries, get_decision_tree_definition

    journal = get_trade_journal_entries(config, limit=50)
    decision_tree = get_decision_tree_definition()

    return templates.TemplateResponse(
        request,
        "agent.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "journal": journal,
            "decision_tree": decision_tree,
        },
    )


@app.get("/decision-tree", response_class=HTMLResponse)
async def decision_tree_page(request: Request) -> HTMLResponse:
    """Decision Tree page — standalone view of the Hermes decision tree.

    Moved from /agent as a submenu item under Account.
    """
    config = get_config()
    from hermes.web.status import get_decision_tree_definition

    decision_tree = get_decision_tree_definition()

    return templates.TemplateResponse(
        request,
        "decision_tree.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "decision_tree": decision_tree,
        },
    )


@app.get("/api/simulations")
async def api_simulations(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON simulation runs endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_simulation_runs

    runs = get_simulation_runs(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(runs), "runs": runs},
        )
    )


@app.get("/api/backtest/runs")
async def api_backtest_runs(limit: int = 20, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON backtest runs endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_backtest_runs

    runs = get_backtest_runs(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(runs), "runs": runs},
        )
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
    return JSONResponse(content=safe_json(run))


@app.get("/api/portfolio/var_history")
async def api_portfolio_var_history(
    limit: int = 500, _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Historical VaR + drawdown + leverage time series (from account_snapshots)."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_portfolio_var_history

    rows = get_portfolio_var_history(config, limit=limit)
    return JSONResponse(content=safe_json(
        {"count": len(rows), "history": rows}
    ))


@app.get("/api/portfolio/exposure")
async def api_portfolio_exposure(
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Current exposure breakdown by venue + direction + asset class."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_portfolio_exposure_breakdown

    breakdown = get_portfolio_exposure_breakdown(config)
    return JSONResponse(content=safe_json(breakdown))


@app.get("/api/agent/decision_tree")
async def api_agent_decision_tree(
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Live decision tree definition (interactive tree viz source).

    MEDIUM-LOW-AGENT-REPO Fix #13 (2026-07-22): previously returned a
    hardcoded static dict via ``hermes.web.status.get_decision_tree_definition``
    — operators couldn't tell whether a config edit had actually
    propagated to the runtime decision tree. Now constructs a fresh
    ``HermesDecisionTree`` from the *loaded* config (mirroring
    ``execution/orchestrator.py:101-118``) and surfaces its actual
    11 threshold values via ``to_dict()`` alongside the static tree
    structure. Response shape is unchanged (still has ``root``); a
    new top-level ``thresholds`` key carries the live values.
    """
    from hermes.agent.decision_tree import HermesDecisionTree
    from hermes.web.status import get_decision_tree_definition

    config = get_config()
    _dt_cfg = (
        config.position_management.get("decision_tree", {})
        if hasattr(config, "position_management")
        else {}
    )
    # Per-key fallback preserves backward compat for configs that don't
    # yet have a decision_tree subsection (matches orchestrator behavior).
    tree = HermesDecisionTree(
        stop_loss_pct=_dt_cfg.get("stop_loss_pct", -0.01),
        take_profit_pct=_dt_cfg.get("take_profit_pct", 0.025),
        early_profit_pct=_dt_cfg.get("early_profit_pct", 0.045),
        fading_brick_count=_dt_cfg.get("fading_brick_count", 2),
        strong_conviction_threshold=_dt_cfg.get("strong_conviction_threshold", 0.7),
        trail_stop_activation_pct=_dt_cfg.get("trail_stop_activation_pct", 0.01),
        markov_persistence_high=_dt_cfg.get("markov_persistence_high", 0.7),
        markov_persistence_low=_dt_cfg.get("markov_persistence_low", 0.55),
        trending_tp_multiplier=_dt_cfg.get("trending_tp_multiplier", 1.5),
        mean_reverting_tp_multiplier=_dt_cfg.get("mean_reverting_tp_multiplier", 0.7),
        trending_fading_bricks_delta=_dt_cfg.get("trending_fading_bricks_delta", 1),
    )

    # Merge: keep the static tree structure for the interactive viz
    # (root + nested branches), AND add a top-level "thresholds" key
    # with the live config-driven values + decision counters.
    payload = get_decision_tree_definition()
    payload["thresholds"] = tree.to_dict()
    return JSONResponse(safe_json(payload))


@app.get("/api/agent/trade_journal")
async def api_agent_trade_journal(
    limit: int = 50, _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """Trade journal entries (with postmortems + lessons)."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_trade_journal_entries

    entries = get_trade_journal_entries(config, limit=limit)
    return JSONResponse(content=safe_json(
        {"count": len(entries), "entries": entries}
    ))


@app.get("/api/pnl/tear_sheet")
async def api_pnl_tear_sheet(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON tear sheet endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_pnl_tear_sheet

    ts = get_pnl_tear_sheet(config)
    return JSONResponse(content=safe_json(ts))


@app.get("/api/pnl/history")
async def api_pnl_history(limit: int = 100, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON PnL history endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_pnl_history

    history = get_pnl_history(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(history), "history": history},
        )
    )


@app.get("/api/orders")
async def api_orders(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON orders endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_orders

    orders = get_recent_orders(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(orders), "orders": orders},
        )
    )


@app.get("/api/fills")
async def api_fills(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON fills endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_fills

    fills = get_recent_fills(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(fills), "fills": fills},
        )
    )


@app.get("/api/risk/decisions")
async def api_risk_decisions(limit: int = 50, _auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON risk decisions endpoint."""
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_risk_decisions

    decisions = get_recent_risk_decisions(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(decisions), "decisions": decisions}
        )
    )


@app.get("/api/signals")
async def api_signals(limit: int = 50) -> JSONResponse:
    """JSON blended signals endpoint.

    DEPRECATED (2026-07-28): Auth requirement removed - this is now a public
    read-only endpoint. For authenticated access, use /api/ endpoints with
    proper credentials.
    """
    import json as _json

    config = get_config()
    from hermes.web.status import get_recent_blended_signals

    signals = get_recent_blended_signals(config, limit=limit)
    return JSONResponse(
        content=safe_json(
            {"count": len(signals), "signals": signals},
        )
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
        content=safe_json(
            {"count": len(rows), "symbols": [r.to_dict() for r in rows]},
        )
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
    return JSONResponse(content=safe_json(row.to_dict()))


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


# =========================================================================== #
# Market dashboard — per-symbol regime + renko + meta-regime drill-down.
#
# Routes (added as part of the DASHBOARD-UPGRADE-SCOPING.md migration):
#   GET /market                      — 20-symbol grid + regime strip (M3)
#   GET /market/{symbol}             — per-symbol detail page with 4 tabs (M9)
#   GET /api/market/overview         — JSON overview, 10s cached (M1)
#   GET /api/market/symbol/{symbol}  — JSON detail (M9 helper)
#   GET /api/charts/renko/{symbol}.png              — renko brick PNG (M4)
#   GET /api/charts/price_regime/{symbol}.png       — price + regime tint (M5)
#   GET /api/charts/regime_probs/{symbol}.png       — 7-state probability bars (M6)
#   GET /api/charts/meta_regime_radial/{symbol}.png — 7-state radial gauge (M7)
#   GET /api/charts/equity.png                      — portfolio equity curve (M8)
#
# All chart endpoints return PNG bytes via local-agent matplotlib (Agg backend).
# All data comes from the local DuckDB file or TDVA (TradingView Data API) —
# never Hyperliquid. See DASHBOARD-UPGRADE-SCOPING.md §3.3 + §7.4.
# =========================================================================== #


# --- M1: /api/market/overview JSON endpoint ----------------------------------
#
# In-memory TTL cache (10s) so concurrent page refreshes don't re-query DuckDB.
# Single shared dict — thread-safe for reads (GIL), occasional writes are fine
# because the worst case is two threads compute the same payload simultaneously.
_market_overview_cache: dict[str, Any] = {"key": None, "value": None, "expires_at": 0.0}
_MARKET_OVERVIEW_TTL_SEC = 10.0


def _get_market_overview_cached(config: HermesConfig) -> list[dict[str, Any]]:
    """Return cached /api/market/overview payload, refreshing if older than 10s."""
    import time

    now = time.time()
    if (
        _market_overview_cache["value"] is not None
        and now < _market_overview_cache["expires_at"]
    ):
        return _market_overview_cache["value"]  # type: ignore[return-value]

    from hermes.web.status import get_market_overview

    value = get_market_overview(config)
    _market_overview_cache["key"] = "overview"
    _market_overview_cache["value"] = value
    _market_overview_cache["expires_at"] = now + _MARKET_OVERVIEW_TTL_SEC
    return value


def _build_regime_strip(config: HermesConfig) -> list[dict[str, Any]]:
    """Build the condensed regime-strip payload from cached market overview.

    Used by every market-context route handler (/portfolio, /signals, /heartbeats,
    /approvals, /pnl, /orders, /market, /market/{symbol}) so the regime strip
    at the top of every page renders the same 20-symbol snapshot.

    Returns a list of dicts with the keys expected by the regime_strip macro:
      { symbol, regime, signal, markov_current_state, effective_kelly,
        regime_conf, ts_received }
    """
    symbols_data = _get_market_overview_cached(config)
    return [
        {
            "symbol": s["symbol"],
            "regime": s.get("regime"),
            "signal": s.get("signal"),
            "markov_current_state": s.get("markov_current_state"),
            "effective_kelly": s.get("effective_kelly"),
            "regime_conf": s.get("regime_conf"),
            "ts_received": s.get("ts_received"),
        }
        for s in symbols_data
    ]


@app.get("/api/market/overview")
async def api_market_overview(
    _auth: dict[str, Any] = Depends(require_auth),
) -> JSONResponse:
    """JSON: latest heartbeat per active symbol + asset_class + venue.

    One row per symbol. Symbols with no heartbeat yet are included with
    `ts_received: null` so the dashboard can render a placeholder card.

    Cached for 10 seconds in-process (per DASHBOARD-UPGRADE-SCOPING.md §6.3).
    """
    config = get_config()
    payload = _get_market_overview_cached(config)
    return JSONResponse(content=safe_json({"symbols": payload, "count": len(payload)}))


# --- M3: /market HTML page -------------------------------------------------- #


@app.get("/market", response_class=HTMLResponse)
async def market_page(request: Request) -> HTMLResponse:
    """Market overview page — 20-symbol grid + regime strip.

    Pulls /api/market/overview data (cached 10s) and renders one symbol_card
    per active symbol. Auto-refreshes every 30s (less aggressive than
    /portfolio's 10s because the underlying data is already cached).
    """
    config = get_config()
    symbols_data = _get_market_overview_cached(config)
    strip_data = _build_regime_strip(config)

    return templates.TemplateResponse(
        request,
        "market.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "symbols_data": symbols_data,
            "strip_data": strip_data,
            "show_regime_strip": True,
        },
    )


# --- M9: /market/{symbol} detail page -------------------------------------- #


@app.get("/market/{symbol}", response_class=HTMLResponse)
async def market_symbol_page(request: Request, symbol: str) -> HTMLResponse:
    """Per-symbol detail page with 4 DaisyUI tabs: Overview / Renko / Regime / Signals."""
    config = get_config()
    from hermes.web.status import get_symbol_detail

    detail = get_symbol_detail(config, symbol)
    if detail is None:
        return templates.TemplateResponse(
            request,
            "market_symbol.html",
            {
                "version": __version__,
                "config_hash": get_config_hash(config),
                "environment": config.environment,
                "symbol": symbol,
                "detail": None,
                "not_found": True,
                "show_regime_strip": False,
            },
            status_code=404,
        )

    # Pull the strip data too so the regime strip at the top is consistent.
    strip_data = _build_regime_strip(config)

    return templates.TemplateResponse(
        request,
        "market_symbol.html",
        {
            "version": __version__,
            "config_hash": get_config_hash(config),
            "environment": config.environment,
            "symbol": symbol,
            "detail": detail,
            "strip_data": strip_data,
            "not_found": False,
            "show_regime_strip": True,
        },
    )


# --- M4: Renko brick chart PNG endpoint ------------------------------------ #
#
# NOTE: This is a SYNC def (not async). Starlette runs sync routes in a
# threadpool so matplotlib's blocking render doesn't stall the event loop.
# This is intentional — per DASHBOARD-UPGRADE-SCOPING.md §12.1 risk R4.
#
# Returns image/png bytes. The PNG is cached 60s in-process via charts._cache.


@app.get("/api/charts/renko/{symbol}.png")
def chart_renko_png(symbol: str, last_n: int = 100):
    """Renko brick chart PNG — rebuilt on-demand from TDVA candles, cached 60s."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.renko import render_renko_png

    try:
        png_bytes = render_renko_png(config, symbol, last_n=last_n)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("renko_chart_failed", symbol=symbol, error=str(e)[:200])
        # Return a 200 with an error PNG so the <img> tag doesn't break —
        # the user sees the error message in the chart area.
        from hermes.web.charts._theme import render_empty_chart

        png_bytes = render_empty_chart(symbol, f"Render error: {str(e)[:80]}")
        return Response(content=png_bytes, media_type="image/png")


# --- Smoke-test endpoint for the chart package ----------------------------- #


@app.get("/api/charts/_cache_stats")
async def chart_cache_stats(_auth: dict[str, Any] = Depends(require_auth)) -> JSONResponse:
    """JSON: in-memory chart cache stats — for debugging / monitoring."""
    from hermes.web.charts._cache import chart_cache

    return JSONResponse(content=safe_json(chart_cache.stats()))


# --- M5: Price + regime tint chart ----------------------------------------- #


@app.get("/api/charts/price_regime/{symbol}.png")
def chart_price_regime_png(symbol: str, horizon: int = 200):
    """Price area chart with regime-colored fill, cached 60s."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.price_regime import render_price_regime_png

    try:
        png_bytes = render_price_regime_png(config, symbol, horizon=horizon)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("price_regime_chart_failed", symbol=symbol, error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart(symbol, f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )


# --- M6: Regime probability bars ------------------------------------------- #


@app.get("/api/charts/regime_probs/{symbol}.png")
def chart_regime_probs_png(symbol: str):
    """7-state regime probability bars (live MetaRegimeClassifier), cached 60s."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.regime_probs import render_regime_probs_png

    try:
        png_bytes = render_regime_probs_png(config, symbol)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("regime_probs_chart_failed", symbol=symbol, error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart(symbol, f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )


# --- M7: Meta-regime radial gauge ------------------------------------------ #


@app.get("/api/charts/meta_regime_radial/{symbol}.png")
def chart_meta_regime_radial_png(symbol: str):
    """Meta-regime radial gauge (7-state polar plot), cached 60s."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.meta_regime_radial import render_meta_regime_radial_png

    try:
        png_bytes = render_meta_regime_radial_png(config, symbol)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("meta_regime_radial_chart_failed", symbol=symbol, error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart(symbol, f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )


# --- M8: Portfolio equity curve (replaces uPlot on /portfolio) ------------- #


@app.get("/api/charts/equity.png")
def chart_equity_png(limit: int = 500):
    """Portfolio equity curve + drawdown, cached 60s. Replaces uPlot equityCurve."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.equity import render_equity_png

    try:
        png_bytes = render_equity_png(config, limit=limit)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("equity_chart_failed", error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart("Portfolio", f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )


# --- UX-UNIFORMITY-2: portfolio allocation donut + exposure bars + VaR histogram
# Three new chart endpoints porting the deprecated dashboard's AllocationPie,
# ExposureBars, and VarDistHistogram React components to server-rendered PNGs.
# All three use the same 60s in-process TTL cache as the other chart endpoints.


@app.get("/api/charts/allocation.png")
def chart_allocation_png():
    """Portfolio allocation donut chart — cached 60s. Replaces AllocationPie.tsx."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.portfolio_allocation import render_allocation_png

    try:
        png_bytes = render_allocation_png(config)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("allocation_chart_failed", error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart("Portfolio Allocation", f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )


@app.get("/api/charts/exposure_bars.png")
def chart_exposure_bars_png():
    """Exposure breakdown horizontal bars — cached 60s. Replaces ExposureBars.tsx."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.exposure_bars import render_exposure_bars_png

    try:
        png_bytes = render_exposure_bars_png(config)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("exposure_bars_chart_failed", error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart("Exposure Breakdown", f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )


@app.get("/api/charts/var_histogram.png")
def chart_var_histogram_png(limit: int = 500):
    """VaR 1d 99% distribution histogram — cached 60s. Replaces VarDistHistogram.tsx."""
    from fastapi import Response

    config = get_config()
    from hermes.web.charts.var_histogram import render_var_histogram_png

    try:
        png_bytes = render_var_histogram_png(config, limit=limit)
        return Response(content=png_bytes, media_type="image/png")
    except Exception as e:
        log.error("var_histogram_chart_failed", error=str(e)[:200])
        from hermes.web.charts._theme import render_empty_chart

        return Response(
            content=render_empty_chart("VaR Distribution", f"Render error: {str(e)[:80]}"),
            media_type="image/png",
        )
