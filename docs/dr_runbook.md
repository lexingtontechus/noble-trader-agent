# Hermes Trading Platform — Disaster Recovery Runbook

## Overview

This runbook covers common failure scenarios and recovery procedures for the
Hermes Trading Platform.

## Emergency Contacts

- **Discord alerts channel**: Configured via `DISCORD_WEBHOOK_URL` in `.env`
- **Telegram alerts**: Configured via `TELEGRAM_BOT_TOKEN` in `.env`
- **Test alerts**: Run `platform alert-test` to verify channels are working

## Kill Switch

The global kill switch halts all trading activity immediately.

### Activate manually
```powershell
# Via Redis (if platform is running):
redis-cli PUBLISH agent.command '{"action": "flatten"}'

# Via dashboard:
# Visit http://127.0.0.1:8080 and check portfolio page

# Via Python (if platform process is accessible):
# Kill switch is activated automatically on:
# - Daily loss limit hit
# - Venue disconnect > 60s
# - Audit log failure
# - Dead man's switch timeout
```

### Deactivate
```powershell
redis-cli PUBLISH agent.command '{"action": "resume"}'
```

## Scenario 1: Process Crash

**Symptoms**: Dashboard shows "unreachable", no heartbeats received, no signals produced.

**Recovery**:
1. Check process: `ps aux | grep hermes` (Linux) or Task Manager (Windows)
2. Check logs: `./logs/hermes.log` (last 100 lines)
3. Restart: `platform init && platform ingest`
4. Verify: `platform health`
5. The dead man's switch should have auto-activated the kill switch.
   Deactivate it after verifying the system is healthy.

## Scenario 2: DuckDB Corruption

**Symptoms**: DuckDB write errors in logs, dashboard shows 0 tables.

**Recovery**:
1. Stop all Hermes processes
2. Backup corrupted DB: `cp data/hermes.duckdb data/hermes.duckdb.corrupt`
3. Delete: `rm data/hermes.duckdb data/hermes.duckdb.wal`
4. Re-initialize: `platform init` (recreates schema)
5. Backfill from Supabase: `platform backfill --days-back 365`
6. Verify: `platform health`
7. Note: Realized PnL and trade history will be lost. Heartbeat history can be
   re-pulled from Supabase. Parquet market data is unaffected.

## Scenario 3: Redis Disconnect

**Symptoms**: "Redis unreachable" in health check, no signals flowing.

**Recovery**:
1. Check Redis: `redis-cli ping` (should return PONG)
2. If Redis is down, restart it:
   - Memurai: `Start-Service Memurai` (Windows)
   - Docker: `docker start hermes-redis`
   - Linux: `sudo systemctl restart redis`
3. Restart Hermes processes: `platform ingest`, `platform synthesize`, etc.
4. The L0 subscriber uses a consumer group, so no messages are lost during
   brief disconnects.

## Scenario 4: Noble Trader Upstream Down

**Symptoms**: No new heartbeats in `signal_heartbeats` table, "upstream stale" alerts.

**Recovery**:
1. Check NT Redis connectivity: `redis-cli -u <nt-redis-url> ping`
2. Check Noble Trader service status
3. The platform will automatically pause new entries when heartbeat gap > 60s
4. Existing positions continue to be managed by the Active Price Monitor
5. When NT comes back, the subscriber will resume automatically (consumer group)

## Scenario 5: Venue API Down (Alpaca/Hyperliquid)

**Symptoms**: "error" status for venue in health check, order failures.

**Recovery**:
1. Check venue status pages:
   - Alpaca: https://status.alpaca.markets
   - Hyperliquid: https://stats.uptimerobot.com/ (or check API directly)
2. The circuit breaker will auto-activate on venue disconnect > 60s
3. All open orders on the affected venue will be cancelled
4. Existing positions are kept (but can't be closed until venue recovers)
5. If the venue is down for an extended period, consider manual flatten:
   `redis-cli PUBLISH agent.command '{"action": "flatten"}'`

## Scenario 6: Daily Loss Limit Hit

**Symptoms**: "daily_loss" circuit breaker event, all new entries blocked.

**Recovery**:
1. This is working as designed — the system is protecting you
2. Check portfolio: `platform pnl` or dashboard `/portfolio`
3. Review what caused the losses: `platform replay --start <today> --end <now>`
4. The block auto-clears at the next trading day (00:00 UTC)
5. Do NOT manually deactivate unless you understand the risk

## Scenario 7: Config Change Went Wrong

**Symptoms**: Bad performance after config change, need to rollback.

**Recovery**:
1. Check config history in DuckDB:
   ```sql
   SELECT * FROM config_history ORDER BY ts DESC LIMIT 10;
   ```
2. Find the last good config hash
3. Edit `config/default.yaml` back to the previous values
4. Restart all Hermes processes (config is loaded at startup)
5. The old config is still queryable in DuckDB for A/B comparison

## Backup Procedures

### Daily (automated)
- DuckDB backup: `cp data/hermes.duckdb backups/hermes_$(date +%Y%m%d).duckdb`
- Parquet data: already append-only, no backup needed (just don't delete)
- Config: `config/default.yaml` should be in git

### Weekly
- Full DuckDB VACUUM: `python -c "import duckdb; duckdb.connect('data/hermes.duckdb').execute('VACUUM')"`
- Review alert history for patterns
- Review hypothesis tracker for promotions/rejections

### Monthly
- Archive old Parquet data (>90 days) to cold storage
- Review and rotate API keys (every 90 days per §13.10)
- Test disaster recovery by running through this runbook

## Health Check Commands

```powershell
# Quick health check
platform health

# Detailed subsystem status
# Visit: http://127.0.0.1:8080

# Check specific subsystems
platform config show                    # Verify config loads
platform ingest --dry-run               # Verify NT Redis connection
python scripts/test_redis.py            # Test Redis connectivity
python scripts/init_duckdb.py           # Verify DuckDB schema

# Load test
platform load-test --duration-sec 10 --rate-per-sec 100

# Replay any time period
platform replay --start 2026-07-01T14:00:00 --end 2026-07-01T15:00:00
```

## Post-Incident Checklist

After any incident, complete this checklist:

- [ ] Incident logged with timestamp, symptoms, root cause, resolution
- [ ] All Hermes processes restarted and healthy
- [ ] `platform health` shows all green
- [ ] DuckDB is writable (test with `platform init`)
- [ ] No positions left in unexpected state
- [ ] Review `circuit_breaker_events` table for what triggered
- [ ] Review `audit_log` table for timeline of events
- [ ] Update this runbook if new scenario was discovered
- [ ] Consider generating a hypothesis to prevent recurrence: `platform agent --eod`
