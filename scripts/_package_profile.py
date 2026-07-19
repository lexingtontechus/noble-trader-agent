#!/usr/bin/env python3
"""Package the Noble Trader agent PROFILE for manual deployment.

Builds a zip of the profile wrapper (personality, skills, cron, manifest)
EXCLUDING secrets, runtime state, caches, and the separate stack-repo git
dependency (declared in distribution.yaml as a git clone, not bundled).

Output: <home>/noble-trader-profile-1.1.0.zip
"""
import os, zipfile, sys

PROFILE = os.path.expanduser(r"~\AppData\Local\hermes\profiles\noble-agent")
OUT = os.path.expanduser(r"~\noble-trader-profile-1.1.0.zip")

# Exact root entries to skip entirely
SKIP_DIRS = {
    ".git", ".env", "auth.json", "auth.lock", "state.db", "state.db-shm",
    "state.db-wal", "verification_evidence.db", "projects.db", "logs",
    "sessions", "cache", "audio_cache", "image_cache", "lsp", "home",
    "workspace", "pairing", "skins", "plans", "pets", "memories",
    ".update_check", ".skills_prompt_snapshot.json", "context_length_cache.yaml",
    "models_dev_cache.json", "ollama_cloud_models_cache.json",
    "provider_models_cache.json", "processes.json", "noble-trader-agent",
}
# Suffixes to skip anywhere
SKIP_SUFFIXES = (".rdb", ".bak", ".db", ".db-shm", ".db-wal", ".log")

def keep(path: str) -> bool:
    rel = os.path.relpath(path, PROFILE)
    parts = rel.split(os.sep)
    # skip the separate stack repo subtree entirely
    if parts[0] == "noble-trader-agent":
        return False
    if parts[0] in SKIP_DIRS:
        return False
    if any(p in SKIP_DIRS for p in parts):
        return False
    if any(rel.endswith(s) for s in SKIP_SUFFIXES):
        return False
    return True

n = 0
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for root, dirs, files in os.walk(PROFILE):
        # prune dirs in-place so os.walk doesn't descend into skipped trees
        dirs[:] = [d for d in dirs if keep(os.path.join(root, d))]
        for f in files:
            fp = os.path.join(root, f)
            if keep(fp):
                z.write(fp, os.path.relpath(fp, PROFILE))
                n += 1

print(f"Wrote {OUT}")
print(f"  files: {n}")
print(f"  size:  {os.path.getsize(OUT)/1024:.0f} KB")
# sanity: ensure no forbidden file leaked
bad = [i.filename for i in zipfile.ZipFile(OUT).infolist()
       if any(i.filename.endswith(s) for s in (".env", "auth.json", ".db", ".rdb", ".bak"))
       or "/noble-trader-agent/" in i.filename
       or i.filename.startswith(".env")]
print("  LEAK CHECK (should be empty):", bad if bad else "OK — none")
