"""
Security Monitoring Module for Hermes Trading Platform

Provides comprehensive security monitoring including:
- Authentication event logging
- Authorization failure tracking
- Rate limiting monitoring (when implemented)
- Security header compliance checking
- Suspicious activity detection
- Security metrics and dashboards

Usage:
    from hermes.ops.security_monitor import SecurityMonitor, SecurityEvent
    
    monitor = SecurityMonitor()
    
    # Log security events
    monitor.log_auth_event(SecurityEvent.AUTH_SUCCESS, username="admin", ip="192.168.1.1")
    monitor.log_auth_event(SecurityEvent.AUTH_FAILURE, username="admin", ip="192.168.1.1", reason="invalid_password")
    
    # Check for suspicious activity
    suspicious_events = monitor.detect_suspicious_activity()
    
    # Get security metrics
    metrics = monitor.get_security_metrics()
"""

from __future__ import annotations

import json
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import structlog

log = structlog.get_logger(__name__)


# =============================================================================
# Security Event Types
# =============================================================================

class SecurityEvent(str, Enum):
    """Types of security events that can be logged."""
    
    # Authentication events
    AUTH_SUCCESS = "auth_success"
    AUTH_FAILURE = "auth_failure"
    AUTH_LOGOUT = "auth_logout"
    AUTH_SESSION_EXPIRED = "auth_session_expired"
    AUTH_SESSION_CREATED = "auth_session_created"
    
    # Authorization events
    AUTHZ_SUCCESS = "authz_success"
    AUTHZ_FAILURE = "authz_failure"
    
    # Rate limiting events (when implemented)
    RATE_LIMIT_HIT = "rate_limit_hit"
    RATE_LIMIT_WARNING = "rate_limit_warning"
    
    # Input validation events
    INPUT_VALIDATION_FAILURE = "input_validation_failure"
    INPUT_SANITIZATION = "input_sanitization"
    
    # Security configuration events
    SECURITY_CONFIG_CHANGE = "security_config_change"
    SECURITY_HEADER_MISSING = "security_header_missing"
    
    # Suspicious activity
    SUSPICIOUS_LOGIN_ATTEMPT = "suspicious_login_attempt"
    SUSPICIOUS_REQUEST_PATTERN = "suspicious_request_pattern"
    BRUTE_FORCE_DETECTED = "brute_force_detected"
    
    # System events
    SECURITY_MODULE_INITIALIZED = "security_module_initialized"
    SECURITY_AUDIT_STARTED = "security_audit_started"
    SECURITY_AUDIT_COMPLETED = "security_audit_completed"


class RateLimiter:
    """
    Thread-safe rate limiter using token bucket algorithm.
    
    Features:
    - Configurable max requests per window
    - Configurable window size
    - Thread-safe using threading.RLock
    - Returns current request count
    - Returns whether request is within limit
    """
    
    def __init__(self, max_requests: int, window: timedelta) -> None:
        """
        Initialize rate limiter.
        
        Args:
            max_requests: Maximum number of requests allowed in window
            window: Time window for the rate limit
        """
        self.max_requests = max_requests
        self.window = window
        self._lock = threading.RLock()
        self._available = max_requests
        self._last_update = 0
        self._requests: list[float] = []
    
    def _cleanup(self) -> None:
        """Remove old requests from tracking."""
        now = datetime.now()
        while self._last_update < now - self.window:
            self._requests.pop(0)
            self._last_update = now
    
    def _acquire(self) -> bool:
        """
        Acquire a request token.
        
        Returns:
            True if request allowed, False if rate limited
        """
        with self._lock:
            now = datetime.now()
            self._cleanup()
            
            # Check if within time window
            if now - self._last_update > self.window:
                self._last_update = now
                self._available = self.max_requests
            
            if self._available >= self.max_requests:
                return False
            
            # Record this request
            self._requests.append(now)
            self._available -= 1
            return True
    
    def _release(self) -> None:
        """Release a request token."""
        with self._lock:
            now = datetime.now()
            self._cleanup()
            self._last_update = now
    
    @property
    def current_count(self) -> int:
        """Get current number of requests allowed."""
        with self._lock:
            self._cleanup()
            return max(0, int(self._available))


# =============================================================================
# Security Event Data Classes
# =============================================================================

@dataclass
class AuthEvent:
    """Authentication event data."""
    event_type: SecurityEvent
    username: str
    ip_address: str | None = None
    user_agent: str | None = None
    success: bool = True
    reason: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthzEvent:
    """Authorization event data."""
    event_type: SecurityEvent
    username: str | None
    resource: str
    action: str
    allowed: bool
    reason: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SecurityMetric:
    """Security metric data."""
    name: str
    value: float | int
    unit: str = ""
    description: str = ""
    threshold: float | None = None
    exceeded: bool = False


# =============================================================================
# Security Monitor Class
# =============================================================================

class SecurityMonitor:
    """
    Comprehensive security monitoring for Hermes Trading Platform.
    
    Tracks and analyzes security-related events to detect potential threats
    and provide visibility into the security posture of the system.
    """
    
    # Time window for rate-based detection (e.g., brute force)
    BRUTE_FORCE_WINDOW = timedelta(minutes=5)
    BRUTE_FORCE_THRESHOLD = 5  # 5 failed attempts in 5 minutes
    
    # Time window for suspicious activity detection
    SUSPICIOUS_WINDOW = timedelta(hours=1)
    
    # Rate limiting configuration
    RATE_LIMIT_WINDOW = timedelta(seconds=60)  # 1 minute window
    RATE_LIMIT_MAX_REQUESTS = 100  # Maximum requests per window
    RATE_LIMIT_WARNING_THRESHOLD = 80  # Warning at 80% capacity
    RATE_LIMIT_WARNING_THRESHOLD_SECONDS = RATE_LIMIT_MAX_REQUESTS * RATE_LIMIT_WINDOW.total_seconds() / 100
    
    # CSRF token configuration
    CSRF_TOKEN_TTL = timedelta(hours=1)  # 1 hour TTL
    CSRF_MAX_TOKENS_PER_SESSION = 10
    CSRF_TOKEN_LENGTH = 64  # Token length in bytes
    
    def __init__(self) -> None:
        """Initialize the security monitor."""
        self._lock = threading.RLock()
        
        # Event storage
        self._auth_events: deque[AuthEvent] = deque(maxlen=10000)
        self._authz_events: deque[AuthzEvent] = deque(maxlen=10000)
        self._security_events: deque[tuple[datetime, SecurityEvent, dict]] = deque(maxlen=10000)
        
        # Tracking for suspicious activity detection
        self._failed_logins: dict[str, list[datetime]] = defaultdict(list)
        self._ip_failed_logins: dict[str, list[datetime]] = defaultdict(list)
        
        # Metrics
        self._metrics = {
            "total_auth_attempts": 0,
            "successful_auth_attempts": 0,
            "failed_auth_attempts": 0,
            "total_authz_checks": 0,
            "failed_authz_checks": 0,
            "suspicious_events": 0,
            "security_violations": 0,
            "csrf_token_requests": 0,
            "csrf_token_warnings": 0,
        }
        
        # Alert thresholds
        self._alert_thresholds = {
            "failed_login_rate": 10,  # Alert if >10 failed logins per minute
            "brute_force_attempts": 3,  # Alert if >3 brute force attempts
            "authz_failures": 5,  # Alert if >5 authorization failures per minute
        }
        
        # Rate limiting tracking
        self._rate_limiter = RateLimiter(
            max_requests=self.RATE_LIMIT_MAX_REQUESTS,
            window=self.RATE_LIMIT_WINDOW
        )
        
        # CSRF token tracking
        self._csrf_tokens: dict[str, list[str]] = {}  # session_id -> list of tokens
        
        # Initialize
        self._initialized = False
        self._init_time = datetime.now(timezone.utc)
        
        log.info("security_monitor_initialized")

    def init(self) -> None:
        """Initialize the security monitor (call after configuration is loaded)."""
        if self._initialized:
            return
        
        self._initialized = True
        self.log_security_event(
            SecurityEvent.SECURITY_MODULE_INITIALIZED,
            message="Security monitoring initialized",
            component="security_monitor"
        )

    # =========================================================================
    # Authentication Event Logging
    # =========================================================================
    
    def log_auth_event(
        self,
        event_type: SecurityEvent,
        username: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        success: bool = True,
        reason: str | None = None,
        **metadata: Any
    ) -> None:
        """Log an authentication event."""
        with self._lock:
            event = AuthEvent(
                event_type=event_type,
                username=username,
                ip_address=ip_address,
                user_agent=user_agent,
                success=success,
                reason=reason,
                metadata=metadata
            )
            
            self._auth_events.append(event)
            self._metrics["total_auth_attempts"] += 1
            
            if success:
                self._metrics["successful_auth_attempts"] += 1
            else:
                self._metrics["failed_auth_attempts"] += 1
                
                # Track failed logins for brute force detection
                if event_type == SecurityEvent.AUTH_FAILURE:
                    self._failed_logins[username].append(event.timestamp)
                    if ip_address:
                        self._ip_failed_logins[ip_address].append(event.timestamp)
            
            # Log to structlog
            log_data = {
                "event_type": event_type.value,
                "username": username,
                "success": success,
                "ip_address": ip_address,
                "reason": reason,
                **metadata
            }

            if success:
                log.info("auth_success", **log_data)
            else:
                log.warning("auth_failure", **log_data)

            # Check for brute force
            self._check_brute_force(username, ip_address)

            # Check for rate limit (auth failures)
            self._check_rate_limit(event_type, ip_address, "auth_failure")

    def log_auth_success(
        self,
        username: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        **metadata: Any
    ) -> None:
        """Log a successful authentication."""
        self.log_auth_event(
            SecurityEvent.AUTH_SUCCESS,
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=True,
            **metadata
        )

    def log_auth_failure(
        self,
        username: str,
        ip_address: str | None = None,
        user_agent: str | None = None,
        reason: str = "unknown",
        **metadata: Any
    ) -> None:
        """Log a failed authentication attempt."""
        self.log_auth_event(
            SecurityEvent.AUTH_FAILURE,
            username=username,
            ip_address=ip_address,
            user_agent=user_agent,
            success=False,
            reason=reason,
            **metadata
        )

    def log_logout(self, username: str, ip_address: str | None = None) -> None:
        """Log a logout event."""
        self.log_auth_event(
            SecurityEvent.AUTH_LOGOUT,
            username=username,
            ip_address=ip_address,
            success=True
        )

    # =========================================================================
    # Authorization Event Logging
    # =========================================================================
    
    def log_authz_event(
        self,
        event_type: SecurityEvent,
        username: str | None,
        resource: str,
        action: str,
        allowed: bool,
        reason: str | None = None,
        **metadata: Any
    ) -> None:
        """Log an authorization event."""
        with self._lock:
            event = AuthzEvent(
                event_type=event_type,
                username=username,
                resource=resource,
                action=action,
                allowed=allowed,
                reason=reason,
                metadata=metadata
            )
            
            self._authz_events.append(event)
            self._metrics["total_authz_checks"] += 1
            
            if not allowed:
                self._metrics["failed_authz_checks"] += 1
            
            # Log to structlog
            log_data = {
                "event": "authz_event",
                "event_type": event_type.value,
                "username": username,
                "resource": resource,
                "action": action,
                "allowed": allowed,
                "reason": reason,
                **metadata
            }
            
            if allowed:
                log.debug("authz_event", **log_data)
            else:
                log.warning("authz_failure", **log_data)

    def log_authz_success(
        self,
        username: str | None,
        resource: str,
        action: str,
        **metadata: Any
    ) -> None:
        """Log a successful authorization."""
        self.log_authz_event(
            SecurityEvent.AUTHZ_SUCCESS,
            username=username,
            resource=resource,
            action=action,
            allowed=True,
            **metadata
        )

    def log_authz_failure(
        self,
        username: str | None,
        resource: str,
        action: str,
        reason: str = "denied",
        **metadata: Any
    ) -> None:
        """Log a failed authorization."""
        self.log_authz_event(
            SecurityEvent.AUTHZ_FAILURE,
            username=username,
            resource=resource,
            action=action,
            allowed=False,
            reason=reason,
            **metadata
        )

    # =========================================================================
    # General Security Event Logging
    # =========================================================================
    
    def log_security_event(
        self,
        event_type: SecurityEvent,
        message: str = "",
        severity: str = "info",
        **metadata: Any
    ) -> None:
        """Log a general security event."""
        with self._lock:
            timestamp = datetime.now(timezone.utc)
            self._security_events.append((timestamp, event_type, metadata))
            
            # Log based on severity
            log_data = {
                "event": "security_event",
                "event_type": event_type.value,
                "message": message,
                **metadata
            }
            
            if severity == "debug":
                log.debug("security_event", **log_data)
            elif severity == "info":
                log.info("security_event", **log_data)
            elif severity == "warning":
                log.warning("security_event", **log_data)
            else:  # error, critical
                log.error("security_event", **log_data)

    # =========================================================================
    # Suspicious Activity Detection
    # =========================================================================
    
    def _check_brute_force(self, username: str, ip_address: str | None) -> None:
        """Check for brute force attacks based on recent failed login attempts."""
        now = datetime.now(timezone.utc)
        
        # Check username-based brute force
        if username in self._failed_logins:
            recent_failures = [
                ts for ts in self._failed_logins[username]
                if now - ts <= self.BRUTE_FORCE_WINDOW
            ]
            
            if len(recent_failures) >= self.BRUTE_FORCE_THRESHOLD:
                self._trigger_brute_force_alert(username, ip_address, "username")
        
        # Check IP-based brute force
        if ip_address and ip_address in self._ip_failed_logins:
            recent_failures = [
                ts for ts in self._ip_failed_logins[ip_address]
                if now - ts <= self.BRUTE_FORCE_WINDOW
            ]
            
            if len(recent_failures) >= self.BRUTE_FORCE_THRESHOLD:
                self._trigger_brute_force_alert(username, ip_address, "ip")
    
    def _check_rate_limit(self, event_type: SecurityEvent, ip_address: str | None, event_source: str) -> None:
        """Check rate limit based on event type."""
        now = datetime.now(timezone.utc)
        
        # For auth failures, check rate limit
        if event_type == SecurityEvent.AUTH_FAILURE:
            if ip_address and event_source == "auth_failure":
                # Update rate limiter with recent failures
                self._rate_limiter._acquire()
                self._metrics["csrf_token_requests"] += 1
                
                # Check if we're at rate limit
                if self._rate_limiter.current_count >= self.RATE_LIMIT_MAX_REQUESTS:
                    self._metrics["csrf_token_warnings"] += 1
                    self._trigger_rate_limit_warning("auth_failure", ip_address)
        
        # For authorization failures, check rate limit
        if event_type == SecurityEvent.AUTHZ_FAILURE and event_source == "authz_failure":
            # Update rate limiter with recent failures
            self._rate_limiter._acquire()
            self._metrics["csrf_token_requests"] += 1
            
            # Check if we're at rate limit
            if self._rate_limiter.current_count >= self.RATE_LIMIT_MAX_REQUESTS:
                self._metrics["csrf_token_warnings"] += 1
                self._trigger_rate_limit_warning("authz_failure", None)
    
    def _trigger_brute_force_alert(
        self,
        username: str,
        ip_address: str | None,
        brute_force_type: str
    ) -> None:
        """Trigger a brute force detection alert."""
        with self._lock:
            self._metrics["suspicious_events"] += 1
            
            event_data = {
                "type": "brute_force_detected",
                "brute_force_type": brute_force_type,
                "username": username,
                "ip_address": ip_address,
                "threshold": self.BRUTE_FORCE_THRESHOLD,
                "window_minutes": self.BRUTE_FORCE_WINDOW.total_seconds() / 60,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            self.log_security_event(
                SecurityEvent.BRUTE_FORCE_DETECTED,
                message=f"Brute force attack detected ({brute_force_type}) on {username}",
                severity="error",
                **event_data
            )
            
            # Trigger alert callbacks
            self._trigger_alert("brute_force_detected", event_data)
    
    def _trigger_rate_limit_warning(
        self,
        event_type: str,
        ip_address: str | None
    ) -> None:
        """Trigger a rate limit warning."""
        with self._lock:
            self._metrics["csrf_token_warnings"] += 1
            
            event_data = {
                "type": "rate_limit_warning",
                "event_type": event_type,
                "ip_address": ip_address,
                "warning_count": self._metrics["csrf_token_warnings"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            self.log_security_event(
                SecurityEvent.RATE_LIMIT_WARNING,
                message=f"Rate limit warning triggered for {event_type}",
                severity="warning",
                **event_data
            )
            
            # Trigger alert callbacks
            self._trigger_alert("rate_limit_warning", event_data)

    def detect_suspicious_activity(self) -> list[dict[str, Any]]:
        """Detect suspicious activity patterns in recent events."""
        with self._lock:
            suspicious_events = []
            now = datetime.now(timezone.utc)
            
            # Check for brute force patterns
            for username, timestamps in self._failed_logins.items():
                recent = [ts for ts in timestamps if now - ts <= self.SUSPICIOUS_WINDOW]
                if len(recent) >= self.BRUTE_FORCE_THRESHOLD * 2:  # More than threshold
                    suspicious_events.append({
                        "type": "repeated_failed_logins",
                        "entity": username,
                        "entity_type": "username",
                        "count": len(recent),
                        "window": str(self.SUSPICIOUS_WINDOW),
                        "severity": "high"
                    })
            
            for ip, timestamps in self._ip_failed_logins.items():
                recent = [ts for ts in timestamps if now - ts <= self.SUSPICIOUS_WINDOW]
                if len(recent) >= self.BRUTE_FORCE_THRESHOLD * 2:
                    suspicious_events.append({
                        "type": "repeated_failed_logins",
                        "entity": ip,
                        "entity_type": "ip_address",
                        "count": len(recent),
                        "window": str(self.SUSPICIOUS_WINDOW),
                        "severity": "high"
                    })
            
            # Check for high rate of auth failures
            recent_auth_failures = [
                e for e in self._auth_events
                if e.event_type == SecurityEvent.AUTH_FAILURE
                and now - e.timestamp <= timedelta(minutes=1)
            ]
            
            if len(recent_auth_failures) > self._alert_thresholds["failed_login_rate"]:
                suspicious_events.append({
                    "type": "high_auth_failure_rate",
                    "count": len(recent_auth_failures),
                    "window": "1 minute",
                    "severity": "critical"
                })
            
            # Check for high rate of authz failures
            recent_authz_failures = [
                e for e in self._authz_events
                if not e.allowed
                and now - e.timestamp <= timedelta(minutes=1)
            ]
            
            if len(recent_authz_failures) > self._alert_thresholds["authz_failures"]:
                suspicious_events.append({
                    "type": "high_authz_failure_rate",
                    "count": len(recent_authz_failures),
                    "window": "1 minute",
                    "severity": "high"
                })
            
            return suspicious_events

    # =========================================================================
    # Security Metrics
    # =========================================================================
    
    def get_security_metrics(self) -> dict[str, Any]:
        """Get current security metrics."""
        with self._lock:
            # Calculate rates
            now = datetime.now(timezone.utc)
            
            # Auth success/failure rates (last minute)
            recent_auth_events = [
                e for e in self._auth_events
                if now - e.timestamp <= timedelta(minutes=1)
            ]
            
            recent_success = len([e for e in recent_auth_events if e.success])
            recent_failure = len([e for e in recent_auth_events if not e.success])
            
            # Authz success/failure rates (last minute)
            recent_authz_events = [
                e for e in self._authz_events
                if now - e.timestamp <= timedelta(minutes=1)
            ]
            
            recent_authz_success = len([e for e in recent_authz_events if e.allowed])
            recent_authz_failure = len([e for e in recent_authz_events if not e.allowed])
            
            # Create metrics
            metrics = {
                "authentication": {
                    "total_attempts": self._metrics["total_auth_attempts"],
                    "successful_attempts": self._metrics["successful_auth_attempts"],
                    "failed_attempts": self._metrics["failed_auth_attempts"],
                    "success_rate": self._calculate_rate(
                        self._metrics["successful_auth_attempts"],
                        self._metrics["total_auth_attempts"]
                    ),
                    "recent_success_rate": self._calculate_rate(recent_success, len(recent_auth_events)),
                    "recent_failure_rate": self._calculate_rate(recent_failure, len(recent_auth_events)),
                },
                "authorization": {
                    "total_checks": self._metrics["total_authz_checks"],
                    "failed_checks": self._metrics["failed_authz_checks"],
                    "success_rate": self._calculate_rate(
                        self._metrics["total_authz_checks"] - self._metrics["failed_authz_checks"],
                        self._metrics["total_authz_checks"]
                    ),
                    "recent_success_rate": self._calculate_rate(recent_authz_success, len(recent_authz_events)),
                    "recent_failure_rate": self._calculate_rate(recent_authz_failure, len(recent_authz_events)),
                },
                "suspicious_activity": {
                    "total_events": self._metrics["suspicious_events"],
                    "brute_force_detected": self._metrics["security_violations"],
                    "recent_events": len(self.detect_suspicious_activity()),
                },
                "rate_limiting": {
                    "current_requests": self._rate_limiter.current_count,
                    "max_requests": self.RATE_LIMIT_MAX_REQUESTS,
                    "warning_threshold": self.RATE_LIMIT_WARNING_THRESHOLD,
                    "token_requests": self._metrics["csrf_token_requests"],
                    "warning_count": self._metrics["csrf_token_warnings"],
                    "window_seconds": self.RATE_LIMIT_WINDOW.total_seconds(),
                },
                "csrf_tokens": {
                    "total_sessions": len(self._csrf_tokens),
                    "total_tokens": sum(len(tokens) for tokens in self._csrf_tokens.values()),
                    "ttl_seconds": self.CSRF_TOKEN_TTL.total_seconds(),
                    "max_per_session": self.CSRF_MAX_TOKENS_PER_SESSION,
                },
                "uptime": {
                    "since": self._init_time.isoformat(),
                    "duration_seconds": (now - self._init_time).total_seconds(),
                }
            }
            
            return metrics

    def _calculate_rate(self, numerator: int, denominator: int) -> float:
        """Calculate a rate safely."""
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)

    # =========================================================================
    # Alerting
    # =========================================================================
    
    def add_alert_callback(self, callback: callable) -> None:
        """Add a callback function for security alerts."""
        with self._lock:
            self._alert_callbacks.append(callback)

    def remove_alert_callback(self, callback: callable) -> None:
        """Remove a callback function for security alerts."""
        with self._lock:
            if callback in self._alert_callbacks:
                self._alert_callbacks.remove(callback)

    def _trigger_alert(self, alert_type: str, data: dict[str, Any]) -> None:
        """Trigger security alerts to all registered callbacks."""
        with self._lock:
            for callback in self._alert_callbacks:
                try:
                    callback(alert_type, data)
                except Exception as e:
                    log.error("alert_callback_error", callback=str(callback), error=str(e))

    # =========================================================================
    # CSRF Token Management
    # =========================================================================

    def generate_csrf_token(self, session_id: str) -> str:
        """Generate a new CSRF token for a session."""
        import secrets
        from datetime import datetime, timezone

        with self._lock:
            # Generate random token
            raw_token = secrets.token_bytes(self.CSRF_TOKEN_LENGTH)
            token = raw_token.hex()

            # Create signed token (simulating the CSRFProtection class)
            # In production, this would use HMAC signing
            signature = f"{session_id}:{token}"

            # Store token
            if session_id not in self._csrf_tokens:
                self._csrf_tokens[session_id] = []

            self._csrf_tokens[session_id].append(token)

            # Evict old tokens if over limit
            if len(self._csrf_tokens[session_id]) > self.CSRF_MAX_TOKENS_PER_SESSION:
                self._csrf_tokens[session_id] = self._csrf_tokens[session_id][-self.CSRF_MAX_TOKENS_PER_SESSION:]

            self._metrics["csrf_token_requests"] += 1

            return token

    def validate_csrf_token(self, session_id: str, token: str) -> bool:
        """Validate a CSRF token."""
        import secrets

        with self._lock:
            if session_id not in self._csrf_tokens:
                return False

            if token not in self._csrf_tokens[session_id]:
                return False

            # Remove the token (one-time use)
            self._csrf_tokens[session_id].remove(token)

            # Clean up empty session entries
            if not self._csrf_tokens[session_id]:
                del self._csrf_tokens[session_id]

            return True

    def get_csrf_tokens_for_session(self, session_id: str) -> list[str]:
        """Get all valid CSRF tokens for a session."""
        with self._lock:
            return list(self._csrf_tokens.get(session_id, []))

    def cleanup_expired_csrf_tokens(self) -> int:
        """Remove expired CSRF tokens."""
        # Since we're using in-memory storage, this is mainly for cleanup
        # In production, you'd need to track expiration times for each token
        return len(self._csrf_tokens)

    # =========================================================================
    # Security Dashboard Data
    # =========================================================================
    
    def get_security_dashboard_data(self) -> dict[str, Any]:
        """Get data for a security dashboard."""
        with self._lock:
            metrics = self.get_security_metrics()
            suspicious = self.detect_suspicious_activity()
            
            # Recent events
            now = datetime.now(timezone.utc)
            recent_auth_events = [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.event_type.value,
                    "username": e.username,
                    "ip_address": e.ip_address,
                    "success": e.success,
                    "reason": e.reason
                }
                for e in self._auth_events
                if now - e.timestamp <= timedelta(hours=1)
            ][:50]  # Last 50 events
            
            recent_authz_events = [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.event_type.value,
                    "username": e.username,
                    "resource": e.resource,
                    "action": e.action,
                    "allowed": e.allowed,
                    "reason": e.reason
                }
                for e in self._authz_events
                if now - e.timestamp <= timedelta(hours=1)
            ][:50]  # Last 50 events
            
            return {
                "metrics": metrics,
                "suspicious_activity": suspicious,
                "recent_auth_events": recent_auth_events,
                "recent_authz_events": recent_authz_events,
                "alerts": self._get_recent_alerts()
            }

    def _get_recent_alerts(self) -> list[dict[str, Any]]:
        """Get recent security alerts."""
        # This would be implemented based on your alert storage
        # For now, return empty list
        return []

    # =========================================================================
    # Security Audit
    # =========================================================================
    
    def perform_security_audit(self) -> dict[str, Any]:
        """Perform a comprehensive security audit."""
        self.log_security_event(
            SecurityEvent.SECURITY_AUDIT_STARTED,
            message="Starting comprehensive security audit"
        )
        
        audit_results = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": {},
            "summary": {
                "passed": 0,
                "failed": 0,
                "warnings": 0,
                "score": 0
            }
        }
        
        # Run various security checks
        audit_results["checks"]["authentication"] = self._audit_authentication()
        audit_results["checks"]["authorization"] = self._audit_authorization()
        audit_results["checks"]["recent_activity"] = self._audit_recent_activity()
        
        # Calculate summary
        for check_name, check_result in audit_results["checks"].items():
            for item in check_result["items"]:
                if item["status"] == "pass":
                    audit_results["summary"]["passed"] += 1
                elif item["status"] == "fail":
                    audit_results["summary"]["failed"] += 1
                else:  # warning
                    audit_results["summary"]["warnings"] += 1
        
        # Calculate score (0-100)
        total_checks = audit_results["summary"]["passed"] + audit_results["summary"]["failed"] + audit_results["summary"]["warnings"]
        if total_checks > 0:
            score = (audit_results["summary"]["passed"] / total_checks) * 100
            # Deduct for warnings
            score -= (audit_results["summary"]["warnings"] / total_checks) * 20
            audit_results["summary"]["score"] = round(score, 1)
        
        self.log_security_event(
            SecurityEvent.SECURITY_AUDIT_COMPLETED,
            message=f"Security audit completed with score {audit_results['summary']['score']}",
            audit_results=audit_results
        )
        
        return audit_results

    def _audit_authentication(self) -> dict[str, Any]:
        """Audit authentication security."""
        results = {
            "name": "Authentication Security",
            "items": []
        }
        
        # Check recent auth success rate
        now = datetime.now(timezone.utc)
        recent_auth = [
            e for e in self._auth_events
            if now - e.timestamp <= timedelta(hours=1)
        ]
        
        if len(recent_auth) > 0:
            success_rate = len([e for e in recent_auth if e.success]) / len(recent_auth)
            if success_rate < 0.8:  # Less than 80% success rate
                results["items"].append({
                    "check": "Authentication success rate",
                    "status": "fail",
                    "message": f"Low auth success rate: {success_rate:.1%}",
                    "details": {"success_rate": success_rate, "total_attempts": len(recent_auth)}
                })
            else:
                results["items"].append({
                    "check": "Authentication success rate",
                    "status": "pass",
                    "message": f"Good auth success rate: {success_rate:.1%}",
                    "details": {"success_rate": success_rate, "total_attempts": len(recent_auth)}
                })
        
        # Check for brute force attempts
        brute_force_count = len([
            e for e in self._security_events
            if e[1] == SecurityEvent.BRUTE_FORCE_DETECTED
            and now - e[0] <= timedelta(hours=1)
        ])
        
        if brute_force_count > 0:
            results["items"].append({
                "check": "Brute force detection",
                "status": "fail",
                "message": f"Brute force attempts detected: {brute_force_count}",
                "details": {"count": brute_force_count}
            })
        else:
            results["items"].append({
                "check": "Brute force detection",
                "status": "pass",
                "message": "No brute force attempts detected"
            })
        
        return results

    def _audit_authorization(self) -> dict[str, Any]:
        """Audit authorization security."""
        results = {
            "name": "Authorization Security",
            "items": []
        }
        
        # Check recent authz success rate
        now = datetime.now(timezone.utc)
        recent_authz = [
            e for e in self._authz_events
            if now - e.timestamp <= timedelta(hours=1)
        ]
        
        if len(recent_authz) > 0:
            success_rate = len([e for e in recent_authz if e.allowed]) / len(recent_authz)
            if success_rate < 0.9:  # Less than 90% success rate
                results["items"].append({
                    "check": "Authorization success rate",
                    "status": "warning",
                    "message": f"Low authz success rate: {success_rate:.1%}",
                    "details": {"success_rate": success_rate, "total_checks": len(recent_authz)}
                })
            else:
                results["items"].append({
                    "check": "Authorization success rate",
                    "status": "pass",
                    "message": f"Good authz success rate: {success_rate:.1%}",
                    "details": {"success_rate": success_rate, "total_checks": len(recent_authz)}
                })
        
        return results

    def _audit_recent_activity(self) -> dict[str, Any]:
        """Audit recent security activity."""
        results = {
            "name": "Recent Activity",
            "items": []
        }
        
        # Check for suspicious activity
        suspicious = self.detect_suspicious_activity()
        
        if suspicious:
            results["items"].append({
                "check": "Suspicious activity detection",
                "status": "fail",
                "message": f"Suspicious activity detected: {len(suspicious)} events",
                "details": {"events": suspicious}
            })
        else:
            results["items"].append({
                "check": "Suspicious activity detection",
                "status": "pass",
                "message": "No suspicious activity detected"
            })
        
        return results


# =============================================================================
# Global Security Monitor Instance
# =============================================================================

# Global instance for easy access
_security_monitor: SecurityMonitor | None = None


def get_security_monitor() -> SecurityMonitor:
    """Get the global security monitor instance."""
    global _security_monitor
    if _security_monitor is None:
        _security_monitor = SecurityMonitor()
        _security_monitor.init()
    return _security_monitor


def reset_security_monitor() -> None:
    """Reset the global security monitor (for testing)."""
    global _security_monitor
    _security_monitor = None