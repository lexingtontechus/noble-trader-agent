#!/usr/bin/env python3
"""Noble Trader live signal listener (daemon).

Subscribes to the upstream Noble Trader Redis channel `signal.raw.noble_trader`
(Upstash RESP, credentials resolved via the platform SecretResolver — never
leave the subprocess), validates each payload with NobleTraderHeartbeat, and
caches the latest heartbeat per symbol into the LOCAL Hermes Redis under
`nt:hb:{symbol}` (JSON, 1h TTL).

This lets the /noble CLI read the most recent signal for every asset instantly
without depending on the upstream publish cadence (which is infrequent /
event-driven). Run once in the background:

    python -m hermes.transport.noble_listener

It is resilient: auto-reconnect with backoff, and it never dies on a single bad
payload (logged + skipped).
"""
from __future__ import annotations

import asyncio
import json
import signal as os_signal
import sys

import structlog

from hermes.core.config import load_config
from hermes.core.secrets import get_secret
from hermes.schemas.heartbeat import NobleTraderHeartbeat, parse_heartbeat

log = structlog.get_logger(__name__)

LOCAL_REDIS_URL = "redis://127.0.0.1:6379/0"
CACHE_KEY_PREFIX = "nt:hb:"
CACHE_TTL = 3600  # 1h


def _safe_host(url: str) -> str:
    if "@" in url:
        return url.split("@")[-1].split(":")[0]
    return url


async def _run(channel: str, upstream_url: str) -> None:
    import redis.asyncio as aioredis

    local = aioredis.from_url(LOCAL_REDIS_URL, decode_responses=True)
    await local.ping()
    log.info("noble_listener_local_redis", url="127.0.0.1:6379")

    up = aioredis.from_url(upstream_url, decode_responses=True)
    pubsub = up.pubsub()
    await pubsub.subscribe(channel)
    log.info("noble_listener_subscribed", channel=channel, upstream=_safe_host(upstream_url))

    while True:
        try:
            raw = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
            if not raw or raw.get("type") != "message":
                continue
            try:
                hb = parse_heartbeat(raw["data"], strategy_id="noble_trader")
                payload = hb.model_dump()
                payload["ts_received"] = __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat()
                await local.set(
                    f"{CACHE_KEY_PREFIX}{hb.symbol}",
                    json.dumps(payload, default=str),
                    ex=CACHE_TTL,
                )
                log.info(
                    "noble_heartbeat_cached",
                    symbol=hb.symbol,
                    signal=hb.signal,
                    regime=hb.regime,
                    entry=hb.entry_price,
                )
                backoff = 1.0
            except Exception as e:
                log.warning("noble_heartbeat_parse_failed", error=str(e)[:200])
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("noble_listener_loop_error", error=str(e)[:200], backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


async def _supervise(channel: str, upstream_url: str) -> None:
    backoff = 1.0
    while True:
        try:
            await _run(channel, upstream_url)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("noble_listener_error", error=str(e)[:200], backoff=backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30.0)


def main() -> None:
    cfg = load_config()
    nt = cfg.upstream.get("noble_trader", {}).get("redis", {})
    upstream_url = nt.get("url", "") or get_secret("noble_trader.redis_url")
    channel = nt.get("channel", "signal.raw.noble_trader")
    if not upstream_url or upstream_url.startswith("secret:") or "<" in upstream_url:
        log.error("noble_listener_no_url", note="set noble_trader.redis_url in .env")
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(_supervise(channel, upstream_url))

    def _shutdown(*_):
        task.cancel()

    for s in (os_signal.SIGINT, os_signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _shutdown)
        except NotImplementedError:
            pass

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        task.cancel()
        loop.run_until_complete(task)
        loop.close()


if __name__ == "__main__":
    main()
