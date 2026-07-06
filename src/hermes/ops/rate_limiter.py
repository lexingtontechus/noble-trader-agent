"""
Rate Limiter Module for Hermes Trading Platform

Provides rate limiting functionality with sliding window algorithm.
Used to enforce per-endpoint and per-client rate limits.

Usage:
    from hermes.ops.rate_limiter import RateLimiter
    
    limiter = RateLimiter(max_requests=100, window_seconds=60)
    
    if limiter.is_allowed(client_id="192.168.1.1"):
        # Process request
        pass
    else:
        # Return 429 Too Many Requests
        pass
"""

from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class RateLimitWindow:
    """Represents a time window for rate limiting."""
    start_time: float
    request_count: int = 0
    
    def is_expired(self, current_time: float, window_seconds: int) -> bool:
        """Check if window has expired."""
        return current_time - self.start_time >= window_seconds


@dataclass
class RateLimitInfo:
    """Information about current rate limit status."""
    allowed: bool
    remaining: int
    reset_time: float
    limit: int
    window_seconds: int


class RateLimiter:
    """
    Thread-safe rate limiter using sliding window algorithm.
    
    Tracks requests per client (IP, user, or custom identifier)
    and enforces maximum requests per time window.
    """
    
    def __init__(self, max_requests: int, window_seconds: int = 60) -> None:
        """
        Initialize the rate limiter.
        
        Args:
            max_requests: Maximum number of requests allowed per window
            window_seconds: Time window in seconds (default 60)
        """
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._lock = RLock()
        
        # Storage: client_id -> deque of request timestamps
        self._requests: dict[str, list[float]] = defaultdict(list)
        
        # Statistics
        self._stats = {
            "total_requests": 0,
            "blocked_requests": 0,
            "unique_clients": 0,
        }
    
    def is_allowed(self, client_id: str, additional_key: str | None = None) -> bool:
        """
        Check if a request is allowed for the given client.
        
        Args:
            client_id: Unique identifier for the client (IP, user, etc.)
            additional_key: Optional secondary key for multi-dimensional limits
            
        Returns:
            True if request is allowed, False if rate limited
        """
        key = f"{client_id}:{additional_key}" if additional_key else client_id
        current_time = time.time()
        
        with self._lock:
            # Clean up expired timestamps
            self._requests[key] = [
                ts for ts in self._requests[key]
                if current_time - ts < self.window_seconds
            ]
            
            # Check if we're under the limit
            if len(self._requests[key]) < self.max_requests:
                self._requests[key].append(current_time)
                self._stats["total_requests"] += 1
                if key not in self._requests or len(self._requests[key]) == 1:
                    self._stats["unique_clients"] += 1
                return True
            else:
                self._stats["blocked_requests"] += 1
                return False
    
    def get_info(self, client_id: str, additional_key: str | None = None) -> RateLimitInfo:
        """
        Get rate limit information for a client.
        
        Args:
            client_id: Unique identifier for the client
            additional_key: Optional secondary key
            
        Returns:
            RateLimitInfo with current status
        """
        key = f"{client_id}:{additional_key}" if additional_key else client_id
        current_time = time.time()
        
        with self._lock:
            # Clean up expired timestamps
            self._requests[key] = [
                ts for ts in self._requests[key]
                if current_time - ts < self.window_seconds
            ]
            
            request_count = len(self._requests[key])
            remaining = max(0, self.max_requests - request_count)
            
            # Calculate reset time
            if self._requests[key]:
                oldest = min(self._requests[key])
                reset_time = oldest + self.window_seconds
            else:
                reset_time = current_time + self.window_seconds
            
            return RateLimitInfo(
                allowed=request_count < self.max_requests,
                remaining=remaining,
                reset_time=reset_time,
                limit=self.max_requests,
                window_seconds=self.window_seconds,
            )
    
    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics."""
        with self._lock:
            return {
                **self._stats,
                "active_clients": len(self._requests),
            }
    
    def reset(self, client_id: str | None = None) -> None:
        """
        Reset rate limit for a client or all clients.
        
        Args:
            client_id: Client to reset, or None to reset all
        """
        with self._lock:
            if client_id:
                self._requests.pop(client_id, None)
            else:
                self._requests.clear()
    
    def get_remaining(self, client_id: str, additional_key: str | None = None) -> int:
        """Get remaining requests for a client."""
        return self.get_info(client_id, additional_key).remaining
    
    def get_reset_time(self, client_id: str, additional_key: str | None = None) -> float:
        """Get reset time for a client's rate limit."""
        return self.get_info(client_id, additional_key).reset_time


class CompositeRateLimiter:
    """
    Rate limiter that combines multiple rate limiters.
    Useful for applying multiple limits (e.g., per-IP + per-user).
    """
    
    def __init__(self, limiters: list[RateLimiter]) -> None:
        """Initialize with a list of rate limiters."""
        self._limiters = limiters
    
    def is_allowed(self, client_id: str, additional_key: str | None = None) -> bool:
        """Check if allowed by ALL limiters."""
        return all(
            limiter.is_allowed(client_id, additional_key)
            for limiter in self._limiters
        )
    
    def get_info(self, client_id: str, additional_key: str | None = None) -> dict[str, RateLimitInfo]:
        """Get info from all limiters."""
        return {
            f"limiter_{i}": limiter.get_info(client_id, additional_key)
            for i, limiter in enumerate(self._limiters)
        }


# Global rate limiters registry
_rate_limiters: dict[str, RateLimiter] = {}


def get_rate_limiter(name: str, max_requests: int, window_seconds: int = 60) -> RateLimiter:
    """Get or create a named rate limiter."""
    if name not in _rate_limiters:
        _rate_limiters[name] = RateLimiter(max_requests, window_seconds)
    return _rate_limiters[name]


def reset_all_rate_limiters() -> None:
    """Reset all rate limiters (useful for testing)."""
    global _rate_limiters
    _rate_limiters.clear()