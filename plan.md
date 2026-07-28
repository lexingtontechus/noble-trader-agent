# Plan — SSE/DuckDB persistence + MetaApi market-data fallback

**Date:** 2026-07-27
**Scope:** 3 tasks (assessed 2026-07-27, approved for build).
**Architecture constraint:** market data = `VenueAdapter`; trade execution = `ExecutionBroker`.
The MetaApi *fallback* belongs in a market-data layer (wrapping the existing
`transport/adapters/metaapi_adapter.py` `MetaApiAdapter`), NOT in
`execution/brokers/metaapi_broker.py`. No repo files are overridden — the
fallback is a new additive module that *uses* `MetaApiAdapter`.

---

## Task 1 — Persist `/sse/alerts` microstructure to DuckDB

**Why:** `p_microstructure` feeds `MetaRegimeClassifier` only as a live,
ephemeral input (10-min in-memory TTL). It is currently lost forever — no
audit trail, no offline research/backtest, no "what was microstructure at
decision time?" reconstruction. `alert` events are intentionally NOT stored
(they duplicate the signal pipeline already persisted by `ingest` L0 to
`signal_heartbeats`).

**What:**
- Add `microstructure_events` table to `src/hermes/db/schema.sql`
  (symbol, ts_ms, received_at, p_microstructure, p_micro_l1, p_micro_ta,
  direction, ta_vetoed; PK (symbol, ts_ms); indexes on symbol+ts).
- In `MicrostructureSSEConsumer._handle_microstructure`, append a row via a
  DuckDB writer following the `SnapshotWriter._write_blocking` pattern
  (`get_duckdb_path` + `safe_duckdb_connect` + executor + retry-on-lock).
  Idempotent on (symbol, ts_ms).
- Update pydantic/schema version note (no migration version bump needed —
  base schema is re-applied idempotently via `CREATE TABLE IF NOT EXISTS`).

**Verify:** unit test with a temp DuckDB path asserts rows written on a
`microstructure` frame; `alert` frames still ignored.

---

## Task 2 — MetaApi market/historical fallback (true fallback)

**Why:** `quote_proxy.fallback_to_tvda: false` → pricing outage = CRITICAL
only, no data. MetaApi already carries per-account CPU-credit budget for
market + historical data, so it is a first-party fallback with no new vendor
and no TradingView rate-limit exposure (the deprecated TVDA-direct fallback's
weakness).

**What:** new module `src/hermes/transport/metaapi_market_fallback.py`:
- `MetaApiMarketFallback` wraps a `MetaApiAdapter` (injected; not modified).
- `get_price(symbol)` → `MetaApiAdapter.get_current_price` with an in-memory
  cache (TTL ~10s) so we never poll per-tick (credit discipline).
- `get_bars(symbol, tf, start, end)` → `MetaApiAdapter.fetch_historical_bars`,
  cached by (symbol, tf, day) to avoid repeat credit burns.
- `get_cpu_credit_usage()` best-effort (reads `get_credit_usage`/similar if
  present on the SDK connection; returns None if unavailable) — used to skip
  history fetches when low.
- `activate()` / `deactivate()` / `active` flag; `healthy` reflects
  connection state. Lazy `connect()` of the adapter only when activated
  (outage-only → no steady-state credit burn).
- Config: rename `quote_proxy.fallback_to_tvda` → `fallback_to_metaapi`
  (default true); keep `fallback_to_tvda` as a deprecated alias (warn).

**Verify:** unit test mocks `MetaApiAdapter`; asserts cache hits avoid
re-calls, `activate`/`deactivate` toggle, and CPU-credit guard skips history
when low.

---

## Task 3 — Watchdog integration (SSE dead → switch to MetaApi)

**Why:** when `pricing_sse_watchdog` detects the `/sse/alerts` stream is dead,
the monitor should switch its market-data source to `MetaApiMarketFallback`
and switch back on restore — instead of only escalating CRITICAL.

**What:**
- `PricingSSEWatchdog`: add optional `on_dead` / `on_restored` callbacks
  (fired at the existing ok→dead / dead→ok transitions). No change to the
  CRITICAL/INFO alert logic.
- Monitor command (`app.py` `monitor`): instantiate `MetaApiMarketFallback`
  (only if `venues.metaapi.enabled`), register `on_dead` → `fallback.activate()`,
  `on_restored` → `fallback.deactivate()`. Expose `fallback.active` in monitor
  stats so `/monitor` + CLI show the active source.

**Verify:** unit test drives the watchdog callback on dead→ok transitions
mocks the fallback; asserts activate/deactivate called.

---

## Files touched
- `src/hermes/db/schema.sql` (Task 1)
- `src/hermes/transport/sse_consumer.py` (Task 1)
- `src/hermes/transport/metaapi_market_fallback.py` (NEW, Task 2)
- `config/default.yaml` (Task 2: fallback flag rename)
- `src/hermes/transport/pricing_sse_watchdog.py` (Task 3)
- `src/hermes/app.py` (Task 3: monitor wiring)

## Out of scope (explicit)
- Synthesizing `p_microstructure` from MetaApi order-book during fallback
  (microstructure stays proxy-only; classifier degrades as already supported).
- Modifying `execution/brokers/metaapi_broker.py` (execution unchanged).
- Editing the locked `noble-trader-proxy` repo.
