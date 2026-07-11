# AGENTS.md — Noble Trader Quant Stack

Operational playbook for autonomous maintenance of the Noble Trader quant trading
stack. **Read this file before any maintenance, debugging, or "keep it alive"
task.** It reflects the actual, verified state of the repo as of 2026-07-11.

---

## 1. What this stack is

A quant hedge-fund-style trading stack that consumes **Noble Trader (NT)** entry
signals from a Redis Stream, runs them through a market monitor + optimizer, and
places trades on **Alpaca** (equities/fx/cmdty, paper) and **Hyperliquid**
(perps, testnet). It is driven by the Hermes-agent `noble-trader-quant-hf-manager`
skill and kept alive by a watchdog cron.

**Operating stance (from the user):**
- The agent must **autonomously operate and self-manage** the stack: run the
  loops, keep the watchdog/cron alive, monitor brokerage-sync health, and **only
  escalate real anomalies** — not routine equity ticks or "still alive" pings.
- **Live brokerage account snapshot is the source of truth** for equity/drawdown
  — never outdated skill/docs. Drawdown is anchored to **real Alpaca+HL equity**
  (the `risk --sync-brokerage` path, default ON), not a static $100k.
- User style: concise/casual, expects **verified live data**, wants real
  tool-execution evidence (not fabricated output).

---

## 2. Architecture — the two ingestion paths (READ THIS)

The stack has **two decoupled ingestion paths**. Do not conflate them.

| Process (loop) | Job | Data source |
|---|---|---|
| `ingest` | NT heartbeat bridge (L0) | Subscribes to `signal.raw.noble_trader` Redis **Stream** via `XREAD`/consumer group. Validates, dedupes, persists to DuckDB `signal_heartbeats`, re-publishes internally on `signal.raw.hermes.{symbol}`. **No venue WebSocket — it never sees a live price tick.** |
| `monitor` | **Active market watcher (L2.8)** | Connects **directly to Alpaca + Hyperliquid WebSockets** via venue adapters. Feeds ticks/order books through `PriceMonitor` (`monitor/orchestrator.py`): TickAggregator → IndicatorEngine (ATR/EMA/RSI) → AnomalyDetector → StopWatcher → CrossPriceMonitor → FundingWatcher. Emits `PriceMonitorEvent`s to DuckDB + internal Redis. **Runs 24/7 regardless of NT.** |

**Between incoming NT signals, the `monitor` loop is what watches the market.**
NT provides *timing*; `monitor` provides *market state*. `ingest` is just the NT
bridge. They are separate processes (see §3).

**Hard rule:** only read from `signal.raw.noble_trader` via `XREAD`/`XREADGROUP`.
**Never** pull `trading:config:{symbol}` snapshots — explicitly excluded per user
directive.

---

## 3. The supervised processes (watchdog)

`scripts/watchdog.sh` (cron `noble-stack-watchdog`, every 5 min) launches and
supervises these **7 instances** (each = 1 venv parent + 1 uvicorn-reloader child;
the child is the actual worker):

| Loop | Launch command (from watchdog `LOOPS` map) |
|---|---|
| `dashboard` | `-m hermes.app dashboard --host 127.0.0.1 --port 8080` |
| `monitor` | `-m hermes.app monitor` |
| `synthesize` | `-m hermes.app synthesize` |
| `risk` | `-m hermes.app risk --equity 108000 --sync-brokerage` |
| `execute` | `-m hermes.app execute --equity 108000 --paper` |
| `ingest` | `-m hermes.app ingest` |
| `_watch_optimize` | `scripts/_watch_optimize.py` (optimizer watcher) |

Plus **Redis** (local bus, required by 5/6 loops) launched detached + PID-guarded.

**Watchdog hardening facts (do not regress):**
- Loops launched **fully detached** via PowerShell `Start-Process` (the only method
  on this git-bash/Windows host that survives session-teardown SIGTERM). `nohup &`
  dies on teardown; `cmd /c start` and `.bat` wrappers fail.
- **Liveness is NAME-BASED** (`proc_alive` matches any live python running the
  loop name), robust against the uvicorn reloader parent/child split and stale
  PIDs. A PID file (`scripts/_pid_<name>.txt`) is written for diagnostics only.
- **Single-instance lock** `scripts/.watchdog.lock` prevents concurrent/watchdog
  double-launch. The cron is the sole owner — **do not also launch loops via
  `terminal(background=true)`**, that was the original source of 2× duplicates.
- Every launch is verified 4s later; a no-show logs a hard **`FAIL`** (visible in
  `/tmp/watchdog.log`), not a silent no-op.

**Daily health check:** `/noble balance` + `/noble assets` (noble skill) for
instant Alpaca+HL equity and held-asset/regime reads. The `risk` proc's
`[brokerage-sync] live equity=$...` ~60s heartbeat lines are **expected** health
pings — acknowledge concisely; only alert if equity leaves the normal live band
(≈$108k as of 2026-07).

---

## 4. Cron jobs (all enabled)

| Job | Schedule | Purpose |
|---|---|---|
| `noble-stack-watchdog` (`1359279cee62`) | `*/5 * * * *` | Keep all loops + Redis + optimizer watcher alive. |
| `security-gate` (`206ce0845862`) | `15 3 * * *` | Run required security gate (tests + detect-secrets). Escalate on failure. |
| `NT EOD self-learning` (`33d2a4688ca4`) | `30 16 * * *` | EOD self-learning loop. |
| `NT shadow promotions` (`941498c21f9e`) | `35 16 * * *` | Shadow-mode hypothesis promotions. |
| `NT underperformance check` (`073b3f377296`) | `40 16 * * *` | Underperforming config check. |
| `NT weekly optimize` (`9bfe2464dc4d`) | `0 4 * * 6` | Entry/execution optimization sweep. |
| `NT weekly rigor` (`38828550eca7`) | `30 4 * * 6` | Statistical rigor checks. |
| `NT weekly vacuum` (`413449a41a85`) | `0 4 * * 0` | DuckDB VACUUM maintenance. |
| `NT monthly maintenance` (`58ddd9214691`) | `0 3 1 * *` | Monthly maintenance. |
| `NT monthly metaregime retrain` (`967aa9773568`) | `30 3 1 * *` | Meta-regime retrain. |
| `NT pre-market account snapshot` (`9bfecb2a51a2`) | `30 6 * * 1-5` | Pre-market account state snapshot. |

All NT jobs use the `noble-trader-quant-hf-manager` skill. If a cron job dies,
restart/verify via its job id — do not recreate unless the config is wrong.

---

## 5. Security posture (enforced, not advisory)

- **Redaction is mandatory.** `src/hermes/ops/security_monitor.py`:
  - `_trigger_alert` redacts payloads (top-level keys + recursive value/nested
    via `_deep_copy_redact`) **before** any callback fires — external sinks
    (Slack/Discord/webhook) never receive raw secrets.
  - `_redact_sensitive_data` substring-matches sensitive tokens (api_key,
    session, auth, secret, token, …) → `***REDACTED***`.
- **25 security tests** in `tests/test_security_scenarios.py` (was uncollectible
  due to a SyntaxError + 5 source bugs, all fixed 2026-07-10/11).
- **Required gate** `scripts/security_gate.sh`: runs the 25 tests + a
  `detect-secrets` scan diffed against `.secrets.baseline` (normalized to
  `results`-only via `scripts/_normalize_baseline.py`). Exits non-zero on either
  failure. Wire to CI/PR review as a required check.
- **Do NOT** touch `src/hermes/web/*` or `dashboard/*` — the user owns those and
  will resolve exposed-secret/defect issues there. If you find a secret there,
  flag it; do not edit those dirs.
- Secrets are redacted in logs/tests/payloads. Never print raw credentials.

### Known source fixes applied (so you don't "rediscover" them)
- `security_monitor.py:1090` was `error=str(e"` (unterminated string) — made the
  module unimportable. Fixed.
- `api_rate_limit` decorator used `type(self).log_security_event.__wrapped__`
  (AttributeError) — fixed to use the captured `func` closure.
- `_sanitize_input` signature reordered so `field_name` is 2nd positional.
- `RateLimiter` datetime calls are `datetime.now(timezone.utc)` (naive/aware fix).
- `validate_csrf_token` invalidates the whole session on use (CSRF replay fix).
- `_check_escalation` `threading.Timer` set `daemon=True` (was a **non-daemon
  thread that hung pytest/interpreter exit** — real shutdown bug).

---

## 6. Daily operations checklist (autonomous)

1. **Confirm watchdog alive:** cron `1359279cee62` enabled + last_status ok. If
   loops are down, the next 5-min tick self-heals — do not manually launch loops.
2. **Quick health:** `/noble balance` + `/noble assets`. Note live equity vs the
   ≈$108k band.
3. **Security gate:** trust cron `206ce0845862`. If it FAILs, read
   `scripts/security_gate.sh` output, surface the failing test/secret — **do not
   auto-fix secrets** (escalate).
4. **Anomalies only:** escalate real issues (loop stuck after multiple watchdog
   ticks, equity outside live band, Redis down and not restarting, gate failure).
   Ignore routine heartbeats.

---

## 7. How to (re)start / recover

- **Stack down:** the watchdog cron self-heals within 5 min. To force:
  `bash scripts/watchdog.sh` from repo root. Do NOT launch loops by hand.
- **Redis down:** watchdog restarts it; verify with
  `tools/redis/redis-cli.exe -h 127.0.0.1 -p 6379 ping` → `PONG`.
- **Security gate (first run):** generates `.secrets.baseline` (PASS that run);
  enforces from the next run. To rotate a secret intentionally: delete
  `.secrets.baseline` and let it regenerate.
- **Run the gate manually:** `bash scripts/security_gate.sh` (pytest 25 + scan).

---

## 8. Environment / paths (verified)

- Repo root: `C:/Users/aloys/AppData/Local/hermes/profiles/noble-agent/noble-trader-agent/repo`
- Venv python: `repo/.venv/Scripts/python.exe` (pytest + detect-secrets installed
  via `uv pip install` — call it **directly**, do NOT use `uv run`, which hangs on
  exit under MSYS/Windows).
- Logs: `repo/logs/`; watchdog log `/tmp/watchdog.log`; PID files
  `repo/scripts/_pid_<name>.txt`; lock `repo/scripts/.watchdog.lock`.
- Redis: `repo/tools/redis/redis-server.exe` + `redis.windows.conf`.
- Shell: git-bash/MSYS (`bash`). No `pgrep`/`pkill`/`setsid`; use
  `powershell.exe -Command "Get-CimInstance Win32_Process …"` for process checks.
  `/tmp` is unreliable for redirects — prefer repo-local temp paths.
- Credentials: loaded from `repo/.env` (auto-loaded). **Redact in all output.**

---

## 9. Golden rules

1. **Execute the codebase; don't write new logic** unless fixing a verified bug.
2. **Live brokerage equity is truth** — anchor drawdown to real Alpaca+HL, not
   static numbers.
3. **Only `signal.raw.noble_trader` via XREAD** — never `trading:config:{symbol}`.
4. **Single watchdog owner** — no manual loop launches.
5. **Redact secrets everywhere**; never edit `src/hermes/web/*` or `dashboard/*`.
6. **Escalate real anomalies only**; routine pings are expected noise.
7. **Verify with real tool output** — never fabricate results.
