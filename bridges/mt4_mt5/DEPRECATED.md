# DEPRECATED — MT4/MT5 bridge (execution path)

**Status: DEPRECATED for trade execution (2026-07-27).**

This bridge (EA→Redis relay + `mt5-trading-mcp`) was the pre-MetaApi signal
*source* / execution experiment. It has been **superseded by MetaApi** as the
live order-execution path:

- Execution now flows through `hermes/execution/brokers/metaapi_broker.py`
  (RPC connection to the provisioned MT4/MT5 account via `metaapi-cloud-sdk`).
- Credentials are supplied via `METAAPI_TOKEN` / `METAAPI_ACCOUNT_ID` /
  `METAAPI_DEMO` (set through the Hermes dashboard setup wizard).

**Why deprecated:** the EA relay never placed real trades (see legacy
`PLAN.md` "readiness gaps"); MetaApi provides a supported, account-native RPC
trading API without requiring a desktop MT5 terminal or a custom MCP server.

**Kept for reference only:** the relay's *signal-source attribution* design
(`source_id` stamping, per-source quota) remains a useful pattern if a future
non-MetaApi signal publisher is added. Do not wire this bridge into the
execution path.

See `scope/metaapi_plan.md` for the active integration.
