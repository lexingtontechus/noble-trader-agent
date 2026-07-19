#!/usr/bin/env bash
# Launches the mt5-trading-mcp server for Hermes discovery.
# Mirrors the hyperliquid-mcp wrapper shape used in ~/.hermes/config.yaml.
#
# The wrapper loads MT5 creds from an adjacent .env (mt5_mcp.env) so secrets
# never live in config.yaml. Install:  pip install mt5-trading-mcp  (own venv)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/mt5_mcp.env"

# Prefer a dedicated venv if present, else system python
VENV_PY="$SCRIPT_DIR/.venv/Scripts/python.exe"
if [ -x "$VENV_PY" ]; then PY="$VENV_PY"; else PY="python3"; fi

# Export MT5_* for the MCP server (it reads them from env)
if [ -f "$ENV_FILE" ]; then
  set -a; . "$ENV_FILE"; set +a
fi

exec "$PY" -m mt5_mcp serve --transport stdio
