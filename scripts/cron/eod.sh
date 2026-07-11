#!/usr/bin/env bash
# EOD self-learning loop (agent_onboarding.md §6 / agent_training.md).
# Wrapper so the Hermes cronjob can call a single self-contained script.
set -uo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/run_guarded.sh" agent --eod
