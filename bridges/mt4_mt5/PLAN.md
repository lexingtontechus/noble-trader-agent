# MT4/MT5 Bridge — Build Plan (a: relay + EA, b: MCP wiring)

**Status:** Plan + drop-in code. Reviewed against `src/hermes/transport/redis_subscriber.py`
and `config.yaml` (`mcp_servers`) — see "Verified facts" below.

---

## Verified facts (ground truth)

1. **Upstream channel = Redis STREAM, not pub/sub.** Hermes `ingest` (`HeartbeatSubscriber`)
  does `XREADGROUP` on `signal.raw.noble_trader` (key from `NOBLE_TRADER_REDIS_CHANNEL`),
   consumer group `hermes-l0` (from `NOBLE_TRADER_REDIS_CONSUMER_GROUP`). Each entry's field
   is read by `_extract_payload` which prefers a single field keyed `data`/`payload`/`heartbeat`/
   `message`, else the lone field, else re-serializes a flat map. **Writer → `XADD` with field
   `payload=<json>` is the canonical form.**
2. `**strategy_id` is HARDCODED** at `redis_subscriber.py:259`:
  `parse_heartbeat(raw_str, strategy_id="noble_trader")`. Everything on the stream is stamped
   `noble_trader`. There is NO channel-name inference despite the schema docstring saying so.
   → **Conclusion:** a per-agent/channel naming scheme alone does NOT give attribution; you'd
   have to change that line. Attaching a `source_id` at the *bridge* (authenticated gateway)
   is the clean, low-touch way to attribute + (later) monetize.
3. **Consumer groups fan out delivery, not identity.** Multiple Hermes agents can each run
  their own consumer group on the SAME shared stream and all receive every message — that's
   free and correct. Consumer groups are read-side; they can't carry a per-publisher identity
   or quota. So "monetize by consumer group" is the wrong primitive.
4. **Redis ACL is the wrong layer for billing.** ACL controls *command/key permissions* per
  user (can user X write to `signal.raw.*`?). It has no concept of per-message source identity,
   rate quota by publisher, or usage accounting. Doing publisher attribution via ACL means
   giving each publisher a distinct ACL user + distinct channel, then reconciling — messy, as
   you suspected. Better: one authenticated **bridge gateway** that stamps `source_id`, enforces
   a per-source quota, and writes to the single shared stream.
5. **MCP config shape** (from `mcp_servers` in `config.yaml`):
  ```yaml
   mcp_servers:
     hyperliquid:
       command: bash
       args: [<abs path to wrapper.sh>]
       timeout: 120
       connect_timeout: 60
  ```
   So the MT5 entry is the same shape: a `bash` command running a wrapper that launches
   `mt5-trading-mcp` with its own `.env`.

---

## Design: shared stream + attribution gateway (the "monetize" answer)

```
 MT4/5 EA ──file/HTTP──► bridge_relay.py ──► [GATEWAY: stamp source_id, quota] ──XADD──► signal.raw.noble_trader
                                   │                                              (NOBLE_TRADER_REDIS_*)
                                   │                                   Hermes ingest (consumer group per agent)
                                   ▼
                          mt5-trading-mcp (read + future trading tools) ◄── Hermes discovery (mcp_servers)
```

- **Delivery (free):** Each Hermes agent uses its OWN consumer group on the shared stream →
every agent sees every heartbeat. No duplication, no per-agent channel needed.
- **Attribution (billable):** The relay is a *gateway*. Every publish carries `source_id`
(signed token per publisher) + `msg_id` + `ts`. The gateway enforces a per-`source_id`
rate quota and emits a daily usage counter. To monetize later: meter `source_id` volume,
bill per heartbeat / per filled-signal. Hermes stores `source_id` in `signal_heartbeats`
(schema already has `strategy_id` — extend to add `source_id`, see patch note).
- **Why not per-agent channels:** a shared stream + per-agent consumer group keeps one pipe,
one ACL user, one quota table. Per-agent channels = N streams + N ACL users + reconfig on
every new agent = exactly the mess you flagged.

### Quota / monetization levers (in the gateway, not Redis)

- `source_id` daily heartbeat cap (soft warn, hard block).
- Per-`source_id` accepted-vs-rejected ratio (quality score → tiered pricing).
- Optional signed JWT so a `source_id` can't be spoofed by another publisher.

---

## Build (a) — files

- `bridges/mt4_mt5/ea_emitter.mq5` — EA `OnTick`/`OnTrade` snippet (file-drop + WebRequest).
- `bridges/mt4_mt5/bridge_relay.py` — tails EA jsonl, validates vs `NobleTraderHeartbeat`,
stamps `source_id`, enforces quota, `XADD`s to upstream stream. Standalone (own venv).
- `bridges/mt4_mt5/bridge_relay.service` — systemd unit (cloud-ready; Windows uses Task Scheduler).
- `bridges/mt4_mt5/README.md` — run instructions.

## Build (b) — files

- `bridges/mt4_mt5/mt5_mcp_wrapper.sh` — launches `mt5-trading-mcp` with its `.env`.
- `bridges/mt4_mt5/mt5_mcp.env.example` — the 4 required MT5 vars + server bind.
- `bridges/mt4_mt5/mcp_servers_entry.yaml` — snippet to paste under `mcp_servers:` in
`~/.hermes/config.yaml`.

## Hermes-side change (small, optional, for true attribution)

- `redis_subscriber.py:259` hardcodes `strategy_id="noble_trader"`. To surface `source_id`,
add `source_id: str|None` to `NobleTraderHeartbeat` and have `bridge_relay` put it in the
payload JSON; then either (i) leave `strategy_id` as-is and read `source_id` from the parsed
payload, or (ii) change line 259 to `strategy_id=hb.source_id or "mt4_bridge"`. Until then,
MT4/5 traffic is indistinguishable from NT in `signal_heartbeats` except by `source_id` if
we add the field. Recommended: add the field (non-breaking, `extra="allow"` already covers it).

---

## Readiness gaps (blockers before the bridge produces real trades)

- Desktop MT5 not installed (PlexyTrade webtrader only). MCP skill: webtrader ≠ MCP-usable.
- `mt5-trading-mcp` not installed; not registered in `mcp_servers`.
- `MT5_*` empty in repo `.env`; profile `.env` has them (SERVER+ACCOUNT present).
- `monitor` loop still watches Alpaca/HL WebSockets only → MT4/5 symbols get signals but no
live price/renko/stop state. Bridge should also push ticks via `mt5-trading-mcp get_bars`
or the EA, OR defer MT4/5 symbols from `monitor` until a MT5 price feed is wired.

 