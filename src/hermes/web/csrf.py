"""
CSRF Protection Module for Hermes Trading Platform

Provides CSRF token generation, validation, and storage.
CSRF (Cross-Site Request Forgery) protection prevents unauthorized
commands from being transmitted from a user that the site knows.

Usage:
    from hermes.web.csrf import CSRFProtection, get_csrf_token
    
    csrf = CSRFProtection(secret_key="your-secret-key")
    
    # Generate a token
    token = csrf.generate_token(session_id="[SESSION_ID]")
    
    # Validate a token
    if csrf.validate_token(token, session_id="[SESSION_ID]"):
        # Token is valid
        pass
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any


# Token storage (in-memory with LRU eviction)
# In production, consider using Redis or database-backed storage
_MAX_TOKENS_PER_SESSION = 10


@dataclass
class CSRFToken:
    """Represents a CSRF token with metadata."""
    token: str
    created_at: float
    session_id: str
    expires_at: float


class CSRFProtection:
    """
    CSRF (Cross-Site Request Forgery) protection implementation.
    
    Features:
    - Cryptographically secure token generation
    - Session-bound tokens
    - Token expiration
    - LRU cache for token storage
    - Configurable token length
    """
    
    def __init__(
        self,
        secret_key: str | None = None,
        token_length: int = 32,
        token_ttl: int = 3600,  # 1 hour default
        max_tokens_per_session: int = _MAX_TOKENS_PER_SESSION,
    ) -> None:
        """
        Initialize CSRF protection.
        
        Args:
            secret_key: Secret key for signing tokens (if None, uses random bytes)
            token_length: Length of generated tokens in bytes
            token_ttl: Token time-to-live in seconds
            max_tokens_per_session: Maximum tokens to keep per session
        """
        self.token_length = token_length
        self.token_ttl = token_ttl
        self.max_tokens_per_session = max_tokens_per_session
        
        # Secret key for HMAC signing
        self._secret_key = secret_key or secrets.token_bytes(32)
        if isinstance(self._secret_key, str):
            self._secret_key = self._secret_key.encode('utf-8')
        
        # Token storage: session_id -> OrderedDict of token -> CSRFToken
        self._tokens: dict[str, OrderedDict[str, CSRFToken]] = {}
    
    def generate_token(self, session_id: str) -> str:
        """
        Generate a new CSRF token for a session.
        
        Args:
            session_id: Unique session identifier
            
        Returns:
            CSRF token string
        """
        # Generate random token
        raw_token = secrets.token_bytes(self.token_length)
        token = raw_token.hex()
        
        # Create signed token
        signature = self._sign_token(token, session_id)
        signed_token = f"{token}:{signature}"
        
        # Store token
        now = time.time()
        csrf_token = CSRFToken(
            token=signed_token,
            created_at=now,
            session_id=session_id,
            expires_at=now + self.token_ttl,
        )
        
        # Add to session's token list
        if session_id not in self._tokens:
            self._tokens[session_id] = OrderedDict()
        
        token_list = self._tokens[session_id]
        token_list[signed_token] = csrf_token
        
        # Evict oldest tokens if over limit
        while len(token_list) > self.max_tokens_per_session:
            token_list.popitem(last=False)
        
        return signed_token
    
    def validate_token(self, token: str, session_id: str) -> bool:
        """
        Validate a CSRF token.
        
        Args:
            token: The token to validate
            session_id: The session ID the token was issued for
            
        Returns:
            True if token is valid, False otherwise
        """
        if not token or not session_id:
            return False
        
        # Parse token
        try:
            parts = token.split(':')
            if len(parts) != 2:
                return False
            raw_token, signature = parts
        except Exception:
            return False
        
        # Verify signature
        if not self._verify_signature(raw_token, signature, session_id):
            return False
        
        # Check token exists and is valid
        if session_id not in self._tokens:
            return False
        
        token_list = self._tokens[session_id]
        if token not in token_list:
            return False
        
        stored_token = token_list[token]
        
        # Check expiration
        if time.time() > stored_token.expires_at:
            self._remove_token(session_id, token)
            return False
        
        # Token is valid - remove it (one-time use) or keep for reuse
        # For security, we remove it so it can't be reused
        self._remove_token(session_id, token)
        
        return True
    
    def _sign_token(self, token: str, session_id: str) -> str:
        """Sign a token with HMAC."""
        message = f"{token}:{session_id}".encode('utf-8')
        signature = hmac.new(self._secret_key, message, hashlib.sha256).hexdigest()
        return signature
    
    def _verify_signature(self, token: str, signature: str, session_id: str) -> bool:
        """Verify a token's signature."""
        expected = self._sign_token(token, session_id)
        return hmac.compare_digest(signature, expected)
    
    def _remove_token(self, session_id: str, token: str) -> None:
        """Remove a token from storage."""
        if session_id in self._tokens:
            token_list = self._tokens[session_id]
            if token in token_list:
                del token_list[token]
    
    def cleanup_expired(self) -> int:
        """
        Remove all expired tokens.
        
        Returns:
            Number of tokens removed
        """
        now = time.time()
        removed_count = 0
        
        for session_id in list(self._tokens.keys()):
            token_list = self._tokens[session_id]
            expired = [t for t, info in token_list.items() if info.expires_at < now]
            for token in expired:
                del token_list[token]
                removed_count += 1
            
            if not token_list:
                del self._tokens[session_id]
        
        return removed_count
    
    def get_token_for_session(self, session_id: str) -> str | None:
        """
        Get a valid token for a session (creates one if none exists).
        
        Args:
            session_id: Session identifier
            
        Returns:
            CSRF token or None if session not found
        """
        if session_id not in self._tokens:
            return None
        
        token_list = self._tokens[session_id]
        if not token_list:
            return None
        
        # Return the most recent valid token
        return next(reversed(token_list.keys()))


# Global CSRF protection instance
_csrf_protection: CSRFProtection | None = None


def get_csrf_protection() -> CSRFProtection:
    """Get or create the global CSRF protection instance."""
    global _csrf_protection
    if _csrf_protection is None:
        _csrf_protection = CSRFProtection()
    return _csrf_protection


def get_csrf_token(session_id: str) -> str:
    """Get or generate a CSRF token for a session."""
    csrf = get_csrf_protection()
    return csrf.generate_token(session_id)


def validate_csrf_token(token: str, session_id: str) -> bool:
    """Validate a CSRF token."""
    csrf = get_csrf_protection()
    return csrf.validate_token(token, session_id)


def reset_csrf_protection() -> None:
    """Reset the global CSRF protection (for testing)."""
    global _csrf_protection
    _csrf_protection = None