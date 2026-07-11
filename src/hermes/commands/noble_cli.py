"""The `/noble` command group — Noble Trader account + signal intelligence.

Two subcommands:
  noble balance   Live equity across Alpaca + Hyperliquid (via platform secret resolver)
  noble assets    Currently held assets with Noble Trader regime, renko bricks (rebuilt
                  from Hyperliquid candles via the project's RenkoConstructor) and the
                  Hermes 7-state MetaRegime overlay.

Signal data comes from `signal.raw.noble_trader`, cached locally by
`hermes.transport.noble_listener` (latest heartbeat per symbol in Redis
`nt:hb:{symbol}`). If the listener isn't running, `noble assets` falls back to a
live 20s subscription to populate the cache first.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import click
import structlog

from hermes.core.config import load_config
from hermes.core.secrets import get_secret
from hermes.portfolio.live_equity import (
    _alpaca_equity,
    _hyperliquid_equity,
)
from hermes.schemas.heartbeat import NobleTraderHeartbeat

log = structlog.get_logger(__name__)

LOCAL_REDIS = "redis://127.0.0.1:6379/0"
CACHE_PREFIX = "nt:hb:"
TTL_SEC = 600


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
async def _alpaca_positions() -> tuple[float, list[dict]]:
    """Return (equity, positions) for Alpaca using platform secret resolver."""
    import httpx

    key = get_secret("alpaca.api_key")
    sec = get_secret("alpaca.api_secret")
    base = get_secret("alpaca.base_url")
    async with httpx.AsyncClient(
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}, timeout=30.0
    ) as c:
        acct = (await c.get(f"{base}/v2/account")).json()
        equity = float(acct.get("equity", 0) or 0)
        positions = (await c.get(f"{base}/v2/positions")).json()
        return equity, positions


async def _hl_state() -> dict:
    """Return HL spot + perp state using platform secret resolver."""
    import httpx

    api = get_secret("hyperliquid.api_url")
    wallet = get_secret("hyperliquid.wallet_address")
    out: dict = {"spot": [], "perp": None}
    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            spot = (await c.post(
                f"{api}/info", json={"type": "spotClearinghouseState", "user": wallet}
            )).json()
            for b in spot.get("balances", []):
                try:
                    total = float(b.get("total", "0") or 0)
                except (TypeError, ValueError):
                    total = 0.0
                if total > 0:
                    out["spot"].append({"token": b.get("coin"), "total": total})
        except Exception:
            pass
        try:
            perp = (await c.post(
                f"{api}/info", json={"type": "clearinghouseState", "user": wallet}
            )).json()
            out["perp"] = perp
        except Exception:
            pass
    return out


def _venues_held_assets(alp_equity: float, alp_pos: list, hl: dict) -> list[dict]:
    """Normalize held assets across both venues into a common row shape."""
    rows: list[dict] = []
    for p in alp_pos:
        qty = float(p.get("qty", 0) or 0)
        if abs(qty) < 1e-9:
            continue
        rows.append({
            "symbol": p.get("symbol"),
            "venue": "alpaca",
            "qty": qty,
            "side": "long" if qty > 0 else "short",
            "entry": float(p.get("avg_entry_price", 0) or 0),
            "mkt_value": float(p.get("market_value", 0) or 0),
            "upnl": float(p.get("unrealized_pl", 0) or 0),
            "asset_class": p.get("asset_class", "equity"),
        })
    # HL perp
    perp = hl.get("perp")
    if perp:
        for ap in perp.get("assetPositions", []):
            try:
                pos_data = ap.get("position", {}) if isinstance(ap, dict) else {}
                coin = pos_data.get("coin")
                szi = float(pos_data.get("szi", 0) or 0)
                if not coin or abs(szi) < 1e-9:
                    continue
                entry = float(pos_data.get("entryPx", 0) or 0)
                upnl = float(pos_data.get("unrealizedPnl", 0) or 0)
                rows.append({
                    "symbol": f"{coin}-PERP",
                    "venue": "hyperliquid",
                    "qty": szi,
                    "side": "long" if szi > 0 else "short",
                    "entry": entry,
                    "mkt_value": abs(szi) * entry,
                    "upnl": upnl,
                    "asset_class": "crypto-perp",
                })
            except Exception:
                continue
    # HL spot
    for b in hl.get("spot", []):
        if b.get("token") in (None, "USDC", "USDT"):
            continue
        total = b.get("total", 0)
        if total > 0:
            rows.append({
                "symbol": b.get("token"),
                "venue": "hyperliquid-spot",
                "qty": total,
                "side": "long",
                "entry": 0.0,
                "mkt_value": total,
                "upnl": 0.0,
                "asset_class": "crypto-spot",
            })
    return rows


async def _get_cached_heartbeat(symbol: str) -> NobleTraderHeartbeat | None:
    import redis.asyncio as aioredis

    r = aioredis.from_url(LOCAL_REDIS, decode_responses=True)
    raw = await r.get(f"{CACHE_PREFIX}{symbol}")
    await r.close()
    if not raw:
        return None
    try:
        return NobleTraderHeartbeat(**json.loads(raw))
    except Exception:
        return None


async def _seed_cache_from_upstream(timeout_sec: int = 20) -> None:
    """If cache empty, subscribe briefly to upstream to populate it."""
    import redis.asyncio as aioredis

    cfg = load_config()
    nt = cfg.upstream.get("noble_trader", {}).get("redis", {})
    url = nt.get("url", "") or get_secret("noble_trader.redis_url")
    channel = nt.get("channel", "signal.raw.noble_trader")
    if not url or url.startswith("secret:") or "<" in url:
        return

    local = aioredis.from_url(LOCAL_REDIS, decode_responses=True)
    up = aioredis.from_url(url, decode_responses=True)
    pubsub = up.pubsub()
    await pubsub.subscribe(channel)
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout_sec
    try:
        while loop.time() < end:
            try:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2.0)
                if not msg or msg.get("type") != "message":
                    continue
                try:
                    from hermes.schemas.heartbeat import parse_heartbeat

                    hb = parse_heartbeat(msg["data"], strategy_id="noble_trader")
                    await local.set(
                        f"{CACHE_PREFIX}{hb.symbol}",
                        json.dumps(hb.model_dump(), default=str),
                        ex=TTL_SEC,
                    )
                except Exception:
                    pass
            except asyncio.CancelledError:
                break
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
        await up.close()
        await local.close()


def _hl_candles(symbol_hl: str, n: int = 200) -> list[float]:
    """Fetch recent closes for a Hyperliquid perp via the project REST endpoint.

    Uses the same credential-resolver pattern as live_equity.py (creds never
    leave the subprocess). Falls back to [] on any error.
    """
    import httpx

    async def _go():
        api = get_secret("hyperliquid.api_url")
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=24)
        params = {
            "type": "candles",
            "coin": symbol_hl,
            "interval": "15m",
            "startTime": int(start.timestamp() * 1000),
            "endTime": int(end.timestamp() * 1000),
        }
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.post(f"{api}/info", json=params)
            if r.status_code != 200:
                return []
            data = r.json()
            candles = data.get("t", []) if isinstance(data, dict) else data
            closes = []
            for cd in candles:
                if isinstance(cd, dict) and cd.get("c"):
                    closes.append(float(cd["c"]))
                elif isinstance(cd, (list, tuple)) and len(cd) > 4:
                    closes.append(float(cd[4]))  # [t,o,h,l,c,...]
            return closes[-n:]

    try:
        try:
            loop = asyncio.get_running_loop()
            fut = asyncio.run_coroutine_threadsafe(_go(), loop)
            return fut.result(timeout=30)
        except RuntimeError:
            return asyncio.run(_go())
    except Exception as e:
        log.warning("hl_candles_failed", symbol=symbol_hl, error=str(e)[:120])
        return []


def _renko_ladder(closes: list[float], brick_size: float, n_show: int = 8) -> dict:
    """Rebuild renko bricks from closes using the project RenkoConstructor."""
    from hermes.schemas.market import Tick, Venue
    from hermes.signals.renko_engine import RenkoConstructor

    if not closes or brick_size <= 0:
        return {"bricks": [], "current_price": closes[-1] if closes else None}
    constructor = RenkoConstructor(brick_size=brick_size, symbol="X", venue=Venue.HYPERLIQUID)
    for i, px in enumerate(closes):
        ts = datetime.fromtimestamp(1_700_000_000 + i * 60, tz=timezone.utc)
        constructor.on_tick(Tick(symbol="X", venue=Venue.HYPERLIQUID, price=px, size=1.0, ts=ts))
    bricks = constructor.get_bricks()
    shown = [
        {"n": b.brick_number, "dir": b.direction.value if hasattr(b.direction, "value") else str(b.direction),
         "open": b.open_price, "close": b.close_price}
        for b in bricks[-n_show:]
    ]
    return {
        "bricks": shown,
        "current_price": closes[-1],
        "last_dir": shown[-1]["dir"] if shown else None,
        "n_total_bricks": len(bricks),
    }


# --------------------------------------------------------------------------- #
# command group
# --------------------------------------------------------------------------- #
@click.group(name="noble", help="Noble Trader account + signal intelligence.")
def noble() -> None:
    pass


@noble.command(name="balance", help="Live equity across Alpaca + Hyperliquid.")
def noble_balance() -> None:
    async def _go():
        alp_eq_v, hl_v = await asyncio.gather(_alpaca_equity(), _hyperliquid_equity())
        total = round(alp_eq_v + hl_v, 2)
        click.echo("=" * 56)
        click.echo("  NOBLE TRADER - ACCOUNT BALANCE")
        click.echo("=" * 56)
        click.echo(f"  Alpaca (PA3C5BJY2CWK)      : ${alp_eq_v:,.2f}")
        click.echo(f"  Hyperliquid (spot+perp)    : ${hl_v:,.2f}")
        click.echo("-" * 56)
        click.echo(f"  TOTAL BROKERAGE EQUITY     : ${total:,.2f}")
        click.echo("=" * 56)

    asyncio.run(_go())


@noble.command(name="assets", help="Held assets + NT regime, renko bricks, meta-regime.")
@click.option("--with-bricks", is_flag=True, default=True, help="Rebuild renko bricks from HL candles.")
@click.option("--seed-timeout", default=20, help="If cache empty, subscribe this many seconds to upstream.")
def noble_assets(with_bricks: bool, seed_timeout: int) -> None:
    async def _go():
        alp_eq, alp_pos = await _alpaca_positions()
        hl = await _hl_state()
        rows = _venues_held_assets(alp_eq, alp_pos, hl)
        if not rows:
            click.echo("No open positions across Alpaca or Hyperliquid.")
            return

        cached = {r["symbol"]: await _get_cached_heartbeat(r["symbol"]) for r in rows}
        if not any(cached.values()):
            await _seed_cache_from_upstream(seed_timeout)
            cached = {r["symbol"]: await _get_cached_heartbeat(r["symbol"]) for r in rows}

        from hermes.signals.meta_regime import MetaRegimeClassifier

        classifier = MetaRegimeClassifier()

        click.echo("=" * 100)
        click.echo("  NOBLE TRADER - HELD ASSETS")
        click.echo("=" * 100)
        for r in rows:
            sym = r["symbol"]
            hb = cached.get(sym)
            click.echo(f"\n  >> {sym}  [{r['venue']}]  {r['side'].upper()}  qty={r['qty']:.4f}")
            click.echo(f"      entry=${r['entry']:.2f}  mkt_val=${r['mkt_value']:,.2f}  uPnL=${r['upnl']:,.2f}")

            if hb:
                click.echo(f"      -- Noble Trader signal --")
                click.echo(f"      signal={hb.signal.upper()}  entry=${hb.entry_price}  SL=${hb.stop_loss}  TP=${hb.take_profit}")
                click.echo(f"      brick_size={hb.brick_size}  sl_bricks={hb.sl_bricks}  tp_bricks={hb.tp_bricks}")
                click.echo(f"      regime={hb.regime} (conf={hb.regime_conf:.2f})  shift={hb.regime_shift}")
                click.echo(f"      kelly={hb.effective_kelly:.3f}  ev/dollar={hb.ev_per_dollar:.3f}  p_win={hb.p_win:.2f}")
                mr = classifier.classify(heartbeat=hb, symbol=sym)
                click.echo(f"      -- Hermes meta-regime --")
                click.echo(f"      state={mr.state} (conf={mr.confidence:.2f})  sizing_x={mr.sizing_multiplier}  entry={mr.entry_aggressiveness}")

                if with_bricks and r["venue"] in ("hyperliquid", "hyperliquid-spot"):
                    hl_sym = sym.replace("-PERP", "")
                    closes = _hl_candles(hl_sym)
                    if closes:
                        lad = _renko_ladder(closes, hb.brick_size)
                        brick_str = " ".join(
                            f"{b['dir'][0].upper()}{b['close']:.0f}" for b in lad["bricks"]
                        )
                        click.echo(f"      -- Renko ladder (last {len(lad['bricks'])} of {lad['n_total_bricks']}) --")
                        click.echo(f"      {brick_str}  | last_dir={lad['last_dir']}  price=${lad['current_price']:.2f}")
                    else:
                        click.echo("      -- Renko ladder: HL candle feed unavailable (brick_size above) --")
            else:
                click.echo("      (no Noble Trader heartbeat cached for this symbol - run: noble listen)")
        click.echo("\n" + "=" * 100)

    asyncio.run(_go())


@noble.command(
    name="config",
    help=(
        "List per-venue trading params (Alpaca + Hyperliquid) for audit. "
        "--audit shows the DuckDB config_history trail per key. "
        "--set KEY VALUE --why RATIONALE requests a tracked change (written to "
        "DuckDB config_history before the file is updated)."
    ),
)
@click.option("--venue", default=None, help="Filter to one venue: alpaca | hyperliquid")
@click.option("--audit", is_flag=True, default=False, help="Show DuckDB config_history trail per key")
@click.option("--key", "key_path", default=None, help="Show only one dotted key path (e.g. venues.hyperliquid.features.max_leverage)")
@click.option("--set", "set_spec", default=None, help="Request a tracked change: --set 'KEY=VALUE' (requires --why; recorded in DuckDB)")
@click.option("--why", default=None, help="Rationale for the change (required with --set; recorded in DuckDB)")
@click.option("--author", default="human", help="Who is requesting the change (default: human)")
def noble_config(venue, audit, key_path, set_spec, why, author):
    from hermes.core.config import get_config_hash, load_config, _find_config_file
    from hermes.db import config_history as ch

    import yaml as _yaml

    config = load_config()
    config_hash = get_config_hash(config)
    raw = _yaml.safe_load(open(_find_config_file(), "r", encoding="utf-8")) or {}

    # --- write path: request a tracked change ---
    if set_spec is not None:
        if "=" not in set_spec:
            click.echo("ERROR: --set expects 'KEY=VALUE' form (e.g. --set 'venues.hyperliquid.features.max_leverage=6.0').")
            return
        set_path, value = set_spec.split("=", 1)
        set_path = set_path.strip()
        value = value.strip()
        if not why:
            click.echo("ERROR: --set requires --why (rationale is recorded in DuckDB).")
            return
        try:
            result = ch.apply_config_change(
                config, set_path, value,
                source="human", rationale=why, author=author,
            )
            click.echo("=" * 64)
            click.echo("  CONFIG CHANGE REQUESTED (audited in DuckDB)")
            click.echo("=" * 64)
            click.echo(f"  key      : {result['key_path']}")
            click.echo(f"  old      : {result['old_value']}")
            click.echo(f"  new      : {result['new_value']}")
            click.echo(f"  hash     : {result['config_hash']}")
            click.echo(f"  rationale: {why}")
            click.echo("=" * 64)
        except Exception as e:
            click.echo(f"ERROR applying change: {e}")
        return

    # --- read path: build audit lookup ---
    audit_by_key: dict[str, dict] = {}
    current_row = None
    if audit:
        try:
            rows = ch.get_config_history(config, limit=300)
            # newest-first; keep the FIRST (most recent) row that touched each key
            for r in rows:
                diff = r.get("diff") or {}
                if not isinstance(diff, dict):
                    continue
                for k in diff.keys():
                    if k not in audit_by_key:
                        audit_by_key[k] = r
            current_row = ch.get_config_by_hash(config, config_hash)
        except Exception as e:
            click.echo(f"(audit lookup failed: {e})")

    def audit_line(path: str) -> str:
        if not audit:
            return ""
        # match exact path or a parent prefix (e.g. daily_profit tier list)
        row = audit_by_key.get(path)
        if row is None:
            for k, v in audit_by_key.items():
                if path == k or path.startswith(k + ".") or k.startswith(path + "."):
                    row = v
                    break
        if not row:
            return "   [audit: no prior change recorded]"
        ts = str(row.get("ts"))[:19]
        return f"   [audit: last changed {ts} by {row.get('author')} ({row.get('source')}) — {str(row.get('rationale'))[:60]}]"

    def mask(v):
        if isinstance(v, str) and v.startswith("secret:"):
            return "secret:***"
        return v

    def walk(prefix, node, depth=0):
        if isinstance(node, dict):
            # don't dump credential VALUES — show only key names
            if prefix.endswith("credentials"):
                for ck in node.keys():
                    click.echo(f"    {ck}: secret:***")
                return
            for k, v in node.items():
                p = f"{prefix}.{k}" if prefix else k
                if key_path and not (p == key_path or p.startswith(key_path + ".") or key_path.startswith(p + ".")):
                    continue
                if isinstance(v, (dict, list)):
                    click.echo(f"  {p}:")
                    walk(p, v, depth + 1)
                else:
                    click.echo(f"    {k}: {mask(v)}")
                    if depth >= 1:
                        click.echo(audit_line(p))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                p = f"{prefix}[{i}]"
                if key_path and not (p == key_path or p.startswith(key_path)):
                    continue
                if isinstance(item, dict):
                    click.echo(f"  {p}:")
                    walk(p, item, depth + 1)
                else:
                    click.echo(f"    - {mask(item)}")

    venues = raw.get("venues", {})
    want = [venue] if venue else list(venues.keys())
    click.echo("=" * 64)
    click.echo("  NOBLE TRADER - TRADING CONFIG (per venue)")
    click.echo("=" * 64)
    for v in want:
        if v not in venues:
            click.echo(f"  (venue '{v}' not found)")
            continue
        click.echo(f"\n>> VENUE: {v}")
        walk(f"venues.{v}", venues[v])

    # Account-level risk (governs leverage/exposure)
    acct = raw.get("account", {})
    if acct and not venue:
        click.echo("\n>> ACCOUNT-LEVEL RISK")
        walk("account", acct)

    # Daily wins / profit cooloff CBs (the new kill switches)
    cbs = raw.get("circuit_breakers", {}).get("manager", {})
    if cbs and not venue:
        for cb in ("daily_wins", "daily_profit"):
            if cb in cbs:
                click.echo(f"\n>> CIRCUIT BREAKER: {cb}")
                walk(f"circuit_breakers.manager.{cb}", cbs[cb])

    if audit:
        if current_row:
            ts = str(current_row.get("ts"))[:19]
            click.echo(f"\n[Audit] Current config snapshot hash={config_hash}")
            click.echo(f"         recorded: {ts} by {current_row.get('author')} ({current_row.get('source')})")
        else:
            click.echo(f"\n[Audit] Current config hash={config_hash} is NOT yet recorded in config_history.")

    click.echo("\n" + "=" * 64)
    click.echo("  To request a change: noble config --set 'venues.x.y=VALUE' --why '<reason>'")
    click.echo("=" * 64)


@noble.command(name="listen", help="Start the Noble Trader signal listener (caches latest heartbeat per symbol to local Redis).")
def noble_listen():

    from hermes.transport.noble_listener import main as listener_main

    click.echo("Starting Noble Trader signal listener (Ctrl+C to stop)...")
    listener_main()


def register_noble(parent) -> None:
    parent.add_command(noble)
