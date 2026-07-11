#!/usr/bin/env python3
"""Normalize a detect-secrets baseline JSON for stable diffing.

Keeps ONLY the `results` key (actual secret findings) and drops volatile
metadata (generated_at, plugins list, filters). Sorted keys for a deterministic
diff. Usage: normalize_baseline.py <path>
"""
import json
import sys

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    data = json.load(fh)

normalized = {"results": data.get("results", {})}
with open(path, "w", encoding="utf-8") as fh:
    json.dump(normalized, fh, sort_keys=True)
