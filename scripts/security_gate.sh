#!/usr/bin/env bash
# =============================================================================
# Noble Trader — Security Gate (required CI/cron check)
#
# Runs two required checks and exits non-zero if EITHER fails:
#   1. The 24+ security scenario tests (tests/test_security_scenarios.py)
#   2. detect-secrets audit over the production source the agent owns
#      (src/, excluding src/hermes/web/* and dashboard/* which the user owns,
#       plus tests/, .venv, *.parquet, *.lock).
#
# Output is concise: a single PASS/FAIL line per check so a cron run can alert
# on failure. Designed to be a required gate — non-zero exit blocks merge/deploy.
# =============================================================================
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W)"
cd "$REPO_ROOT" || { echo "GATE FAIL: cannot cd to repo root"; exit 1; }

# Use the project venv interpreter directly (pytest + detect-secrets installed
# via `uv pip install`). Avoids `uv run`, which hangs on exit under MSYS/Windows
# after the command completes (leaving the gate script stalled).
VENV_PY="$REPO_ROOT/.venv/Scripts/python.exe"
RUN="$VENV_PY -m"
# detect-secrets is invoked via its console script (not `python -m detect-secrets`,
# which is not exposed as a module entry point).
DS="$REPO_ROOT/.venv/Scripts/detect-secrets.exe"

# ---------------------------------------------------------------------------
# Check 1: security scenario tests
# ---------------------------------------------------------------------------
echo "[gate] 1/2 security tests: tests/test_security_scenarios.py"
if $RUN pytest tests/test_security_scenarios.py -q -p no:cacheprovider > /tmp/gate_pytest.log 2>&1; then
    echo "[gate] 1/2 security tests: PASS"
    TEST_OK=1
else
    echo "[gate] 1/2 security tests: FAIL"
    tail -15 /tmp/gate_pytest.log
    TEST_OK=0
fi

# ---------------------------------------------------------------------------
# Check 2: detect-secrets audit over owned source
# ---------------------------------------------------------------------------
echo "[gate] 2/2 detect-secrets secret scan"
# detect-secrets `audit` is interactive (prompts per secret) and `--audit` is not
# a flag in 1.5.0, so we enforce non-interactively by diffing a fresh `scan`
# against a committed baseline: any new/changed secret fails the gate.
EXCL='.*\.venv.*|.*/tests/.*|.*/dashboard/.*|.*/src/hermes/web/.*|.*\.parquet|.*\.lock'
if [ ! -f .secrets.baseline ]; then
    echo "[gate] 2/2 no .secrets.baseline found — generating one (re-run to enforce)."
    $DS scan --force-use-all-plugins --exclude-files "$EXCL" src/ > .secrets.baseline 2>/tmp/gate_ds_scan.log
    # Normalize immediately so the committed baseline is metadata-free (stable diffs).
    "$VENV_PY" "$REPO_ROOT/scripts/_normalize_baseline.py" .secrets.baseline
    SCAN_RC=$?
    if [ "$SCAN_RC" -ne 0 ]; then
        echo "[gate] 2/2 baseline generation FAILED (rc=$SCAN_RC):"; tail -15 /tmp/gate_ds_scan.log
        SECRET_OK=0
    else
        echo "[gate] 2/2 baseline created — treating as PASS this run (re-run to enforce)."
        SECRET_OK=1
    fi
else
    # Fresh scan -> temp (repo-local; /tmp is unreliable under MSYS); normalize
    # away volatile metadata, then diff against baseline. Any remaining diff =
    # a new/changed secret -> gate FAILS.
    TMP_NEW="$REPO_ROOT/.gate_ds_new.json"
    $DS scan --force-use-all-plugins --exclude-files "$EXCL" src/ > "$TMP_NEW" 2>/tmp/gate_ds_scan.log
    SCAN_RC=$?
    if [ "$SCAN_RC" -ne 0 ]; then
        echo "[gate] 2/2 scan FAILED (rc=$SCAN_RC):"; tail -15 /tmp/gate_ds_scan.log
        SECRET_OK=0
    else
        # Keep only the `results` (actual findings); drop volatile metadata so
        # the diff reflects real secret changes. Use the helper script (reliable
        # across shells; inline -c with backslash-newlines is fragile on MSYS).
        "$VENV_PY" "$REPO_ROOT/scripts/_normalize_baseline.py" .secrets.baseline
        "$VENV_PY" "$REPO_ROOT/scripts/_normalize_baseline.py" "$TMP_NEW"
        if diff -q .secrets.baseline "$TMP_NEW" >/dev/null 2>&1; then
            echo "[gate] 2/2 detect-secrets: PASS (no new secrets)"
            SECRET_OK=1
        else
            echo "[gate] 2/2 detect-secrets: FAIL (secret baseline drift — new/changed secret in owned source)"
            diff .secrets.baseline "$TMP_NEW" | head -30
            SECRET_OK=0
        fi
        rm -f "$TMP_NEW"
    fi
fi

# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------
if [ "$TEST_OK" -eq 1 ] && [ "$SECRET_OK" -eq 1 ]; then
    echo "[gate] OVERALL: PASS"
    exit 0
else
    echo "[gate] OVERALL: FAIL (tests=$TEST_OK secrets=$SECRET_OK)"
    exit 1
fi
