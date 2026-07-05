"""
Supabase backfill adapter.

Pulls historical Noble Trader data from Supabase:
- `nt_sweep_result`: weekly heavy + light sweeps (optimal brick_size/sl/tp per symbol)
- `nt_regime_log`: periodic regime snapshots (every 5-15min per symbol)

Mirrors into local DuckDB tables for fast offline analysis.

See roadmap §6.2.10 for full schema documentation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog

from hermes.core.config import HermesConfig
from hermes.db.migrate import get_duckdb_path
from hermes.core.secrets import get_secret_or_none

log = structlog.get_logger(__name__)


class SupabaseBackfiller:
    """
    Pulls historical NT data from Supabase into local DuckDB mirrors.

    Usage:
        backfiller = SupabaseBackfiller(config)
        await backfiller.backfill(days_back=365)
    """

    def __init__(self, config: HermesConfig) -> None:
        nt_supabase = config.upstream.get("noble_trader", {}).get("supabase", {})

        self._url = nt_supabase.get("url", "")
        # Use anon_key (publishable key subject to RLS policies) — NOT the
        # service_role key. The service_role key bypasses RLS and grants full
        # admin access to the entire Supabase project, which is unsafe to
        # distribute in an open-source / multi-agent deployment.
        self._key = nt_supabase.get("anon_key", "") or nt_supabase.get("key", "")
        self._sweep_table = nt_supabase.get("sweep_result_table", "nt_sweep_result")
        self._regime_table = nt_supabase.get("regime_log_table", "nt_regime_log")
        self._db_path = get_duckdb_path(config)

        # Resolve secret: prefixes
        if self._url.startswith("secret:"):
            self._url = get_secret_or_none(self._url[7:], "") or ""
        if self._key.startswith("secret:"):
            self._key = get_secret_or_none(self._key[7:], "") or ""

        self._http_client = None

    async def _get_client(self):
        """Lazy-init HTTP client for Supabase REST API."""
        if self._http_client is not None:
            return self._http_client

        if not self._url or "<" in self._url or not self._key or "<" in self._key:
            raise RuntimeError(
                "Supabase URL or key not configured. Fill in .env with real values."
            )

        import httpx

        self._http_client = httpx.AsyncClient(
            base_url=self._url,
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self._http_client

    async def backfill(
        self,
        days_back: int = 365,
        symbols: list[str] | None = None,
        batch_size: int = 1000,
    ) -> dict[str, int]:
        """
        Backfill historical data from Supabase into local DuckDB.

        Args:
            days_back: How many days of history to pull
            symbols: Filter by symbols (None = all)
            batch_size: Rows per request (Supabase paginates at 1000 max)

        Returns:
            Stats dict with counts of rows ingested per table
        """
        log.info(
            "backfill_starting",
            days_back=days_back,
            symbols=symbols,
            supabase_url=self._url,
        )

        since = (datetime.now(timezone.utc) - timedelta(days=days_back)).isoformat()

        stats = {
            "sweep_results_ingested": 0,
            "regime_logs_ingested": 0,
            "errors": 0,
        }

        try:
            sweep_count = await self._backfill_table(
                table=self._sweep_table,
                local_table="nt_sweep_results_local",
                since=since,
                symbols=symbols,
                batch_size=batch_size,
                apply_dq=True,
            )
            stats["sweep_results_ingested"] = sweep_count

            regime_count = await self._backfill_table(
                table=self._regime_table,
                local_table="nt_regime_log_local",
                since=since,
                symbols=symbols,
                batch_size=batch_size,
                apply_dq=False,
            )
            stats["regime_logs_ingested"] = regime_count

        except Exception as e:
            stats["errors"] += 1
            log.error("backfill_failed", error=str(e))
            raise

        log.info("backfill_complete", **stats)
        return stats

    async def _backfill_table(
        self,
        table: str,
        local_table: str,
        since: str,
        symbols: list[str] | None,
        batch_size: int,
        apply_dq: bool,
    ) -> int:
        """Backfill a single table from Supabase to DuckDB."""
        import duckdb
        import httpx

        client = await self._get_client()
        total_ingested = 0
        offset = 0

        # Build query params
        base_params = {
            "select": "*",
            "sweep_timestamp": f"gte.{since}",
            "order": "sweep_timestamp.asc",
            "limit": str(batch_size),
        }
        if symbols:
            base_params["symbol"] = f"in.({','.join(symbols)})"

        while True:
            params = {**base_params, "offset": str(offset)}
            log.debug("fetching_batch", table=table, offset=offset, limit=batch_size)

            try:
                response = await client.get(
                    f"/rest/v1/{table}", params=params
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                log.error(
                    "supabase_http_error",
                    table=table,
                    status=e.response.status_code,
                    body=e.response.text[:500],
                )
                raise

            rows = response.json()
            if not rows:
                break

            # Transform rows for DuckDB insert
            transformed = [self._transform_row(row, apply_dq) for row in rows]

            # Insert into DuckDB
            with duckdb.connect(str(self._db_path)) as conn:
                self._insert_rows(conn, local_table, transformed)

            total_ingested += len(rows)
            offset += len(rows)
            log.info(
                "batch_ingested",
                table=table,
                batch_size=len(rows),
                total_so_far=total_ingested,
            )

            if len(rows) < batch_size:
                break  # last batch

        return total_ingested

    @staticmethod
    def _transform_row(row: dict[str, Any], apply_dq: bool) -> dict[str, Any]:
        """Transform a Supabase row to DuckDB insert format."""
        # Parse sweep_timestamp to datetime
        sweep_ts = row.get("sweep_timestamp")
        if isinstance(sweep_ts, str):
            sweep_ts = datetime.fromisoformat(sweep_ts.replace("Z", "+00:00"))

        transformed = {
            "nt_id": row["id"],
            "symbol": row["symbol"],
            "asset_class": row["asset_class"],
            "brick_size": row["brick_size"],
            "sl_bricks": row["sl_bricks"],
            "tp_bricks": row["tp_bricks"],
            "sharpe": row.get("sharpe"),
            "total_return": row.get("total_return"),
            "annual_return": row.get("annual_return"),
            "max_drawdown_pct": row.get("max_drawdown_pct"),
            "win_rate": row.get("win_rate"),
            "n_trades": row.get("n_trades"),
            "profit_factor": row.get("profit_factor"),
            "regime": row.get("regime"),
            "regime_conf": row.get("regime_conf"),
            "kelly_f": row.get("kelly_f"),
            "markov_p_up": row.get("markov_p_up"),
            "markov_p_dn": row.get("markov_p_dn"),
            "sweep_window": row.get("sweep_window"),
            "sweep_duration_ms": row.get("sweep_duration_ms"),
            "n_combos_tested": row.get("n_combos_tested"),
            "error": row.get("error"),
            "sweep_timestamp": sweep_ts,
            "source": row.get("source"),
        }

        # Apply data quality checks for sweep results
        if apply_dq:
            anomalies = SupabaseBackfiller._check_dq_anomalies(transformed)
            transformed["dq_anomalies"] = anomalies
            transformed["dq_trusted"] = len(anomalies) == 0
        else:
            transformed["dq_anomalies"] = []
            transformed["dq_trusted"] = True

        return transformed

    @staticmethod
    def _check_dq_anomalies(row: dict[str, Any]) -> list[str]:
        """
        Apply data quality checks per roadmap §6.2.10.

        Flags known NT anomalies so Hermes can exclude bad rows from analysis.
        """
        anomalies: list[str] = []

        sharpe = row.get("sharpe")
        if sharpe is not None and sharpe > 20:
            anomalies.append("sharpe_too_high")

        max_dd = row.get("max_drawdown_pct")
        if max_dd is not None and max_dd == 0:
            anomalies.append("max_dd_zero")
        if max_dd is not None and max_dd > 0:
            anomalies.append("max_dd_positive_sign")

        pf = row.get("profit_factor")
        if pf is not None and pf == 0:
            anomalies.append("profit_factor_zero")

        # Regime says bull but strategy losing (Hermes's value-add signal)
        regime = row.get("regime")
        sharpe = row.get("sharpe")
        if regime and "bull" in regime.lower() and sharpe is not None and sharpe < 0:
            anomalies.append("regime_strategy_disagree")

        return anomalies

    @staticmethod
    def _insert_rows(conn, table: str, rows: list[dict[str, Any]]) -> None:
        """Insert rows into DuckDB (upsert by nt_id)."""
        if not rows:
            return

        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        column_str = ", ".join(columns)

        # Delete existing rows first (upsert via delete+insert)
        nt_ids = [r["nt_id"] for r in rows]
        placeholders_ids = ", ".join(["?"] * len(nt_ids))
        conn.execute(
            f"DELETE FROM {table} WHERE nt_id IN ({placeholders_ids})", nt_ids
        )

        # Insert new rows
        tuples = [tuple(r[c] for c in columns) for r in rows]
        conn.executemany(
            f"INSERT INTO {table} ({column_str}) VALUES ({placeholders})",
            tuples,
        )

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
