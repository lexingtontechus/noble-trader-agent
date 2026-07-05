"""
Status checker — pings each subsystem and returns current state.

Used by the dashboard to display connection status for:
- DuckDB (local file)
- Hermes internal Redis
- Noble Trader upstream Redis
- Supabase
- Alpaca (paper trading API)
- Hyperliquid (REST API)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.core.secrets import get_secret_or_none
from hermes.db.migrate import get_duckdb_path

log = structlog.get_logger(__name__)


def _is_placeholder(value: str | None) -> bool:
    """Check if a value is still a placeholder (not yet configured)."""
    if not value:
        return True
    if value.startswith("secret:"):
        return True
    if "<" in value and ">" in value:
        return True
    return False


def _safe_url(url: str) -> str:
    """Redact password from URL for display."""
    if not url:
        return ""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        if "@" in rest:
            creds, host = rest.split("@", 1)
            return f"{scheme}://***@{host}"
    return url


async def check_duckdb(config: HermesConfig) -> dict[str, Any]:
    """Check DuckDB is openable and report table counts."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return {
                "name": "DuckDB",
                "status": "not_initialized",
                "detail": f"File not found: {db_path}. Run `platform init` first.",
                "path": str(db_path),
            }

        with duckdb.connect(str(db_path), read_only=True) as conn:
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='main' ORDER BY table_name"
            ).fetchall()
            table_names = [t[0] for t in tables]

            counts: dict[str, int] = {}
            for tn in table_names:
                try:
                    counts[tn] = conn.execute(f"SELECT COUNT(*) FROM {tn}").fetchone()[0]
                except Exception:
                    counts[tn] = -1

            # Schema version
            try:
                sv = conn.execute(
                    "SELECT MAX(version) FROM schema_version"
                ).fetchone()
                schema_version = sv[0] if sv[0] is not None else 0
            except Exception:
                schema_version = 0

        return {
            "name": "DuckDB",
            "status": "connected",
            "detail": f"{len(table_names)} tables, schema v{schema_version}",
            "path": str(db_path),
            "table_count": len(table_names),
            "tables": counts,
            "schema_version": schema_version,
        }
    except Exception as e:
        return {
            "name": "DuckDB",
            "status": "error",
            "detail": str(e)[:200],
            "path": "",
        }


async def check_hermes_redis(config: HermesConfig) -> dict[str, Any]:
    """Check Hermes internal Redis connection."""
    url = config.hermes_redis.get("url", "redis://localhost:6379/1")
    if _is_placeholder(url):
        return {
            "name": "Hermes Redis (internal)",
            "status": "not_configured",
            "detail": "Set HERMES_REDIS_URL in .env",
            "url": "",
        }
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, socket_connect_timeout=2)
        pong = await client.ping()
        info = await client.info()
        await client.close()
        return {
            "name": "Hermes Redis (internal)",
            "status": "connected" if pong else "error",
            "detail": f"v{info.get('redis_version', '?')}, "
                     f"db_size={info.get('db_size', '?')}",
            "url": _safe_url(url),
        }
    except Exception as e:
        return {
            "name": "Hermes Redis (internal)",
            "status": "error",
            "detail": str(e)[:200],
            "url": _safe_url(url),
        }


async def check_nt_redis(config: HermesConfig) -> dict[str, Any]:
    """Check Noble Trader upstream Redis connection."""
    nt = config.upstream.get("noble_trader", {}).get("redis", {})
    url = nt.get("url", "")
    if _is_placeholder(url):
        return {
            "name": "Noble Trader Redis (upstream)",
            "status": "not_configured",
            "detail": "Set NOBLE_TRADER_REDIS_URL in .env",
            "url": "",
            "channel": nt.get("channel", ""),
        }
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(url, socket_connect_timeout=2)
        pong = await client.ping()
        # Check if the channel has any subscribers (we can't see publishers)
        info = await client.info()
        await client.close()
        return {
            "name": "Noble Trader Redis (upstream)",
            "status": "connected" if pong else "error",
            "detail": f"v{info.get('redis_version', '?')}",
            "url": _safe_url(url),
            "channel": nt.get("channel", ""),
        }
    except Exception as e:
        return {
            "name": "Noble Trader Redis (upstream)",
            "status": "error",
            "detail": str(e)[:200],
            "url": _safe_url(url),
            "channel": nt.get("channel", ""),
        }


async def check_supabase(config: HermesConfig) -> dict[str, Any]:
    """Check Supabase connection (just ping the REST endpoint)."""
    nt = config.upstream.get("noble_trader", {}).get("supabase", {})
    url = nt.get("url", "")
    key = nt.get("key", "")
    if _is_placeholder(url) or _is_placeholder(key):
        return {
            "name": "Supabase",
            "status": "not_configured",
            "detail": "Set SUPABASE_URL and SUPABASE_KEY in .env",
            "url": "",
        }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            # Ping the REST root — should return 404 or similar, not connection error
            response = await client.get(
                f"{url}/rest/v1/",
                headers={"apikey": key, "Authorization": f"Bearer {key}"},
            )
            # 200 or 404 both mean "we reached Supabase"
            if response.status_code in (200, 404):
                return {
                    "name": "Supabase",
                    "status": "connected",
                    "detail": f"HTTP {response.status_code} — reachable",
                    "url": url,
                }
            else:
                return {
                    "name": "Supabase",
                    "status": "error",
                    "detail": f"HTTP {response.status_code}: {response.text[:100]}",
                    "url": url,
                }
    except Exception as e:
        return {
            "name": "Supabase",
            "status": "error",
            "detail": str(e)[:200],
            "url": url,
        }


async def check_alpaca(config: HermesConfig) -> dict[str, Any]:
    """Check Alpaca paper trading API connection."""
    venue = config.venues.get("alpaca", {})
    if not venue.enabled:
        return {
            "name": "Alpaca",
            "status": "disabled",
            "detail": "Set venues.alpaca.enabled=true in config",
            "url": "",
        }
    creds = venue.credentials
    api_key = creds.get("api_key", "")
    api_secret = creds.get("api_secret", "")
    base_url = creds.get("base_url", "https://paper-api.alpaca.markets")

    if _is_placeholder(api_key) or _is_placeholder(api_secret):
        return {
            "name": "Alpaca",
            "status": "not_configured",
            "detail": "Set ALPACA_API_KEY and ALPACA_API_SECRET in .env",
            "url": base_url,
        }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(
                f"{base_url}/v2/account",
                headers={
                    "APCA-API-KEY-ID": api_key,
                    "APCA-API-SECRET-KEY": api_secret,
                },
            )
            if response.status_code == 200:
                account = response.json()
                return {
                    "name": "Alpaca",
                    "status": "connected",
                    "detail": f"Account: {account.get('account_number', '?')}, "
                             f"Status: {account.get('status', '?')}, "
                             f"Equity: ${account.get('equity', '?')}",
                    "url": base_url,
                    "account": {
                        "number": account.get("account_number"),
                        "status": account.get("status"),
                        "equity": account.get("equity"),
                        "buying_power": account.get("buying_power"),
                        "cash": account.get("cash"),
                    },
                }
            else:
                return {
                    "name": "Alpaca",
                    "status": "error",
                    "detail": f"HTTP {response.status_code}: {response.text[:100]}",
                    "url": base_url,
                }
    except Exception as e:
        return {
            "name": "Alpaca",
            "status": "error",
            "detail": str(e)[:200],
            "url": base_url,
        }


async def check_hyperliquid(config: HermesConfig) -> dict[str, Any]:
    """Check Hyperliquid REST API connection."""
    venue = config.venues.get("hyperliquid", {})
    if not venue.enabled:
        return {
            "name": "Hyperliquid",
            "status": "disabled",
            "detail": "Set venues.hyperliquid.enabled=true in config",
            "url": "",
        }
    creds = venue.credentials
    api_url = creds.get("api_url", "https://api.hl.cyber")
    wallet = creds.get("wallet_address", "")

    if _is_placeholder(api_url):
        return {
            "name": "Hyperliquid",
            "status": "not_configured",
            "detail": "Set HYPERLIQUID_API_URL in .env",
            "url": "",
        }
    try:
        import httpx

        # Ping the meta endpoint — doesn't require wallet auth
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(
                f"{api_url}/info",
                json={"type": "meta"},
            )
            if response.status_code == 200:
                meta = response.json()
                return {
                    "name": "Hyperliquid",
                    "status": "connected",
                    "detail": f"API reachable, "
                             f"{len(meta.get('universe', []))} assets listed",
                    "url": api_url,
                    "wallet_configured": not _is_placeholder(wallet),
                    "wallet_preview": (
                        f"{wallet[:6]}...{wallet[-4:]}" if wallet and len(wallet) > 12
                        else "(not set)"
                    ),
                }
            else:
                return {
                    "name": "Hyperliquid",
                    "status": "error",
                    "detail": f"HTTP {response.status_code}: {response.text[:100]}",
                    "url": api_url,
                }
    except Exception as e:
        return {
            "name": "Hyperliquid",
            "status": "error",
            "detail": str(e)[:200],
            "url": api_url,
        }


async def check_all(config: HermesConfig) -> dict[str, Any]:
    """Check all subsystems in parallel and return summary."""
    import asyncio

    results = await asyncio.gather(
        check_duckdb(config),
        check_hermes_redis(config),
        check_nt_redis(config),
        check_supabase(config),
        check_alpaca(config),
        check_hyperliquid(config),
    )

    statuses = {r["name"]: r["status"] for r in results}
    overall_ok = sum(1 for s in statuses.values() if s == "connected")
    overall_total = len(results)

    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "overall": f"{overall_ok}/{overall_total} connected",
        "overall_ok": overall_ok,
        "overall_total": overall_total,
        "subsystems": results,
    }


def get_recent_heartbeats(config: HermesConfig, limit: int = 20) -> list[dict[str, Any]]:
    """Get recent heartbeats from DuckDB (read-only)."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            result = conn.execute(
                f"""
                SELECT heartbeat_id, ts_received, ts_upstream, symbol, signal,
                       regime, regime_conf, regime_shift, entry_price, stop_loss,
                       take_profit, brick_size, kelly_f, effective_kelly,
                       p_win, ev_per_dollar, accepted, reject_reason
                FROM signal_heartbeats
                ORDER BY ts_received DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_recent_heartbeats_failed", error=str(e))
        return []


def get_ingest_stats(config: HermesConfig) -> dict[str, Any]:
    """Get heartbeat ingest statistics from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return {}
        with duckdb.connect(str(db_path), read_only=True) as conn:
            # Total counts
            total = conn.execute("SELECT COUNT(*) FROM signal_heartbeats").fetchone()[0]
            accepted = conn.execute(
                "SELECT COUNT(*) FROM signal_heartbeats WHERE accepted = TRUE"
            ).fetchone()[0]
            rejected = total - accepted

            # By symbol
            by_symbol = conn.execute(
                """
                SELECT symbol, COUNT(*) as n, MAX(ts_received) as last_seen
                FROM signal_heartbeats
                GROUP BY symbol
                ORDER BY n DESC
                LIMIT 10
                """
            ).fetchall()

            # By signal
            by_signal = conn.execute(
                """
                SELECT signal, COUNT(*) as n
                FROM signal_heartbeats
                GROUP BY signal
                ORDER BY n DESC
                """
            ).fetchall()

            # Recent rate (last hour)
            last_hour = conn.execute(
                """
                SELECT COUNT(*) FROM signal_heartbeats
                WHERE ts_received >= now() - INTERVAL '1 hour'
                """
            ).fetchone()[0]

            # Regime shifts
            shifts = conn.execute(
                "SELECT COUNT(*) FROM signal_heartbeats WHERE regime_shift = TRUE"
            ).fetchone()[0]

            return {
                "total": total,
                "accepted": accepted,
                "rejected": rejected,
                "last_hour": last_hour,
                "regime_shifts": shifts,
                "by_symbol": [
                    {"symbol": r[0], "count": r[1], "last_seen": str(r[2])}
                    for r in by_symbol
                ],
                "by_signal": [{"signal": r[0], "count": r[1]} for r in by_signal],
            }
    except Exception as e:
        log.warning("get_ingest_stats_failed", error=str(e))
        return {}


def get_recent_monitor_events(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent price monitor events from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            # Check if table exists
            try:
                conn.execute("SELECT 1 FROM price_monitor_events LIMIT 1")
            except Exception:
                return []  # table doesn't exist yet

            result = conn.execute(
                f"""
                SELECT event_id, ts, symbol, venue, event_type, severity,
                       last_price, spread_bps, book_imbalance,
                       realized_vol_1m, atr_14, payload, position_id
                FROM price_monitor_events
                ORDER BY ts DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_recent_monitor_events_failed", error=str(e))
        return []


def get_recent_blended_signals(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent blended signals from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM trade_signals_blended LIMIT 1")
            except Exception:
                return []  # table doesn't exist yet

            result = conn.execute(
                f"""
                SELECT signal_id, ts_emitted, symbol, venue, direction,
                       nt_entry_price, nt_stop_price, nt_target_price,
                       nt_effective_kelly, nt_brick_size,
                       meta_regime, meta_regime_confidence, sizing_multiplier,
                       entry_strategy, execution_method,
                       entry_price_target, limit_price,
                       final_size_usd, final_size_pct, risk_amount_usd,
                       brick_pattern, expected_entry_alpha_bps,
                       sizing_limits_hit, sizing_reason
                FROM trade_signals_blended
                ORDER BY ts_emitted DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_recent_blended_signals_failed", error=str(e))
        return []


def get_portfolio_metrics(config: HermesConfig) -> dict[str, Any]:
    """Get latest portfolio metrics from account_snapshots."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return {}
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                result = conn.execute(
                    """
                    SELECT * FROM account_snapshots
                    ORDER BY ts DESC LIMIT 1
                    """
                ).fetchdf()
                if result.empty:
                    return {}
                return result.iloc[0].to_dict()
            except Exception:
                return {}
    except Exception as e:
        log.warning("get_portfolio_metrics_failed", error=str(e))
        return {}


def get_recent_risk_decisions(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent risk decisions from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM risk_decisions LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT decision_id, ts, signal_id, approved,
                       requested_size_usd, approved_size_usd,
                       limits_hit, reason, circuit_breaker_level,
                       var_pre, var_post, autonomy_tier
                FROM risk_decisions
                ORDER BY ts DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_recent_risk_decisions_failed", error=str(e))
        return []


def get_recent_orders(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent orders from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM orders LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT order_id, trade_id, signal_id, ts_created,
                       symbol, venue, side, order_type, time_in_force,
                       qty_requested, price_limit,
                       qty_filled, avg_fill_price, status,
                       algo, total_fees, total_slippage, maker_rebate,
                       position_id
                FROM orders
                ORDER BY ts_created DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_recent_orders_failed", error=str(e))
        return []


def get_recent_fills(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent fills from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM fills LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT fill_id, order_id, ts, symbol, venue, side,
                       qty, price, fee, fee_currency,
                       is_maker, liquidity, arrival_price, slippage_bps
                FROM fills
                ORDER BY ts DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_recent_fills_failed", error=str(e))
        return []


def get_pnl_tear_sheet(config: HermesConfig) -> dict[str, Any]:
    """Generate a PnL tear sheet from DuckDB data."""
    try:
        from hermes.analytics.pnl_service import PnLService
        from hermes.analytics.tear_sheet import TearSheet
        from hermes.portfolio.state import PortfolioStateService

        portfolio_state = PortfolioStateService(config_hash="dashboard")
        pnl_service = PnLService(config, portfolio_state)
        tear_sheet = TearSheet(pnl_service)
        return tear_sheet.generate()
    except Exception as e:
        log.warning("get_pnl_tear_sheet_failed", error=str(e))
        return {"error": str(e)}


def get_pnl_history(config: HermesConfig, limit: int = 100) -> list[dict[str, Any]]:
    """Get realized PnL history from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM pnl_realized LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT pnl_id, ts, symbol, venue, regime_at_close,
                       gross_pnl, fees_total, funding_pnl, slippage_cost,
                       net_pnl, net_pnl_bps, r_multiple,
                       hold_duration_sec, n_fills,
                       direction_pnl, timing_pnl, sizing_pnl, regime_pnl
                FROM pnl_realized
                ORDER BY ts DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_pnl_history_failed", error=str(e))
        return []


def get_equity_curve(config: HermesConfig, limit: int = 500) -> list[dict[str, Any]]:
    """Get equity curve from account_snapshots."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM account_snapshots LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT ts, equity_total, drawdown_pct, gross_exposure_usd,
                       realized_pnl, unrealized_pnl
                FROM account_snapshots
                ORDER BY ts ASC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_equity_curve_failed", error=str(e))
        return []


def get_backtest_runs(config: HermesConfig, limit: int = 20) -> list[dict[str, Any]]:
    """Get recent backtest runs from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM backtest_runs LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT run_id, ts_started, ts_finished, duration_sec,
                       mode, start_ts, end_ts, symbols, initial_equity,
                       n_heartbeats, n_signals_produced, n_signals_approved,
                       n_signals_rejected, n_orders, n_fills,
                       final_equity, total_return_pct, total_net_pnl,
                       max_drawdown_pct, error
                FROM backtest_runs
                ORDER BY ts_started DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_backtest_runs_failed", error=str(e))
        return []


def get_backtest_run_detail(config: HermesConfig, run_id: str) -> dict[str, Any] | None:
    """Get a single backtest run with its tear_sheet (which contains the
    equity curve + per-trade stats). Returns None if not found.
    """
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return None
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM backtest_runs LIMIT 1")
            except Exception:
                return None

            result = conn.execute(
                """
                SELECT run_id, ts_started, ts_finished, duration_sec,
                       mode, start_ts, end_ts, symbols, initial_equity,
                       n_heartbeats, n_signals_produced, n_signals_approved,
                       n_signals_rejected, n_orders, n_fills,
                       final_equity, total_return_pct, total_net_pnl,
                       max_drawdown_pct, tear_sheet, config_hash, error
                FROM backtest_runs
                WHERE run_id = ?
                """,
                [run_id],
            ).fetchdf()
            if result.empty:
                return None
            row = result.iloc[0].to_dict()

            # Parse tear_sheet JSON if present
            import json as _json
            tear_sheet_raw = row.get("tear_sheet")
            if isinstance(tear_sheet_raw, str) and tear_sheet_raw:
                try:
                    row["tear_sheet"] = _json.loads(tear_sheet_raw)
                except Exception:
                    row["tear_sheet"] = {}
            elif tear_sheet_raw is None:
                row["tear_sheet"] = {}
            return row
    except Exception as e:
        log.warning("get_backtest_run_detail_failed", error=str(e), run_id=run_id)
        return None


def get_portfolio_var_history(
    config: HermesConfig, limit: int = 500,
) -> list[dict[str, Any]]:
    """Get historical VaR + drawdown + leverage time series from account_snapshots.

    Used by the Portfolio page's VaR distribution histogram + exposure bars over time.
    """
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM account_snapshots LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT ts, equity_total, drawdown_pct, leverage_gross, leverage_net,
                       gross_exposure_usd, net_exposure_usd,
                       long_exposure_usd, short_exposure_usd,
                       var_1d_99, cvar_1d_99, n_open_positions
                FROM account_snapshots
                WHERE var_1d_99 IS NOT NULL
                ORDER BY ts ASC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_portfolio_var_history_failed", error=str(e))
        return []


def get_portfolio_exposure_breakdown(config: HermesConfig) -> dict[str, Any]:
    """Get current portfolio exposure broken down by venue + asset class.

    Used by the Portfolio page's allocation pie + exposure bars.
    Returns: {by_venue: {...}, by_asset_class: {...}, totals: {...}}
    """
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return {"by_venue": {}, "by_asset_class": {}, "totals": {}}
        with duckdb.connect(str(db_path), read_only=True) as conn:
            # Try the positions table — fall back to latest account_snapshot
            try:
                conn.execute("SELECT 1 FROM trade_journal LIMIT 1")
                # Sum notional by venue + direction from closed trades (as a proxy
                # for current allocation if positions table doesn't exist)
                result = conn.execute(
                    """
                    SELECT venue, direction,
                           COUNT(*) as n_trades,
                           SUM(CASE WHEN exit_pnl IS NOT NULL THEN exit_pnl ELSE 0 END) as pnl
                    FROM trade_journal
                    WHERE closed_at IS NOT NULL
                      AND closed_at > now() - INTERVAL '30 days'
                    GROUP BY venue, direction
                    ORDER BY pnl DESC
                    """
                ).fetchdf()
                if result.empty:
                    return {"by_venue": {}, "by_asset_class": {}, "totals": {}}
                rows = result.to_dict("records")
                by_venue: dict[str, float] = {}
                for r in rows:
                    v = r.get("venue", "unknown")
                    by_venue[v] = by_venue.get(v, 0) + float(r.get("pnl", 0) or 0)
                return {
                    "by_venue": by_venue,
                    "by_asset_class": {},  # not available without a positions table
                    "by_direction": {r.get("direction", "?"): float(r.get("pnl", 0) or 0) for r in rows},
                    "n_trades_30d": int(sum(r.get("n_trades", 0) or 0 for r in rows)),
                }
            except Exception:
                # Fall back to latest snapshot's exposure numbers
                snap = conn.execute(
                    """
                    SELECT gross_exposure_usd, net_exposure_usd,
                           long_exposure_usd, short_exposure_usd, n_open_positions
                    FROM account_snapshots
                    ORDER BY ts DESC LIMIT 1
                    """
                ).fetchdf()
                if snap.empty:
                    return {"by_venue": {}, "by_asset_class": {}, "totals": {}}
                row = snap.iloc[0].to_dict()
                return {
                    "by_venue": {},
                    "by_asset_class": {},
                    "totals": {
                        "gross_exposure_usd": float(row.get("gross_exposure_usd", 0)),
                        "net_exposure_usd": float(row.get("net_exposure_usd", 0)),
                        "long_exposure_usd": float(row.get("long_exposure_usd", 0)),
                        "short_exposure_usd": float(row.get("short_exposure_usd", 0)),
                        "n_open_positions": int(row.get("n_open_positions", 0)),
                    },
                }
    except Exception as e:
        log.warning("get_portfolio_exposure_breakdown_failed", error=str(e))
        return {"by_venue": {}, "by_asset_class": {}, "totals": {}}


def get_decision_tree_definition() -> dict[str, Any]:
    """Static definition of the Hermes Agent decision tree.

    Returned as a nested JSON structure suitable for rendering as an
    interactive tree visualization in the SPA. Thresholds come from
    HermesDecisionTree defaults (configurable in config/default.yaml).
    """
    return {
        "root": {
            "id": "root",
            "label": "Evaluate position",
            "question": "Position exists?",
            "thresholds": {},
            "branches": {
                "yes": {
                    "id": "existing",
                    "label": "Existing position",
                    "question": "PnL ≤ -1% (stop-loss)?",
                    "thresholds": {"stop_loss_pct": -0.01},
                    "branches": {
                        "yes": {
                            "id": "close_sl",
                            "label": "Close (stop-loss)",
                            "action": "close_stop_loss",
                            "color": "error",
                            "icon": "🛑",
                        },
                        "no": {
                            "id": "check_signal",
                            "label": "Signal present?",
                            "question": "BlendedSignal.direction ≠ neutral?",
                            "branches": {
                                "no": {
                                    "id": "native_stops",
                                    "label": "Native stops manage",
                                    "question": "PnL ≥ 2.5% (native TP)?",
                                    "thresholds": {"take_profit_pct": 0.025},
                                    "branches": {
                                        "yes": {
                                            "id": "close_tp",
                                            "label": "Close (take-profit)",
                                            "action": "close_take_profit",
                                            "color": "success",
                                            "icon": "💰",
                                        },
                                        "no": {
                                            "id": "hold_native",
                                            "label": "Hold (native SL/TP)",
                                            "action": "hold_native_stops",
                                            "color": "neutral",
                                            "icon": "⏸",
                                        },
                                    },
                                },
                                "yes": {
                                    "id": "agent_takes_over",
                                    "label": "Agent takes over (native TP suspended)",
                                    "question": "Same direction as position?",
                                    "branches": {
                                        "yes": {
                                            "id": "same_direction",
                                            "label": "Same direction",
                                            "question": "Which exit condition?",
                                            "branches": {
                                                "fading": {
                                                    "id": "trail_stop",
                                                    "label": "PnL > 0 + fading (2+ adverse bricks)",
                                                    "action": "trail_stop",
                                                    "color": "info",
                                                    "icon": "📍",
                                                    "thresholds": {"fading_brick_count": 2},
                                                },
                                                "early_profit": {
                                                    "id": "close_early",
                                                    "label": "PnL ≥ 4.5% (early profit)",
                                                    "action": "close_early_profit",
                                                    "color": "success",
                                                    "icon": "💸",
                                                    "thresholds": {"early_profit_pct": 0.045},
                                                },
                                                "default": {
                                                    "id": "hold",
                                                    "label": "No exit condition → hold",
                                                    "action": "hold",
                                                    "color": "neutral",
                                                    "icon": "⏸",
                                                },
                                            },
                                        },
                                        "no": {
                                            "id": "opposite_direction",
                                            "label": "Opposite direction",
                                            "question": "Strong signal? (conviction ≥ 0.7 + regime confirms)",
                                            "thresholds": {"strong_conviction_threshold": 0.7},
                                            "branches": {
                                                "yes": {
                                                    "id": "flip",
                                                    "label": "Flip (close + reverse)",
                                                    "action": "close_flip",
                                                    "color": "warning",
                                                    "icon": "🔄",
                                                },
                                                "no": {
                                                    "id": "hold_opposite",
                                                    "label": "Hold (native stops)",
                                                    "action": "hold_native_stops",
                                                    "color": "neutral",
                                                    "icon": "⏸",
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
                "no": {
                    "id": "new_signal",
                    "label": "No existing position",
                    "question": "Renko signal present?",
                    "branches": {
                        "no": {
                            "id": "skip",
                            "label": "Skip (no entry)",
                            "action": "skip_no_signal",
                            "color": "neutral",
                            "icon": "⏭",
                        },
                        "yes": {
                            "id": "enter",
                            "label": "Kelly sizing + Execute",
                            "action": "enter_new",
                            "color": "primary",
                            "icon": "✅",
                        },
                    },
                },
            },
        },
        "thresholds": {
            "stop_loss_pct": -0.01,
            "take_profit_pct": 0.025,
            "early_profit_pct": 0.045,
            "fading_brick_count": 2,
            "strong_conviction_threshold": 0.7,
            "trail_stop_activation_pct": 0.01,
        },
        "actions": [
            {"id": "close_stop_loss", "label": "Close (stop-loss)", "color": "error"},
            {"id": "close_take_profit", "label": "Close (take-profit)", "color": "success"},
            {"id": "close_early_profit", "label": "Close (early profit)", "color": "success"},
            {"id": "close_flip", "label": "Flip (close + reverse)", "color": "warning"},
            {"id": "trail_stop", "label": "Trail stop", "color": "info"},
            {"id": "hold", "label": "Hold", "color": "neutral"},
            {"id": "hold_native_stops", "label": "Hold (native stops)", "color": "neutral"},
            {"id": "enter_new", "label": "Enter new", "color": "primary"},
            {"id": "skip_no_signal", "label": "Skip (no signal)", "color": "neutral"},
        ],
    }


def get_simulation_runs(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get recent simulation runs from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM simulation_runs LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT run_id, ts_started, ts_finished, duration_sec,
                       mode, triggered_by, symbols,
                       n_trades, win_rate, avg_r_multiple,
                       sharpe, sortino, calmar,
                       max_drawdown_pct, profit_factor,
                       net_pnl_usd, entry_alpha_bps,
                       deflated_sharpe, rigor_checks_passed, accepted,
                       promoted_to_shadow, promoted_to_live, promotion_decision,
                       baseline_sharpe, beat_baseline, error
                FROM simulation_runs
                ORDER BY ts_started DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_simulation_runs_failed", error=str(e))
        return []


def get_hypotheses(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get hypotheses from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM hermes_hypotheses LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT hypothesis_id, ts_created, hypothesis, rationale,
                       status, confidence, promoted_at
                FROM hermes_hypotheses
                ORDER BY ts_created DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_hypotheses_failed", error=str(e))
        return []


def get_trade_journal_entries(config: HermesConfig, limit: int = 50) -> list[dict[str, Any]]:
    """Get trade journal entries from DuckDB."""
    try:
        import duckdb

        db_path = get_duckdb_path(config)
        if not db_path.exists():
            return []
        with duckdb.connect(str(db_path), read_only=True) as conn:
            try:
                conn.execute("SELECT 1 FROM trade_journal LIMIT 1")
            except Exception:
                return []

            result = conn.execute(
                f"""
                SELECT journal_id, trade_id, symbol, venue, direction, regime_tag,
                       entry_thesis, exit_reason, exit_pnl, exit_r_multiple,
                       hold_duration_sec, postmortem, lessons, tags,
                       opened_at, closed_at
                FROM trade_journal
                ORDER BY created_at DESC
                LIMIT {int(limit)}
                """
            ).fetchdf()
            if result.empty:
                return []
            return result.to_dict("records")
    except Exception as e:
        log.warning("get_trade_journal_failed", error=str(e))
        return []


def get_recent_market_data_stats(config: HermesConfig) -> dict[str, Any]:
    """Get market data storage statistics (Parquet files)."""
    try:
        from pathlib import Path

        parquet_base = Path("./data/parquet")
        if not parquet_base.exists():
            return {"parquet_exists": False, "total_files": 0}

        bars_files = list(parquet_base.glob("bars/**/*.parquet"))
        ticks_files = list(parquet_base.glob("ticks/**/*.parquet"))

        total_bars_size = sum(f.stat().st_size for f in bars_files)
        total_ticks_size = sum(f.stat().st_size for f in ticks_files)

        return {
            "parquet_exists": True,
            "bars_files": len(bars_files),
            "ticks_files": len(ticks_files),
            "bars_size_mb": round(total_bars_size / 1024 / 1024, 2),
            "ticks_size_mb": round(total_ticks_size / 1024 / 1024, 2),
        }
    except Exception as e:
        log.warning("get_market_data_stats_failed", error=str(e))
        return {"parquet_exists": False, "total_files": 0}
