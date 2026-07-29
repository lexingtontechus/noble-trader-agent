#!/usr/bin/env python3
"""
Daily subscription validation script.

Called by cron at 03:00 UTC daily to validate:
- Redis credentials are still active (not revoked)
- Subscriptions are still active
- Alert if any issues found

Usage:
    python scripts/validate_subscription.py

Environment variables:
    SUPABASE_URL - Supabase project URL
    SUPABASE_SERVICE_ROLE_KEY - Service role key for server-side access
    REDIS_CRED_ENCRYPTION_KEY - Key for decrypting passwords
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone

import structlog

log = structlog.get_logger(__name__)


async def validate_subscriptions() -> dict[str, any]:
    """Validate all active Redis credentials against subscriptions."""
    import httpx
    
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    encryption_key = os.getenv("REDIS_CRED_ENCRYPTION_KEY", "")
    
    if not supabase_url or not service_key:
        log.error("missing_supabase_config", 
                  supabase_url=bool(supabase_url),
                  service_key=bool(service_key))
        return {"valid": False, "error": "Missing Supabase configuration"}
    
    headers = {
        "apikey": service_key,
        "Authorization": f"Bearer {service_key}",
        "Content-Type": "application/json",
    }
    
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "valid": True,
        "checked": 0,
        "issues": [],
    }
    
    # Fetch all active Redis credentials
    redis_url = f"{supabase_url}/rest/v1/redis_credentials"
    params = {"revoked_at": "is.null"}
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(redis_url, headers=headers, params=params)
        
        if response.status_code == 404:
            log.warning("redis_credentials_table_not_found")
            return results
        
        if response.status_code != 200:
            log.error("failed_to_fetch_credentials", 
                      status=response.status_code,
                      body=response.text[:500])
            return {"valid": False, "error": f"HTTP {response.status_code}"}
        
        credentials = response.json()
    
    # For each credential, check subscription status
    for cred in credentials:
        results["checked"] += 1
        
        subscription_id = cred.get("subscription_id")
        if not subscription_id:
            results["issues"].append({
                "type": "missing_subscription",
                "redis_username": cred.get("redis_username"),
            })
            continue
        
        # Check subscription status
        sub_url = f"{supabase_url}/rest/v1/subscriptions"
        sub_params = {
            "id": f"eq.{subscription_id}",
            "status": "eq.active",
        }
        
        sub_response = await client.get(sub_url, headers=headers, params=sub_params)
        
        if sub_response.status_code != 200:
            results["issues"].append({
                "type": "subscription_check_failed",
                "subscription_id": str(subscription_id),
                "status_code": sub_response.status_code,
            })
            continue
        
        sub_data = sub_response.json()
        if not sub_data:
            results["issues"].append({
                "type": "subscription_inactive",
                "subscription_id": str(subscription_id),
                "redis_username": cred.get("redis_username"),
            })
            continue
    
    if results["issues"]:
        results["valid"] = False
    
    return results


def main() -> int:
    """Main entry point."""
    log.info("subscription_validation_started", 
             timestamp=datetime.now(timezone.utc).isoformat())
    
    try:
        results = asyncio.run(validate_subscriptions())
    except Exception as e:
        log.error("subscription_validation_failed", error=str(e))
        return 1
    
    # Log results
    if results["valid"]:
        log.info("subscription_validation_passed",
                 checked=results["checked"],
                 issues=results.get("issues", []))
    else:
        log.warning("subscription_validation_failed",
                    checked=results["checked"],
                    issues=results.get("issues", []))
    
    # Output JSON for cron monitoring
    print(json.dumps(results, indent=2))
    
    # Return 0 if valid, 1 if issues found
    return 0 if results["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())