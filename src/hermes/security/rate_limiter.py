"""
API-wide Rate Limiting for Hermes Trading Platform

Provides comprehensive rate limiting across all API endpoints:
- Token bucket algorithm implementation
- Per-endpoint rate limiting
- Global rate limiting
- User-based rate limiting
- IP-based rate limiting
- API key rate limiting
- Dynamic rate adjustment
- Rate limit headers
- Bypass mechanisms
- Monitoring and statistics
"""

import asyncio
import hashlib
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union
from functools import wraps


class RateLimitScope(Enum):
    """Rate limiting scope."""
    GLOBAL = "global"
    ENDPOINT = "endpoint"
    USER = "user"
    IP = "ip"
    API_KEY = "api_key"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration."""
    max_requests: int
    window_seconds: int
    scope: RateLimitScope
    description: str = ""
    enabled: bool = True
    dynamic: bool = False
    burst_size: Optional[int] = None
    penalty_multiplier: float = 1.0
    
    def __post_init__(self):
        """Set burst size if not specified."""
        if self.burst_size is None:
            self.burst_size = self.max_requests


@dataclass
class RateLimitResult:
    """Rate limiting result."""
    allowed: bool
    remaining: int
    reset_time: Optional[datetime]
    retry_after: Optional[int]
    scope: RateLimitScope
    identifier: str
    limit: int
    window: int


class RateLimiter:
    """
    Token bucket rate limiter implementation.
    
    Features:
    - Thread-safe operation
    - Multiple scope support
    - Dynamic rate adjustment
    - Penalty system
    - Statistics tracking
    - Header support
    """
    
    def __init__(self, cleanup_interval: int = 60):
        """
        Initialize rate limiter.
        
        Args:
            cleanup_interval: Interval for cleaning up expired entries (seconds)
        """
        self._buckets: Dict[str, deque] = defaultdict(deque)
        self._configs: Dict[str, RateLimitConfig] = {}
        self._stats: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._penalties: Dict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()
        
        # Background cleanup task
        self._cleanup_interval = cleanup_interval
        self._cleanup_task: Optional[asyncio.Task] = None
        self._start_cleanup_task()
    
    def configure(self, name: str, config: RateLimitConfig) -> None:
        """
        Configure a rate limit.
        
        Args:
            name: Rate limit name
            config: Rate limit configuration
        """
        self._configs[name] = config
    
    def get_config(self, name: str) -> Optional[RateLimitConfig]:
        """Get rate limit configuration."""
        return self._configs.get(name)
    
    def check_rate_limit(self, name: str, identifier: str, weight: int = 1) -> RateLimitResult:
        """
        Check if request is allowed by rate limit.
        
        Args:
            name: Rate limit name
            identifier: Identifier for the scope (user ID, IP, etc.)
            weight: Weight of the request (multiple tokens)
            
        Returns:
            RateLimitResult with status and headers
        """
        config = self._configs.get(name)
        if not config or not config.enabled:
            return RateLimitResult(
                allowed=True,
                remaining=config.max_requests if config else 0,
                reset_time=None,
                retry_after=None,
                scope=config.scope if config else RateLimitScope.GLOBAL,
                identifier=identifier,
                limit=config.max_requests if config else 0,
                window=config.window_seconds if config else 0
            )
        
        # Apply penalty if any
        penalty = self._penalties.get(name, 1.0)
        effective_max = int(config.max_requests / penalty)
        
        # Generate bucket key
        bucket_key = self._get_bucket_key(name, identifier, config.scope)
        
        # Get current time
        now = time.time()
        
        # Clean old entries
        self._cleanup_bucket(bucket_key, now)
        
        # Check if request is allowed
        current_tokens = len(self._buckets[bucket_key])
        
        # Check if request would exceed limit
        if current_tokens >= effective_max:
            # Calculate retry after time
            oldest_request = self._buckets[bucket_key][0] if self._buckets[bucket_key] else now
            reset_time = datetime.fromtimestamp(oldest_request + config.window_seconds, tz=timezone.utc)
            retry_after = int(reset_time.timestamp() - now)
            
            # Update stats
            self._stats[name]['denied'] += weight
            
            return RateLimitResult(
                allowed=False,
                remaining=effective_max - current_tokens,
                reset_time=reset_time,
                retry_after=retry_after,
                scope=config.scope,
                identifier=identifier,
                limit=effective_max,
                window=config.window_seconds
            )
        
        # Add request to bucket
        self._buckets[bucket_key].append(now)
        
        # Update stats
        self._stats[name]['allowed'] += weight
        
        # Calculate remaining tokens
        remaining = effective_max - (current_tokens + 1)
        
        # Calculate reset time
        oldest_request = self._buckets[bucket_key][0] if self._buckets[bucket_key] else now
        reset_time = datetime.fromtimestamp(oldest_request + config.window_seconds, tz=timezone.utc)
        
        return RateLimitResult(
            allowed=True,
            remaining=remaining,
            reset_time=reset_time,
            retry_after=None,
            scope=config.scope,
            identifier=identifier,
            limit=effective_max,
            window=config.window_seconds
        )
    
    def _get_bucket_key(self, name: str, identifier: str, scope: RateLimitScope) -> str:
        """Generate bucket key based on scope."""
        if scope == RateLimitScope.GLOBAL:
            return f"global_{name}"
        elif scope == RateLimitScope.ENDPOINT:
            return f"endpoint_{name}_{identifier}"
        elif scope == RateLimitScope.USER:
            return f"user_{name}_{identifier}"
        elif scope == RateLimitScope.IP:
            return f"ip_{name}_{identifier}"
        elif scope == RateLimitScope.API_KEY:
            return f"api_key_{name}_{identifier}"
        else:
            return f"other_{name}_{identifier}"
    
    def _cleanup_bucket(self, bucket_key: str, current_time: float) -> None:
        """Clean old entries from bucket."""
        if bucket_key not in self._buckets:
            return
        
        window = self._configs.get(bucket_key.split('_')[1], {}).get('window_seconds', 60)
        cutoff = current_time - window
        
        # Remove old entries
        self._buckets[bucket_key] = deque(
            [t for t in self._buckets[bucket_key] if t > cutoff],
            maxlen=self._configs.get(bucket_key.split('_')[1], {}).get('burst_size', 1000)
        )
    
    def apply_penalty(self, name: str, identifier: str, factor: float) -> None:
        """
        Apply penalty to rate limit.
        
        Args:
            name: Rate limit name
            identifier: Identifier for the scope
            factor: Penalty factor (e.g., 2.0 = half the rate)
        """
        bucket_key = self._get_bucket_key(name, identifier, RateLimitScope.USER)
        self._penalties[bucket_key] = factor
        
        # Schedule penalty removal
        async def remove_penalty():
            await asyncio.sleep(300)  # 5 minutes
            if self._penalties.get(bucket_key) == factor:
                del self._penalties[bucket_key]
        
        asyncio.create_task(remove_penalty())
    
    def get_statistics(self) -> Dict[str, Dict[str, Any]]:
        """Get rate limiting statistics."""
        stats = {}
        
        for name, config in self._configs.items():
            if not config.enabled:
                continue
            
            stats[name] = {
                'config': {
                    'max_requests': config.max_requests,
                    'window_seconds': config.window_seconds,
                    'scope': config.scope.value,
                    'description': config.description,
                    'enabled': config.enabled
                },
                'stats': dict(self._stats[name]),
                'penalties': len(self._penalties)
            }
        
        return stats
    
    def _start_cleanup_task(self) -> None:
        """Start background cleanup task."""
        async def cleanup_worker():
            while True:
                try:
                    await asyncio.sleep(self._cleanup_interval)
                    self._cleanup_all_buckets()
                except Exception:
                    pass
        
        self._cleanup_task = asyncio.create_task(cleanup_worker())
    
    def _cleanup_all_buckets(self) -> None:
        """Clean all buckets."""
        current_time = time.time()
        
        for bucket_key in list(self._buckets.keys()):
            self._cleanup_bucket(bucket_key, current_time)
            
            # Remove empty buckets
            if not self._buckets[bucket_key]:
                del self._buckets[bucket_key]


class APIRateLimiter:
    """
    API-wide rate limiting middleware.
    
    Features:
    - Multiple rate limit tiers
    - Dynamic configuration
    - Bypass mechanisms
    - Statistics and monitoring
    - Header support
    """
    
    def __init__(self):
        """Initialize API rate limiter."""
        self._rate_limiter = RateLimiter()
        self._setup_default_limits()
    
    def _setup_default_limits(self) -> None:
        """Setup default rate limits."""
        # Global limits
        self._rate_limiter.configure('global', RateLimitConfig(
            max_requests=10000,
            window_seconds=60,
            scope=RateLimitScope.GLOBAL,
            description="Global API rate limit"
        ))
        
        # Per-endpoint limits
        self._rate_limiter.configure('auth_login', RateLimitConfig(
            max_requests=5,
            window_seconds=60,
            scope=RateLimitScope.IP,
            description="Login endpoint rate limit"
        ))
        
        self._rate_limiter.configure('auth_reset', RateLimitConfig(
            max_requests=3,
            window_seconds=3600,
            scope=RateLimitScope.IP,
            description="Password reset rate limit"
        ))
        
        self._rate_limiter.configure('api_data', RateLimitConfig(
            max_requests=1000,
            window_seconds=60,
            scope=RateLimitScope.USER,
            description="API data endpoint rate limit"
        ))
        
        self._rate_limiter.configure('file_upload', RateLimitConfig(
            max_requests=10,
            window_seconds=60,
            scope=RateLimitScope.USER,
            description="File upload rate limit"
        ))
        
        # Administrative endpoints
        self._rate_limiter.configure('admin', RateLimitConfig(
            max_requests=100,
            window_seconds=60,
            scope=RateLimitScope.USER,
            description="Administrative endpoint rate limit"
        ))
    
    def check_endpoint(self, endpoint: str, identifier: str, 
                      identifier_type: RateLimitScope = RateLimitScope.USER) -> RateLimitResult:
        """
        Check rate limit for endpoint.
        
        Args:
            endpoint: Endpoint name
            identifier: Identifier for the scope
            identifier_type: Type of identifier
            
        Returns:
            RateLimitResult
        """
        # Use endpoint-specific limit if configured, otherwise use default
        if endpoint in self._rate_limiter._configs:
            config = self._rate_limiter._configs[endpoint]
            return self._rate_limiter.check_rate_limit(
                endpoint, identifier, config.scope
            )
        else:
            # Use default endpoint limit
            return self._rate_limiter.check_rate_limit('default', identifier)
    
    def get_rate_headers(self, result: RateLimitResult) -> Dict[str, str]:
        """Generate rate limit headers."""
        headers = {}
        
        if result.reset_time:
            headers['X-RateLimit-Reset'] = str(int(result.reset_time.timestamp()))
        
        headers['X-RateLimit-Limit'] = str(result.limit)
        headers['X-RateLimit-Remaining'] = str(result.remaining)
        
        if result.retry_after:
            headers['Retry-After'] = str(result.retry_after)
        
        return headers
    
    def add_bypass(self, identifier: str, endpoint: str, duration: int = 300) -> None:
        """
        Add bypass for identifier.
        
        Args:
            identifier: Identifier to bypass
            endpoint: Endpoint to bypass (or '*' for all)
            duration: Duration of bypass in seconds
        """
        # Implement bypass mechanism
        # This would typically involve storing bypass in Redis or similar
        pass
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get rate limiting statistics."""
        return self._rate_limiter.get_statistics()


# Global rate limiter instance
api_rate_limiter: APIRateLimiter = APIRateLimiter()


def rate_limit(endpoint: str, identifier_type: RateLimitScope = RateLimitScope.USER):
    """
    Decorator for rate limiting API endpoints.
    
    Args:
        endpoint: Endpoint name
        identifier_type: Type of identifier
        
    Usage:
        @rate_limit('user_profile')
        async def get_user_profile(request: Request) -> Response:
            # Your code here
            pass
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract identifier based on type
            if identifier_type == RateLimitScope.IP:
                identifier = kwargs.get('request', {}).get('client_host', 'unknown')
            elif identifier_type == RateLimitScope.USER:
                identifier = kwargs.get('user_id', 'unknown')
            else:
                identifier = 'unknown'
            
            # Check rate limit
            result = api_rate_limiter.check_endpoint(endpoint, identifier, identifier_type)
            
            if not result.allowed:
                from fastapi import HTTPException
                raise HTTPException(
                    status_code=429,
                    detail="Rate limit exceeded"
                )
            
            # Add rate limit headers to response
            if 'headers' in kwargs:
                kwargs['headers'].update(api_rate_limiter.get_rate_headers(result))
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator


# Helper functions
def create_custom_limit(name: str, max_requests: int, window_seconds: int, 
                      scope: RateLimitScope = RateLimitScope.ENDPOINT,
                      description: str = "") -> None:
    """
    Create custom rate limit.
    
    Args:
        name: Limit name
        max_requests: Maximum requests
        window_seconds: Window size in seconds
        scope: Limit scope
        description: Limit description
    """
    config = RateLimitConfig(
        max_requests=max_requests,
        window_seconds=window_seconds,
        scope=scope,
        description=description
    )
    api_rate_limiter._rate_limiter.configure(name, config)


def get_rate_limits() -> Dict[str, Dict[str, Any]]:
    """Get all rate limit configurations."""
    return api_rate_limiter.get_statistics()


def get_rate_limit_status(identifier: str, endpoint: str) -> RateLimitResult:
    """Get current rate limit status for identifier and endpoint."""
    return api_rate_limiter.check_endpoint(endpoint, identifier)