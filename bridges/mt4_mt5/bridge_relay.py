"""
MT4/MT5 -> Noble Trader / Hermes heartbeat relay (bridge gateway).

Reads EA-emitted heartbeats (jsonl or HTTP), validates them against the EXACT
Hermes schema (hermes.schemas.heartbeat.NobleTraderHeartbeat), stamps a signed
`source_id` for per-publisher attribution/quota, and XADDs to the shared upstream
Redis STREAM that Hermes `ingest` (HeartbeatSubscriber) already consumes.

No Hermes code change is required for the signal-in path EXCEPT optionally adding
`source_id` to the schema (see PLAN.md). The relay writes the canonical field
`payload=<json>` which redis_subscriber._extract_payload prefers.

Run as its own process (own venv, NOT the repo venv):
    python bridge_relay.py --source-id mt4_plexytrade --watch hermes_heartbeats.jsonl

Env (or args): NOBLE_TRADER_REDIS_URL, NOBLE_TRADER_REDIS_CHANNEL,
NOBLE_TRADER_REDIS_CONSUMER_GROUP.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timezone

import redis
import structlog

# Import the SAME schema Hermes validates with, so we reject early.
try:
    from hermes.schemas.heartbeat import NobleTraderHeartbeat, HeartbeatValidationError
except Exception:  # relay may run outside repo venv
    NobleTraderHeartbeat = None
    HeartbeatValidationError = Exception

log = structlog.get_logger("mt4_bridge")


# ---------------------------------------------------------------------------
# Attribution / quota (the "monetize" primitive — lives HERE, not in Redis ACL)
# ---------------------------------------------------------------------------
class SourceRegistry:
    """Per-source_id token + daily quota. Replace with a DB/secret manager in prod."""

    def __init__(self, source_id: str, token: str, daily_cap: int = 50_000):
        self.source_id = source_id
        self.token = token  # shared secret; in prod sign with HMAC/JWT
        self.daily_cap = daily_cap
        self._used = 0
        self._day = datetime.now(timezone.utc).date()

    def _rollover(self):
        today = datetime.now(timezone.utc).date()
        if today != self._day:
            self._day, self._used = today, 0

    def check(self) -> tuple[bool, str]:
        self._rollover()
        if self._used >= self.daily_cap:
            return False, f"daily cap {self.daily_cap} reached for {self.source_id}"
        return True, ""

    def consume(self):
        self._rollover()
        self._used += 1

    def sign(self, payload: str) -> str:
        return hashlib.sha256(f"{self.token}:{payload}".encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# EA file-drop watcher
# ---------------------------------------------------------------------------
async def watch_file(path: str, reg: SourceRegistry, r: redis.Redis, channel: str):
    last = 0
    while True:
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            for raw in lines[last:]:
                raw = raw.strip()
                if raw:
                    publish(payload_str=raw, reg=reg, r=r, channel=channel)
            last = len(lines)
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.25)


# ---------------------------------------------------------------------------
# Optional HTTP endpoint (lower latency than file-drop)
# ---------------------------------------------------------------------------
def make_http_app(reg: SourceRegistry, r: redis.Redis, channel: str):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n).decode("utf-8", "ignore")
            ok, msg = publish(body, reg, r, channel)
            code = 200 if ok else (400 if "schema" in msg or "quota" in msg else 502)
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": ok, "msg": msg}).encode())

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", int(os.getenv("BRIDGE_HTTP_PORT", "9100"))), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log.info("http_listener_up", port=os.getenv("BRIDGE_HTTP_PORT", "9100"))


# ---------------------------------------------------------------------------
# Source -> exchange mapping (for multi-exchange instruments).
# A bare symbol from a source that maps to an exchange is qualified as
# "EXCHANGE:SYMBOL" (TradingView convention) so COINBASE:BTCUSD and
# BINANCE:BTCUSD are distinct registry rows with correct asset_class
# (drives PnL). Edit per deployment. Empty value = leave symbol bare.
#
# KEY DESIGN POINT: this map is keyed by SOURCE_ID (the feed), NOT per
# symbol. Switching a feed's exchange is a one-line change here — the per-
# symbol `exchange` column is recorded once at registration and then handled
# by staleness + dynamic-symbol delisting. No growing per-symbol list to
# sync.
#
# The CURRENT live signal feed is Coinbase-priced, so its BTCUSD becomes
# COINBASE:BTCUSD. If you repoint the feed at a Binance-priced source, flip
# this value (or add a new source_id) — do NOT touch individual symbols.
SOURCE_EXCHANGE = {
    "mt4_plexytrade": "COINBASE",   # current live feed = Coinbase-priced
    # "mt4_binance": "BINANCE",     # alt feed = Binance-priced
}
DEFAULT_EXCHANGE = os.getenv("BRIDGE_DEFAULT_EXCHANGE", "")  # e.g. COINBASE


def _qualify_symbol(payload_str: str, source_id: str) -> str:
    """Qualify the symbol with its exchange (TradingView EXCHANGE:SYMBOL form).

    Uses the shared hermes.db.symbol_key.qualify_symbol so the bridge and the
    redis_subscriber agree on one exchange-resolution rule. Precedence:
    explicit `exchange` field in the EA payload -> SOURCE_EXCHANGE[source_id]
    -> BRIDGE_DEFAULT_EXCHANGE. Idempotent (already-qualified left alone).
    """
    import json as _json
    exch = ""
    try:
        obj = _json.loads(payload_str)
        exch = (obj.get("exchange") or "").strip().upper()
    except Exception:
        pass
    from hermes.db.symbol_key import qualify_symbol
    sym = ""
    try:
        sym = (_json.loads(payload_str).get("symbol") or "")
    except Exception:
        pass
    new_sym = qualify_symbol(sym, source_id=source_id, exchange=exch or None,
                             source_exchange=SOURCE_EXCHANGE,
                             default_exchange=DEFAULT_EXCHANGE or None)
    if not new_sym or new_sym == (sym or "").upper():
        return payload_str
    try:
        obj = _json.loads(payload_str)
        obj["symbol"] = new_sym
        return _json.dumps(obj)
    except Exception:
        return payload_str


# ---------------------------------------------------------------------------
# Core publish: validate -> stamp source_id -> quota -> XADD
# ---------------------------------------------------------------------------
def publish(payload_str: str, reg: SourceRegistry, r: redis.Redis, channel: str) -> tuple[bool, str]:
    # 0. Qualify the symbol with its exchange (multi-exchange support)
    payload_str = _qualify_symbol(payload_str, reg.source_id)

    # 1. Schema validation (fail fast, before we count quota)
    if NobleTraderHeartbeat is not None:
        try:
            NobleTraderHeartbeat(**json.loads(payload_str))
        except Exception as e:
            log.warning("schema_invalid", err=str(e)[:200])
            return False, f"schema: {e}"

    # 2. Attribution + quota
    ok, msg = reg.check()
    if not ok:
        return False, msg
    sig = reg.sign(payload_str)
    envelope = {
        "source_id": reg.source_id,
        "msg_id": f"{reg.source_id}:{int(time.time()*1000)}",
        "sig": sig,
        "payload": payload_str,
    }
    try:
        r.xadd(channel, {"payload": json.dumps(envelope)})
    except Exception as e:
        return False, f"redis: {e}"
    reg.consume()
    log.info("published", source_id=reg.source_id, channel=channel)
    return True, "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-id", default=os.getenv("BRIDGE_SOURCE_ID", "mt4_bridge"))
    ap.add_argument("--token", default=os.getenv("BRIDGE_TOKEN", "changeme"))
    ap.add_argument("--daily-cap", type=int, default=int(os.getenv("BRIDGE_DAILY_CAP", "50000")))
    ap.add_argument("--watch", default=os.getenv("BRIDGE_WATCH_FILE", ""))
    ap.add_argument("--http", action="store_true", help="also run HTTP :9100 listener")
    ap.add_argument("--redis-url", default=os.getenv("NOBLE_TRADER_REDIS_URL", "redis://localhost:6379"))
    ap.add_argument("--channel", default=os.getenv("NOBLE_TRADER_REDIS_CHANNEL", "signal.raw.noble_trader"))
    args = ap.parse_args()

    reg = SourceRegistry(args.source_id, args.token, args.daily_cap)
    r = redis.Redis.from_url(args.redis_url, decode_responses=True)
    r.ping()
    log.info("relay_started", source_id=args.source_id, channel=args.channel)

    if args.http:
        make_http_app(reg, r, args.channel)
    if args.watch:
        asyncio.run(watch_file(args.watch, reg, r, args.channel))
    elif not args.http:
        log.warning("nothing_to_do", msg="pass --watch <file> or --http")


if __name__ == "__main__":
    main()
