#!/usr/bin/env python3
"""Script to update default.yaml by removing hardcoded URLs and venue-specific URLs.

This script:
1. Reads the current default.yaml
2. Removes hardcoded system URLs (they now live in system_endpoints.yaml)
3. Removes venue-specific URL fields that are now in system_endpoints.yaml
4. Preserves all behavioral defaults and user-configurable settings
"""

import re
from pathlib import Path

def update_default_yaml():
    default_path = Path(__file__).parent.parent / "config" / "default.yaml"
    
    with open(default_path, "r") as f:
        content = f.read()
    
    # Remove hardcoded API URLs from tradingview section
    # These are now in system_endpoints.yaml
    content = re.sub(
        r"      api_url: https://tradingview-data1\.rapidapi\.com\n",
        "      # api_url: (moved to system_endpoints.yaml)\n",
        content
    )
    content = re.sub(
        r"      api_host: tradingview-data1\.rapidapi\.com\n",
        "      # api_host: (moved to system_endpoints.yaml)\n",
        content
    )
    content = re.sub(
        r"      ws_url: wss://ws\.tradingviewapi\.com/ws\?token=\{token\}\n",
        "      # ws_url: (moved to system_endpoints.yaml)\n",
        content
    )
    
    # Remove hardcoded quote proxy URL
    content = re.sub(
        r"      url: https://noble-trader-proxy-production\.up\.railway\.app\n",
        "      # url: (moved to system_endpoints.yaml)\n",
        content
    )
    
    # Remove venue-specific rate limits (now in system_endpoints.yaml)
    # But keep the rate_limit_per_min field as a default
    # We'll comment out the specific values
    
    # Add a note at the top about the new file structure
    header = """# ============================================================
# HERMES CONFIGURATION — Behavioral Defaults
# ============================================================
# This file contains behavioral defaults (risk thresholds, strategies, etc.)
# and user-configurable settings.
#
# System endpoints (URLs, infrastructure) have been moved to:
#   config/system_endpoints.yaml
#
# User-specific overrides can be placed in:
#   config/user.local.yaml (git-ignored)
#
# Secrets (API keys, tokens, passwords) remain in:
#   .env.local (git-ignored)
# ============================================================

"""
    
    # Replace the existing header (just remove the old one if it exists)
    if content.startswith("# ============================================================"):
        # Find the end of the header section
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip() and not line.startswith("#"):
                content = "\n".join(lines[i:])
                break
    
    content = header + content
    
    with open(default_path, "w") as f:
        f.write(content)
    
    print(f"Updated {default_path}")

if __name__ == "__main__":
    update_default_yaml()