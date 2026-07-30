# Worklog: User Variables vs System Variables Separation

**Date:** 2026-07-28  
**Author:** Ultron (Developer Agent)  
**Status:** Planning Phase  
**Branch:** feature/user-system-var-separation  

---

## 1. Objective

Establish a clean separation between **user variables** (configuration values that are user-specific, environment-specific, or tenant-specific) and **system variables** (configuration values that are codebase-defined, system endpoints, or infrastructure defaults). This separation ensures that code updates are self-contained and do not require touching user-specific configuration files.

---

## 2. Current State Analysis

### 2.1 Configuration Files in the Repository

| File | Type | Purpose |
|------|------|---------|
| `config/default.yaml` | System/User | Main configuration file - mixed content |
| `.env.example` | Template | Template for user environment variables |
| `.env.local` | User | User-specific secrets (git-ignored) |
| `.env` | User | User-specific secrets (git-ignored) |
| `.secrets.baseline` | System | Baseline for detect-secrets scanning |
| `.pre-commit-config.yaml` | System | Pre-commit hook configuration |
| `pyproject.toml` | System | Python project configuration |

### 2.2 Current Variable Classification

#### User Variables (Currently in `config/default.yaml` as `secret:` references)

These are secrets/credentials that vary per deployment:

1. **Authentication Secrets:**
   - `secret:hermes.admin_username`
   - `secret:hermes.admin_password`
   - `secret:hermes.session_secret`
   - `secret:hermes.agent_token`

2. **Venue Credentials:**
   - `secret:alpaca.api_key`
   - `secret:alpaca.api_secret`
   - `secret:alpaca.base_url`
   - `secret:alpaca.data_url`
   - `secret:hyperliquid.wallet_address`
   - `secret:hyperliquid.private_key`
   - `secret:hyperliquid.api_url`
   - `secret:hyperliquid.vault_address`
   - `secret:mt4_mt5_bridge_token`
   - `secret:mt4_mt5_source_id`
   - `secret:mt4_mt5_relay_url`

3. **External Service URLs:**
   - `secret:noble_trader.proxy_redis_url`
   - `secret:supabase.url`
   - `secret:supabase.anon_key`
   - `secret:noble_trader.license_key`

4. **Infrastructure Paths:**
   - `secret:hermes.duckdb_path`
   - `secret:hermes.redis_url`

5. **Notification Endpoints:**
   - `secret:discord.webhook_url`
   - `secret:telegram.bot_token`
   - `secret:telegram.chat_id`

6. **TradingView API:**
   - `secret:tradingview.api_key`

#### System Variables (Hardcoded in `config/default.yaml`)

These are infrastructure endpoints, defaults, and code-defined values:

1. **Fixed URLs (System Endpoints):**
   - `https://tradingview-data1.p.rapidapi.com` (TradingView API host)
   - `wss://ws.tradingviewapi.com/ws?token={token}` (TradingView WebSocket)
   - `https://noble-trader-proxy-production.up.railway.app` (Quote proxy URL)

2. **Trading Behavior Defaults:**
   - `poll_intervals` for quote proxy (hot: 5s, warm: 15s, cold: 60s, stale: 300s)
   - `rate_limit_per_min` for venues
   - `data_modes` (live/historical flags)
   - `features` (forex, options, shorting, leverage, etc.)

3. **Risk Management Thresholds:**
   - `max_portfolio_drawdown_pct: 0.15`
   - `daily_loss_limit_pct: 0.03`
   - `max_leverage_total: 3.0`
   - `max_gross_exposure_pct: 1.5`
   - Circuit breaker thresholds

4. **Signal Processing:**
   - `staleness_ms: 30000`
   - `min_edge_estimate_bps: 5`
   - `reward_risk_min: 1.5`

5. **Entry/Exit Strategies:**
   - Strategy mappings (calm_trend, choppy_range, etc.)
   - `brick_confirmation_count: 2`
   - `pullback_depth_brick_fraction: 0.5`

6. **Position Management:**
   - Trailing stop parameters
   - Decision tree thresholds
   - Markov persistence values

7. **Circuit Breakers:**
   - ATR baseline lookback
   - Vol multiplier threshold
   - VaR confidence
   - Kill switch triggers

8. **Autonomy Tiers:**
   - `tier_1.max_notional_usd: 2000`
   - `tier_3.max_notional_usd: 25000`
   - Approval rules

9. **Upstream Configuration:**
   - `proxy_channel: signal.proxy.noble_trader`
   - `consumer_group: noble-1`
   - `staleness_ms: 30000`
   - `signal_drought_alert_sec: 14400`
   - `proxy_heartbeat_timeout_sec: 480`
   - `sse_liveness_timeout_sec: 90`

10. **Quote Proxy Settings:**
    - `timeout_sec: 5`
    - `fallback_to_metaapi: true`
    - `poll_intervals` (system-defined values)

---

## 3. Proposed Separation Strategy

### 3.1 New File Structure

```
config/
├── default.yaml          # System defaults (NO user secrets)
├── system_endpoints.yaml # System URLs, ports, infrastructure
├── user.example.yaml     # Template for user overrides
└── user.local.yaml       # User-specific overrides (git-ignored)
```

### 3.2 Classification Matrix

| Category | Variable Type | Location | Example |
|----------|---------------|----------|---------|
| **System Endpoints** | Hardcoded URL | `system_endpoints.yaml` | Noble Trader proxy URL |
| **System Defaults** | Behavioral defaults | `default.yaml` | Circuit breaker thresholds |
| **User Secrets** | Credential values | `.env.local` | API keys, tokens |
| **User Overrides** | Tenant-specific config | `user.local.yaml` | Venue enablement, initial symbols |

---

## 4. Detailed File Changes

### 4.1 New File: `config/system_endpoints.yaml`

**Purpose:** System-defined infrastructure endpoints and service URLs that are part of the codebase.

**Content:**
```yaml
# System Endpoints — These are codebase-defined, not user-configurable
# Updated when system endpoints change (e.g., proxy URL migration)

quote_proxy:
  url: https://noble-trader-proxy-production.up.railway.app
  timeout_sec: 5
  fallback_to_metaapi: true
  fallback_to_tvda: false
  poll_intervals:
    hot: 5
    warm: 15
    cold: 60
    stale: 300

tradingview:
  base_url: https://tradingview-data1.p.rapidapi.com
  api_host: tradingview-data1.p.rapidapi.com
  ws_url_template: wss://ws.tradingviewapi.com/ws?token={token}
  ws_plan: ultra
  ws_mode: on_demand
  ws_schedule: use_active_hours
  rest_fallback_interval_sec: 60
  active_ws_ttl_sec: 300

redis:
  channel: signal.raw.noble_trader
  signal_source: proxy
  proxy_channel: signal.proxy.noble_trader
  consumer_group: noble-1
  staleness_ms: 30000

supabase:
  sweep_result_table: nt_sweep_result
  regime_log_table: nt_regime_log
  backfill_on_startup: true
  backfill_lookback_days: 365
  nt_symbol_table: nt_symbol
  nt_symbol_cache_ttl_sec: 300

upstream_heartbeat:
  signal_drought_alert_sec: 14400
  proxy_heartbeat_timeout_sec: 480
  sse_liveness_timeout_sec: 90
  proxy_delivery_table: proxy_delivery_log
  signal_cooldown_minutes: 60
```

### 4.2 Modified File: `config/default.yaml`

**Changes:**
- Remove all hardcoded URLs (reference system_endpoints.yaml instead)
- Keep behavioral defaults (thresholds, strategies, risk parameters)
- Keep venue configurations with `secret:` references for credentials
- Add reference to system_endpoints.yaml

**Example Changes:**

**Before:**
```yaml
venues:
  tradingview:
    enabled: true
    asset_classes:
    - crypto
    - forex
    - equities
    - commodities
    credentials:
      api_key: secret:tradingview.api_key
    rate_limit_per_min: 300
    data_modes:
      live: true
      historical: true
    features:
      venue_agnostic: true
      source: tradingviewapi_com
      api_url: https://tradingview-data1.p.rapidapi.com
      api_host: tradingview-data1.p.rapidapi.com
      ws_url: wss://ws.tradingviewapi.com/ws?token={token}
```

**After:**
```yaml
venues:
  tradingview:
    enabled: true
    asset_classes:
    - crypto
    - forex
    - equities
    - commodities
    credentials:
      api_key: secret:tradingview.api_key
    rate_limit_per_min: 300
    data_modes:
      live: true
      historical: true
    features:
      venue_agnostic: true
      source: tradingviewapi_com
      # URL references come from system_endpoints.yaml
      # api_url, api_host, ws_url are resolved from system config
```

### 4.3 New File: `config/user.example.yaml`

**Purpose:** Template for user-specific overrides that are NOT secrets.

**Content:**
```yaml
# User-Specific Configuration Overrides
# Copy to user.local.yaml and customize

# Portfolio configuration
portfolio:
  target_allocation:
    crypto: 0.7
    equities: 0.15
    commodities: 0.0
    forex: 0.15
  rebalance_threshold_drift_pct: 0.1
  rebalance_frequency: on_drift
  rebalance_method: threshold
  start_smart: true
  initial_symbols:
  - symbol: BTC/USD
    venue: alpaca
    asset_class: crypto
  - symbol: BTC-PERP
    venue: hyperliquid
    asset_class: crypto
  - symbol: ETH-PERP
    venue: hyperliquid
    asset_class: crypto

# Venue enablement (user decides which venues to use)
venues:
  alpaca:
    enabled: false  # User choice: enable if using Alpaca
  hyperliquid:
    enabled: false  # User choice: enable if using Hyperliquid
  mt4_mt5:
    enabled: true   # User choice: enable if using MetaApi/MT bridge

# Execution mode
execution:
  mode: paper  # Override: paper or live

# Notification channels (user-specific webhook URLs are in .env)
notifications:
  discord:
    enabled: false
  telegram:
    enabled: false

# Active hours (user's timezone)
active_hours:
  timezone: America/Los_Angeles
  start: 09:30
  end: '16:00'
  crypto_24_7: true
  degrade_outside_hours: true
```

### 4.4 Modified File: `.env.example`

**Changes:**
- Keep all secrets documentation
- Add note about user.local.yaml for non-secret overrides
- Document the new file structure

**Added Section:**
```yaml
# ============================================================
# USER CONFIGURATION OVERRIDES
# ============================================================
# For non-secret configuration overrides (venue enablement, 
# initial symbols, risk thresholds, etc.), create:
#   config/user.local.yaml
# This file is git-ignored and allows tenant-specific config
# without touching code or .env files.
#
# See config/user.example.yaml for the template.
# ============================================================
```

---

## 5. Configuration Loading Changes

### 5.1 Modified `src/hermes/core/config.py`

**Changes Required:**

1. Add `load_user_config()` function to merge user.local.yaml
2. Add `load_system_endpoints()` function to load system_endpoints.yaml
3. Modify `load_config()` to:
   - Load `system_endpoints.yaml` first (system defaults)
   - Load `user.local.yaml` (user overrides, if exists)
   - Load `default.yaml` (behavioral defaults)
   - Merge in order: system_endpoints → user → default → secrets resolution

**New Loading Order:**
```
1. system_endpoints.yaml (system URLs, infrastructure)
2. user.local.yaml (user overrides, git-ignored)
3. default.yaml (behavioral defaults)
4. Secret resolution (from .env, Vault, AWS SM, etc.)
```

### 5.2 Updated Secrets Resolution

The `secret:` prefix resolution remains unchanged:
- `secret:alpaca.api_key` → `ALPACA_API_KEY` in `.env`
- `secret:noble_trader.proxy_redis_url` → `NOBLE_TRADER_PROXY_REDIS_URL` in `.env`

---

## 6. Files Affected by System Endpoint Changes

When system endpoints change (e.g., proxy URL migration), only these files need modification:

### 6.1 Files That Would Change (System Update)

| File | Change Type | Impact |
|------|-------------|--------|
| `config/system_endpoints.yaml` | **ADD/UPDATE** | New proxy URL, new API endpoints |
| `src/hermes/core/config.py` | **UPDATE** | If new endpoint structure requires code changes |
| `src/hermes/upstream/redis_subscriber.py` | **CHECK** | If Redis stream names change |
| `src/hermes/data_sources/tradingview_adapter.py` | **CHECK** | If API host/WS URL changes |

### 6.2 Files That Would NOT Change (User Variables)

| File | Change Type | Impact |
|------|-------------|--------|
| `.env` | **NEVER TOUCH** | User secrets |
| `.env.local` | **NEVER TOUCH** | User secrets |
| `config/user.local.yaml` | **NEVER TOUCH** | User overrides |
| `config/default.yaml` | **NEVER TOUCH** | Behavioral defaults |

---

## 7. Implementation Steps

### Phase 1: Create New Configuration Files

1. Create `config/system_endpoints.yaml` with all hardcoded URLs
2. Create `config/user.example.yaml` template
3. Update `.env.example` with documentation

### Phase 2: Update Configuration Loader

1. Modify `src/hermes/core/config.py`:
   - Add system_endpoints.yaml loading
   - Add user.local.yaml merging
   - Update merge order
   - Add backward compatibility for existing setups

### Phase 3: Update default.yaml

1. Remove hardcoded URLs from `default.yaml`
2. Remove venue-specific URL configurations that belong in system_endpoints
3. Keep behavioral defaults intact

### Phase 4: Update Documentation

1. Update README.md with new file structure
2. Update AGENTS.md with configuration loading changes
3. Update onboarding documentation

### Phase 5: Testing

1. Test backward compatibility (existing setups still work)
2. Test new file structure with fresh installation
3. Verify secret resolution still works
4. Run all 297 tests

---

## 8. Migration Path

### For Existing Users

1. **No immediate action required** — backward compatibility maintained
2. **Optional migration:**
   ```bash
   # Copy system endpoints to new file
   cp config/default.yaml config/system_endpoints.yaml
   
   # Extract behavioral defaults to new default.yaml
   # (automated migration script)
   
   # Create user.local.yaml for any overrides
   cp config/user.example.yaml config/user.local.yaml
   ```

### For New Installations

1. Use new file structure from the start
2. `config/default.yaml` contains only behavioral defaults
3. `config/system_endpoints.yaml` contains system URLs
4. `config/user.local.yaml` contains user overrides
5. `.env.local` contains secrets

---

## 9. Benefits of This Separation

### 9.1 Code Updates

- **System endpoint changes** only require updating `system_endpoints.yaml`
- **No risk of overwriting user secrets** in `.env` or `.env.local`
- **Clear ownership** of configuration values

### 9.2 User Experience

- **Clear separation** between what users control vs. what's system-defined
- **Easier troubleshooting** — system endpoints are in one place
- **Tenant isolation** — each deployment can have its own `user.local.yaml`

### 9.3 Operations

- **Automated updates** can safely update system_endpoints.yaml
- **CI/CD pipelines** can modify only system files
- **Audit trail** is cleaner — changes to system endpoints are obvious

---

## 10. Edge Cases and Considerations

### 10.1 Backward Compatibility

- Existing installations must continue to work
- Default behavior: if files don't exist, use current behavior
- Deprecation warning for old-style configs

### 10.2 Secret Resolution

- `secret:` prefix resolution unchanged
- Secrets always come from `.env` or secret backends
- No secrets in YAML files (by design)

### 10.3 Environment Variables

- `HERMES_CONFIG_PATH` continues to work for `default.yaml`
- New env var: `HERMES_USER_CONFIG_PATH` for `user.local.yaml`
- New env var: `HERMES_SYSTEM_ENDPOINTS_PATH` for `system_endpoints.yaml`

### 10.4 Testing

- All existing tests must pass
- New tests for configuration loading order
- Tests for backward compatibility

---

## 11. Files Summary

### New Files to Create

1. `config/system_endpoints.yaml` - System URLs and infrastructure
2. `config/user.example.yaml` - Template for user overrides

### Modified Files

1. `config/default.yaml` - Remove hardcoded URLs, keep behavioral defaults
2. `.env.example` - Add documentation about new file structure
3. `src/hermes/core/config.py` - Update loading logic

### Git-Ignored Files (Unchanged)

1. `.env` - User secrets
2. `.env.local` - User secrets
3. `config/user.local.yaml` - User overrides (to be added to .gitignore)
4. `.secrets.baseline` - Secret scanning baseline

---

## 12. Conclusion

This separation provides:

1. **Clean boundaries** between system and user configuration
2. **Self-contained code updates** that don't touch user secrets
3. **Clear documentation** of what values are user-controlled vs. system-defined
4. **Backward compatibility** for existing deployments
5. **Future-proof design** for multi-tenant deployments

The key insight is that system endpoints (proxy URLs, API hosts, Redis stream names) are **code infrastructure**, not user configuration. They should be version-controlled and updated via code changes, not user edits.
---

## 11. Implementation Status (2026-07-28)

### Completed:
- ✅ Created `config/system_endpoints.yaml` with system URLs
- ✅ Created `config/user.example.yaml` template
- ✅ Updated `config/default.yaml` to remove duplicate quote_proxy config
- ✅ Updated `src/hermes/web/templates/config.html` to reference user.local.yaml
- ✅ Updated `.env.example` with documentation

### In Progress:
- ⚠️ Configuration loading works but `VenueConfig` model needs update

### Required Code Change (NEEDS "Build Code"):

The `VenueConfig` model in `src/hermes/core/config.py` needs to be updated to support `api_url` and `api_host` as extra fields (like how `FeaturesConfig` works):

```python
class VenueConfig(BaseModel):
    enabled: bool = True
    asset_classes: list[str] = Field(default_factory=list)
    credentials: dict[str, str] = Field(default_factory=dict)
    rate_limit_per_min: int = 200
    data_modes: dict[str, bool] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    
    model_config = {"extra": "allow"}  # ADD THIS LINE
```

Or alternatively, the code in `src/hermes/marketdata/price_feed.py` line 301 should be updated to:
```python
# Before:
base = config.venues.tradingview.api_url or base

# After:
base = config.venues.tradingview.features.get("api_url", base)
```

### Test Results:
- ✅ 12/12 smoke tests pass
- ⚠️ 43 tests fail due to pre-existing issues (database schema, middleware, etc.)
- ❌ Some tests fail due to VenueConfig not having `api_url` attribute (needs code change)

---

## 12. Files Affected Summary

### System Endpoint Changes (Code Update Required):
When system endpoints change, only these files need modification:
- `config/system_endpoints.yaml` - Update URLs
- `src/hermes/core/config.py` - Add `model_config = {"extra": "allow"}` to VenueConfig (NEEDS BUILD CODE)
- `src/hermes/marketdata/price_feed.py` - Update api_url access pattern (NEEDS BUILD CODE)

### User Variables (Never Touch):
- `.env` - User secrets
- `.env.local` - User secrets  
- `config/user.local.yaml` - User overrides
- `config/default.yaml` - Behavioral defaults (only for user-controlled thresholds)

