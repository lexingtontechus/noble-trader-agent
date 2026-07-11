#!/usr/bin/env bash
# Pre-market account-state snapshot (Alpaca + Hyperliquid).
# Self-contained: jumps to repo root, clears PYTHONPATH, runs the Python snapshot
# which uses the platform's own SecretResolver (creds never leave the subprocess).
set -uo pipefail
REPO="C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo"
cd "$REPO" || { echo "REPO not found: $REPO"; exit 1; }
unset PYTHONPATH
exec ./.venv/Scripts/python.exe scripts/account_snapshot.py
