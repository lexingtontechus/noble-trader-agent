# MT4/MT5 Bridge — Tracking README

This folder is the drop-in MT4/MT5 integration for the Noble Trader / Hermes stack.
It does NOT modify the core repo today; the items below list what the *core repo*
must change when we finalize.

## Architecture
```
MT4/5 EA ──file/HTTP──► bridge_relay.py ──XADD(payload={source_id,sig,payload})──► signal.raw.noble_trader
                                   │                                         (Hermes L0 consumes via consumer group)
                                   ▼
                          mt5-trading-mcp  ◄── Hermes mcp_servers (read + future trading tools)
```

## Files
| File | Role |
|---|---|
| `bridge_relay.py` | Gateway: validate → stamp `source_id` → quota → XADD |
| `ea_emitter.mq5` | EA signal emitter (file-drop or WebRequest) |
| `mt5_mcp_wrapper.sh` | Launch `mt5-trading-mcp` (stdio) for Hermes |
| `mt5_mcp.env.example` | MT5 creds template |
| `mcp_servers_entry.yaml` | Paste under `mcp_servers:` in `~/.hermes/config.yaml` |
| `PLAN.md` | Full design + readiness gaps |
| `CHANGELOG.md` | Dated change tracker |

## Required core-repo changes (when finalizing)
1. **Attribution**: `src/hermes/schemas/heartbeat.py` add `source_id: str|None`;
   `src/hermes/transport/redis_subscriber.py:259` use `hb.source_id or "mt4_bridge"`.
2. **Monitor pricing**: add `src/hermes/transport/adapters/tradingview_adapter.py`
   (implements `VenueAdapter`); register `tradingview` venue in `config.yaml`;
   wire into `monitor` (app.py:1120) + `price_feed.py` so pricing comes from
   tradingviewapi.com (RapidAPI, Ultra plan) — same source Noble Trader backend uses.
3. **Symbol registry**: add MT4/5 symbols (EURUSD, XAUUSD, …) via
   `platform symbols add <sym> --venue tradingview --asset-class forex`.

## Verification done
- Relay round-trip validated against real `NobleTraderHeartbeat` schema + live Redis.
- See `CHANGELOG.md` for dates.
