#!/usr/bin/env bash
# Entry/execution optimization sweep (agent_onboarding.md §7.2 Sat 04:00 PT).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# `--symbols` is required by `platform optimize` (no registry default); pass the
# active symbol universe so the scheduled sweep runs end-to-end.
SYMS="BTC-PERP,BTC/USD,ETH-PERP,SOL/USD"
exec "$DIR/run_guarded.sh" optimize --symbols "$SYMS" --days-back 90 --n-trials 200
