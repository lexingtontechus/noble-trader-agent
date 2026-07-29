"""
Redis credentials validator — validates Redis user and plan subscription.

This module provides local validation for Redis credentials before they
are used to connect to the Noble Trader Redis stream.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
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
    stream_name: str | None = None
    user_exists: bool = False
    subscription_active: bool = False
    error: str | None = None


# Known plan prefixes (from subscription system)
VALID_PLAN_PREFIXES = frozenset(["pp", "ps"])

# Redis username pattern: sub_<32 hex chars> (from migration 0004)
REDIS_USERNAME_PATTERN = re.compile(r"^sub_[a-f0-9]{32}$")

# Stream name mapping
PLAN_TO_STREAM = {
    "pp": "signals:precision_pro",
    "ps": "signals:signal_scout",
}


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
    """Extract plan prefix from Redis username.
    
    The username format is now sub_<32hex> (from migration 0004), but we
    also support the legacy <prefix>-sub-<hex> format for backward compatibility.
    """
    if not username:
        return None
    
    # New format: sub_<32hex>
    if REDIS_USERNAME_PATTERN.match(username):
        # This format doesn't contain the plan prefix directly
        # It should come from the subscription record
        return None
    
    # Legacy format: <prefix>-sub-<hex>
    match = re.match(r"^([a-z]{2})-sub-[a-f0-9]+$", username)
    if match:
        return match.group(1)
    
    return None


def validate_plan_prefix(prefix: str | None) -> tuple[bool, str]:
    """Validate that plan prefix is recognized."""
    if not prefix:
        return False, "No plan prefix found"
    
    if prefix not in VALID_PLAN_PREFIXES:
        return False, f"Unknown plan prefix: {prefix}"
    
    return True, ""


def get_stream_name(plan_prefix: str | None) -> str:
    """Get Redis stream name from plan prefix."""
    if plan_prefix and plan_prefix in PLAN_TO_STREAM:
        return PLAN_TO_STREAM[plan_prefix]
    return "signals:precision_pro"  # Default


def derive_key(password: str, salt: bytes = b"noble_trader_redis_v1") -> bytes:
    """Derive Fernet key from password for decryption."""
    import base64
    import hashlib
    
    return base64.urlsafe_b64encode(
        hashlib.sha256(password.encode() + salt).digest()
    )


def decrypt_password(
    password_cipher: str,
    password_iv: str,
    encryption_key: str,
) -> str:
    """Decrypt AES-256-GCM encrypted password.
    
    Args:
        password_cipher: Base64-encoded ciphertext with 16-byte auth tag appended
        password_iv: Base64-encoded 12-byte IV
        encryption_key: Base64-encoded 256-bit encryption key
    
    Returns:
        Decrypted password string
    """
    from cryptography.fernet import Fernet
    
    # Convert IV to bytes
    iv = base64.b64decode(password_iv)
    
    # Decode ciphertext (includes auth tag)
    ciphertext = base64.b64decode(password_cipher)
    
    # Derive key from encryption key
    key = derive_key(encryption_key)
    
    # Create Fernet cipher
    fernet = Fernet(key)
    
    # Decrypt (Fernet expects IV:ciphertext format internally)
    # We need to reconstruct the proper format
    try:
        # Fernet stores: timestamp(8) + iv(16) + ciphertext + hmac(32)
        # We have: iv(12) + ciphertext(48) + tag(16)
        # Need to combine properly
        import os
        
        # Generate a valid Fernet token
        # Fernet format: version(1) | timestamp(8) | iv(16) | ciphertext | hmac(32)
        # We'll use a simplified approach
        
        # Actually, for AES-GCM decryption, we need the cryptography library directly
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        
        key_bytes = base64.urlsafe_b64decode(key)
        aesgcm = AESGCM(key_bytes)
        
        # The ciphertext should be: nonce(12) + ciphertext + tag(16)
        # But we have them separate, so we need to reconstruct
        # Actually, AESGCM.decrypt expects: nonce + ciphertext + tag
        # We have iv (nonce) and ciphertext (which includes the tag at the end)
        
        # Wait - the migration says: "ciphertext includes the 16-byte auth tag appended"
        # So we have: iv + ciphertext_with_tag
        # AESGCM.decrypt expects: nonce + ciphertext
        
        # Let's try a different approach - use the raw AESGCM
        full_nonce_and_ciphertext = iv + ciphertext
        
        # AESGCM expects nonce (12 bytes) + ciphertext (variable) + tag (16 bytes appended)
        # Our ciphertext already has the tag appended
        # So we need: nonce (12 bytes) + ciphertext (with tag)
        
        # Actually, let me re-read: "ciphertext includes the 16-byte auth tag appended"
        # So ciphertext = actual_ciphertext + tag
        # We need to pass: iv + actual_ciphertext + tag
        # But AESGCM.decrypt expects: nonce + ciphertext (where ciphertext includes tag)
        
        # So we just need to concatenate iv + ciphertext
        return aesgcm.decrypt(iv, ciphertext, None).decode()
        
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}") from e


async def fetch_redis_credentials_from_supabase(
    user_id: str,
    supabase_url: str,
    service_role_key: str,
) -> dict[str, Any] | None:
    """Fetch Redis credentials from Supabase redis_credentials table.
    
    Args:
        user_id: The user's ID in Supabase
        supabase_url: Supabase project URL
        service_role_key: Supabase service role key (bypasses RLS)
    
    Returns:
        Dictionary with credentials or None if not found
    """
    import httpx
    
    url = f"{supabase_url}/rest/v1/redis_credentials"
    
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    
    params = {
        "user_id": f"eq.{user_id}",
        "revoked_at": "is.null",
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=headers, params=params)
        
        if response.status_code == 404:
            return None
        
        response.raise_for_status()
        data = response.json()
        
        if not data:
            return None
        
        return data[0]  # Return first match


async def validate_subscription(
    subscription_id: str,
    supabase_url: str,
    service_role_key: str,
) -> bool:
    """Validate subscription is active.
    
    Args:
        subscription_id: The subscription ID
        supabase_url: Supabase project URL
        service_role_key: Supabase service role key
    
    Returns:
        True if subscription is active
    """
    import httpx
    
    url = f"{supabase_url}/rest/v1/subscriptions"
    
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }
    
    params = {
        "id": f"eq.{subscription_id}",
        "status": "eq.active",
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url, headers=headers, params=params)
        
        if response.status_code == 404:
            return False
        
        response.raise_for_status()
        data = response.json()
        
        return bool(data)


async def check_redis_connection(
    redis_url: str,
    timeout: float = 5.0,
) -> bool:
    """Check if Redis connection works with provided credentials.
    
    Args:
        redis_url: Full Redis URL with credentials
        timeout: Connection timeout in seconds
    
    Returns:
        True if connection successful
    """
    import redis.asyncio as aioredis
    
    try:
        # Parse URL to extract components
        parsed = parse_redis_url(redis_url)
        
        # Create Redis client
        client = aioredis.Redis(
            host=parsed["host"],
            port=parsed["port"],
            username=parsed["username"] or None,
            password=parsed["password"] or None,
            db=int(parsed["database"]),
            ssl=parsed["scheme"].startswith("rediss"),
            decode_responses=False,
        )
        
        # Try to ping
        await asyncio.wait_for(client.ping(), timeout=timeout)
        await client.close()
        
        return True
    except Exception as e:
        log.debug("redis_connection_check_failed", error=str(e))
        return False