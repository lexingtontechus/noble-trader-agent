#!/usr/bin/env bash
# Check for underperforming configs (agent_onboarding.md §6 daily 16:40 PT).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/run_guarded.sh" agent --check-underperformance
