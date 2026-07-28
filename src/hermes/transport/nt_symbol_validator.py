"""nt_symbol validator — checks that a symbol is CURRENTLY ACTIVE in the agent's
local DuckDB `symbols` registry (the dynamic trading universe).

Option (a) + correction (2026-07-24): the agent does NOT own a
plan→symbol allowlist. That mapping lives only in the backend Supabase
`nt_symbol` table (symbol, plan_ids[]) — which the agent does not
have locally. The agent's source of truth for "can I trade this symbol
right now?" is its OWN DuckDB `symbols` table (active|inactive),
populated dynamically by:
    • seed_from_config()      — first-run bootstrap from default.yaml
    • add_symbol()           — idempotent upsert (CLI / L0 subscriber)
    • touch_symbol_seen()     — L0 subscriber marks a symbol seen on
                                  the stream, keeping is_active dynamic

So this validator answers ONE question: is `symbol` an ACTIVE row in the
local `symbols` table? Plan entitlement (10 vs 20 symbols) is
enforced UPSTREAM by the proxy, which receives the plan prefix from the
agent's Redis URL (X-Plan-Prefix header) and filters against its own
static allowlist. The agent is defense-in-depth: if the proxy lets a
symbol through but it isn't in the agent's active universe, the agent
refuses to trade it.

Failure modes:
    • DuckDB unavailable / row missing → FAIL OPEN (return True). The
      proxy is the canonical plan gate; the agent's check is secondary.
    • Row exists but is_active = FALSE → FAIL CLOSED (return False).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from hermes.core.config import HermesConfig
log = structlog.get_logger(__name__)


# ─── Cache ──────────────────────────────────────────────────────────────────
# symbol → (is_active, expires_at_monotonic)
_cache: dict[str, tuple[bool, float]] = {}
_cache_lock = asyncio.Lock()


def plan_slug_from_redis_url(config: HermesConfig) -> str | None:
    """Return the plan slug for the single active plan (Precision Pro = "pp").

    Historically this parsed the plan prefix from the Noble Trader Redis URL
    username. There is now a single plan ("pp" / precision_pro) that receives
    Redis signals, so the slug is a fixed constant — no Redis-URL parsing is
    required. Kept as a function (with the original name) for caller
    compatibility; the `config` argument is retained but unused.
    """
    return "precision_pro"


async def is_symbol_authorized(
    config: HermesConfig,
    symbol: str,
    *,
    plan_id: str | None = None,
) -> bool:
    """Check that `symbol` is an ACTIVE row in the local DuckDB registry.

    Args:
        config: HermesConfig (reads DuckDB via hermes.db.symbol_registry).
        symbol: e.g. "BTCUSD" (bare) or "COINBASE:BTCUSD" (qualified).
        plan_id: Optional explicit plan slug. Currently informational only
                 (logged); the authorization decision is DuckDB active-membership.
                 Kept for caller compatibility.

    Returns:
        True if the (bare) symbol is an active row in `symbols`.
        True if DuckDB is unreachable (fail-open — proxy is the gate).
        False if the row exists but is_active = FALSE (fail-closed).
    """
    cache_ttl = int(
        config.upstream.get("noble_trader", {})
        .get("supabase", {})
        .get("nt_symbol_cache_ttl_sec", 300)
    )

    # ── Cache hit ───────────────────────────────────────────────────────
    now = time.monotonic()
    async with _cache_lock:
        cached = _cache.get(symbol)
        if cached and cached[1] > now:
            return cached[0]

    # ── Resolve plan slug (informational; logged, not used for authZ) ──
    if plan_id is None:
        plan_id = plan_slug_from_redis_url(config)
    if plan_id:
        log.debug("nt_symbol_validator.plan", plan_id=plan_id)

    # ── DuckDB active-membership check ──────────────────────────────
    bare_symbol = symbol.split(":")[-1].upper() if ":" in symbol else symbol.upper()
    try:
        from hermes.db.symbol_registry import get_symbol

        row = await asyncio.to_thread(get_symbol, config, bare_symbol)
    except Exception as exc:
        # DuckDB unreachable — fail OPEN (proxy is the canonical gate).
        log.warning(
            "nt_symbol_validator.duckdb_unreachable",
            symbol=bare_symbol,
            error=str(exc),
            note="Failing open; proxy will enforce plan gate at request time",
        )
        return True

    if row is None:
        # Symbol not in the registry at all — fail CLOSED (unknown symbol).
        log.info("nt_symbol_validator.not_registered", symbol=bare_symbol)
        is_authorized = False
    else:
        is_authorized = bool(row.is_active)

    async with _cache_lock:
        _cache[symbol] = (is_authorized, now + cache_ttl)

    log.debug(
        "nt_symbol_validator.checked",
        symbol=bare_symbol,
        is_active=is_authorized,
    )
    return is_authorized


def invalidate_cache(symbol: str | None = None) -> None:
    """Force-evict cache entries. If symbol is None, clear all."""
    import asyncio as _asyncio

    try:
        loop = _asyncio.get_running_loop()
        loop.create_task(_invalidate_async(symbol))
    except RuntimeError:
        if symbol is None:
            _cache.clear()
        else:
            _cache.pop(symbol, None)


async def _invalidate_async(symbol: str | None) -> None:
    async with _cache_lock:
        if symbol is None:
            _cache.clear()
        else:
            _cache.pop(symbol, None)
