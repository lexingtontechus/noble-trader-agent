#!/usr/bin/env bash
# Check shadow-mode hypotheses for promotion (agent_onboarding.md §6 daily 16:35 PT).
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/run_guarded.sh" agent --check-shadow-promotions
