"""
Noble Trader — Python Plugin (Agent-side tools)

Registers noble_* tools that the agent (LLM) can invoke via normal tool-calling.
These tools wrap the noble_cli command group functionality for use inside
Hermes chat sessions.

This plugin also provides the dashboard plugin backend (see dashboard/plugin_api.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import subprocess
from typing import Any, Optional

log = logging.getLogger(__name__)


def _run_async(coro):
    """Run an async coroutine to sync result, handling existing event loops."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an event loop — can't use asyncio.run
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Auto-start: launch the Noble Trader stack watchdog when the Hermes agent
# session starts, so the dashboard (src/hermes/web) and the agent loops come
# up automatically without manual intervention or relying solely on the 5-min
# cron. The watchdog is idempotent (single-instance lock + name-based liveness)
# so (re)launching it is always safe.
# ---------------------------------------------------------------------------

# Per-process guard so we only attempt one launch per Hermes process lifetime.
_watchdog_launched = False


def _resolve_watchdog_script() -> str:
    """Locate scripts/watchdog.sh (deployed runtime first, repo source fallback).

    The deployed runtime (per AGENTS.md) is the canonical running location and
    matches the hardcoded REPO inside watchdog.sh. Override via NOBLE_WATCHDOG_SH.
    """
    override = os.environ.get("NOBLE_WATCHDOG_SH")
    if override and os.path.exists(override):
        return override
    deployed = (
        "C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/"
        "noble-trader-agent/repo/scripts/watchdog.sh"
    )
    if os.path.exists(deployed):
        return deployed
    repo_src = (
        "C:/Users/aloys/OneDrive/Documents/GitHub/noble-trader-workspace/"
        "noble-trader-agent/scripts/watchdog.sh"
    )
    if os.path.exists(repo_src):
        return repo_src
    return deployed  # best-effort default


def _is_dashboard_alive(timeout: float = 0.5) -> bool:
    """Check if the agent dashboard (port 8080) is reachable."""
    import socket
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=timeout):
            return True
    except (OSError, ConnectionRefusedError):
        return False


def _maybe_relaunch_watchdog() -> None:
    """If the dashboard port (8080) is not reachable, relaunch the watchdog.

    The on_session_start hook fires when the Hermes *agent process* starts, not
    when the Electron desktop app restarts. If the user kills the desktop app
    and reconnects, the old dashboard process dies but the agent process may
    still be running — so on_session_start never fires again and the dashboard
    stays down. This health check bridges that gap: any plugin call that
    detects the dashboard is dead will relaunch the watchdog.
    """
    global _watchdog_launched
    if _is_dashboard_alive():
        return  # dashboard is running, nothing to do
    log.warning("noble_trader_dashboard_not_reachable port=%s", 8080)
    _watchdog_launched = False  # allow relaunch
    _start_watchdog()


def _start_watchdog() -> None:
    """Launch scripts/watchdog.sh detached (non-blocking)."""
    global _watchdog_launched
    if _watchdog_launched:
        return
    _watchdog_launched = True
    script = _resolve_watchdog_script()
    log.info("noble_trader_session_start_launch_watchdog script=%s", script)
    
    # Log watchdog launch to a file we can inspect for errors
    # (stdout/stderr were devnull'd before — that hid failures)
    log_dir = os.environ.get(
        "NOBLE_WATCHDOG_LOG_DIR",
        "C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo/logs"
    )
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "watchdog_hook.log")
    
    # Find a bash executable — cross-platform: check $SHELL (macOS/Linux),
    # then common Git-for-Windows locations on Windows, then /usr/bin/bash on Linux
    bash_exe = None
    for candidate in (
        os.environ.get("SHELL", ""),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        "/usr/bin/bash",
        "/bin/bash",
    ):
        if candidate and os.path.exists(candidate):
            bash_exe = candidate
            break
    if bash_exe is None:
        # Fallback: try 'bash' on PATH (Unix) or 'sh' (minimal systems)
        import shutil as _shutil
        bash_exe = _shutil.which("bash") or _shutil.which("sh") or "bash"

    try:
        detached = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
        with open(log_file, "ab") as lf:
            # Log the launch attempt
            lf.write(f"\n[{_now()}] Launching watchdog: {script}\n".encode())
            lf.write(f"bash: {bash_exe}\n".encode())
            lf.write(f"cwd: {os.getcwd()}\n".encode())
            lf.write(f"exists: {os.path.exists(script)}\n".encode())
            lf.flush()
            
            proc = subprocess.Popen(
                [bash_exe, script],
                stdout=lf,
                stderr=lf,
                stdin=subprocess.DEVNULL,
                close_fds=True,
                creationflags=detached,
                cwd=os.path.dirname(script) or os.getcwd(),
            )
            lf.write(f"pid: {proc.pid}\n".encode())
            lf.flush()
            log.info("noble_trader_watchdog_launched pid=%s log_file=%s", proc.pid, log_file)
    except Exception as e:
        with open(log_file, "ab") as lf:
            lf.write(f"\n[{_now()}] LAUNCH FAILED: {e}\n".encode())
        log.error("noble_trader_watchdog_launch_failed error=%s", str(e))
        _watchdog_launched = False  # allow retry on next session start


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _on_session_start(**kwargs) -> None:
    """on_session_start hook: fire-and-forget watchdog launch.

    Must never block session start or raise — the plugin manager swallows
    hook exceptions, but we stay defensive and return immediately.
    """
    try:
        _start_watchdog()
    except Exception:  # pragma: no cover - defensive
        pass
    return None


def register_tools(ctx: Any) -> None:
    """Register noble-trader tools with the Hermes agent.

    Args:
        ctx: PluginContext from hermes_cli.plugins — provides register_tool().
    """

    # --- noble_balance ---
    def _noble_balance() -> dict[str, Any]:
        """Get live equity across Noble Trader venues (Alpaca + Hyperliquid).

        Returns total equity and per-venue breakdown. Anchored to real brokerage
        equity via MetaAPI/MT4-MT5 bridge (not static numbers).
        """
        try:
            from hermes.commands.noble_cli import _alpaca_equity, _hyperliquid_equity
            result = _run_async(asyncio.gather(_alpaca_equity(), _hyperliquid_equity()))
            alpaca_eq, hl_eq = result
            total = round(alpaca_eq + hl_eq, 2)
            return {
                "total_equity": total,
                "alpaca": round(alpaca_eq, 2),
                "hyperliquid": round(hl_eq, 2),
                "status": "connected" if total > 0 else "check_credentials",
            }
        except Exception as e:
            log.error("noble_balance_failed error=%s", str(e))
            return {"error": str(e), "status": "error"}

    ctx.register_tool(
        name="noble_balance",
        toolset="trading",
        schema={
            "description": "Get live equity across Noble Trader venues (Alpaca + Hyperliquid).",
            "name": "noble_balance",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_noble_balanced_wrapper if False else _noble_balance,
    )

    # --- noble_assets ---
    def _noble_assets(with_bricks: bool = True, seed_timeout: int = 20) -> dict[str, Any]:
        """Get currently held assets with Noble Trader regime info and renko bricks.

        Args:
            with_bricks: Rebuild renko bricks from TDVA candles.
            seed_timeout: Seconds to subscribe to upstream if cache is empty.
        """
        try:
            from hermes.commands.noble_cli import (
                _alpaca_positions, _hyperliquid_equity, _hyperliquid_positions,
                _get_cached_heartbeat, _seed_cache_from_upstream,
                _venues_held_assets, _renko_ladder,
            )
            from hermes.web.charts._data import fetch_tdva_candles
            from hermes.core.config import load_config
            from hermes.signals.meta_regime import MetaRegimeClassifier

            cfg = load_config()

            async def _go():
                alp_eq, alp_pos = await _alpaca_positions()
                hl = await _hyperliquid_positions()
                rows = _venues_held_positions(alp_eq, alp_pos, hl) if rows else _venues_held_positions(alp_eq, alp_pos, hl)
                if not rows:
                    return {"positions": [], "total_equity": alp_eq + hl.get("equity", 0)}

                cached = {r["symbol"]: await _get_cached_heartbeat(r["symbol"]) for r in rows}
                if not any(cached.values()):
                    await _seed_cache_from_upstream(seed_timeout)
                    cached = {r["symbol"]: await _get_cached_heartbeat(r["symbol"]) for r in rows}

                classifier = MetaRegimeClassifier()
                enriched = []
                for r in rows:
                    hb = cached.get(r["symbol"])
                    entry = {"symbol": r["symbol"], "venue": r["venue"], "qty": r["qty"],
                             "side": r["side"], "entry": r["entry"], "mkt_value": r["mkt_value"],
                             "upnl": r["upnl"]}
                    if hb:
                        entry["signal"] = hb.signal
                        entry["regime"] = hb.regime
                        entry["regime_conf"] = hb.regime_conf
                        mr = classifier.classify(heartbeat=hb, symbol=r["symbol"])
                        entry["meta_regime"] = mr.state
                        entry["meta_regime_conf"] = mr.confidence
                        if with_bricks:
                            closes = fetch_tdva_candles(cfg, r["symbol"], limit=200, timeframe="15m")
                            if closes and hb.brick_size:
                                entry["renko"] = _renko_ladder(closes, hb.brick_size)
                    enriched.append(entry)
                return {"positions": enriched, "total_equity": alp_eq + hl.get("equity", 0)}

            return _run_async(_go())
        except Exception as e:
            log.error("noble_assets_failed error=%s", str(e))
            return {"error": str(e), "status": "error"}

    ctx.register_tool(
        name="noble_assets",
        toolset="trading",
        schema={
            "description": "Get held assets with Noble Trader signal regime and renko bricks.",
            "name": "noble_assets",
            "parameters": {
                "type": "object",
                "properties": {
                    "with_bricks": {"type": "boolean", "description": "Rebuild renko bricks from candles", "default": True},
                    "seed_timeout": {"type": "integer", "description": "Seconds to subscribe if cache empty", "default": 20, "minimum": 5, "maximum": 60},
                },
            },
        },
        handler=_noble_assets,
    )

    # --- noble_status ---
    def _noble_status() -> dict[str, Any]:
        """Check Noble Trader stack health: watchdog, processes, Redis, venue connections."""
        try:
            from hermes.web.status import check_all, get_ingest_stats
            from hermes.core.config import load_config
            config = load_config()

            health = check_all(config)
            ingest = get_ingest_stats(config)

            return {
                "stack": "noble-trader",
                "watchdog": "active",  # watchdog is external cron
                "health": health,
                "ingest": ingest,
                "config_hash": "redacted",
            }
        except Exception as e:
            log.error("noble_status_failed error=%s", str(e))
            return {"error": str(e)}

    ctx.register_tool(
        name="noble_status",
        toolset="trading",
        schema={
            "description": "Check Noble Trader stack health: watchdog, processes, Redis, venues.",
            "name": "noble_status",
            "parameters": {"type": "object", "properties": {}},
        },
        handler=_noble_status,
    )


def register(ctx: Any) -> None:
    """Entry point called by the Hermes plugin loader.

    This runs once at plugin load time — NOT on session start/end. The plugin
    registers tools, hooks, and starts a background health-check thread.

    The background thread polls port 8080 every 30s and relaunches the watchdog
    if the dashboard is unreachable. This is the production-ready approach:
    it survives Electron-only restarts (where on_session_start may not fire)
    and doesn't require manual process management.
    """
    log.info("noble_trader_plugin_loaded")
    register_tools(ctx)

    # on_session_start hook: fast-path for agent backend restarts
    if hasattr(ctx, "register_hook"):
        try:
            ctx.register_hook("on_session_start", _on_session_start)
            log.info("noble_trader_on_session_start_hook_registered")
        except Exception as e:
            log.warning("noble_trader_hook_register_failed error=%s", str(e))

    # Background health-check: poll port 8080 periodically and relaunch
    # watchdog if dashboard is down. This is the primary recovery mechanism
    # — it works regardless of session lifecycle events.
    _start_health_check_thread()
    _prewarm_brokerage()

    # Also check immediately if dashboard is already live
    _maybe_relaunch_watchdog()


def _start_health_check_thread() -> None:
    """Start a daemon thread that polls port 8080 and relaunches watchdog if down.

    Runs every 5 seconds by default (configurable via NOBLE_HEALTH_CHECK_INTERVAL).
    Daemon thread dies automatically when the process exits.
    """
    import threading

    interval = float(os.environ.get("NOBLE_HEALTH_CHECK_INTERVAL", "5"))

    def _loop() -> None:
        while True:
            try:
                time.sleep(interval)
                _maybe_relaunch_watchdog()
            except Exception:  # pragma: no cover - defensive
                pass

    t = threading.Thread(target=_loop, daemon=True, name="noble-health-check")
    t.start()
    log.info("noble_trader_health_check_thread_started interval=%ss", interval)


def _prewarm_brokerage() -> None:
    """Pre-establish the MetaApi connection in the background.

    This fires a background thread that connects to MetaApi and primes the
    _BROKERAGE_CACHE so the first brokerage API call is fast.
    """
    import threading

    def _warm() -> None:
        try:
            import time, sys
            # Ensure the deployed runtime src is importable
            rt_src = (
                "C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/"
                "noble-trader-agent/repo/src"
            )
            if rt_src not in sys.path and os.path.exists(rt_src):
                sys.path.insert(0, rt_src)
            # Also check repo src as fallback
            repo_src = (
                "C:/Users/aloys/OneDrive/Documents/GitHub/noble-trader-workspace/"
                "noble-trader-agent/src"
            )
            if repo_src not in sys.path and os.path.exists(repo_src):
                sys.path.insert(0, repo_src)

            # Add the deployed venv site-packages so hermes.* deps
            # (structlog, pydantic_core, etc.) resolve correctly
            rt_venv_site = os.path.normpath(
                "C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/"
                "noble-trader-agent/repo/.venv/Lib/site-packages"
            )
            if rt_venv_site not in sys.path and os.path.exists(rt_venv_site):
                sys.path.insert(0, rt_venv_site)

            # Wait a moment for the backend to be ready
            time.sleep(2)
            from hermes.execution.brokers.metaapi_broker import (
                MetaApiBroker,
                resolve_metaapi_credentials,
            )
            token, account_id, demo = resolve_metaapi_credentials()
            if not token or not account_id:
                return
            client = MetaApiBroker(token=token, account_id=account_id, demo=demo)
            import asyncio
            asyncio.run(client.connect())
            log.info("noble_trader_brokerage_prewarmed")
        except Exception as e:  # pragma: no cover
            log.warning("noble_trader_brokerage_prewarm_failed error=%s", str(e))

    t = threading.Thread(target=_warm, daemon=True, name="noble-trader-prewarm")
    t.start()
    log.info("noble_trader_brokerage_prewarm_started")
