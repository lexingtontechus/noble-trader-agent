#!/usr/bin/env bash
# Meta-regime classifier retrain (agent_onboarding.md §7.3 1st 03:30 PT).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/run_guarded.sh" meta-regime --retrain
