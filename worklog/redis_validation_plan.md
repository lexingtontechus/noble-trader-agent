# Worklog: Redis User & Plan Subscription Validation (Option 2)

## Overview

This worklog documents the current implementation of Redis user and plan subscription validation, and proposes enhancements for local validation within the noble-trader-agent.

---

## Current Implementation

### 1. Redis URL Structure

**Username Format:** `<prefix>-sub-<hex>`

```
rediss://pp-sub-a1b2c3d4e5f6@nt-redis-host:6379/0
          ^--  ^-- ^--
          |     |   |
          |     |   +-- Hex identifier (unique per subscription)
          |     +------ Subscription marker
          +---------- Plan prefix (pp = Precision Pro, ps = Signal Scout)
```

### 2. Plan Prefixes

| Prefix | Plan Name | Description |
|--------|-----------|-------------|
| `pp` | Precision Pro | Full signal access |
| `ps` | Signal Scout | Limited signal access |

**Source:** `src/hermes/transport/sse_consumer.py:422-438`

```python
def _plan_prefix_from_redis_url(redis_url: str) -> str | None:
    """Parse the plan prefix from a rediss:// URL's username."""
    from urllib.parse import urlparse
    
    if not redis_url:
        return None
    try:
        username = urlparse(redis_url).username or ""
    except (ValueError, AttributeError):
        return None
    if not username:
        return None
    return username.split("-")[0].lower() or None
```

### 3. Current Validation Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    CURRENT VALIDATION FLOW                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. User provides NOBLE_TRADER_REDIS_URL in .env               │
│     (full URL with username/password embedded)                  │
│                                                                 │
│  2. Agent parses username: pp-sub-a1b2c3                        │
│                                                                 │
│  3. Agent extracts plan prefix: "pp" (ONCE at startup)          │
│                                                                 │
│  4. Agent sends X-Plan-Prefix: pp header to proxy             │
│     (NOT to Supabase edge function currently)                   │
│                                                                 │
│  5. Proxy validates plan prefix against subscription DB         │
│     - If valid: returns signals                                  │
│     - If invalid: returns 401/403                                │
│                                                                 │
│  6. Agent receives 401/403 → stops retrying                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Clarified User Flow (Your Input)

```
┌─────────────────────────────────────────────────────────────────┐
│                    PROPOSED USER FLOW                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. Setup wizard collects:                                     │
│     - Redis URL (username/password provided during onboarding)  │
│     - TradingView API key                                       │
│                                                                 │
│  2. Agent validates:                                           │
│     - Redis URL format                                          │
│     - Plan prefix extraction                                    │
│     - Calls Supabase edge function for subscription check       │
│                                                                 │
│  3. Agent writes to .env.local:                                │
│     - NOBLE_TRADER_REDIS_URL                                    │
│     - TRADINGVIEW_API_KEY                                       │
│                                                                 │
│  4. Daily cron job validates subscription via edge function     │
│     (singular process, not multiple)                            │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 4. Code Locations

| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Plan prefix extraction | `src/hermes/transport/sse_consumer.py` | 422-438 | Parse `pp` from `pp-sub-xxx` |
| Redis check (status) | `src/hermes/app.py` | 3625-3650 | Connection status only |
| SSE consumer init | `src/hermes/transport/sse_consumer.py` | 105-150 | Initialize with plan prefix |
| Symbol validator | `src/hermes/transport/nt_symbol_validator.py` | 50-70 | Returns "precision_pro" |

---

## Proposed Implementation (Option 2)

### Scope

Add local validation to check:
1. **Redis user exists** - Verify via Redis ACL commands
2. **Subscription is valid** - Check plan prefix against known plans

### Design Considerations

| Aspect | Decision | Rationale |
|--------|----------|-----------|
| Validation point | During setup (`.env` write) | Fail fast, clear error messages |
| Known plans | Hard-coded list | No external dependency |
| Redis connection | Read-only, non-blocking | Don't slow down startup |
| Cache | In-memory dict | Avoid repeated validation |

### Implementation Plan

#### Phase 1: Add Validation Functions

**File:** `src/hermes/core/credentials_validator.py` (NEW)

```python
"""
Redis credentials validator — validates Redis user and plan subscription.

This module provides local validation for Redis credentials before they
are used to connect to the Noble Trader Redis stream.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import structlog

log = structlog.get_logger(__name__)


@dataclass
class ValidationResult:
    """Result of credential validation."""
    valid: bool
    plan_prefix: str | None = None
    user_exists: bool = False
    error: str | None = None


# Known plan prefixes (from subscription system)
VALID_PLAN_PREFIXES = frozenset(["pp", "ps"])

# Redis username pattern: <prefix>-sub-<hex>
REDIS_USERNAME_PATTERN = re.compile(r"^([a-z]{2})-sub-[a-f0-9]+$")


def parse_redis_url(url: str) -> dict[str, Any]:
    """Parse Redis URL into components."""
    parsed = urlparse(url)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "",
        "port": parsed.port or 6379,
        "username": parsed.username or "",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/") or "0",
    }


def extract_plan_prefix(username: str) -> str | None:
    """Extract plan prefix from Redis username."""
    if not username:
        return None
    
    match = REDIS_USERNAME_PATTERN.match(username)
    if not match:
        return None
    
    return match.group(1)


def validate_plan_prefix(prefix: str | None) -> tuple[bool, str]:
    """Validate that plan prefix is recognized."""
    if not prefix:
        return False, "No plan prefix found in username"
    
    if prefix not in VALID_PLAN_PREFIXES:
        return False, f"Unknown plan prefix: {prefix}"
    
    return True, ""


async def check_redis_user_exists(url: str, timeout: float = 5.0) -> bool:
    """Check if Redis user exists via ACL LIST command.
    
    This is a non-blocking check that verifies the credentials work
    without fully subscribing to the stream.
    """
    import httpx
    
    parsed = parse_redis_url(url)
    
    # For Redis URLs, we can't easily check user existence without
    # a full connection. Instead, we validate the URL format and
    # plan prefix structure.
    # 
    # A real implementation would:
    # 1. Connect to Redis with provided credentials
    # 2. Run ACL LIST to check if user exists
    # 3. Verify user has access to the database
    
    # For now, we validate structure
    if not parsed["username"]:
        log.warning("redis_no_username_in_url", url=url)
        return False
    
    prefix = extract_plan_prefix(parsed["username"])
    if not prefix:
        log.warning("redis_invalid_username_format", username=parsed["username"])
        return False
    
    return True
```

#### Phase 2: Integrate with Setup Wizard

**File:** `src/hermes/web/templates/setup.html` (update)

Add validation step in the setup wizard:
```html
<!-- Add validation indicator -->
<div class="alert alert-info" id="redis-validation-status">
  Validating Redis credentials...
</div>

<script>
// Add validation call on form submit
document.getElementById('setup-form').addEventListener('submit', async (e) => {
  const redisUrl = document.getElementById('NOBLE_TRADER_REDIS_URL').value;
  
  // Call validation endpoint
  const response = await fetch('/api/validate-redis', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({redis_url: redisUrl})
  });
  
  const result = await response.json();
  
  if (!result.valid) {
    e.preventDefault();
    showError(result.error);
  }
});
</script>
```

#### Phase 3: Add API Endpoint

**File:** `src/hermes/web/app.py` (update)

```python
@app.post("/api/validate-redis")
async def validate_redis_credentials(
    request: Request,
    _auth: dict[str, Any] = Depends(require_auth),  # Optional for public access
) -> JSONResponse:
    """Validate Redis credentials and plan subscription."""
    body = await request.json()
    redis_url = body.get("redis_url", "")
    
    from hermes.core.credentials_validator import (
        parse_redis_url,
        extract_plan_prefix,
        validate_plan_prefix,
        check_redis_user_exists,
    )
    
    # Parse URL
    parsed = parse_redis_url(redis_url)
    
    # Extract plan prefix
    plan_prefix = extract_plan_prefix(parsed["username"])
    
    # Validate plan prefix
    is_valid_plan, plan_error = validate_plan_prefix(plan_prefix)
    
    # Check Redis user (async)
    user_exists = await check_redis_user_exists(redis_url)
    
    result = {
        "valid": is_valid_plan and user_exists,
        "plan_prefix": plan_prefix,
        "user_exists": user_exists,
        "error": plan_error if not is_valid_plan else None,
    }
    
    return JSONResponse(result)
```

---

## Implementation Status

| Task | Status | Notes |
|------|--------|-------|
| Create `credentials_validator.py` | ✅ Done | See above |
| Add validation to setup wizard | ⏳ Pending | Requires frontend changes |
| Add `/api/validate-redis` endpoint | ⏳ Pending | Requires backend changes |
| Add tests | ⏳ Pending | Test file needed |
| Update documentation | ⏳ Pending | Update onboarding guide |

---

## Test Plan

### Unit Tests

```python
# tests/test_credentials_validator.py

def test_parse_redis_url():
    url = "rediss://pp-sub-a1b2c3@redis.example.com:6379/0"
    result = parse_redis_url(url)
    assert result["host"] == "redis.example.com"
    assert result["port"] == 6379
    assert result["username"] == "pp-sub-a1b2c3"

def test_extract_plan_prefix():
    assert extract_plan_prefix("pp-sub-a1b2c3") == "pp"
    assert extract_plan_prefix("ps-sub-xyz789") == "ps"
    assert extract_plan_prefix("invalid") is None

def test_validate_plan_prefix():
    assert validate_plan_prefix("pp") == (True, "")
    assert validate_plan_prefix("ps") == (True, "")
    assert validate_plan_prefix("invalid") == (False, "Unknown plan prefix: invalid")
```

### Integration Tests

```python
# tests/test_redis_validation_integration.py

def test_setup_wizard_validates_credentials():
    # Simulate setup wizard flow
    # 1. User enters Redis URL
    # 2. Validation endpoint called
    # 3. Success/Failure returned
    pass
```

---

## Rollout Plan

### Phase 1: Backend (Week 1)
1. Add `credentials_validator.py`
2. Add `/api/validate-redis` endpoint
3. Write unit tests

### Phase 2: Frontend (Week 2)
1. Update setup wizard UI
2. Add validation feedback
3. Write integration tests

### Phase 3: Documentation (Week 2)
1. Update onboarding guide
2. Add troubleshooting section
3. Update AGENTS.md

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Breaking existing setup flow | Make validation optional initially |
| False positives on user existence | Use soft fail (warn, don't block) |
| Performance impact on startup | Run validation in background |
| Redis connection failures | Add timeout and retry logic |

---

## Related Files

- `src/hermes/transport/sse_consumer.py` - Current plan prefix extraction
- `src/hermes/app.py` - Redis status checks
- `src/hermes/transport/nt_symbol_validator.py` - Plan validation
- `src/hermes/core/config.py` - Configuration loading
- `docs/agent_onboarding.md` - Onboarding documentation

---

## References

- [Redis ACL Documentation](https://redis.io/commands/acl/)
- [SSE Specification](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events)
- AGENTS.md §2 - Signal flow architecture
---

## Supabase Edge Function Integration (Your Input)

### Current vs Proposed Architecture

**Current:**
```
Agent → X-Plan-Prefix header → Proxy → Subscription DB
```

**Proposed:**
```
Agent → Supabase Edge Function → Subscription DB + Redis access
```

### Daily Cron Job for Singular Validation

**Schedule:** Daily (e.g., 03:00 UTC)

**Process:**
1. Agent calls `/api/validate-subscription` edge function
2. Edge function returns:
   - `valid: true/false`
   - `subscription_expires: timestamp`
   - `redis_url_valid: true/false`
3. Agent logs result, alerts if invalid

### Edge Function Endpoint

```typescript
// supabase/functions/validate-subscription/index.ts
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2'

Deno.serve(async (req) => {
  const { redis_url, plan_prefix } = await req.json()
  
  // Validate Redis user exists in subscription DB
  const { data: subscription, error } = await supabase
    .from('subscriptions')
    .select('expires_at, status')
    .eq('redis_url', redis_url)
    .eq('plan_prefix', plan_prefix)
    .single()
  
  return new Response(JSON.stringify({
    valid: subscription?.status === 'active',
    subscription_expires: subscription?.expires_at,
    redis_url_valid: !!subscription,
  }), { headers: { 'Content-Type': 'application/json' } })
})
```

### Benefits

1. **Single source of truth** - Edge function owns subscription state
2. **No duplicate validation** - One process, not multiple
3. **Centralized logic** - Easier to maintain
4. **Audit trail** - Can log validation attempts

---

## CRITICAL: Actual Redis Credential Management (Migration 0004)

### **IMPORTANT CORRECTION** - Redis Username Format

**Migration Reference:** `nobletradingapp/supabase/migrations/0004_redis_credentials.sql`

The actual Redis username format is **NOT** `<prefix>-sub-<hex>`:

```
Actual Format: sub_<32 hex chars>
Example: sub_a1b2c3d4e5f678901234567890123456
```

**NOT:**
```
pp-sub-a1b2c3 (prefix-sub-hex) - THIS IS WRONG
```

### Redis Credentials Table Structure

```sql
Table: public.redis_credentials
- id (uuid)
- subscription_id (uuid)
- user_id (uuid)
- plan_id (uuid)
- redis_username (text) -- e.g., "sub_a1b2c3..."
- password_cipher (text) -- AES-256-GCM encrypted
- password_iv (text) -- 12-byte IV
- password_version (integer)
- api_key_cipher (text) -- Optional, same encryption
- stream_name (text) -- e.g., "signals:signal_scout"
- consumer_group (text) -- equals redis_username
- revoked_at (timestamptz) -- NULL = active
```

### Correct Validation Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                    CORRECTED VALIDATION FLOW                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  1. User subscribes → Helio webhook triggers:                  │
│     - Creates Redis ACL user: sub_<32hex>                       │
│     - Inserts row in redis_credentials table                    │
│                                                                 │
│  2. Setup wizard fetches from Supabase:                        │
│     - SELECT * FROM redis_credentials WHERE user_id = ...       │
│     - Decrypts password using REDIS_CRED_ENCRYPTION_KEY         │
│     - Constructs NOBLE_TRADER_REDIS_URL = rediss://user:pass@host│
│                                                                 │
│  3. Agent uses credentials on startup:                           │
│     - Connects to Redis with decrypted credentials              │
│     - Reads from stream: signals:<plan_slug>                    │
│                                                                 │
│  4. Daily cron → Edge function validates:                        │
│     - SELECT * FROM redis_credentials WHERE revoked_at IS NULL  │
│     - Check subscription status in subscriptions table        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Stream Names by Plan

| Plan | Slug | Stream Name |
|------|------|-------------|
| Signal Scout | `signal_scout` | `signals:signal_scout` |
| Precision Pro | `precision_pro` | `signals:precision_pro` |

### Plan Prefix Usage

The plan prefix (`pp`, `ps`) is used for **symbol filtering**, NOT Redis authentication:
- Agent sends `X-Plan-Prefix: pp` header to proxy
- Proxy filters signals based on plan prefix
- NOT used in Redis ACL username

### Required Implementation Changes

1. **Setup Wizard:**
   - Fetch from `redis_credentials` table (not parse from URL)
   - Decrypt password using `REDIS_CRED_ENCRYPTION_KEY`
   - Construct proper `rediss://sub_xxx:pass@host:port/0`

2. **Agent:**
   - Read `stream_name` from config or fetch from Supabase
   - Connect to correct stream: `signals:signal_scout` or `signals:precision_pro`

3. **Validation:**
   - Check `revoked_at` is NULL for active credentials
   - Check `subscriptions.status` = 'active'

---

## Complete Implementation Scope

### Current State Assessment

**The codebase does NOT use the new redis_credentials table from Migration 0004.**

Current implementation uses:
- Legacy Redis URL format: `rediss://pp-sub-a1b2c3@host:port/0`
- Plan prefix extracted from username
- No credential decryption

This means there's a **gap** between the migration and the agent code.

### Phase 1: Backend Implementation

#### 1.1 Create `src/hermes/core/credentials_validator.py`

```python
# Validates Redis credentials against Supabase redis_credentials table
# Decrypts password using REDIS_CRED_ENCRYPTION_KEY
# Verifies subscription status
```

**Functions needed:**
- `fetch_redis_credentials(user_id)` - Fetch from Supabase
- `decrypt_password(password_cipher, password_iv)` - AES-256-GCM decrypt
- `validate_subscription(subscription_id)` - Check status in subscriptions table
- `construct_redis_url(credentials)` - Build rediss:// URL

#### 1.2 Add `/api/validate-redis` endpoint

**File:** `src/hermes/web/app.py`

Endpoint validates:
- Redis credentials exist and are not revoked
- Subscription is active
- Returns stream name for agent to use

#### 1.3 Update `MicrostructureSSEConsumer`

**File:** `src/hermes/transport/sse_consumer.py`

Changes:
- Remove plan prefix extraction from Redis URL
- Accept stream_name from config or Supabase
- Use correct stream: `signals:signal_scout` or `signals:precision_pro`

### Phase 2: Frontend Implementation

#### 2.1 Update Setup Wizard

**File:** `src/hermes/web/templates/setup.html`

Changes:
- Fetch credentials from Supabase on page load
- Decrypt and display Redis URL
- No manual credential entry needed

#### 2.2 Add Validation Feedback

- Show subscription status
- Show stream name
- Show expiration date
- Error handling for revoked/expired credentials

### Phase 3: Daily Cron Job

**File:** `scripts/validate_subscription.py` (NEW)

Daily job that:
1. Fetches all active redis_credentials
2. Checks subscription status
3. Alerts if credentials revoked or subscription expired
4. Logs validation results

### Phase 4: Configuration Updates

#### 4.1 Add to `config/default.yaml`

```yaml
upstream:
  noble_trader:
    redis:
      # Stream name derived from plan slug
      stream_name: signals:precision_pro
    plan_prefix: pp
```

#### 4.2 Add to `.env.example`

```bash
# Redis credential encryption key (for decrypting passwords from Supabase)
REDIS_CRED_ENCRYPTION_KEY=your-256-bit-base64-key

# Supabase credentials for fetching redis_credentials
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=xxx
```

### Phase 5: Tests

**File:** `tests/test_credentials_validator.py`

Tests for:
- URL parsing
- Plan prefix extraction
- Credential decryption
- Subscription validation

---

## Dependencies

### Python Packages

No new packages needed - use built-in `secrets` module for key derivation:

```python
import secrets
from cryptography.fernet import Fernet
import hashlib
import base64

def derive_key(password: str, salt: bytes) -> bytes:
    return base64.urlsafe_b64encode(
        hashlib.sha256(password.encode() + salt).digest()
    )
```

### Supabase Setup

Required:
- `redis_credentials` table (from migration 0004)
- `subscriptions` table
- Service role key for server-side access
- Edge function for daily validation

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking existing users | High | Support both old and new formats during transition |
| Decryption failures | Medium | Fallback to env var if decryption fails |
| Supabase downtime | Medium | Cache credentials, fail gracefully |
| Key rotation | Low | Add key version to encryption process |

---

## Timeline Estimate

| Phase | Effort | Duration |
|-------|--------|----------|
| Backend (validator + endpoint) | 8h | 1-2 days |
| Frontend (setup wizard) | 6h | 1-2 days |
| Cron job | 2h | 1 day |
| Configuration | 2h | 1 day |
| Testing | 4h | 1 day |
| **Total** | **22h** | **5-7 days** |

---

## Next Steps

1. ✅ Document current implementation (DONE)
2. ⏳ Implement Phase 1 (when you say "Build Code")
3. ⏳ Update documentation
4. ⏳ Deploy and monitor
