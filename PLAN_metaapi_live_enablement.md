# MetaAPI Live Trade Execution — Plan B (COMPLETED)

**Status:** ✅ COMPLETE (verified 2026-07-28) — demo-account trade executed end-to-end
**Author:** Ultron (agent)
**Date:** 2026-07-28
**Scope:** Make the deployed Noble Trader agent capable of executing real trades via MetaAPI, driven by incoming qualified signals.

---

## 0. Outcome (verified)

| Item | Result |
|---|---|
| Code deployed | ✅ `src/hermes/execution/brokers/metaapi_broker.py` + `brokers/__init__.py` + `brokers/base.py` + `orchestrator.py` copied from workspace `src/hermes/execution/` into the **deployed runtime** `src/hermes/execution/` (the agent's Hermes profile copy, e.g. `~/.hermes/profiles/noble-agent/noble-trader-agent/`) |
| SDK installed | ✅ `metaapi-cloud-sdk` + `psutil` + `aiohttp` + `requests` in the deployed `.venv` (`uv pip install`) |
| Credentials | ✅ `.env.local` (git-ignored) carries `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` / `METAAPI_DEMO=true`; secrets loader patched to also load `.env.local` (overrides `.env`) |
| Execution mode | ✅ `config/default.yaml → execution.mode: live` (was not present in deployed config; added) |
| Broker bugs fixed | ✅ (1) `options` passed as **keyword** not positional; (2) `clientId` **omitted** (this broker rejects every clientId format with "must match required pattern"); (3) units→lots conversion honored (`qty_requested` units ÷ contractSize) |
| **Live demo trade** | ✅ **XAUUSD BUY 0.10 lots** submitted to MetaAPI demo account → `VENUE ORDER ID 188363851` → position opened (`POSITION_TYPE_BUY, volume 0.1, openPrice 4039.73`) → closed cleanly for hygiene (0 open positions) |
| Regression test | ✅ `test_metaapi_broker.py` added (mocks SDK, offline) — `2 passed` |

**Conclusion:** The MetaAPI live-execution path is wired and verified on the demo account. No real-money trade was placed (demo only). The supervised loops were NOT restarted during this work — the running processes still use the pre-deploy code; a restart is required to activate `execution.mode: live` system-wide.

---

## 1. Credentials — `.env.local` convention

Token + account ID live in **`.env.local`** (git-ignored, never committed), NOT in the repo `.env`. The runtime's `core/secrets.py` `EnvFileBackend` now loads `.env.local` after `.env` (override), so `METAAPI_*` vars are picked up without touching `.env`.

```bash
METAAPI_TOKEN=<your MetaApi API token>
METAAPI_ACCOUNT_ID=<provisioned MT4/MT5 account id>
METAAPI_DEMO=true        # demo account; set false only for live money (after autonomy gate approval)
```

---

## 2. Deployment steps (as executed 2026-07-28)

1. **Sync workspace → deployed runtime.** Copied `metaapi_broker.py`, `brokers/__init__.py`, `brokers/base.py`, `orchestrator.py` into the deployed runtime `src/hermes/execution/`. (The deployed `execution/brokers/` package was missing `__init__.py`/`base.py`/`metaapi.py` — only `metaapi_broker.py` had been copied previously, which is why imports failed.)
2. **Install SDK deps** into the deployed `.venv`: `uv pip install metaapi-cloud-sdk psutil aiohttp requests`.
3. **Patch `core/secrets.py`** to also `load_dotenv(".env.local", override=True)`.
4. **Set `execution.mode: live`** in deployed `config/default.yaml` (added the `execution:` block).
5. **Place `.env.local`** beside the deployed `.venv`/`.env`.

---

## 3. Verification (as executed)

| Step | Check | Result |
|---|---|---|
| 3.1 | Broker constructs | ✅ `MetaApiBroker()` initializes; `METAAPI_TOKEN`/`METAAPI_ACCOUNT_ID` set |
| 3.2 | Account connects | ✅ WebSocket to `mt-client-api-v1.london-{a,b}.agiliumtrade.ai`; `wait_connected` + `wait_synchronized`; `connected demo=True` |
| 3.3 | Spec + lot conversion | ✅ XAUUSD `contractSize=100` → `qty_requested=10 units` = **0.10 lots** |
| 3.4 | Margin pre-check | ✅ `margin=80.8` USD for 0.1 lot |
| 3.5 | **Demo order submit** | ✅ `create_market_buy_order("XAUUSD", 0.1, options={})` → `ORDER STATUS: submitted`, `VENUE ORDER ID 188363851` |
| 3.6 | Position opened | ✅ separate poll: `POSITION_TYPE_BUY, volume 0.1, openPrice 4039.73` |
| 3.7 | Closed for hygiene | ✅ `close_position(188363851)` → `OPEN POSITIONS: 0` |
| 3.8 | Ingest still healthy | ✅ `signal_heartbeats` continued accepting (6 rows); `rejected_invalid` stayed 0 |

---

## 4. First LIVE (real-money) trade — checklist (NOT yet done)

Only after an explicit user go-ahead + autonomy-gate approval:
1. `METAAPI_DEMO=false` in `.env.local` (points broker at the live MT4/MT5 account).
2. Enable the autonomy live-trade gate (`AUTONOMY_ALLOW_LIVE_TRADES=true` + any per-symbol/size caps).
3. **Restart the supervised loops** (the watchdog cron self-heals within 5 min, or `bash scripts/watchdog.sh`) so the running `execute` loop picks up `execution.mode: live` + `MetaApiBroker`.
4. Execute with **minimum contract size**; confirm fill via broker fill-polling; journal to `trade_journal` + `orders`.

---

## 5. Risks / guardrails

- **Credential exposure:** `.env.local` only; never commit; never echo values.
- **Wrong broker:** code defaults to `METAAPI_DEMO=true`; live (`false`) only after explicit approval.
- **Autonomy gate:** keep `AUTONOMY_ALLOW_LIVE_TRADES=false` until real-money approval.
- **clientId quirk:** this broker rejects ALL `clientId` formats — the broker OMITs it (correlation falls back to symbol + position timestamp).
- **Rollback:** if deploy breaks ingest, revert deployed `repo/src/` from git and restart loops.

---

## 6. Out-of-scope

- Backend signal schema (unchanged: `2998b37`).
- Proxy changes (verbatim pass-through; none needed).
- Agent `NobleTraderHeartbeat` schema (unchanged).
