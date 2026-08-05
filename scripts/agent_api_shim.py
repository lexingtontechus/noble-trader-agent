"""Agent-side API shim for the Noble Trader Hermes plugin (native data path).

This runs INSIDE the noble-trader-agent runtime (its own venv + PYTHONPATH),
so `import hermes.web.app` resolves to the agent's package (not the Hermes
desktop app's `hermes`). It uses Starlette's TestClient against the real agent
app and hits the auth-exempt `/api/plugin/*` routes, printing the JSON body
to STDOUT (a single JSON line, nothing else on STDOUT), and captures all
agent logging noise on STDERR, keeping STDOUT clean for the caller to parse.

The Hermes plugin (plugin_api.py) shells out to this script via the agent's
venv python, so it can read live portfolio/status/setup data WITHOUT the
standalone `:8080` web dashboard being running. This is the Hermes-native
data path that lets us soft-deprecate the dashboard.

Usage:
    python agent_api_shim.py <op>            # GET /api/plugin/<op>
    NT_SHIM_METHOD=POST python agent_api_shim.py setup   # POST /setup (body on stdin)

Operations map to auth-exempt routes:
    health         -> GET /api/plugin/health
    portfolio      -> GET /api/plugin/portfolio
    status         -> GET /api/plugin/status
    setup-status   -> GET /api/plugin/setup-status
    brokerage      -> GET /api/plugin/brokerage
    setup          -> POST /setup (NT_SHIM_METHOD=POST, body on stdin)

Cold-start optimization: for lightweight ops (health, setup-status), we avoid
the full create_app() import chain by reading .env directly in-process.
This keeps the shim fast enough to complete within the Hermes Electron IPC
15-second timeout during agent cold-start.
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys

# Suppress Python's logging module BEFORE importing agent code.
logging.disable(logging.CRITICAL)

_OP_PATHS = {
    "health": "/api/plugin/health",
    "portfolio": "/api/plugin/portfolio",
    "status": "/api/plugin/status",
    "setup-status": "/api/plugin/setup-status",
    "brokerage": "/api/plugin/brokerage",
}

# Ops that can be served without the full FastAPI app import chain.
# These only need .env file reads — no Redis, no DB, no broker connections.
_LIGHTWEIGHT_OPS = {
    "health": True,
    "setup-status": True,
}


def _read_env(path: str) -> dict[str, str]:
    """Parse a .env file into a dict (best-effort, ignores comments/blank)."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return {}
    env: dict[str, str] = {}
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


def _ensure_env_template(env_path: str) -> dict[str, str]:
    """Ensure .env exists with system vars. Create template if missing.

    System vars (URLs, endpoints) are populated with defaults.
    User vars (credentials) are left empty for the user to fill in.
    Returns the parsed env dict.
    """
    from pathlib import Path

    p = Path(env_path)
    # If .env exists, parse it
    if p.exists():
        return _read_env(env_path)

    # Create .env.template if it doesn't exist
    # Look for template in repo or deployed runtime
    runtime = os.environ.get("NOBLE_AGENT_RUNTIME", os.getcwd())
    template_candidates = [
        os.path.join(runtime, ".env"),
        os.path.join(os.getcwd(), ".env"),
    ]

    env_template = _SYSTEM_ENV_TEMPLATE
    for candidate in template_candidates:
        c = Path(candidate)
        if c.exists():
            # Use existing .env as the base template structure
            existing = _read_env(candidate)
            # Keep system vars, clear user vars
            for k, v in _SYSTEM_ENV_DEFAULTS.items():
                if not existing.get(k):
                    existing[k] = v
                elif '<' in str(existing.get(k, '')) or not existing[k]:
                    existing[k] = v
            env_template = "\n".join(f"{k}={v}" for k, v in existing.items())
            break

    p.write_text(env_template, encoding="utf-8")
    return _read_env(env_path)


# System vars — populated with defaults
_SYSTEM_ENV_DEFAULTS = {
    # System vars (URLs, endpoints)
    "NOBLE_TRADER_PROXY_REDIS_URL": "",  # User fills after onboarding
    "NOBLE_TRADER_QUOTE_PROXY_URL": "",  # User fills after onboarding
    "TRADINGVIEW_API_KEY": "",  # User fills
    # MetaApi credentials (dual-mode)
    "NT_MODE": "demo",  # auto-graduates to live after 20+ profitable trades
    "METAAPI_TOKEN_DEMO": "",
    "METAAPI_ACCOUNT_ID_DEMO": "",
    "METAAPI_TOKEN": "",
    "METAAPI_ACCOUNT_ID": "",
    # Supabase (required for delivery log + backfill)
    "SUPABASE_URL": "",
    "SUPABASE_ANON_KEY": "",
    # Redis signal channel
    "REDIS_FANOUT_URL": "",
    "REDIS_URL": "",
    "REDIS_CHANNEL": "signal.proxy",
    "REDIS_CONSUMER_GROUP": "noble-1",
}

_SYSTEM_ENV_TEMPLATE = (
    "# Generated by noble-trader-agent on first run.\n"
    "# Fill in values marked with YOUR_TOKEN below.\n\n"
    "# ── System vars ── (auto-filled)\n"
) + "".join(
    f"{k}={v}\n" for k, v in _SYSTEM_ENV_DEFAULTS.items()
)


def _lightweight_setup_status(env_path: str) -> dict:
    """Compute setup-status without loading the full FastAPI app.

    Reads .env directly and checks the required keys + NT_MODE, mirroring
    is_setup_complete() in app.py. This avoids importing hermes.web.app
    (which pulls in Redis, Structlog, pydantic, etc.) and completes in
    <100ms even during agent cold-start.
    """
    # Auto-create .env template if missing
    env = _ensure_env_template(env_path)
    nt_mode = (env.get("NT_MODE") or "").strip()
    if nt_mode not in ("demo", "live"):
        return {
            "setup_complete": False,
            "nt_mode": "",
            "missing_vars": ["NOBLE_TRADER_PROXY_REDIS_URL", "TRADINGVIEW_API_KEY", "METAAPI_TOKEN_DEMO", "METAAPI_ACCOUNT_ID_DEMO"],
            "local_plugin": True,
        }

    required = (
        "NOBLE_TRADER_PROXY_REDIS_URL",
        "NOBLE_TRADER_QUOTE_PROXY_URL",
        "TRADINGVIEW_API_KEY",
        "METAAPI_TOKEN_DEMO",
        "METAAPI_ACCOUNT_ID_DEMO",
        "METAAPI_TOKEN",
        "METAAPI_ACCOUNT_ID",
    )
    placeholders = {"", "<nt-redis-host>", "redis://<nt-redis-host>:<port>",
                    "<publishable-anon-key>", "<paper-api-key>",
                    "<0x-your-dedicated-trading-wallet>", "<quote-proxy-url>"}
    missing = []
    for key in required:
        val = (env.get(key) or "").strip()
        if not val or val in placeholders:
            missing.append(key)
    if missing:
        return {
            "setup_complete": False,
            "nt_mode": nt_mode,
            "missing_vars": missing,
            "local_plugin": True,
        }

    return {"setup_complete": True, "nt_mode": nt_mode, "local_plugin": True}


def _lightweight_health() -> dict:
    """Compute health without loading the full FastAPI app."""
    from hermes import __version__
    return {
        "status": "healthy",
        "version": __version__,
        "local_plugin": True,
    }


def _main() -> None:
    op = sys.argv[1] if len(sys.argv) > 1 else "status"
    method = os.environ.get("NT_SHIM_METHOD", "GET").upper()
    path = _OP_PATHS.get(op, op if op.startswith("/") else f"/api/plugin/{op}")

    # If the operation is "setup" (POST), route to the agent's POST /setup.
    if op == "setup" and method == "POST":
        path = "/setup"

    payload: object | None = None
    if method == "POST":
        try:
            raw = sys.stdin.read() or "{}"
            payload = json.loads(raw)
        except Exception:
            payload = None

    # ── Cold-start fast-path: for lightweight ops, avoid the full
    #    create_app(load_config()) import chain. Read .env directly.
    #    This keeps the shim under 15s (Hermes Electron IPC timeout)
    #    during agent cold-start. ──────────────────────────────────
    if method == "GET" and _LIGHTWEIGHT_OPS.get(op):
        env_path = os.environ.get("SECRETS_ENV_FILE_PATH", "./.env")
        # Resolve relative to the agent runtime if it's not absolute
        if not os.path.isabs(env_path):
            runtime = os.environ.get("NOBLE_AGENT_RUNTIME", os.getcwd())
            env_path = os.path.join(runtime, env_path)
        try:
            if op == "setup-status":
                result = _lightweight_setup_status(env_path)
            elif op == "health":
                result = _lightweight_health()
            else:
                result = {}
            print(json.dumps({"status": 200, "data": result}, default=str))
            return
        except Exception:
            # Fall through to the full app path if lightweight fails
            pass

    from hermes.core.config import load_config
    from hermes.web.app import create_app
    from starlette.testclient import TestClient

    # The agent's structlog factory captures sys.stdout at configuration time
    # (inside create_app). We redirect stdout to a StringIO buffer so ALL
    # logging noise is captured (in-memory), NOT written to the real stdout.
    # After the TestClient run, we emit a single JSON line to the real stdout.
    captured = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = captured

    try:
        app = create_app(load_config())
        with TestClient(app) as c:
            if method == "POST":
                if payload is not None:
                    resp = c.post(path, json=payload)
                else:
                    resp = c.post(path)
            else:
                resp = c.get(path)

            try:
                body = resp.json()
            except Exception:
                body = {"_raw": resp.text}
    finally:
        # Restore stdout and drain captured logging to STDERR (so it's
        # visible for debugging but doesn't pollute the JSON output).
        sys.stdout = real_stdout
        captured_val = captured.getvalue()
        if captured_val.strip():
            # Write captured noise to stderr for debuggability.
            sys.stderr.write(captured_val)
            sys.stderr.flush()

    # Emit a single JSON line to STDOUT: {status, data}.
    # This is the ONLY line on stdout.
    print(json.dumps({"status": resp.status_code, "data": body}, default=str))


if __name__ == "__main__":
    _main()
