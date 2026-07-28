"""Proxy delivery heartbeat liveness — Monitor A-pipeline (CRITICAL, 480s).

Part of the SIGNAL pipeline health (backend -> proxy -> agent), DISTINCT from:
  - pricing_sse_watchdog  (pricing/SSE pipeline — TVDA -> proxy -> agent SSE)
  - signal_drought_watchdog (agent received no signal in >4h — WARNING)

The proxy is the single ingestion gateway. It logs a `proxy_heartbeat` row to
Supabase `proxy_delivery_log` every PROXY_HEARTBEAT_LOG_SEC (300s), INDEPENDENT
of signal traffic. This watchdog polls that table for the most recent heartbeat
timestamp and raises CRITICAL if none has arrived within `proxy_heartbeat_timeout_sec`
(default 480s = 300s + 180s buffer, safely above the 5-min sweep/heartbeat cadence).

This proves the proxy is alive AND forwarding — a missing heartbeat means the
proxy process is down or its Supabase writer is broken, so signal delivery has
stopped regardless of whether the agent happens to be in a quiet signal period.

State machine (anti-spam): CRITICAL on transition ok->dead; INFO on recovery.

Reads Supabase via the same anon-key REST pattern as SupabaseBackfiller.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import structlog

from hermes.core.config import HermesConfig
from hermes.core.secrets import get_secret_or_none
from hermes.ops.alerting import Alert, AlertManager, AlertSeverity

log = structlog.get_logger(__name__)

# Default: no heartbeat in 480s = proxy delivery pipeline dead.
DEFAULT_HEARTBEAT_TIMEOUT_SEC = 480.0
# Poll every 60s (heartbeat is 300s; 60s poll catches a miss within one interval).
POLL_INTERVAL_SEC = 60.0
# Table the proxy writes heartbeats to (configurable).
DEFAULT_DELIVERY_TABLE = "proxy_delivery_log"


class ProxyLiveness:
    """CRITICAL alert if the proxy stops heartbeating to proxy_delivery_log."""

    def __init__(
        self,
        config: HermesConfig,
        alert_manager: AlertManager,
        *,
        timeout_sec: float | None = None,
        poll_interval_sec: float = POLL_INTERVAL_SEC,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._alert_manager = alert_manager
        nt = config.upstream.get("noble_trader", {})
        sup = nt.get("supabase", {})
        self._url = sup.get("url", "") or ""
        self._key = sup.get("anon_key", "") or sup.get("key", "") or ""
        if self._url.startswith("secret:"):
            self._url = get_secret_or_none(self._url[7:], "") or ""
        if self._key.startswith("secret:"):
            self._key = get_secret_or_none(self._key[7:], "") or ""
        self._table = nt.get("proxy_delivery_table", DEFAULT_DELIVERY_TABLE)

        self._timeout_sec = (
            timeout_sec
            if timeout_sec is not None
            else float(nt.get("proxy_heartbeat_timeout_sec", DEFAULT_HEARTBEAT_TIMEOUT_SEC))
        )
        self._poll_interval = poll_interval_sec
        self._now = now

        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._dead = False
        self._http_client = None
        self._stats = {
            "checks": 0,
            "critical_alerts": 0,
            "restore_alerts": 0,
            "last_heartbeat_at": 0.0,
            "errors": 0,
        }

    async def _get_client(self):
        if self._http_client is not None:
            return self._http_client
        if not self._url or "<" in self._url or not self._key or "<" in self._key:
            return None
        import httpx

        self._http_client = httpx.AsyncClient(
            base_url=self._url.rstrip("/"),
            headers={
                "apikey": self._key,
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )
        return self._http_client

    async def _latest_heartbeat_ts(self) -> float:
        """Return epoch-seconds of the most recent proxy_heartbeat row, or 0.0."""
        client = await self._get_client()
        if client is None:
            return 0.0
        try:
            # Supabase REST: select max(ts) where event_type='proxy_heartbeat'.
            resp = await client.get(
                f"/rest/v1/{self._table}",
                params={
                    "select": "ts",
                    "event_type": "eq.proxy_heartbeat",
                    "order": "ts.desc",
                    "limit": "1",
                },
            )
            if resp.status_code != 200:
                self._stats["errors"] += 1
                return 0.0
            rows = resp.json()
            if not rows:
                return 0.0
            ts = rows[0].get("ts")
            if not ts:
                return 0.0
            # ts is ISO-8601 UTC; convert to epoch seconds.
            from datetime import datetime, timezone

            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception as exc:
            self._stats["errors"] += 1
            log.warning("proxy_liveness_query_failed", error=str(exc)[:200])
            return 0.0

    async def start(self) -> None:
        if self._running:
            log.warning("proxy_liveness_already_running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run(), name="proxy-liveness")
        log.info("proxy_liveness_started", timeout_sec=self._timeout_sec, table=self._table)

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None
        log.info("proxy_liveness_stopped", stats=self._stats)

    def get_stats(self) -> dict[str, Any]:
        return dict(self._stats)

    async def _run(self) -> None:
        while self._running:
            try:
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("proxy_liveness_error", error=str(exc)[:200])
            await asyncio.sleep(self._poll_interval)

    async def _check_once(self) -> None:
        self._stats["checks"] += 1
        last = await self._latest_heartbeat_ts()
        self._stats["last_heartbeat_at"] = last
        now = self._now()
        if last == 0.0:
            # No heartbeat ever seen. Don't alarm during initial warm-up; the
            # proxy may not have logged one yet. Skip until we have a baseline.
            return
        dead = (now - last) > self._timeout_sec
        if dead and not self._dead:
            self._dead = True
            self._stats["critical_alerts"] += 1
            await self._alert_manager.send_alert(
                Alert(
                    title="Proxy delivery heartbeat MISSING — signal pipeline down",
                    message=(
                        f"No proxy_heartbeat row in Supabase `{self._table}` for "
                        f"> {self._timeout_sec:.0f}s (last at epoch {last:.0f}). The "
                        "noble-trader-proxy is the sole signal gateway "
                        "(backend -> proxy -> signal.proxy.noble_trader). A missing "
                        "heartbeat means the proxy is down or not forwarding — signal "
                        "delivery has stopped. This is the SIGNAL pipeline (separate "
                        "from the pricing/SSE pipeline). Check the proxy process + its "
                        "Supabase writer."
                    ),
                    severity=AlertSeverity.CRITICAL,
                    source="proxy_liveness",
                    data={
                        "timeout_sec": self._timeout_sec,
                        "last_heartbeat_at": last,
                        "seconds_since_heartbeat": round(now - last, 1),
                        "pipeline": "signal",
                    },
                )
            )
            log.error(
                "proxy_liveness_critical",
                timeout_sec=self._timeout_sec,
                last_heartbeat_at=last,
            )
        elif not dead and self._dead:
            self._dead = False
            self._stats["restore_alerts"] += 1
            await self._alert_manager.send_alert(
                Alert(
                    title="Proxy delivery heartbeat RESTORED",
                    message=(
                        "Proxy heartbeat rows are flowing again. Signal delivery "
                        "pipeline (backend -> proxy -> agent) is healthy."
                    ),
                    severity=AlertSeverity.INFO,
                    source="proxy_liveness",
                    data={"pipeline": "signal"},
                )
            )
            log.info("proxy_liveness_restored")
