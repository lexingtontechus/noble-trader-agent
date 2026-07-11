#!/usr/bin/env bash
# Monthly maintenance (agent_onboarding.md §7.3 1st 03:00 PT).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/run_guarded.sh" agent --monthly-maintenance
