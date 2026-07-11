#!/usr/bin/env bash
# Statistical rigor checks on the last optimization run (§7.2 Sat 04:30 PT).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
# `--symbols` is required by `platform rigor` (no registry default); pass the
# active symbol universe so the scheduled check runs end-to-end.
SYMS="BTC-PERP,BTC/USD,ETH-PERP,SOL/USD"
exec "$DIR/run_guarded.sh" rigor --symbols "$SYMS" --days-back 90
