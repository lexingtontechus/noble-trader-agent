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

import functools
import json
import logging
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)


def api_rate_limit(api_method: str):
    """
    Decorator to rate limit API methods of the security monitor.
    
    Args:
        api_method: Name of the API method being called
        
    Returns:
        Decorated function with rate limiting
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            # Check if rate limiting is enabled
            if not hasattr(self, '_api_rate_limiter'):
                return func(self, *args, **kwargs)
            
            # Acquire API rate limiting token
            if not self._api_rate_limiter._acquire():
                # Rate limit exceeded
                self.log_security_event(
                    event_type=SecurityEvent.RATE_LIMIT_WARNING,
                    message=f"API rate limit exceeded for {api_method}",
                    severity="warning",
                    api_method=api_method
                )
                # Return a safe response without raising exception
                return {"error": "Rate limit exceeded", "retry_after": 30}
            
            # Track API call
            if api_method in self._api_calls:
                self._api_calls[api_method] += 1
            
            try:
                return func(self, *args, **kwargs)
            except Exception as e:
                # Log error with sanitization
                self.log_security_event(
                    event_type=SecurityEvent.INPUT_VALIDATION_FAILURE,
                    message=f"API error in {api_method}",
                    severity="error",
                    api_method=api_method,
                    error=str(e)
                )
                raise
        return wrapper
    return decorator


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
            
        Raises:
            ValueError: If max_requests is negative or window is zero/negative
        """
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window <= timedelta(0):
            raise ValueError("window must be positive")
            
        self.max_requests = max_requests
        self.window = window
        self._lock = threading.RLock()
        self._available = max_requests
        self._last_update = datetime.now()
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
            
            if self._available <= 0:
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
    
    # API rate limiting for security monitor itself
    API_RATE_LIMIT_WINDOW = timedelta(seconds=30)  # 30 second window
    API_RATE_LIMIT_MAX_REQUESTS = 50  # Maximum API calls per window
    API_RATE_LIMIT_WARNING_THRESHOLD = 40  # Warning at 80% capacity
    
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
        
        # Enhanced alerting configuration
        self._alert_thresholds = {
            # Authentication alerts
            "failed_login_rate": 10,  # Alert if >10 failed logins per minute
            "failed_login_rate_critical": 25,  # Critical if >25 failed logins per minute
            "brute_force_attempts": 3,  # Alert if >3 brute force attempts
            "brute_force_attempts_critical": 5,  # Critical if >5 brute force attempts
            "success_rate_drop": 0.8,  # Alert if success rate drops below 80%
            "success_rate_drop_critical": 0.6,  # Critical if success rate drops below 60%
            
            # Authorization alerts
            "authz_failures": 5,  # Alert if >5 authorization failures per minute
            "authz_failures_critical": 15,  # Critical if >15 authorization failures per minute
            "privilege_escalation_attempts": 2,  # Alert if >2 privilege escalation attempts
            "privilege_escalation_attempts_critical": 5,  # Critical if >5 privilege escalation attempts
            
            # Rate limiting alerts
            "rate_limit_warnings": 80,  # Alert at 80% capacity
            "rate_limit_critical": 95,  # Critical at 95% capacity
            
            # Suspicious activity alerts
            "suspicious_requests_per_minute": 5,  # Alert if >5 suspicious requests per minute
            "suspicious_requests_per_minute_critical": 15,  # Critical if >15 suspicious requests per minute
            
            # Session management
            "concurrent_sessions_per_user": 5,  # Alert if >5 concurrent sessions per user
            "session_hijacking_attempts": 3,  # Alert if >3 session hijacking attempts
            
            # Data access patterns
            "unusual_data_access": 10,  # Alert if >10 unusual data accesses per minute
            "data_export_attempts": 3,  # Alert if >3 data export attempts per hour
        }
        
        # Escalation procedures configuration
        self._escalation_config = {
            # Authentication escalation
            "brute_force_detected": {
                "severity": "high",
                "immediate_action": "temporary_ip_block",
                "notification": "security_team",
                "escalation_time": 300,  # 5 minutes
                "resolution_action": "ip_whitelist_review"
            },
            "brute_force_critical": {
                "severity": "critical",
                "immediate_action": "permanent_ip_block",
                "notification": "security_team_incident",
                "escalation_time": 60,  # 1 minute
                "resolution_action": "incident_response_protocol"
            },
            "success_rate_drop": {
                "severity": "high",
                "immediate_action": "auth_review",
                "notification": "security_team",
                "escalation_time": 600,  # 10 minutes
                "resolution_action": "auth_system_audit"
            },
            "privilege_escalation": {
                "severity": "critical",
                "immediate_action": "account_lockdown",
                "notification": "security_incident",
                "escalation_time": 30,  # 30 seconds
                "resolution_action": "forensic_analysis"
            },
            "data_breach_suspected": {
                "severity": "critical",
                "immediate_action": "system_isolation",
                "notification": "incident_response_team",
                "escalation_time": 15,  # 15 seconds
                "resolution_action": "containment_procedure"
            },
            "dos_attack_detected": {
                "severity": "high",
                "immediate_action": "rate_limit_increase",
                "notification": "operations_team",
                "escalation_time": 120,  # 2 minutes
                "resolution_action": "attack_mitigation"
            }
        }
        
        # Alert tracking
        self._alert_history: list[dict] = []
        self._active_alerts: dict[str, dict] = {}
        self._alert_counts: dict[str, int] = defaultdict(int)
        self._last_alert_times: dict[str, datetime] = {}
        
        # Rate limiting tracking
        self._rate_limiter = RateLimiter(
            max_requests=self.RATE_LIMIT_MAX_REQUESTS,
            window=self.RATE_LIMIT_WINDOW
        )
        
        # API rate limiting for security monitor itself
        self._api_rate_limiter = RateLimiter(
            max_requests=self.API_RATE_LIMIT_MAX_REQUESTS,
            window=self.API_RATE_LIMIT_WINDOW
        )
        
        # API call tracking
        self._api_calls = {
            "get_security_metrics": 0,
            "log_auth_event": 0,
            "log_authz_event": 0,
            "log_security_event": 0,
            "detect_suspicious_activity": 0,
            "perform_security_audit": 0,
            "generate_csrf_token": 0,
            "validate_csrf_token": 0,
            "get_security_dashboard_data": 0,
        }
        
        # CSRF token tracking
        self._csrf_tokens: dict[str, list[str]] = {}  # session_id -> list of tokens
        
        # Alert callbacks and notification channels
        self._alert_callbacks: list[callable] = []
        self._notification_channels: list[callable] = []
        self._escalation_handlers: dict[str, callable] = {}
        
        # Configure default notification channels
        self._setup_default_notification_channels()
        
        # Initialize
        self._initialized = False
        self._init_time = datetime.now(timezone.utc)
        
        log.info("security_monitor_initialized")

    def init(self) -> None:
        """Initialize the security monitor (call after configuration is loaded)."""
        if self._initialized:
            return
        
        self._initialized = True
        
        # Log initialization event without rate limiting (since we're in init)
        timestamp = datetime.now(timezone.utc)
        message = "Security monitoring initialized"
        sanitized_message = self._sanitize_input(message, max_length=1000, field_name="message")
        
        log_data = {
            "event_type": SecurityEvent.SECURITY_MODULE_INITIALIZED.value,
            "message": sanitized_message,
            "component": "security_monitor"
        }
        
        # Redact sensitive data from logs
        redacted_log_data = self._redact_sensitive_data(log_data)
        log.info("security_monitor_initialized", **redacted_log_data)

    # =========================================================================
    # Authentication Event Logging
    # =========================================================================
    
    @api_rate_limit("log_auth_event")
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
            # Sanitize all inputs
            sanitized_username = self._sanitize_input(username, max_length=100, field_name="username")
            sanitized_ip = self._sanitize_input(ip_address, max_length=45, field_name="ip_address") if ip_address else None
            sanitized_user_agent = self._sanitize_input(user_agent, max_length=500, field_name="user_agent") if user_agent else None
            sanitized_reason = self._sanitize_input(reason, max_length=200, field_name="reason")
            
            # Sanitize metadata
            sanitized_metadata = {}
            for key, value in metadata.items():
                if isinstance(value, str):
                    sanitized_metadata[key] = self._sanitize_input(value, max_length=500, field_name=f"metadata_{key}")
                else:
                    sanitized_metadata[key] = value
            
            event = AuthEvent(
                event_type=event_type,
                username=sanitized_username,
                ip_address=sanitized_ip,
                user_agent=sanitized_user_agent,
                success=success,
                reason=sanitized_reason,
                metadata=sanitized_metadata
            )
            
            self._auth_events.append(event)
            self._metrics["total_auth_attempts"] += 1
            
            if success:
                self._metrics["successful_auth_attempts"] += 1
            else:
                self._metrics["failed_auth_attempts"] += 1
                
                # Track failed logins for brute force detection
                if event_type == SecurityEvent.AUTH_FAILURE:
                    # Use sanitized username and IP for tracking
                    track_username = sanitized_username
                    track_ip = sanitized_ip
                    
                    self._failed_logins[track_username].append(event.timestamp)
                    if track_ip:
                        self._ip_failed_logins[track_ip].append(event.timestamp)
            
            # Log to structlog with redacted sensitive data
            log_data = {
                "event_type": event_type.value,
                "username": sanitized_username,
                "success": success,
                "ip_address": sanitized_ip,
                "reason": sanitized_reason,
                **sanitized_metadata
            }

            # Redact sensitive data from logs
            redacted_log_data = self._redact_sensitive_data(log_data)

            if success:
                log.info("auth_success", **redacted_log_data)
            else:
                log.warning("auth_failure", **redacted_log_data)

            # Check for brute force
            self._check_brute_force(sanitized_username, sanitized_ip)

            # Check for rate limit (auth failures)
            self._check_rate_limit(event_type, sanitized_ip, "auth_failure")

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
    
    @api_rate_limit("log_authz_event")
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
            # Sanitize all inputs
            sanitized_username = self._sanitize_input(username, max_length=100, field_name="username") if username else None
            sanitized_resource = self._sanitize_input(resource, max_length=500, field_name="resource")
            sanitized_action = self._sanitize_input(action, max_length=50, field_name="action")
            sanitized_reason = self._sanitize_input(reason, max_length=200, field_name="reason")
            
            # Sanitize metadata
            sanitized_metadata = {}
            for key, value in metadata.items():
                if isinstance(value, str):
                    sanitized_metadata[key] = self._sanitize_input(value, max_length=500, field_name=f"metadata_{key}")
                else:
                    sanitized_metadata[key] = value
            
            event = AuthzEvent(
                event_type=event_type,
                username=sanitized_username,
                resource=sanitized_resource,
                action=sanitized_action,
                allowed=allowed,
                reason=sanitized_reason,
                metadata=sanitized_metadata
            )
            
            self._authz_events.append(event)
            self._metrics["total_authz_checks"] += 1
            
            if not allowed:
                self._metrics["failed_authz_checks"] += 1
            
            # Log to structlog with redacted sensitive data
            log_data = {
                "authz_event": "authz_event",
                "event_type": event_type.value,
                "username": sanitized_username,
                "resource": sanitized_resource,
                "action": sanitized_action,
                "allowed": allowed,
                "reason": sanitized_reason,
                **sanitized_metadata
            }
            
            # Redact sensitive data from logs
            redacted_log_data = self._redact_sensitive_data(log_data)
            
            if allowed:
                log.debug("authz_event", **redacted_log_data)
            else:
                log.warning("authz_failure", **redacted_log_data)

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
    
    @api_rate_limit("log_security_event")
    def log_security_event(
        self,
        event_type: SecurityEvent,
        message: str = "",
        severity: str = "info",
        **metadata: Any
    ) -> None:
        """Log a general security event."""
        with self._lock:
            # Sanitize inputs
            sanitized_message = self._sanitize_input(message, max_length=1000, field_name="message")
            
            # Sanitize metadata
            sanitized_metadata = {}
            for key, value in metadata.items():
                if isinstance(value, str):
                    sanitized_metadata[key] = self._sanitize_input(value, max_length=500, field_name=f"metadata_{key}")
                else:
                    sanitized_metadata[key] = value
            
            timestamp = datetime.now(timezone.utc)
            self._security_events.append((timestamp, event_type, sanitized_metadata))
            
            # Log based on severity
            log_data = {
                "event_type": event_type.value,
                "message": sanitized_message,
                **sanitized_metadata
            }
            
            # Redact sensitive data from logs
            redacted_log_data = self._redact_sensitive_data(log_data)
            
            if severity == "debug":
                log.debug("security_event", **redacted_log_data)
            elif severity == "info":
                log.info("security_event", **redacted_log_data)
            elif severity == "warning":
                log.warning("security_event", **redacted_log_data)
            else:  # error, critical
                log.error("security_event", **redacted_log_data)

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
            
            # Check for alert threshold
            if len(recent_failures) >= self._alert_thresholds["brute_force_attempts"]:
                self._trigger_security_alert(
                    alert_type="brute_force_detected",
                    severity="high",
                    message=f"Brute force attack detected for user {username}",
                    data={
                        "username": username,
                        "ip_address": ip_address,
                        "failure_count": len(recent_failures),
                        "window": str(self.BRUTE_FORCE_WINDOW)
                    }
                )
            
            # Check for critical threshold
            if len(recent_failures) >= self._alert_thresholds["brute_force_attempts_critical"]:
                self._trigger_security_alert(
                    alert_type="brute_force_critical",
                    severity="critical",
                    message=f"CRITICAL: Brute force attack detected for user {username}",
                    data={
                        "username": username,
                        "ip_address": ip_address,
                        "failure_count": len(recent_failures),
                        "window": str(self.BRUTE_FORCE_WINDOW)
                    }
                )
        
        # Check IP-based brute force
        if ip_address and ip_address in self._ip_failed_logins:
            recent_failures = [
                ts for ts in self._ip_failed_logins[ip_address]
                if now - ts <= self.BRUTE_FORCE_WINDOW
            ]
            
            if len(recent_failures) >= self._alert_thresholds["brute_force_attempts"]:
                self._trigger_security_alert(
                    alert_type="brute_force_detected",
                    severity="high",
                    message=f"Brute force attack detected from IP {ip_address}",
                    data={
                        "username": username,
                        "ip_address": ip_address,
                        "failure_count": len(recent_failures),
                        "window": str(self.BRUTE_FORCE_WINDOW)
                    }
                )
            
            if len(recent_failures) >= self._alert_thresholds["brute_force_attempts_critical"]:
                self._trigger_security_alert(
                    alert_type="brute_force_critical",
                    severity="critical",
                    message=f"CRITICAL: Brute force attack detected from IP {ip_address}",
                    data={
                        "username": username,
                        "ip_address": ip_address,
                        "failure_count": len(recent_failures),
                        "window": str(self.BRUTE_FORCE_WINDOW)
                    }
                )
    
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
                event_type=SecurityEvent.BRUTE_FORCE_DETECTED,
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
                "event_type_orig": event_type,
                "ip_address": ip_address,
                "warning_count": self._metrics["csrf_token_warnings"],
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            
            self.log_security_event(
                event_type=SecurityEvent.RATE_LIMIT_WARNING,
                message=f"Rate limit warning triggered for {event_type}",
                severity="warning",
                **event_data
            )
            
            # Trigger alert callbacks
            self._trigger_alert("rate_limit_warning", event_data)

    # =========================================================================
    # Enhanced Alerting System
    # =========================================================================

    def _setup_default_notification_channels(self) -> None:
        """Setup default notification channels for alerts."""
        # Email notification channel
        self._notification_channels.append(self._email_notification)
        
        # Log notification channel
        self._notification_channels.append(self._log_notification)
        
        # Slack/Teams notification channel
        self._notification_channels.append(self._slack_notification)
        
        # Configure default escalation handlers
        self._escalation_handlers = {
            "temporary_ip_block": self._temporary_ip_block,
            "permanent_ip_block": self._permanent_ip_block,
            "account_lockdown": self._account_lockdown,
            "auth_review": self._auth_review,
            "system_isolation": self._system_isolation,
            "rate_limit_increase": self._rate_limit_increase,
            "ip_whitelist_review": self._ip_whitelist_review,
            "incident_response_protocol": self._incident_response_protocol,
            "forensic_analysis": self._forensic_analysis,
            "containment_procedure": self._containment_procedure,
            "attack_mitigation": self._attack_mitigation,
            "auth_system_audit": self._auth_system_audit
        }

    def _trigger_security_alert(self, alert_type: str, severity: str, message: str, data: dict[str, Any] = None) -> None:
        """Trigger a security alert with escalation procedures."""
        now = datetime.now(timezone.utc)
        alert_id = f"{alert_type}_{now.timestamp()}"
        
        # Check if this alert type is already active and if we should skip duplicates
        if alert_type in self._active_alerts:
            last_alert = self._active_alerts[alert_type]
            if now - last_alert["timestamp"] < timedelta(minutes=5):  # 5 minute cooldown
                return  # Skip duplicate alert
        
        # Create alert
        alert = {
            "id": alert_id,
            "type": alert_type,
            "severity": severity,
            "message": message,
            "data": data or {},
            "timestamp": now,
            "status": "active",
            "escalation_triggered": False,
            "resolution_actions": []
        }
        
        # Store alert
        self._active_alerts[alert_type] = alert
        self._alert_history.append(alert)
        self._alert_counts[alert_type] += 1
        self._last_alert_times[alert_type] = now
        
        # Log the alert
        self.log_security_event(
            event_type=SecurityEvent.SECURITY_CONFIG_CHANGE,
            message=message,
            severity=severity,
            alert_type=alert_type,
            alert_data=data
        )
        
        # Trigger notifications
        self._send_alert_notifications(alert)
        
        # Check for escalation
        self._check_escalation(alert)
        
        # Trigger callbacks
        self._trigger_alert(alert_type, alert)

    def _check_escalation(self, alert: dict) -> None:
        """Check if alert should be escalated and trigger appropriate actions."""
        alert_type = alert["type"]
        severity = alert["severity"]
        
        # Get escalation configuration
        escalation_key = alert_type
        if severity == "critical":
            escalation_key = f"{alert_type}_critical"
        
        if escalation_key in self._escalation_config:
            config = self._escalation_config[escalation_key]
            
            # Check if we should escalate based on time
            if not alert["escalation_triggered"]:
                # Schedule escalation
                threading.Timer(
                    config["escalation_time"],
                    self._execute_escalation,
                    args=[alert, config]
                ).start()
                
                # Mark alert as escalated
                alert["escalation_triggered"] = True
                alert["escalation_config"] = config
                alert["escalation_time"] = datetime.now(timezone.utc)
                
                log.warning(
                    "alert_escalation_scheduled",
                    alert_type=alert_type,
                    severity=severity,
                    escalation_time=config["escalation_time"]
                )

    def _execute_escalation(self, alert: dict, config: dict) -> None:
        """Execute escalation procedures for an alert."""
        alert_type = alert["type"]
        severity = config["severity"]
        
        log.critical(
            "alert_escalation_executing",
            alert_type=alert_type,
            severity=severity,
            immediate_action=config["immediate_action"]
        )
        
        # Execute immediate action
        if config["immediate_action"] in self._escalation_handlers:
            try:
                handler = self._escalation_handlers[config["immediate_action"]]
                result = handler(alert)
                alert["resolution_actions"].append({
                    "action": config["immediate_action"],
                    "result": result,
                    "timestamp": datetime.now(timezone.utc)
                })
            except Exception as e:
                log.error(
                    "alert_escalation_failed",
                    alert_type=alert_type,
                    action=config["immediate_action"],
                    error=str(e)
                )
                alert["resolution_actions"].append({
                    "action": config["immediate_action"],
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc)
                })
        
        # Send critical notification
        self._send_critical_notification(alert, config)
        
        # Update alert status
        alert["status"] = "escalated"

    def _send_alert_notifications(self, alert: dict) -> None:
        """Send notifications for an alert."""
        # Send to all notification channels
        for channel in self._notification_channels:
            try:
                channel(alert)
            except Exception as e:
                log.error(
                    "alert_notification_failed",
                    channel=channel.__name__,
                    alert_type=alert["type"],
                    error=str(e)
                )

    def _send_critical_notification(self, alert: dict, config: dict) -> None:
        """Send critical notification for escalated alerts."""
        critical_alert = alert.copy()
        critical_alert["escalation_level"] = "critical"
        critical_alert["immediate_action"] = config["immediate_action"]
        
        # Send to emergency notification channels
        emergency_channels = self._notification_channels[:2]  # Email and log only
        for channel in emergency_channels:
            try:
                channel(critical_alert)
            except Exception as e:
                log.error(
                    "critical_notification_failed",
                    channel=channel.__name__,
                    alert_type=alert["type"],
                    error=str(e"
                )

    # Notification channel implementations
    def _email_notification(self, alert: dict) -> None:
        """Send alert via email notification."""
        # Placeholder for email notification implementation
        log.info("email_notification_sent", alert_type=alert["type"], severity=alert["severity"])

    def _log_notification(self, alert: dict) -> None:
        """Send alert via logging."""
        log_data = {
            "alert_type": alert["type"],
            "severity": alert["severity"],
            "message": alert["message"],
            "timestamp": alert["timestamp"].isoformat(),
            "alert_id": alert["id"]
        }
        
        if alert["severity"] == "critical":
            log.critical("security_alert", **log_data)
        elif alert["severity"] == "high":
            log.warning("security_alert", **log_data)
        else:
            log.info("security_alert", **log_data)

    def _slack_notification(self, alert: dict) -> None:
        """Send alert via Slack/Teams webhook."""
        # Placeholder for Slack/Teams webhook implementation
        log.info("slack_notification_sent", alert_type=alert["type"], severity=alert["severity"])

    # Escalation handler implementations
    def _temporary_ip_block(self, alert: dict) -> str:
        """Implement temporary IP block."""
        ip_address = alert["data"].get("ip_address")
        if ip_address:
            # Placeholder for IP blocking implementation
            log.warning("ip_temporarily_blocked", ip_address=ip_address, duration="5_minutes")
            return f"IP {ip_address} temporarily blocked for 5 minutes"
        return "No IP address to block"

    def _permanent_ip_block(self, alert: dict) -> str:
        """Implement permanent IP block."""
        ip_address = alert["data"].get("ip_address")
        if ip_address:
            # Placeholder for permanent IP blocking implementation
            log.warning("ip_permanently_blocked", ip_address=ip_address)
            return f"IP {ip_address} permanently blocked"
        return "No IP address to block"

    def _account_lockdown(self, alert: dict) -> str:
        """Implement account lockdown."""
        username = alert["data"].get("username")
        if username:
            # Placeholder for account lockdown implementation
            log.warning("account_locked_down", username=username)
            return f"Account {username} locked down"
        return "No username to lockdown"

    def _auth_review(self, alert: dict) -> str:
        """Trigger authentication review."""
        log.warning("auth_review_triggered")
        return "Authentication review triggered"

    def _system_isolation(self, alert: dict) -> str:
        """Implement system isolation."""
        log.warning("system_isolation_triggered")
        return "System isolation initiated"

    def _rate_limit_increase(self, alert: dict) -> str:
        """Increase rate limits."""
        log.warning("rate_limits_increased")
        return "Rate limits increased"

    def _ip_whitelist_review(self, alert: dict) -> str:
        """Review IP whitelist."""
        log.warning("ip_whitelist_review_triggered")
        return "IP whitelist review triggered"

    def _incident_response_protocol(self, alert: dict) -> str:
        """Follow incident response protocol."""
        log.warning("incident_response_protocol_initiated")
        return "Incident response protocol initiated"

    def _forensic_analysis(self, alert: dict) -> str:
        """Perform forensic analysis."""
        log.warning("forensic_analysis_initiated")
        return "Forensic analysis initiated"

    def _containment_procedure(self, alert: dict) -> str:
        """Implement containment procedure."""
        log.warning("containment_procedure_initiated")
        return "Containment procedure initiated"

    def _attack_mitigation(self, alert: dict) -> str:
        """Implement attack mitigation."""
        log.warning("attack_mitigation_initiated")
        return "Attack mitigation initiated"

    def _auth_system_audit(self, alert: dict) -> str:
        """Perform authentication system audit."""
        log.warning("auth_system_audit_initiated")
        return "Authentication system audit initiated"

    def get_active_alerts(self) -> list[dict]:
        """Get all active security alerts."""
        with self._lock:
            return [
                alert for alert in self._active_alerts.values()
                if alert["status"] == "active"
            ]

    def get_alert_history(self, hours: int = 24) -> list[dict]:
        """Get alert history for specified time period."""
        with self._lock:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            return [
                alert for alert in self._alert_history
                if alert["timestamp"] >= cutoff_time
            ]

    def get_alert_statistics(self) -> dict[str, Any]:
        """Get alert statistics and summary."""
        with self._lock:
            now = datetime.now(timezone.utc)
            
            # Calculate alert rates
            recent_alerts = [
                alert for alert in self._alert_history
                if now - alert["timestamp"] <= timedelta(hours=1)
            ]
            
            # Count by severity
            severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
            for alert in recent_alerts:
                severity_counts[alert["severity"]] = severity_counts.get(alert["severity"], 0) + 1
            
            # Count by type
            type_counts = {}
            for alert in recent_alerts:
                alert_type = alert["type"]
                type_counts[alert_type] = type_counts.get(alert_type, 0) + 1
            
            return {
                "total_alerts": len(self._alert_history),
                "active_alerts": len(self._active_alerts),
                "alert_rate_last_hour": len(recent_alerts),
                "severity_distribution": severity_counts,
                "top_alert_types": sorted(type_counts.items(), key=lambda x: x[1], reverse=True)[:5],
                "escalation_rate": sum(
                    1 for alert in self._alert_history 
                    if alert.get("escalation_triggered", False)
                ) / max(1, len(self._alert_history)) * 100
            }

    @api_rate_limit("detect_suspicious_activity")
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
                        "entity": self._sanitize_input(username, field_name="suspicious_username"),
                        "entity_type": "username",
                        "count": len(recent),
                        "window": str(self.SUSPICIOUS_WINDOW),
                        "severity": "high"
                    })
            
            for ip, timestamps in self._ip_failed_logins.items():
                recent = [ts for ts in timestamps if now - ts <= self.SUSPICIOUS_WINDOW]
                if len(recent) >= self.BRUTE_FORCE_THRESHOLD * 2:
                    # Partially redact IP for display
                    redacted_ip = self._sanitize_input(ip, field_name="suspicious_ip")
                    if '.' in redacted_ip:
                        parts = redacted_ip.split('.')
                        if len(parts) >= 4:
                            redacted_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
                    
                    suspicious_events.append({
                        "type": "repeated_failed_logins",
                        "entity": redacted_ip,
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
                    "severity": "high"
                })
                
                # Trigger alert for high failure rate
                self._trigger_security_alert(
                    alert_type="high_auth_failure_rate",
                    severity="high",
                    message=f"High authentication failure rate detected: {len(recent_auth_failures)} failures in 1 minute",
                    data={
                        "failure_count": len(recent_auth_failures),
                        "window": "1 minute"
                    }
                )
            
            # Check for critical failure rate
            if len(recent_auth_failures) > self._alert_thresholds["failed_login_rate_critical"]:
                self._trigger_security_alert(
                    alert_type="auth_failure_critical",
                    severity="critical",
                    message=f"CRITICAL: Very high authentication failure rate: {len(recent_auth_failures)} failures in 1 minute",
                    data={
                        "failure_count": len(recent_auth_failures),
                        "window": "1 minute"
                    }
                )
            
            # Check for success rate drop
            recent_auth_total = len(recent_auth_failures) + len([
                e for e in self._auth_events
                if e.event_type == SecurityEvent.AUTH_SUCCESS
                and now - e.timestamp <= timedelta(minutes=1)
            ])
            
            if recent_auth_total > 0:
                success_rate = len([
                    e for e in self._auth_events
                    if e.event_type == SecurityEvent.AUTH_SUCCESS
                    and now - e.timestamp <= timedelta(minutes=1)
                ]) / recent_auth_total
                
                if success_rate < self._alert_thresholds["success_rate_drop"]:
                    self._trigger_security_alert(
                        alert_type="success_rate_drop",
                        severity="high",
                        message=f"Authentication success rate dropped to {success_rate:.1%}",
                        data={
                            "success_rate": success_rate,
                            "total_attempts": recent_auth_total,
                            "successful_attempts": int(recent_auth_total * success_rate)
                        }
                    )
                
                if success_rate < self._alert_thresholds["success_rate_drop_critical"]:
                    self._trigger_security_alert(
                        alert_type="success_rate_drop_critical",
                        severity="critical",
                        message=f"CRITICAL: Authentication success rate critically low: {success_rate:.1%}",
                        data={
                            "success_rate": success_rate,
                            "total_attempts": recent_auth_total,
                            "successful_attempts": int(recent_auth_total * success_rate)
                        }
                    )
            
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
                
                # Trigger alert for high authz failure rate
                self._trigger_security_alert(
                    alert_type="high_authz_failure_rate",
                    severity="high",
                    message=f"High authorization failure rate detected: {len(recent_authz_failures)} failures in 1 minute",
                    data={
                        "failure_count": len(recent_authz_failures),
                        "window": "1 minute"
                    }
                )
            
            # Check for critical authz failure rate
            if len(recent_authz_failures) > self._alert_thresholds["authz_failures_critical"]:
                self._trigger_security_alert(
                    alert_type="authz_failure_critical",
                    severity="critical",
                    message=f"CRITICAL: Very high authorization failure rate: {len(recent_authz_failures)} failures in 1 minute",
                    data={
                        "failure_count": len(recent_authz_failures),
                        "window": "1 minute"
                    }
                )
            
            # Check for privilege escalation attempts
            privilege_escalation_attempts = [
                e for e in recent_authz_failures
                if self._is_privilege_escalation(e)
            ]
            
            if len(privilege_escalation_attempts) > self._alert_thresholds["privilege_escalation_attempts"]:
                suspicious_events.append({
                    "type": "privilege_escalation_attempt",
                    "count": len(privilege_escalation_attempts),
                    "window": "1 minute",
                    "severity": "high"
                })
                
                self._trigger_security_alert(
                    alert_type="privilege_escalation",
                    severity="high",
                    message=f"Privilege escalation attempt detected: {len(privilege_escalation_attempts)} attempts in 1 minute",
                    data={
                        "attempt_count": len(privilege_escalation_attempts),
                        "failed_attempts": len(privilege_escalation_attempts),
                        "window": "1 minute"
                    }
                )
            
            if len(privilege_escalation_attempts) > self._alert_thresholds["privilege_escalation_attempts_critical"]:
                self._trigger_security_alert(
                    alert_type="privilege_escalation_critical",
                    severity="critical",
                    message=f"CRITICAL: Multiple privilege escalation attempts detected: {len(privilege_escalation_attempts)} attempts in 1 minute",
                    data={
                        "attempt_count": len(privilege_escalation_attempts),
                        "failed_attempts": len(privilege_escalation_attempts),
                        "window": "1 minute"
                    }
                )
            
            return suspicious_events

    # =========================================================================
    # Security Metrics
    # =========================================================================
    
    @api_rate_limit("get_security_metrics")
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
            
            # Create metrics - defensive copy and validation
            total_auth_attempts = max(0, self._metrics["total_auth_attempts"])
            successful_attempts = max(0, min(total_auth_attempts, self._metrics["successful_auth_attempts"]))
            failed_attempts = max(0, min(total_auth_attempts - successful_attempts, self._metrics["failed_auth_attempts"]))
            
            total_authz_checks = max(0, self._metrics["total_authz_checks"])
            failed_authz_checks = max(0, min(total_authz_checks, self._metrics["failed_authz_checks"]))
            
            # Create metrics
            metrics = {
                "authentication": {
                    "total_attempts": total_auth_attempts,
                    "successful_attempts": successful_attempts,
                    "failed_attempts": failed_attempts,
                    "success_rate": self._calculate_rate(successful_attempts, total_auth_attempts),
                    "recent_success_rate": self._calculate_rate(recent_success, len(recent_auth_events)),
                    "recent_failure_rate": self._calculate_rate(recent_failure, len(recent_auth_events)),
                },
                "authorization": {
                    "total_checks": total_authz_checks,
                    "failed_checks": failed_authz_checks,
                    "success_rate": self._calculate_rate(total_authz_checks - failed_authz_checks, total_authz_checks),
                    "recent_success_rate": self._calculate_rate(recent_authz_success, len(recent_authz_events)),
                    "recent_failure_rate": self._calculate_rate(recent_authz_failure, len(recent_authz_events)),
                },
                "suspicious_activity": {
                    "total_events": max(0, self._metrics["suspicious_events"]),
                    "brute_force_detected": max(0, self._metrics["security_violations"]),
                    "recent_events": len(self.detect_suspicious_activity()),
                },
                "rate_limiting": {
                    "current_requests": self._rate_limiter.current_count,
                    "max_requests": self.RATE_LIMIT_MAX_REQUESTS,
                    "warning_threshold": self.RATE_LIMIT_WARNING_THRESHOLD,
                    "token_requests": max(0, self._metrics["csrf_token_requests"]),
                    "warning_count": max(0, self._metrics["csrf_token_warnings"]),
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
                    "duration_seconds": max(0, (now - self._init_time).total_seconds()),
                },
                "api_rate_limiting": {
                    "window_seconds": self.API_RATE_LIMIT_WINDOW.total_seconds(),
                    "max_requests": self.API_RATE_LIMIT_MAX_REQUESTS,
                    "current_count": self._api_rate_limiter.current_count,
                }
            }
            
            return metrics

    def _calculate_rate(self, numerator: int, denominator: int) -> float:
        """Calculate a rate safely."""
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)
    
    def _sanitize_input(self, value: str | None, max_length: int = 1000, field_name: str = "input") -> str:
        """
        Sanitize input values to prevent injection and information disclosure.
        
        Args:
            value: Input value to sanitize
            max_length: Maximum allowed length
            field_name: Name of the field for logging purposes
            
        Returns:
            Sanitized string
        """
        if value is None:
            return ""
        
        # Convert to string and truncate
        str_value = str(value)
        if len(str_value) > max_length:
            str_value = str_value[:max_length]
        
        # Remove control characters except allowed whitespace (tab, newline, carriage return)
        sanitized = []
        for char in str_value:
            if char.isprintable() or char in ('\t', '\n', '\r'):
                sanitized.append(char)
            else:
                # Replace control characters with placeholder
                sanitized.append('?')
        
        result = ''.join(sanitized)
        
        # Log sanitization events if significant changes were made
        if len(result) != len(str_value):
            log.warning("input_sanitized", field=field_name, original_length=len(str_value), sanitized_length=len(result))
        
        return result
    
    def _redact_sensitive_data(self, data: dict[str, Any], redact_fields: list[str] = None) -> dict[str, Any]:
        """
        Redact sensitive information from data before logging.
        
        Args:
            data: Data dictionary to redact
            redact_fields: List of field names to redact (default: sensitive fields)
            
        Returns:
            Redacted data dictionary
        """
        if redact_fields is None:
            redact_fields = ['password', 'token', 'secret', 'key', 'authorization', 'cookie']
        
        redacted = data.copy()
        
        for field in redact_fields:
            if field in redacted:
                if redacted[field] is not None:
                    # Log redaction
                    log.debug("sensitive_data_redacted", field=field, length=len(str(redacted[field])))
                redacted[field] = "***REDACTED***"
        
        # Redact IP addresses partially
        if 'ip_address' in redacted and redacted['ip_address']:
            ip = redacted['ip_address']
            if isinstance(ip, str) and '.' in ip:
                parts = ip.split('.')
                if len(parts) >= 4:
                    redacted['ip_address'] = f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
        
        return redacted
    
    def _redact_ip_address(self, ip: str | None) -> str | None:
        """Partially redact an IP address for display purposes."""
        if not ip:
            return None
        
        ip = self._sanitize_input(ip, field_name="ip_address")
        if '.' in ip:
            parts = ip.split('.')
            if len(parts) >= 4:
                return f"{parts[0]}.{parts[1]}.{parts[2]}.xxx"
        
        return ip

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

    @api_rate_limit("generate_csrf_token")
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

    @api_rate_limit("validate_csrf_token")
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
    
    @api_rate_limit("get_security_dashboard_data")
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
                    "username": self._sanitize_input(e.username, field_name="dashboard_username"),
                    "ip_address": self._redact_ip_address(e.ip_address),
                    "success": e.success,
                    "reason": self._sanitize_input(e.reason, field_name="dashboard_reason")
                }
                for e in self._auth_events
                if now - e.timestamp <= timedelta(hours=1)
            ][:50]  # Last 50 events
            
            recent_authz_events = [
                {
                    "timestamp": e.timestamp.isoformat(),
                    "type": e.event_type.value,
                    "username": self._sanitize_input(e.username, field_name="dashboard_username") if e.username else None,
                    "resource": self._sanitize_input(e.resource, field_name="dashboard_resource"),
                    "action": self._sanitize_input(e.action, field_name="dashboard_action"),
                    "allowed": e.allowed,
                    "reason": self._sanitize_input(e.reason, field_name="dashboard_reason")
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

    def _is_privilege_escalation(self, authz_event: AuthzEvent) -> bool:
        """Check if an authorization failure indicates privilege escalation attempt."""
        # Check for common privilege escalation patterns
        sensitive_resources = [
            "/admin",
            "/api/admin",
            "/system",
            "/config",
            "/users",
            "/roles",
            "/permissions",
            "/audit",
            "/logs"
        ]
        
        sensitive_actions = [
            "create",
            "delete",
            "modify",
            "update",
            "administer",
            "configure",
            "manage"
        ]
        
        # Check if accessing sensitive resource with sensitive action
        if authz_event.resource in sensitive_resources:
            return authz_event.action.lower() in [a.lower() for a in sensitive_actions]
        
        # Check for user-to-admin privilege escalation
        if authz_event.metadata.get("user_role") == "user" and authz_event.metadata.get("required_role") == "admin":
            return True
        
        # Check for unauthorized access to data
        if "data" in authz_event.resource and authz_event.action not in ["read", "view"]:
            return True
        
        return False

    # =========================================================================
    # Security Audit
    # =========================================================================
    
    @api_rate_limit("perform_security_audit")
    def perform_security_audit(self) -> dict[str, Any]:
        """Perform a comprehensive security audit."""
        self.log_security_event(
            event_type=SecurityEvent.SECURITY_AUDIT_STARTED,
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
            event_type=SecurityEvent.SECURITY_AUDIT_COMPLETED,
            message=f"Security audit completed with score {audit_results['summary']['score']}",
            audit_results=self._redact_sensitive_data(audit_results)
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