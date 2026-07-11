"""
Security Headers Middleware for Hermes Trading Platform

Provides comprehensive security headers and HTTPS enforcement:
- Content Security Policy (CSP)
- Strict Transport Security (HSTS)
- X-Frame-Options
- X-Content-Type-Options
- Permissions-Policy
- Referrer-Policy
- HTTPS enforcement
- Content Security Policy Reporting
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class SecurityLevel(Enum):
    """Security levels for header configuration."""
    MINIMAL = "minimal"
    MODERATE = "moderate"
    STRICT = "strict"
    PARANOID = "paranoid"


@dataclass
class SecurityHeaderConfig:
    """Configuration for security headers."""
    level: SecurityLevel
    enable_https_enforcement: bool = True
    enable_csp: bool = True
    enable_hsts: bool = True
    enable_frame_options: bool = True
    enable_content_type_options: bool = True
    enable_permissions_policy: bool = True
    enable_referrer_policy: bool = True
    enable_reporting: bool = False


class SecurityHeadersMiddleware:
    """
    Security headers middleware for FastAPI applications.
    
    Features:
    - Configurable security levels
    - HTTPS enforcement
    - Content Security Policy (CSP)
    - Strict Transport Security (HSTS)
    - Anti-clickjacking protection
    - MIME type sniffing protection
    - Feature policy
    - Referrer control
    - Security reporting
    """
    
    def __init__(self, config: Optional[SecurityHeaderConfig] = None):
        """
        Initialize security headers middleware.
        
        Args:
            config: Security header configuration
        """
        self.config = config or SecurityHeaderConfig(level=SecurityLevel.STRICT)
        
        # Initialize headers
        self.headers = {}
        self._set_headers()
    
    def _set_headers(self) -> None:
        """Set security headers based on configuration."""
        # Basic security headers
        self._set_content_security_policy()
        self._set_strict_transport_security()
        self._set_frame_options()
        self._set_content_type_options()
        self._set_permissions_policy()
        self._set_referrer_policy()
        
        # Additional headers
        self._set_xss_protection()
        self._set_server_header()
        self._set_cache_control()
        self._set_feature_policy()
    
    def _set_content_security_policy(self) -> None:
        """Set Content Security Policy header."""
        if not self.config.enable_csp:
            self.headers['Content-Security-Policy'] = "default-src 'self'"
            return
        
        # Base policy - more permissive for TailwindCSS v4 and DaisyUI v5
        policy_parts = [
            "default-src 'self'",
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' cdn.jsdelivr.net",
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net *.tailwindcss.com",
            "img-src 'self' data: https:",
            "connect-src 'self' https:",
            "font-src 'self' cdn.jsdelivr.net",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
            "frame-src 'none'",
            "block-all-mixed-content"
        ]
        
        # Add reporting if enabled
        if self.config.enable_reporting:
            policy_parts.append("report-uri /csp-violation-report-endpoint/")
        
        self.headers['Content-Security-Policy'] = "; ".join(policy_parts)
    
    def _set_strict_transport_security(self) -> None:
        """Set Strict Transport Security header."""
        if not self.config.enable_hsts:
            return
        
        hsts_parts = [
            "max-age=31536000",  # 1 year
            "includeSubDomains",
            "preload"
        ]
        
        if self.config.level in [SecurityLevel.STRICT, SecurityLevel.PARANOID]:
            hsts_parts.append("preload")
        
        self.headers['Strict-Transport-Security'] = "; ".join(hsts_parts)
    
    def _set_frame_options(self) -> None:
        """Set X-Frame-Options header."""
        if not self.config.enable_frame_options:
            return
        
        if self.config.level in [SecurityLevel.STRICT, SecurityLevel.PARANOID]:
            self.headers['X-Frame-Options'] = "DENY"
        else:
            self.headers['X-Frame-Options'] = "SAMEORIGIN"
    
    def _set_content_type_options(self) -> None:
        """Set X-Content-Type-Options header."""
        if not self.config.enable_content_type_options:
            return
        
        self.headers['X-Content-Type-Options'] = 'nosniff'
    
    def _set_permissions_policy(self) -> None:
        """Set Permissions-Policy header."""
        if not self.config.enable_permissions_policy:
            return
        
        # Base policy
        policy_parts = []
        
        if self.config.level in [SecurityLevel.STRICT, SecurityLevel.PARANOID]:
            # Strict policy
            policy_parts = [
                "geolocation=()",
                "microphone=()",
                "camera=()",
                "payment=()",
                "usb=()",
                "accelerometer=()",
                "gyroscope=()",
                "magnetometer=()",
                "ambient-light-sensor=()",
                "accessibility-events=()",
                "autoplay=()",
                "document-domain=()",
                "encrypted-media=()",
                "fullscreen=(self)",
                "orientation=()",
                "oversized-images=()",
                "picture-in-picture=()",
                "publickey-credentials-get=()",
                "screen-wake-lock=()",
                "sync-xhr=(self)",
                "usb=()",
                "web-share=()",
                "xr-spatial-tracking=()"
            ]
        elif self.config.level == SecurityLevel.MODERATE:
            # Moderate policy
            policy_parts = [
                "geolocation=()",
                "microphone=()",
                "camera=()",
                "payment=()",
                "usb=()",
                "accelerometer=()",
                "gyroscope=()",
                "ambient-light-sensor=()",
                "accessibility-events=()",
                "autoplay=(self)",
                "fullscreen=(self)",
                "document-domain=()",
                "encrypted-media=(self)",
                "picture-in-picture=()",
                "screen-wake-lock=()"
            ]
        else:
            # Minimal policy
            policy_parts = [
                "geolocation=()",
                "microphone=()",
                "camera=()",
                "payment=()",
                "usb=()"
            ]
        
        self.headers['Permissions-Policy'] = ", ".join(policy_parts)
    
    def _set_referrer_policy(self) -> None:
        """Set Referrer-Policy header."""
        if not self.config.enable_referrer_policy:
            return
        
        if self.config.level in [SecurityLevel.STRICT, SecurityLevel.PARANOID]:
            self.headers['Referrer-Policy'] = "no-referrer"
        else:
            self.headers['Referrer-Policy'] = "strict-origin-when-cross-origin"
    
    def _set_xss_protection(self) -> None:
        """Set X-XSS-Protection header."""
        if self.config.level in [SecurityLevel.STRICT, SecurityLevel.PARANOID]:
            self.headers['X-XSS-Protection'] = '1; mode=block'
        else:
            self.headers['X-XSS-Protection'] = '1; mode=block'
    
    def _set_server_header(self) -> None:
        """Set Server header (minimal information)."""
        self.headers['Server'] = 'Hermes'
    
    def _set_cache_control(self) -> None:
        """Set Cache-Control header for sensitive data."""
        self.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        self.headers['Pragma'] = 'no-cache'
        self.headers['Expires'] = '0'
    
    def _set_feature_policy(self) -> None:
        """Set Feature-Policy header (deprecated but still useful)."""
        # Feature-Policy is deprecated but widely supported
        if self.config.level in [SecurityLevel.STRICT, SecurityLevel.PARANOID]:
            self.headers['Feature-Policy'] = "geolocation 'none'; microphone 'none'; camera 'none'"
    
    def get_headers(self) -> Dict[str, str]:
        """Get security headers dictionary."""
        return self.headers.copy()
    
    def add_custom_header(self, name: str, value: str) -> None:
        """Add a custom security header."""
        # Sanitize header name
        if not re.match(r'^[a-zA-Z0-9-]+$', name):
            raise ValueError("Invalid header name")
        
        self.headers[name] = value
    
    def remove_header(self, name: str) -> None:
        """Remove a security header."""
        if name in self.headers:
            del self.headers[name]


class HTTPSRedirectMiddleware:
    """
    HTTPS enforcement middleware.
    
    Features:
    - Redirect HTTP to HTTPS
    - Custom redirect status codes
    - Skip for certain paths
    - Support for proxy headers
    """
    
    def __init__(self, exempt_paths: List[str] = None, 
                 redirect_code: int = 301,
                 trust_proxy_headers: bool = False):
        """
        Initialize HTTPS redirect middleware.
        
        Args:
            exempt_paths: List of paths exempt from HTTPS enforcement
            redirect_code: HTTP status code for redirect (301 or 308)
            trust_proxy_headers: Whether to trust X-Forwarded-Proto headers
        """
        self.exempt_paths = exempt_paths or ["/health", "/metrics"]
        self.redirect_code = redirect_code
        self.trust_proxy_headers = trust_proxy_headers
        self._app = None
    
    def should_redirect(self, request_path: str, headers: Dict[str, str]) -> bool:
        """
        Check if request should be redirected to HTTPS.
        
        Args:
            request_path: Request path
            headers: Request headers
            
        Returns:
            True if should redirect
        """
        # Check exempt paths
        for exempt_path in self.exempt_paths:
            if request_path.startswith(exempt_path):
                return False
        
        # Check if already HTTPS
        if self._is_https_request(headers):
            return False
        
        return True
    
    def _is_https_request(self, headers: Dict[str, str]) -> bool:
        """Check if request is already HTTPS."""
        # Check X-Forwarded-Proto header if trusting proxy headers
        if self.trust_proxy_headers:
            forwarded_proto = headers.get('X-Forwarded-Proto', '').lower()
            if forwarded_proto == 'https':
                return True
        
        # Check X-Forwarded-Ssl header
        if headers.get('X-Forwarded-Ssl', '').lower() == 'on':
            return True

        return False

    async def __call__(self, scope, receive, send) -> None:
        """ASGI entrypoint. Redirects plaintext HTTP requests to HTTPS.

        NOTE: previously this class only had should_redirect()/_is_https_request()
        helpers and NO __call__, so adding it as middleware did nothing (non-ASGI
        stub). This makes it a real ASGI middleware.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        # Reconstruct a case-insensitive header view
        header_view = {
            (k.decode().lower() if isinstance(k, bytes) else k.lower()): (
                v.decode() if isinstance(v, bytes) else v)
            for k, v in headers.items()
        }
        path = scope.get("path", "/")
        if not self.should_redirect(path, header_view):
            await self._app(scope, receive, send)
            return
        # Build redirect to https
        host = header_view.get("host", "localhost")
        location = f"https://{host}{path}"
        await send({
            "type": "http.response.start",
            "status": self.redirect_code,
            "headers": [(b"location", location.encode()), (b"content-length", b"0")],
        })
        await send({"type": "http.response.body", "body": b""})

    def set_app(self, app) -> None:
        """Bind the downstream ASGI app (call before adding to the stack)."""
        self._app = app
        
        # Check other common headers
        if headers.get('X-Url-Scheme', '').lower() == 'https':
            return True
        
        return False


# Global security headers instance
_security_headers: SecurityHeadersMiddleware | None = None


def get_security_headers(config: Optional[SecurityHeaderConfig] = None) -> SecurityHeadersMiddleware:
    """Get or create the global security headers middleware."""
    global _security_headers
    if _security_headers is None:
        _security_headers = SecurityHeadersMiddleware(config)
    return _security_headers


def get_default_security_headers() -> Dict[str, str]:
    """Get default security headers."""
    middleware = get_security_headers()
    return middleware.get_headers()


def create_csp_report_uri() -> str:
    """Create CSP violation reporting endpoint URL."""
    return "/api/security/csp-violation-report"


def validate_csp_report(report_data: Dict[str, str]) -> bool:
    """
    Validate CSP violation report.
    
    Args:
        report_data: CSP violation report data
        
    Returns:
        True if report is valid
    """
    required_fields = [
        'document-uri',
        'referrer',
        'blocked-uri',
        'violated-directive',
        'original-policy'
    ]
    
    # Check required fields
    for field in required_fields:
        if field not in report_data:
            return False
    
    # Basic validation of report data
    try:
        # Check for suspicious reports
        if len(report_data.get('blocked-uri', '')) > 1000:
            return False
        
        # Check for pattern of malicious reports
        if report_data.get('referrer', '').startswith('javascript:'):
            return False
        
        return True
        
    except Exception:
        return False


# Helper functions for header configuration
def get_production_headers() -> Dict[str, str]:
    """Get headers optimized for production environments."""
    config = SecurityHeaderConfig(
        level=SecurityLevel.STRICT,
        enable_https_enforcement=True,
        enable_csp=True,
        enable_hsts=True,
        enable_frame_options=True,
        enable_content_type_options=True,
        enable_permissions_policy=True,
        enable_referrer_policy=True,
        enable_reporting=False
    )
    
    middleware = SecurityHeadersMiddleware(config)
    return middleware.get_headers()


def get_development_headers() -> Dict[str, str]:
    """Get headers optimized for development environments."""
    config = SecurityHeaderConfig(
        level=SecurityLevel.MODERATE,
        enable_https_enforcement=False,
        enable_csp=True,
        enable_hsts=False,
        enable_frame_options=True,
        enable_content_type_options=True,
        enable_permissions_policy=True,
        enable_referrer_policy=True,
        enable_reporting=False
    )
    
    middleware = SecurityHeadersMiddleware(config)
    return middleware.get_headers()


def get_testing_headers() -> Dict[str, str]:
    """Get headers optimized for testing environments."""
    config = SecurityHeaderConfig(
        level=SecurityLevel.MINIMAL,
        enable_https_enforcement=False,
        enable_csp=False,
        enable_hsts=False,
        enable_frame_options=False,
        enable_content_type_options=False,
        enable_permissions_policy=False,
        enable_referrer_policy=False,
        enable_reporting=False
    )
    
    middleware = SecurityHeadersMiddleware(config)
    return middleware.get_headers()