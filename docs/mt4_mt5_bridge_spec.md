# MT4 / MT5 Signal Bridge Specification (Hermes ↔ Noble Trader pipeline)

**Status:** Draft v0.1 — for review
**Author:** Hermes (noble-agent)
**Context:** MVP for horizontal scale-out to multiple Hermes agents (24/7 Hermes cloud).
Paper mode is acceptable for the MVP. Noble Trader's upstream now normalizes pricing
via `tradingviewapi` (multi-source, single API) — the Hermes-side signal contract is
already venue-agnostic, so MT4/5 plugs in as just another heartbeat source.

---

## 1. Goal & topology

MT4/5 acts as a **signal source + (future) execution venue**. Hermes owns regime overlay,
portfolio risk, circuit breakers, rigor, audit, and multi-agent governance — exactly the
split already encoded in the `noble-trader-quant-hf-manager` skill (strategy brain vs
entry/execution brain), extended to an external broker.

```
 MT4/5 EA (OnTick / OnTrade)                Hermes (unchanged)
 ┌──────────────────────┐                   ┌──────────────────────────────┐
 │ computes buy/sell/   │   heartbeat JSON  │ L0 HeartbeatSubscriber        │
 │ SL/TP from its logic │ ────────────────► │  XREAD signal.raw.noble_trader│
 └──────────────────────┘   (file / HTTP)   │  → validate (NobleTraderHeart-│
        │                                   │    beat) → DuckDB → re-publish│
        │  (future / live phase)            │    signal.raw.hermes.{symbol} │
        ▼                                   │       │                        │
 mt5-trading-mcp (read tools)               │  L4 Synthesizer → BlendedSignal│
  prices / positions / fills                │       │                        │
        ▲                                   │  L5 risk gate → risk.decision │
        │  (future execution adapter)       │       ▼                        │
 Hermes execution → MT4/5 fill  ◄────────── │  L3 execute (paper now)       │
 └──────────────────────┘                   └──────────────────────────────┘
```

**Key invariant:** the heartbeat payload is byte-for-byte the same schema Hermes already
consumes from Noble Trader. No Hermes code change required for the *signal-in* path.

---

## 2. Signal contract (source of truth: `src/hermes/schemas/heartbeat.py`)

The bridge MUST emit a JSON object validating against `NobleTraderHeartbeat`
(Pydantic v2). `model_config = {"extra": "allow"}` so the bridge may add forward-compat
fields. Field requirements:

### Required (no default — must be supplied)
| Field | Type | Notes for MT4/5 bridge |
|---|---|---|
| `symbol` | str | e.g. `EURUSD`, `XAUUSD`. Use MT5 symbol name. |
| `ts` | int | Unix **ms** from EA clock (`TimeCurrent()*1000`). |
| `signal` | `buy`\|`sell`\|`neutral` | Map EA order side. |
| `entry_price` | float >0 | EA suggested entry. |
| `stop_loss` | float >0 | EA SL. |
| `take_profit` | float >0 | EA TP. |
| `aggression` | `passive`\|`mid`\|`aggressive` | Routing hint; default `mid`. |
| `brick_size` | float >0 | Renko brick. Bridge computes (see §4). |
| `sl_bricks` | float >0 | `(entry-stop)/brick_size`. |
| `tp_bricks` | float >0 | `(tp-entry)/brick_size`. |
| `regime` | str | Any label; bridge sends `ea_native` if EA has none. |
| `regime_conf` | float 0–1 | Default `0.5` if EA provides none. |
| `regime_shift` | `true`\|`false` | Default `false`. |
| `shift_at` | int ≥0 | `0` if no shift. |
| `shifts_24h` | int ≥0 | `0` if untracked. |
| `kelly_f` | float ≥0 | See §4 — do NOT send `0` (kills sizing). |
| `effective_kelly` | float ≥0 | Same as above. |
| `ev` | float | Default `0.0`. |
| `ev_per_dollar` | float | Default `0.0`. |
| `p_win` | float 0–1 | Default `0.5`. |
| `p_regime` | float 0–1 | Default `0.5`. |
| `p_imbalance` | float 0–1 | Default `0.5`. |
| `p_markov` | float 0–1 | Default `0.5`. |
| `ev_scale` | float | Default `1.0`. |
| `markov_current_state` | `UP`\|`DOWN`\|`FLAT` | Derive from `signal`. |

### Optional (omit if unavailable)
- `p_timesfm`, `timesfm_horizon`
- `tail_risk_score`, `tail_risk_action`
- `prev_regime`
- `heartbeat_id`, `strategy_id` → **do NOT send**; Hermes assigns `heartbeat_id`
  (UUID) and infers `strategy_id` from the channel name.

> Hermes's own `MetaRegimeClassifier` + `EntryTimingOptimizer` overlay the upstream
> regime/EV. Defaults above are safe for MVP — Hermes re-derives the real decision.

---

## 3. Component A — MT4/5 EA emitter

The EA must hand its decision to the bridge relay. Two transport options:

### A1. File drop (simplest, zero MT5 config)
EA writes one JSON line per signal to
`<MT5 Data Folder>/Files/hermes_heartbeats.jsonl` (append). Relay tails the file.

```mql5
// OnTrade / OnTick — when a new signal is generated
void EmitHeartbeat(string symbol, int signal, double entry, double sl, double tp)
{
   string fn = "hermes_heartbeats.jsonl";
   long  h = FileOpen(fn, FILE_READ|FILE_WRITE|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   long ts = (long)TimeCurrent() * 1000;
   double brick = SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE) * 10; // tune
   string dir = (signal>0)?"buy":(signal<0?"sell":"neutral");
   string j = StringFormat(
     "{\"symbol\":\"%s\",\"ts\":%lld,\"signal\":\"%s\",\"entry_price\":%f,"
     "\"stop_loss\":%f,\"take_profit\":%f,\"aggression\":\"mid\","
     "\"brick_size\":%f,\"sl_bricks\":%f,\"tp_bricks\":%f,"
     "\"regime\":\"ea_native\",\"regime_conf\":0.5,\"regime_shift\":\"false\","
     "\"shift_at\":0,\"shifts_24h\":0,\"kelly_f\":0.02,\"effective_kelly\":0.02,"
     "\"ev\":0.0,\"ev_per_dollar\":0.0,\"p_win\":0.5,\"p_regime\":0.5,"
     "\"p_imbalance\":0.5,\"p_markov\":0.5,\"ev_scale\":1.0,\"markov_current_state\":\"%s\"}",
     symbol, ts, dir, entry, sl, tp, brick,
     (entry-sl)/brick, (tp-entry)/brick,
     (signal>0)?"UP":(signal<0?"DOWN":"FLAT"));
   FileWrite(h, j);
   FileClose(h);
}
```

### A2. WebRequest (lower latency)
EA `WebRequest()` POSTs the same JSON to `http://127.0.0.1:9100/heartbeat`.
Requires adding the URL to MT5 *Tools → Options → Expert Advisors → Allow WebRequest*.

**Recommendation:** A1 for MVP (no config, robust); A2 when latency matters.

---

## 4. Component B — Python relay (`mt5_heartbeat_bridge.py`)

Reads EA output → validates against `NobleTraderHeartbeat` → **XADD** to the upstream
Redis **STREAM** `signal.raw.noble_trader` (Hermes L0 already XREADs this with consumer
group `hermes-l0` — see `src/hermes/transport/redis_subscriber.py`). No Hermes change.

```python
# mt5_heartbeat_bridge.py  (runs as its own process; not in repo venv)
import json, time, redis, asyncio
from hermes.schemas.heartbeat import NobleTraderHeartbeat, HeartbeatValidationError

r = redis.Redis.from_url("redis://<NT-UPSTASH-OR-LOCAL>:6379", decode_responses=True)
CHANNEL = "signal.raw.noble_trader"          # multi-agent: signal.raw.noble_trader.{agent_id}
STRATEGY_ID = "mt4_bridge"                    # inferred by Hermes from channel

async def relay(path="hermes_heartbeats.jsonl"):
    last = 0
    while True:
        try:
            with open(path) as f:
                lines = f.readlines()
            for ln in lines[last:]:
                try:
                    hb = NobleTraderHeartbeat(**json.loads(ln))
                    r.xadd(CHANNEL, {"payload": hb.model_dump_json()})
                except (HeartbeatValidationError, json.JSONDecodeError) as e:
                    log.warning("bad_heartbeat", err=str(e))
            last = len(lines)
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.25)
```

**Multi-agent:** set `CHANNEL = f"signal.raw.noble_trader.{agent_id}"` and point each
Hermes agent's `config.upstream.noble_trader.redis.channel` at the same value. Hermes
infers `strategy_id` from the channel name → per-agent provenance with no code change.

**`kelly_f` / `effective_kelly`:** Hermes sizing reads `nt_effective_kelly`. Sending `0`
yields zero size. Bridge MUST send a real fraction — derive from the EA's risk % per
trade (e.g. risk 1% of equity → `effective_kelly ≈ 0.01`), or set a fixed `0.02` for MVP.
Flagged as a config decision: alternatively add a Hermes config flag
`treat_ea_kelly_as_fixed` so EA-sourced signals use a constant sizing multiplier.

---

## 5. Component C — MT4/5 execution adapter (FUTURE / live phase)

Paper mode is fine for the MVP, so this is **out of scope now** but specced for the
paper→live flip (the Hermes `execute --paper=False` branch is currently stubbed at
`app.py:1769`).

Mirror `src/hermes/transport/adapters/alpaca_adapter.py` /
`hyperliquid_adapter.py`: a `BaseVenueAdapter` subclass `MT4Adapter` implementing
`submit_order / cancel_order / get_position / get_account`. Back it with
`mt5-trading-mcp` (read + trading tools) or the `MetaTrader5` Python lib directly.
Wire into `src/hermes/execution/router.py` so `risk.decision.*` → MT4 fill.

This is the path that makes MT4/5 a **full execution venue**, closing the only dimension
where MT4 EA currently beats Hermes (live real-capital fills).

---

## 6. MCP readiness (PlexyTrade `mt5-trading-mcp`) — VERDICT: NOT good-to-go

Verified against ground truth (repo `$NOBLE_STACK_ROOT`, profile `.env`):

| Check | State | Detail |
|---|---|---|
| Desktop MT5 terminal installed | ❌ | `terminal64.exe` not at default path; no running process. User currently uses **PlexyTrade webtrader** — the MCP skill is explicit that webtrader **cannot** be used by the MCP server. Must install desktop MT5 + log in + enable **AlgoTrading**. |
| `mt5-trading-mcp` installed | ❌ | Not in repo venv (and shouldn't be — it's a standalone server). PyPI reachable: latest **1.4.2**. |
| MCP server registered with Hermes | ❌ | No `mcp.json` / `mcpServers` key found in Hermes config. Hermes cannot discover it. |
| Credentials populated | ⚠️ | Repo `.env` has `MT5_*` keys but **empty values**. Profile `.env` has `MT5_ID` + `MT5_PASSWORD` set, but `MT5_SERVER` / `MT5_ACCOUNT` **unverified**. MCP needs its own `.env` with all four. |

**Required steps before MCP is usable:**
1. Install **desktop** PlexyTrade MT5 terminal; log in; enable AlgoTrading (green button).
2. `pip install mt5-trading-mcp` (standalone venv, not repo venv).
3. Create the MCP server's `.env` with `MT5_ID / MT5_PASSWORD / MT5_SERVER / MT5_ACCOUNT`
   (pull from profile `.env`; confirm SERVER + ACCOUNT are present there first).
4. Run `python -m mt5_mcp doctor` → expect `[PASS]` on all checks.
5. Register the MCP server with Hermes (add `mcpServers` entry) so it is auto-discovered.
6. Start server (`python -m mt5_mcp serve`), then Hermes can call its read/trading tools.

> The MCP is most useful for Component C (execution) and for enriching heartbeats with
> live MT5 prices/positions. For the **signal-in** path, Component A+B (file/HTTP → Redis
> stream) is sufficient and does not require the MCP at all.

---

## 7. Cloud / multi-agent readiness gaps (out of scope for MVP, track for migration)

- **Windows-bound ops:** `scripts/watchdog.sh` uses PowerShell `Start-Process`; tporadowski
  Redis 5 (RESP2). Cloud (Linux) needs containerized Redis, systemd/supervisord, and the
  `redis==4.6.0` pin must move to `pyproject.toml` (already noted in skill pitfalls).
- **State portability:** `state.db` (SQLite) + local `DuckDB` + filesystem logs. For N
  agents, externalize to managed Postgres/DuckDB + object storage; isolate per agent.
- **Secrets:** `.env` file → cloud secret manager (Vault / cloud KMS).
- **Per-agent governance:** `config promote/rollback` is global today; multi-agent needs
  per-agent autonomy policy + provenance (NT-sourced vs MT4/5-sourced signals).
- **Observability:** health badges + logs exist; fleet needs metrics + alerting pipeline.

---

## 8. Open decisions (need user input)

1. **Transport:** file-drop (A1) vs WebRequest (A2) for the EA emitter?
2. **`effective_kelly` for EA signals:** derive from EA risk % vs fixed constant vs
   new Hermes `treat_ea_kelly_as_fixed` flag?
3. **Multi-agent channel scheme:** single shared stream + per-agent consumer group, or
   per-agent channel `signal.raw.noble_trader.{agent_id}` (recommended for clean isolation)?
4. **Execution timeline:** defer Component C until after cloud migration, or build the
   `MT4Adapter` now behind the existing paper/live autonomy gate?
