# MetaApi Integration — Scope / Worklog / Plan

**Status:** BUILT (phases 1–6 complete). Live trading path wired; pending
real-account smoke test with `METAAPI_DEMO=true`.

**Goal:** Execute Noble Trader signals as live MT4/MT5 orders via MetaApi,
replacing the legacy `bridges/mt4_mt5/` EA→Redis relay as the execution path.
Client credentials are supplied via env vars (set through the Hermes dashboard
setup wizard → `.env`):
  - `METAAPI_TOKEN`        (required) — MetaApi API token
  - `METAAPI_ACCOUNT_ID`   (required) — provisioned MT4/MT5 account id
  - `METAAPI_DEMO`         (optional, default true) — demo flag (label only;
                            the account id itself determines demo vs live)

`execution.mode: paper | live` in `config/default.yaml` selects the broker.
Live requests MetaApi; paper (default) uses `PaperTradingEngine`. If live is
requested but env vars are missing, the engine logs CRITICAL and falls back to
paper (fail-safe — never silently trades paper when live was intended, never
crashes the agent).

---

## Architecture

```
RiskDecision (L5)
      │
      ▼
ExecutionEngine.execute_decision()          # execution/orchestrator.py
      │  SmartOrderRouter → Order(s)
      ▼
ExecutionBroker (interface: execution/brokers/base.py)
      ├── PaperTradingEngine   (paper mode, default)
      └── MetaApiBroker        (live mode)  ← NEW
              │  metaapi_cloud_sdk
              ▼
        MetaApi account.get_rpc_connection()  →  create_market/limit/stop_*
```

`ExecutionBroker` is a SEPARATE abstraction from `transport/adapters/base.py`
`VenueAdapter` (which is market-data only: ticks/bars/order book). Trading is a
different capability with its own connection lifecycle.

---

## SDK best practices applied (per metaapi.cloud/docs/client/sdkBestPractices)

1. **One instance, one connection.** A single `MetaApi(token)` and a single
   RPC connection (`account.get_rpc_connection()`) are created at
   `connect()` and reused for the broker's lifetime. Orders never create a new
   client/connection.
2. **Synchronize before trading.** `account.wait_connected()` then
   `connection.wait_synchronized()` are awaited before any trade call.
3. **Deploy once.** `account.deploy()` is called only if the account is not
   already in `DEPLOYED`/`DEPLOYING`. On `disconnect()`, `undeploy()` runs ONLY
   if we deployed it (tracked via `_deployed_by_us`) — never undeploy a
   account that was already up.
4. **Correlate with `clientId`.** Every order is submitted with
   `clientId = order.order_id`. MetaApi echoes `clientId` on the resulting deal,
   so fills reconcile back to the Hermes order for attribution.
5. **Format errors.** All exceptions are rendered via `MetaApi.format_error()`
   for actionable messages.
6. **Order types.** Market → `create_market_buy/sell_order`; Limit/Post-Only →
   `create_limit_buy/sell_order`; Stop → `create_stop_buy/sell_order`; ICEBERG
   has no RPC equivalent → best-effort MARKET (logged). `volume` = `Order.qty_requested`
   treated as MT **lots** (per user directive — no USD→lots conversion).
7. **Fill polling.** Market orders fill (near) synchronously; the broker polls
   `connection.get_order()` briefly (`execution.metaapi_fill_poll_sec`, default
   5s) and transitions the order to FILLED + emits a `Fill` via the registered
   callback. Resting orders (limit/stop) stay SUBMITTED until filled by the
   venue (reconciliation via deal streaming is the next phase — see below).
8. **Reconnect.** `connection.close()` on shutdown; connect failures in live
   mode fall back to paper for the session rather than crashing the agent.

---

## Files

| File | Change |
|---|---|
| `src/hermes/execution/brokers/__init__.py` | New package; re-exports `ExecutionBroker` |
| `src/hermes/execution/brokers/base.py` | New `ExecutionBroker` ABC (connect/disconnect/submit_order/cancel_order/close_position + optional reads) |
| `src/hermes/execution/brokers/metaapi_broker.py` | New `MetaApiBroker` (live MT4/MT5 via RPC) |
| `src/hermes/execution/orchestrator.py` | Select broker by `execution.mode`; wire callbacks; connect/disconnect; submit via `self._broker`; close via `self._broker.close_position` |
| `config/default.yaml` | `execution.mode: paper`, `execution.metaapi.demo: true` (pre-existing) |
| `src/hermes/web/app.py` | Dashboard already collects `METAAPI_TOKEN`/`METAAPI_ACCOUNT_ID`/`METAAPI_DEMO` |
| `pyproject.toml` | `metaapi-cloud-sdk>=28.0.0` (pre-existing) |
| `bridges/mt4_mt5/*` | DEPRECATED — superseded by MetaApi for execution (kept as signal-source reference) |
| `tests/test_metaapi_broker.py` | New — unit tests with mocked `metaapi_cloud_sdk` |

---

## Decisions (from user)

- **qty = lots.** `Order.qty_requested` is passed directly as MetaApi `volume`.
- **Env vars set; `METAAPI_DEMO=true`.** Broker reads them at construct time.
- **Legacy bridge deprecated/decommissioned** for execution.

---

## Next phases (not in this build)

- **Deal-streaming reconciliation.** Subscribe to `account.get_streaming_connection()`
  deal events; on `clientId` match, transition orders to FILLED and record
  `Fill` (covers limit/stop orders that don't fill synchronously).
- **SL/TP on submit.** Read `signal.stop_loss`/`take_profit` and pass
  `stopLoss`/`takeProfit` in the order options.
- **Position sync.** Periodic `get_positions()` reconciliation into portfolio
  state (covers external closes / partials).
- **Real-account smoke test** against the demo account (`METAAPI_DEMO=true`):
  submit a micro-lot market order, confirm fill + attribution, then close.
