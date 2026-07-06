"""
Rate Limit Middleware for Hermes Trading Platform

Provides FastAPI middleware for rate limiting requests.
Integrates with the rate_limiter module and security_monitor.

Usage:
    from hermes.web.rate_limit_middleware import RateLimitMiddleware
    
    app.add_middleware(RateLimitMiddleware, config=config)
"""

from __future__ import annotations

import time
from collections import defaultdict
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from hermes.core.config import VenueConfig
from hermes.ops.rate_limiter import RateLimiter, RateLimitInfo


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces rate limits on API endpoints.
    
    Features:
    - Per-endpoint rate limiting
    - Per-venue rate limits (from config)
    - Rate limit headers in responses
    - Integration with SecurityMonitor for logging
    """
    
    # Default rate limits for API endpoints
    DEFAULT_RATE_LIMITS = {
        "api": {"requests": 1000, "window": 60},  # 1000 req/min for API
        "auth": {"requests": 10, "window": 60},   # 10 req/min for auth
        "symbols": {"requests": 60, "window": 60},  # 60 req/min for symbols
        "general": {"requests": 200, "window": 60},  # 200 req/min general
    }
    
    def __init__(self, app: ASGIApp, config: Any = None) -> None:
        """
        Initialize the rate limit middleware.
        
        Args:
            app: The ASGI application
            config: HermesConfig instance with venue rate limits
        """
        super().__init__(app)
        self.config = config
        self._lock = time.RLock() if hasattr(time, 'RLock') else None
        
        # Initialize rate limiters per endpoint pattern
        self._limiters: dict[str, RateLimiter] = {}
        self._venue_limiters: dict[str, RateLimiter] = {}
        
        if config:
            self._init_venue_limiters(config)
    
    def _init_venue_limiters(self, config: Any) -> None:
        """Initialize rate limiters from venue config."""
        if not hasattr(config, 'venues'):
            return
        
        for venue_name, venue_config in config.venues.items():
            if isinstance(venue_config, VenueConfig) and venue_config.enabled:
                rate_limit = getattr(venue_config, 'rate_limit_per_min', 200)
                self._venue_limiters[venue_name] = RateLimiter(
                    max_requests=rate_limit,
                    window_seconds=60
                )
    
    def _get_client_id(self, request: Request) -> str:
        """Extract client identifier from request."""
        # Try to get real IP from various headers
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            return forwarded.split(',')[0].strip()
        
        real_ip = request.headers.get('x-real-ip')
        if real_ip:
            return real_ip
        
        # Fall back to client host
        if request.client and request.client.host:
            return request.client.host
        
        return "unknown"
    
    def _get_endpoint_category(self, path: str) -> str:
        """Determine rate limit category for an endpoint."""
        if path.startswith('/api/auth'):
            return 'auth'
        if path.startswith('/api/symbols'):
            return 'symbols'
        if path.startswith('/api/'):
            return 'api'
        return 'general'
    
    def _get_venue_from_path(self, path: str) -> str | None:
        """Extract venue name from request path if applicable."""
        # Paths like /api/venue/alpaca/... would indicate venue
        if '/venue/' in path:
            parts = path.split('/')
            for i, part in enumerate(parts):
                if part == 'venue' and i + 1 < len(parts):
                    return parts[i + 1]
        return None
    
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Process the request with rate limiting."""
        path = request.url.path
        method = request.method
        
        # Skip rate limiting for safe methods
        if method in ('GET', 'HEAD', 'OPTIONS'):
            return await call_next(request)
        
        client_id = self._get_client_id(request)
        
        # Check general rate limit
        category = self._get_endpoint_category(path)
        limit_config = self.DEFAULT_RATE_LIMITS.get(category, self.DEFAULT_RATE_LIMITS['general'])
        
        limiter_key = f"{category}_{method}"
        if limiter_key not in self._limiters:
            self._limiters[limiter_key] = RateLimiter(
                max_requests=limit_config['requests'],
                window_seconds=limit_config['window']
            )
        
        limiter = self._limiters[limiter_key]
        
        # Check if rate limit exceeded
        if not limiter.is_allowed(client_id):
            info = limiter.get_info(client_id)
            
            # Log to security monitor if available
            try:
                from hermes.ops.security_monitor import get_security_monitor
                monitor = get_security_monitor()
                monitor.log_security_event(
                    "rate_limit_hit",
                    message=f"Rate limit exceeded for {client_id}",
                    severity="warning",
                    client_id=client_id,
                    endpoint=str(path),
                    method=method,
                    limit=info.limit,
                    remaining=info.remaining,
                )
            except Exception:
                pass  # Don't fail request if monitoring fails
            
            # Build rate limit response
            response = JSONResponse(
                {
                    "error": "Rate limit exceeded",
                    "retry_after": int(info.reset_time - time.time()),
                    "limit": info.limit,
                    "window": info.window_seconds,
                },
                status_code=429,
            )
            
            # Add rate limit headers
            response.headers['X-RateLimit-Limit'] = str(info.limit)
            response.headers['X-RateLimit-Remaining'] = str(info.remaining)
            response.headers['X-RateLimit-Reset'] = str(int(info.reset_time))
            response.headers['Retry-After'] = str(int(info.reset_time - time.time()))
            
            return response
        
        # Process the request
        response = await call_next(request)
        
        # Add rate limit headers to successful responses
        if limiter_key in self._limiters:
            info = limiter.get_info(client_id)
            response.headers['X-RateLimit-Limit'] = str(info.limit)
            response.headers['X-RateLimit-Remaining'] = str(info.remaining)
            response.headers['X-RateLimit-Reset'] = str(int(info.reset_time))
        
        return response


class PerVenueRateLimitMiddleware(BaseHTTPMiddleware):
    """
    Middleware that enforces rate limits based on venue configuration.
    Used for API clients that interact with specific trading venues.
    """
    
    def __init__(self, app: ASGIApp, config: Any = None) -> None:
        """Initialize with venue-specific rate limits."""
        super().__init__(app)
        self.config = config
        self._venue_limiters: dict[str, RateLimiter] = {}
        self._init_limiters()
    
    def _init_limiters(self) -> None:
        """Initialize rate limiters from config."""
        if not self.config or not hasattr(self.config, 'venues'):
            return
        
        for venue_name, venue_config in self.config.venues.items():
            if isinstance(venue_config, VenueConfig) and venue_config.enabled:
                rate_limit = getattr(venue_config, 'rate_limit_per_min', 200)
                self._venue_limiters[venue_name] = RateLimiter(
                    max_requests=rate_limit,
                    window_seconds=60
                )
    
    def _get_venue_from_request(self, request: Request) -> str | None:
        """Extract venue from request."""
        # Check path
        path = request.url.path
        if '/venue/' in path:
            parts = path.split('/')
            for i, part in enumerate(parts):
                if part == 'venue' and i + 1 < len(parts):
                    return parts[i + 1]
        
        # Check header
        venue_header = request.headers.get('X-Venue')
        if venue_header:
            return venue_header.lower()
        
        return None
    
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Process request with venue-specific rate limiting."""
        method = request.method
        
        # Skip safe methods
        if method in ('GET', 'HEAD', 'OPTIONS'):
            return await call_next(request)
        
        client_id = self._get_client_id(request)
        venue = self._get_venue_from_request(request)
        
        if venue and venue in self._venue_limiters:
            limiter = self._venue_limiters[venue]
            
            if not limiter.is_allowed(client_id):
                info = limiter.get_info(client_id)
                
                response = JSONResponse(
                    {
                        "error": f"Rate limit exceeded for venue {venue}",
                        "retry_after": int(info.reset_time - time.time()),
                        "venue": venue,
                    },
                    status_code=429,
                )
                
                response.headers['X-RateLimit-Limit'] = str(info.limit)
                response.headers['X-RateLimit-Remaining'] = str(info.remaining)
                response.headers['X-RateLimit-Reset'] = str(int(info.reset_time))
                
                return response
        
        return await call_next(request)
    
    def _get_client_id(self, request: Request) -> str:
        """Extract client identifier."""
        forwarded = request.headers.get('x-forwarded-for')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return request.client.host if request.client else "unknown"