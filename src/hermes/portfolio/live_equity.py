"""Live brokerage equity feed for the L5 risk engine.

Sums actual account equity across both venues using the platform's own
SecretResolver (credentials never leave the subprocess):

  - Alpaca  : GET /v2/account -> equity (USD)
  - Hyperliquid: spotClearinghouseState (USDC) + clearinghouseState (accountValue)

Returns a single USD total used to anchor the risk engine's drawdown tracking
to real brokerage equity instead of the static --equity default.
"""
from __future__ import annotations

import asyncio

from hermes.core.secrets import get_secret


async def _alpaca_equity() -> float:
    import httpx

    key = get_secret("alpaca.api_key")
    sec = get_secret("alpaca.api_secret")
    base = get_secret("alpaca.base_url")
    async with httpx.AsyncClient(
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
        timeout=30.0,
    ) as c:
        acct = (await c.get(f"{base}/v2/account")).json()
        return float(acct.get("equity", 0) or 0)


async def _hyperliquid_equity() -> float:
    import httpx

    api = get_secret("hyperliquid.api_url")
    wallet = get_secret("hyperliquid.wallet_address")
    total = 0.0
    async with httpx.AsyncClient(timeout=30.0) as c:
        try:
            spot = (await c.post(
                f"{api}/info",
                json={"type": "spotClearinghouseState", "user": wallet},
            )).json()
            for b in spot.get("balances", []):
                try:
                    total += float(b.get("total", "0") or 0)
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass
        try:
            perp = (await c.post(
                f"{api}/info",
                json={"type": "clearinghouseState", "user": wallet},
            )).json()
            try:
                total += float(perp.get("marginSummary", {}).get("accountValue", 0) or 0)
            except (TypeError, ValueError):
                pass
        except Exception:
            pass
    return total


async def get_live_total_equity() -> float:
    """Total USD equity across Alpaca + Hyperliquid.

    Returns 0.0 if both venues fail (caller should fall back to static equity).
    """
    results = await asyncio.gather(
        _alpaca_equity(), _hyperliquid_equity(), return_exceptions=True
    )
    total = 0.0
    for r in results:
        if isinstance(r, float):
            total += r
    return round(total, 2)
