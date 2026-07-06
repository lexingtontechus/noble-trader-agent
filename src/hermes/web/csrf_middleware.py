"""
CSRF Middleware for Hermes Trading Platform

Provides FastAPI middleware for CSRF protection.
Validates CSRF tokens on state-changing requests.

Usage:
    from hermes.web.csrf_middleware import CSRFMiddleware
    
    app.add_middleware(CSRFMiddleware)
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from hermes.web.csrf import get_csrf_protection, validate_csrf_token


class CSRFMiddleware(BaseHTTPMiddleware):
    """
    Middleware that validates CSRF tokens on state-changing requests.
    
    Features:
    - Validates tokens from X-CSRF-Token header
    - Validates tokens from csrf_token form field
    - Validates tokens from csrf_token query parameter
    - Exempts safe HTTP methods (GET, HEAD, OPTIONS)
    - Configurable header name and form field
    """
    
    # HTTP methods that don't need CSRF protection
    SAFE_METHODS = {'GET', 'HEAD', 'OPTIONS', 'TRACE'}
    
    # Header name for CSRF token
    HEADER_NAME = 'X-CSRF-Token'
    
    # Form field name for CSRF token
    FORM_FIELD_NAME = 'csrf_token'
    
    # Query parameter name for CSRF token
    QUERY_PARAM_NAME = 'csrf_token'
    
    def __init__(
        self,
        app: ASGIApp,
        header_name: str = HEADER_NAME,
        form_field_name: str = FORM_FIELD_NAME,
        query_param_name: str = QUERY_PARAM_NAME,
        exempt_paths: list[str] | None = None,
    ) -> None:
        """
        Initialize CSRF middleware.
        
        Args:
            app: The ASGI application
            header_name: Header name for CSRF token
            form_field_name: Form field name for CSRF token
            query_param_name: Query parameter name for CSRF token
            exempt_paths: List of paths that don't need CSRF protection
        """
        super().__init__(app)
        self.header_name = header_name
        self.form_field_name = form_field_name
        self.query_param_name = query_param_name
        self.exempt_paths = exempt_paths or []
    
    async def dispatch(self, request: Request, call_next: Any) -> Any:
        """Process request with CSRF validation."""
        method = request.method
        path = request.url.path
        
        # Skip CSRF for safe methods
        if method in self.SAFE_METHODS:
            return await call_next(request)
        
        # Skip CSRF for exempt paths (e.g., public API endpoints)
        if any(path.startswith(exempt) for exempt in self.exempt_paths):
            return await call_next(request)
        
        # Get session ID from request
        session_id = self._get_session_id(request)
        if not session_id:
            # No session - deny request (can't validate CSRF without session)
            return JSONResponse(
                {
                    'error': 'CSRF validation failed',
                    'message': 'No session found - cannot validate CSRF token',
                },
                status_code=403,
            )
        
        # Get CSRF token from request
        token = await self._get_token(request)
        if not token:
            return JSONResponse(
                {
                    'error': 'CSRF validation failed',
                    'message': 'No CSRF token provided',
                },
                status_code=403,
            )
        
        # Validate token
        csrf = get_csrf_protection()
        if not csrf.validate_token(token, session_id):
            return JSONResponse(
                {
                    'error': 'CSRF validation failed',
                    'message': 'Invalid or expired CSRF token',
                },
                status_code=403,
            )
        
        # Token is valid - proceed with request
        response = await call_next(request)
        
        # Add new CSRF token to response for subsequent requests
        # (allows token rotation after each successful POST)
        new_token = csrf.generate_token(session_id)
        response.headers['X-CSRF-Token'] = new_token
        
        return response
    
    def _get_session_id(self, request: Request) -> str | None:
        """Extract session ID from request."""
        # Check session cookie first
        if hasattr(request, 'session'):
            session = request.session
            if isinstance(session, dict) and 'user' in session:
                user = session.get('user')
                if isinstance(user, dict) and 'username' in user:
                    # Use username as session identifier
                    return f"user:{user['username']}"
        
        # Check for session ID in cookie
        session_id = request.cookies.get('session')
        if session_id:
            return f"session:{session_id}"
        
        # Check for bearer token (agent auth)
        auth_header = request.headers.get('Authorization')
        if auth_header and auth_header.startswith('Bearer '):
            token = auth_header[7:]
            # Hash the token for privacy
            import hashlib
            token_hash = hashlib.sha256(token.encode()).hexdigest()[:16]
            return f"agent:{token_hash}"
        
        return None
    
    async def _get_token(self, request: Request) -> str | None:
        """Extract CSRF token from request."""
        # 1. Check header
        token = request.headers.get(self.header_name)
        if token:
            return token
        
        # 2. Check query parameter
        token = request.query_params.get(self.query_param_name)
        if token:
            return token
        
        # 3. Check form body (for form submissions)
        if request.headers.get('Content-Type', '').startswith('application/x-www-form-urlencoded'):
            try:
                body = await request.body()
                if body:
                    from urllib.parse import parse_qs
                    form_data = parse_qs(body.decode('utf-8'))
                    tokens = form_data.get(self.form_field_name, [])
                    if tokens:
                        return tokens[0]
            except Exception:
                pass
        
        # 4. Check JSON body
        if request.headers.get('Content-Type', '').startswith('application/json'):
            try:
                body = await request.json()
                if isinstance(body, dict):
                    token = body.get(self.form_field_name)
                    if token:
                        return token
            except Exception:
                pass
        
        return None


# Decorator for CSRF exemption
def csrf_exempt(func: Any) -> Any:
    """
    Decorator to exempt a route from CSRF protection.
    Use only for public webhooks or other special cases.
    """
    # Add marker attribute
    func.__csrf_exempt__ = True
    return func