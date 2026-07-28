# MetaApi Integration — Scope / Worklog / Plan

**Status:** Phase 1–6 implemented (2026-07-27). Live execution via MetaApi RPC, replacing the legacy MT4/MT5 EA bridge.

**Author:** Ultron (Developer agent)
**Repo:** `noble-trader-agent`
**Supersedes:** `bridges/mt4_mt5/` (EA→Redis relay — deprecated, retained for signal-source reference only).

---

## 1. Goal

Make the agent **execute trades via MetaApi** as the live brokerage backend, behind an env-var-driven client setup surfaced through the Hermes dashboard setup wizard. Replace the paper-only `ExecutionEngine` with a broker abstraction that defaults to paper but switches to MetaApi when `execution.mode=live` and `METAAPI_*` env vars are present.

### Decisions (locked with user)
- **Quantity = lots.** `Order.qty_requested` is passed to MetaApi `volume` directly (MT lots), NOT converted from USD notional.
- **Demo first.** `METAAPI_DEMO=true` (MetaApi demo account) for build + verification.
- **Legacy bridge deprecated.** `bridges/mt4_mt5/` kept as reference only; execution moves to MetaApi.
- **Env-var client setup.** Token + account ID come from `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` / `METAAPI_DEMO` (dashboard → `.env`), not YAML `secret:`.

---

## 2. Architecture

```
RiskDecision (L5)
      │  approved
      ▼
ExecutionEngine.execute_decision()                 [execution/orchestrator.py]
      │  creates Order via SmartOrderRouter
      ▼
ExecutionBroker.submit_order(order, client_id=order.order_id)
      │
      ├── PaperTradingEngine   (mode=paper, default)
      └── MetaApiBroker        (mode=live)            [execution/brokers/metaapi.py]
              │  MetaApi RPC connection
              ▼
        MetaApi account (MT4/MT5)  ── clientId correlation ──► fills → OrderStateMachine → DuckDB
```

`ExecutionBroker` is a **new abstraction**, parallel to (not part of) `transport/adapters/base.VenueAdapter`.
Rationale: `VenueAdapter` is a **market-data** interface (ticks/bars/orderbook). MetaApi is a **brokerage RPC execution** API. They are different concerns; conflating them would pollute the market-data hierarchy.

### Module layout
```
hermes/execution/brokers/
  __init__.py        → exports ExecutionBroker, MetaApiBroker, build_metaapi_broker_from_env
  base.py            → ExecutionBroker (ABC; mirrors PaperTradingEngine surface)
  metaapi.py         → MetaApiBroker (lazy SDK import + injectable metaapi_cls for tests)
```

---

## 3. SDK best practices (applied)

Sourced from MetaApi client docs + the reference `metapi/example.py`.

1. **RPC connection, not streaming, for trading.** Use `account.get_rpc_connection()` (synchronizes terminal state locally; fast queries). The streaming connection (`get_streaming_connection()`) is for trade-copiers and is NOT used here.
2. **Connection lifecycle.** On `connect()`: `MetaApi(token)` → `get_account(account_id)` → `deploy()` if state ∉ {DEPLOYED, DEPLOYING} → `wait_connected()` (can take 1–2 min on cold deploy) → `get_rpc_connection()` → `connect()` → `wait_synchronized()`. Reuse the single connection for the engine lifetime; reconnect on failure.
3. **`clientId` is the correlation key.** Every order passes `clientId = order.order_id`. MetaApi stamps `clientId` on the resulting position/deal, so fills reconcile back to the Hermes `Order` for attribution (existing `_on_fill` / `DecisionBranchTracker` path). This is the single most important correctness detail.
4. **Error handling.** All trade calls wrapped; on exception use `MetaApi.format_error(err)` for a readable message. Map non-`OK` `stringCode` → `OrderStatus.REJECTED`.
5. **Order-type mapping** (MetaApi RPC methods):
   - `BUY + MARKET` → `create_market_buy_order(symbol, volume, options)`
   - `SELL + MARKET` → `create_market_sell_order(...)`
   - `BUY + LIMIT` → `create_limit_buy_order(symbol, volume, openPrice, stopLimit=0, takeProfit=0, options)`
   - `SELL + LIMIT` → `create_limit_sell_order(...)`
   - `BUY + STOP` → `create_stop_buy_order(...)`
   - `SELL + STOP` → `create_stop_sell_order(...)`
   - close → `connection.close_position(position_id, options)`
6. **Market vs pending fills.** Market orders fill immediately → broker queries `get_positions()` filtered by `clientId` and emits the `Fill`. Limit/Stop orders are acknowledged (`SUBMITTED`) and resolved by a background **reconcile loop** (`_reconcile_loop`, every `reconcile_interval` sec) that polls `get_orders()` for the pending `clientId` and emits fills when filled. (Event-stream `on_order`/`on_deal` reconciliation is a future enhancement.)
7. **Lazy + injectable SDK import.** `metaapi_cloud_sdk` is imported lazily inside `connect()` and the class is injectable (`metaapi_cls=`) so unit tests mock the SDK without installing it.
8. **Fail-safe.** If `mode=live` but env vars are missing → `build_metaapi_broker_from_env()` returns `None` → engine logs CRITICAL and **falls back to paper** (never silently trades paper when live was intended, and never crashes the agent).

---

## 4. Build phases (completed this session)

- **P1 — ExecutionBroker ABC + MetaApiBroker.** `hermes/execution/brokers/{base,metaapi,__init__}.py`.
- **P2 — Wire into ExecutionEngine.** Mode selection (paper|live), `broker.submit_order` + `broker.close_position`, lifecycle `connect`/`disconnect`, `close_position` no-op on paper engine.
- **P3 — Env vars.** `METAAPI_TOKEN`, `METAAPI_ACCOUNT_ID`, `METAAPI_DEMO` read in `build_metaapi_broker_from_env()`.
- **P4 — Config.** `config/default.yaml`: `execution.mode: paper`, `execution.metaapi.demo: true`; `venues.mt4_mt5` marked DEPRECATED (signal-source reference only).
- **P5 — Deps.** `metaapi-cloud-sdk>=28.0.0` added to `pyproject.toml` `dependencies`.
- **P6 — Tests.** `tests/test_metaapi_broker.py`: mock SDK, verify market/limit submit, `clientId` threading, `close_position`, error→REJECTED, env factory, fail-safe. `tests/test_execution_live_mode.py` (optional) verifies engine broker selection.

### Dashboard (bonus, in-scope)
- `web/templates/setup.html`: added `METAAPI_TOKEN`, `METAAPI_ACCOUNT_ID`, `METAAPI_DEMO` fields (replacing the legacy `MT4_MT5_BRIDGE_TOKEN` field).
- `web/app.py`: those keys read from the setup POST and written to `.env`.

### Legacy bridge deprecation
- `bridges/mt4_mt5/DEPRECATED.md` added; `README.md`/`PLAN.md` banner. Execution no longer uses the EA relay.

---

## 5. Worklog

| Date | Action |
|---|---|
| 2026-07-27 | Reviewed `metapi/` (valid SDK reference) + `bridges/mt4_mt5/` (legacy). Scoped integration. |
| 2026-07-27 | Built `ExecutionBroker` ABC + `MetaApiBroker` (lazy/injectable SDK, clientId correlation, reconcile loop). |
| 2026-07-27 | Wired `ExecutionEngine` (paper\|live mode), replaced `app.py` "Live mode not implemented" gate. |
| 2026-07-27 | Added `METAAPI_*` env reading, `config/default.yaml` `execution.mode` + `metaapi.demo`, `pyproject` dep, dashboard fields, legacy-bridge deprecation banner. |
| 2026-07-27 | Tests: `test_metaapi_broker.py` (mock SDK) — green. Committed. |

---

## 6. Open items / future enhancements

- **Event-stream reconciliation.** Replace the poll-based `_reconcile_loop` with `connection.on_order` / `on_deal` subscriptions for lower-latency fill attribution (MetaApi streaming API).
- **Lot size vs USD sizing.** Currently `qty_requested` is treated as MT lots per decision. If a future signal expresses size in USD notional, add a `volume = size_usd / (entry_price * contract_size)` conversion in the router.
- **Stop-loss / take-profit on entry.** `submit_order` options already accept `stopLoss`/`takeProfit`; wire from `RiskDecision` when the risk engine emits them.
- **Position sync for risk.** `MetaApiBroker.get_positions()` / `get_account_information()` exist for `--sync-brokerage` equity anchoring; wire into `risk --sync-brokerage`.
- **Multi-account.** Today a single `METAAPI_ACCOUNT_ID`. A future per-plan account map (Signal Scout / Precision Pro) would select account by plan.
