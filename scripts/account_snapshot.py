#!/usr/bin/env python3
"""Live account snapshot: Alpaca + Hyperliquid.
Uses the platform's own SecretResolver so credentials never leave the subprocess.
Outputs redacted-safe JSON to stdout (no secrets printed).
"""
from __future__ import annotations
import asyncio, json, os, sys
from datetime import datetime, timezone

# Make repo imports + .env resolution work regardless of CWD
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

from hermes.core.secrets import get_secret


def _money(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return x


async def alpaca():
    import httpx
    key = get_secret("alpaca.api_key")
    sec = get_secret("alpaca.api_secret")
    base = get_secret("alpaca.base_url")
    async with httpx.AsyncClient(
        headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec},
        timeout=30.0,
    ) as c:
        acct = (await c.get(f"{base}/v2/account")).json()
        positions = (await c.get(f"{base}/v2/positions")).json()
        ph = (await c.get(f"{base}/v2/account/portfolio/history",
                          params={"period": "1D", "timeframe": "1H"})).json()
        return {
            "account_number": acct.get("account_number"),
            "status": acct.get("status"),
            "currency": acct.get("currency"),
            "equity": _money(acct.get("equity")),
            "cash": _money(acct.get("cash")),
            "buying_power": _money(acct.get("buying_power")),
            "day_trade_count": acct.get("daytrade_count"),
            "positions": [
                {
                    "symbol": p["symbol"],
                    "qty": _money(p["qty"]),
                    "avg_entry": _money(p["avg_entry_price"]),
                    "market_value": _money(p["market_value"]),
                    "unrealized_pl": _money(p["unrealized_pl"]),
                    "unrealized_plpc": _money(p["unrealized_plpc"]),
                    "side": p["side"],
                }
                for p in positions
            ],
            "portfolio_1d_equity": [round(float(e), 2) for e in ph.get("equity", [])][-6:] if ph.get("equity") else [],
        }


async def hyperliquid():
    import httpx
    api = get_secret("hyperliquid.api_url")
    wallet = get_secret("hyperliquid.wallet_address")
    async with httpx.AsyncClient(timeout=30.0) as c:
        # Unified spot balance
        spot = (await c.post(f"{api}/info",
                             json={"type": "spotClearinghouseState", "user": wallet})).json()
        # Perp clearinghouse
        perp = (await c.post(f"{api}/info",
                             json={"type": "clearinghouseState", "user": wallet})).json()
        # Mark the spot balances: filter to >0
        spot_balances = []
        for b in spot.get("balances", []):
            try:
                total = float(b.get("total", "0"))
            except (TypeError, ValueError):
                total = 0.0
            if total > 0:
                spot_balances.append({
                    "coin": b.get("coin"),
                    "total": round(total, 6),
                    "available": round(float(b.get("available", "0") or 0), 6),
                })
        asset_pos = perp.get("assetPositions", [])
        return {
            "wallet": f"{wallet[:6]}...{wallet[-4:]}" if wallet else None,
            "spot_balances": spot_balances,
            "perp_margin_summary": {
                "account_value": _money(perp.get("marginSummary", {}).get("accountValue")),
                "total_margin_used": _money(perp.get("marginSummary", {}).get("totalMarginUsed")),
                "total_nlv": _money(perp.get("marginSummary", {}).get("totalNtlPos")),
            },
            "perp_positions": [
                {
                    "coin": p["position"].get("coin"),
                    "szi": _money(p["position"].get("szi")),
                    "entry_px": _money(p["position"].get("entryPx")),
                    "leverage": p["position"].get("leverage", {}).get("value"),
                    "unrealized_pnl": _money(p["position"].get("unrealizedPnl")),
                    "liquidation_px": _money(p["position"].get("liquidationPx")),
                }
                for p in asset_pos if float(p.get("position", {}).get("szi", 0) or 0) != 0
            ],
        }


async def main():
    out = {"ts": datetime.now(timezone.utc).isoformat(), "alpaca": None, "hyperliquid": None, "errors": {}}
    try:
        out["alpaca"] = await alpaca()
    except Exception as e:
        out["errors"]["alpaca"] = str(e)
    try:
        out["hyperliquid"] = await hyperliquid()
    except Exception as e:
        out["errors"]["hyperliquid"] = str(e)
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
