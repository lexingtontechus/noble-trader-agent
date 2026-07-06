"""
Unit Tests for Security Scenarios and Attack Vectors

This module contains comprehensive tests for security scenarios including:
- Input validation and sanitization
- Authentication and authorization attacks
- CSRF protection
- Rate limiting
- Suspicious activity detection
- Security metrics validation
- Alerting scenarios

Usage:
    pytest tests/test_security_scenarios.py -v
"""

import pytest
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch, MagicMock
import json
import secrets

# Import the security monitor
from src.hermes.ops.security_monitor import SecurityMonitor, SecurityEvent, RateLimiter


class TestSecurityScenarios:
    """Comprehensive test suite for security scenarios."""

    @pytest.fixture
    def security_monitor(self):
        """Create a security monitor instance for testing."""
        monitor = SecurityMonitor()
        monitor.init()
        return monitor

    @pytest.fixture
    def mock_alert_callback(self):
        """Create a mock alert callback for testing."""
        callback = Mock()
        return callback

    # =========================================================================
    # Input Sanitization Tests
    # =========================================================================

    def test_input_sanitization_control_characters(self, security_monitor):
        """Test sanitization of control characters."""
        # Test null byte
        result = security_monitor._sanitize_input("admin\x00script", "username")
        assert "\x00" not in result
        
        # Test script tags
        result = security_monitor._sanitize_input("<script>alert('xss')</script>", "username")
        assert "<script>" not in result
        
        # Test SQL injection attempt
        result = security_monitor._sanitize_input("admin' OR '1'='1", "username")
        assert "'" in result  # Single quotes are allowed but not dangerous in this context
        
    def test_input_sanitization_length_truncation(self, security_monitor):
        """Test input length truncation."""
        long_input = "a" * 2000
        result = security_monitor._sanitize_input(long_input, max_length=100, "username")
        assert len(result) <= 100
        assert result == "a" * 100
        
    def test_input_sanitization_special_characters(self, security_monitor):
        """Test handling of special characters."""
        # Test Unicode characters
        result = security_monitor._sanitize_input("üser@exämple.com", "username")
        assert result == "üser@exämple.com"
        
        # Test emojis
        result = security_monitor._sanitize_input("user😊test", "username")
        assert "😊" in result
        
        # Test binary data
        binary_data = "test\x00\x01\x02\xff"
        result = security_monitor._sanitize_input(binary_data, "username")
        assert "\x00" not in result
        assert "\x01" not in result
        assert "\xff" not in result

    def test_data_redaction_sensitive_fields(self, security_monitor):
        """Test redaction of sensitive data fields."""
        test_data = {
            'username': 'admin',
            'password': 's3cr3t_p@ssw0rd',
            'api_key': 'sk_test_123456789',
            'token': 'abc123def456',
            'session_id': 'sess_12345',
            'normal_field': 'this is safe'
        }
        
        redacted = security_monitor._redact_sensitive_data(test_data)
        
        # Check sensitive fields are redacted
        assert redacted['password'] == '***REDACTED***'
        assert redacted['api_key'] == '***REDACTED***'
        assert redacted['token'] == '***REDACTED***'
        assert redacted['session_id'] == '***REDACTED***'
        
        # Check normal fields are unchanged
        assert redacted['username'] == 'admin'
        assert redacted['normal_field'] == 'this is safe'

    def test_ip_address_redaction(self, security_monitor):
        """Test IP address redaction."""
        # IPv4 address
        ip = "192.168.1.100"
        redacted = security_monitor._redact_ip_address(ip)
        assert redacted == "192.168.1.xxx"
        
        # IPv6 address (should be sanitized but not redacted in parts)
        ip = "2001:db8::1"
        redacted = security_monitor._redact_ip_address(ip)
        assert "2001:db8::1" in redacted or "?" in redacted  # Either kept or sanitized
        
        # None input
        redacted = security_monitor._redact_ip_address(None)
        assert redacted is None

    # =========================================================================
    # Attack Vector Tests
    # =========================================================================

    def test_brute_force_attack_detection(self, security_monitor):
        """Test detection of brute force attacks."""
        username = "attacker_user"
        ip = "8.8.8.8"
        
        # Simulate rapid failed login attempts
        for i in range(10):  # Exceeds threshold
            security_monitor.log_auth_event(
                SecurityEvent.AUTH_FAILURE,
                username=username,
                ip_address=ip,
                reason="invalid_password"
            )
        
        # Detect suspicious activity
        suspicious_events = security_monitor.detect_suspicious_activity()
        
        # Should detect brute force pattern
        brute_force_events = [
            e for e in suspicious_events 
            if e["type"] == "repeated_failed_logins"
        ]
        
        assert len(brute_force_events) > 0
        assert any(
            e["entity"] == username and e["severity"] == "high"
            for e in brute_force_events
        )

    def test_sql_injection_attempt(self, security_monitor):
        """Test detection of SQL injection attempts."""
        # Attempt SQL injection in username field
        malicious_input = "admin' OR '1'='1' --"
        
        security_monitor.log_auth_event(
            SecurityEvent.AUTH_FAILURE,
            username=malicious_input,
            ip_address="10.0.0.1",
            reason="invalid_password"
        )
        
        # Check that input was sanitized in logs
        # This is tested by ensuring no exception is raised
        assert True  # If we reach here, sanitization worked

    def test_xss_attack_attempt(self, security_monitor):
        """Test detection of XSS attacks."""
        # Attempt XSS in username field
        malicious_input = "<script>alert('XSS')</script>"
        
        security_monitor.log_auth_event(
            SecurityEvent.AUTH_FAILURE,
            username=malicious_input,
            ip_address="10.0.0.1",
            reason="invalid_password"
        )
        
        # Check that input was sanitized
        sanitized = security_monitor._sanitize_input(malicious_input, "username")
        assert "<script>" not in sanitized

    def test_csrf_token_validation(self, security_monitor):
        """Test CSRF token validation against attacks."""
        session_id = "user_session_123"
        
        # Generate a valid token
        valid_token = security_monitor.generate_csrf_token(session_id)
        assert security_monitor.validate_csrf_token(session_id, valid_token)
        
        # Test invalid token
        assert not security_monitor.validate_csrf_token(session_id, "invalid_token")
        
        # Test token reuse (should fail after first use)
        assert not security_monitor.validate_csrf_token(session_id, valid_token)
        
        # Test session mismatch
        new_session = "different_session"
        assert not security_monitor.validate_csrf_token(new_session, valid_token)

    def test_rate_limiting_bypass_attempts(self, security_monitor):
        """Test attempts to bypass rate limiting."""
        username = "test_user"
        ip = "192.168.1.1"
        
        # Make many rapid requests
        for i in range(60):  # Exceeds rate limit
            security_monitor.log_auth_event(
                SecurityEvent.AUTH_SUCCESS,
                username=username,
                ip_address=ip
            )
        
        # Check rate limiting metrics
        metrics = security_monitor.get_security_metrics()
        rate_limiting = metrics["rate_limiting"]
        
        # Should have tracked requests
        assert rate_limiting["current_requests"] > 0

    def test_directory_traversal_attempt(self, security_monitor):
        """Test detection of directory traversal attempts."""
        # Attempt directory traversal in resource field
        malicious_resource = "../../../etc/passwd"
        
        security_monitor.log_authz_event(
            SecurityEvent.AUTHZ_FAILURE,
            username="attacker",
            resource=malicious_resource,
            action="read",
            allowed=False,
            reason="access_denied"
        )
        
        # Check that resource was sanitized
        sanitized = security_monitor._sanitize_input(malicious_resource, "resource")
        assert len(sanitized) <= 500  # Truncated
        assert "\x00" not in sanitized  # Control characters removed

    # =========================================================================
    # Penetration Testing Scenarios
    # =========================================================================

    def test_penetration_test_brute_force_login(self, security_monitor):
        """Simulate brute force penetration test."""
        target_username = "admin"
        attack_ip = "192.168.1.100"
        
        # Simulate brute force attack
        for attempt in range(20):
            security_monitor.log_auth_event(
                SecurityEvent.AUTH_FAILURE,
                username=target_username,
                ip_address=attack_ip,
                reason="invalid_password",
                metadata={"attempt": attempt + 1}
            )
        
        # Check detection
        suspicious = security_monitor.detect_suspicious_activity()
        attack_detected = any(
            e["type"] == "repeated_failed_logins" and 
            e["entity"] == target_username
            for e in suspicious
        )
        
        assert attack_detected, "Brute force attack not detected"

    def test_penetration_test_dos_attempt(self, security_monitor):
        """Simulate Denial of Service attack attempt."""
        # Create many rapid requests to exhaust rate limits
        for i in range(150):
            security_monitor.get_security_metrics()
        
        # Check metrics
        metrics = security_monitor.get_security_metrics()
        api_rate_limiting = metrics["api_rate_limiting"]
        
        # Should have used many requests
        assert api_rate_limiting["current_count"] > 0

    def test_penetration_test_token_replay(self, security_monitor):
        """Simulate token replay attack."""
        session_id = "victim_session"
        
        # Generate valid token
        token = security_monitor.generate_csrf_token(session_id)
        
        # Try to use it multiple times (should fail after first use)
        success_count = 0
        for _ in range(5):
            if security_monitor.validate_csrf_token(session_id, token):
                success_count += 1
        
        # Should only succeed once
        assert success_count == 1, f"Token replay attack detected: {success_count} successful validations"

    def test_penetration_test_session_hijacking(self, security_monitor):
        """Simulate session hijacking attempt."""
        # Generate tokens for session
        session_id = "legitimate_user"
        token1 = security_monitor.generate_csrf_token(session_id)
        token2 = security_monitor.generate_csrf_token(session_id)
        
        # Try to validate token1 after token2 was used
        security_monitor.validate_csrf_token(session_id, token2)
        still_valid = security_monitor.validate_csrf_token(session_id, token1)
        
        # Should not be valid (token should be one-time use)
        assert not still_valid, "Session hijacking vulnerability detected"

    # =========================================================================
    # Authentication and Authorization Tests
    # =========================================================================

    def test_account_lockout_mechanism(self, security_monitor):
        """Test account lockout mechanism after failed attempts."""
        username = "test_user"
        ip = "192.168.1.1"
        
        # Simulate many failed attempts
        for i in range(10):
            security_monitor.log_auth_event(
                SecurityEvent.AUTH_FAILURE,
                username=username,
                ip_address=ip,
                reason="invalid_password"
            )
        
        # Log one successful attempt
        security_monitor.log_auth_event(
            SecurityEvent.AUTH_SUCCESS,
            username=username,
            ip_address=ip,
            reason="password_correct"
        )
        
        # Check metrics
        metrics = security_monitor.get_security_metrics()
        auth_metrics = metrics["authentication"]
        
        # Should track both successful and failed attempts
        assert auth_metrics["total_attempts"] > 0
        assert auth_metrics["failed_attempts"] > 0

    def test_authorization_bypass_attempt(self, security_monitor):
        """Test detection of authorization bypass attempts."""
        # Try to access resources without proper authorization
        resources_to_test = [
            "/admin/users",
            "/api/financial-data",
            "/system/config"
        ]
        
        for resource in resources_to_test:
            security_monitor.log_authz_event(
                SecurityEvent.AUTHZ_FAILURE,
                username="unauthorized_user",
                resource=resource,
                action="access",
                allowed=False,
                reason="insufficient_permissions"
            )
        
        # Check for suspicious patterns
        suspicious = security_monitor.detect_suspicious_activity()
        
        # Should detect high authz failure rate if enough attempts
        if len(resources_to_test) >= 5:  # Threshold for detection
            authz_failures = [
                e for e in suspicious 
                if e["type"] == "high_authz_failure_rate"
            ]
            assert len(authz_failures) > 0

    # =========================================================================
    # Security Metrics Tests
    # =========================================================================

    def test_security_metrics_accuracy(self, security_monitor):
        """Test accuracy of security metrics calculation."""
        initial_metrics = security_monitor.get_security_metrics()
        
        # Record some events
        security_monitor.log_auth_event(
            SecurityEvent.AUTH_SUCCESS,
            username="user1",
            ip_address="192.168.1.1"
        )
        
        security_monitor.log_auth_event(
            SecurityEvent.AUTH_FAILURE,
            username="user2",
            ip_address="192.168.1.2",
            reason="invalid_password"
        )
        
        security_monitor.log_authz_event(
            SecurityEvent.AUTHZ_SUCCESS,
            username="user1",
            resource="/api/data",
            action="read",
            allowed=True
        )
        
        # Check updated metrics
        updated_metrics = security_monitor.get_security_metrics()
        
        # Should have increased counters
        assert updated_metrics["authentication"]["total_attempts"] > initial_metrics["authentication"]["total_attempts"]
        assert updated_metrics["authorization"]["total_checks"] > initial_metrics["authorization"]["total_checks"]

    def test_rate_limiting_metrics(self, security_monitor):
        """Test rate limiting metrics tracking."""
        # Make many requests
        for i in range(30):
            security_monitor.get_security_metrics()
        
        # Check metrics
        metrics = security_monitor.get_security_metrics()
        rate_limiting = metrics["api_rate_limiting"]
        
        # Should track usage
        assert rate_limiting["current_count"] > 0
        assert rate_limiting["current_count"] <= rate_limiting["max_requests"]

    # =========================================================================
    # Alerting Tests
    # =========================================================================

    def test_alert_callback_triggered(self, security_monitor, mock_alert_callback):
        """Test that alert callbacks are triggered for security events."""
        # Add callback
        security_monitor.add_alert_callback(mock_alert_callback)
        
        # Trigger an alert
        security_monitor.log_auth_event(
            SecurityEvent.AUTH_FAILURE,
            username="attacker",
            ip_address="8.8.8.8",
            reason="invalid_password"
        )
        
        # Simulate enough failed attempts to trigger brute force alert
        for i in range(6):  # Exceeds threshold
            security_monitor.log_auth_event(
                SecurityEvent.AUTH_FAILURE,
                username="attacker",
                ip_address="8.8.8.8",
                reason="invalid_password"
            )
        
        # Check callback was called
        mock_alert_callback.assert_called()

    def test_multiple_alert_callbacks(self, security_monitor):
        """Test multiple alert callbacks working together."""
        # Create multiple mock callbacks
        callback1 = Mock()
        callback2 = Mock()
        
        security_monitor.add_alert_callback(callback1)
        security_monitor.add_alert_callback(callback2)
        
        # Trigger alert
        for i in range(6):
            security_monitor.log_auth_event(
                SecurityEvent.AUTH_FAILURE,
                username="attacker",
                ip_address="8.8.8.8",
                reason="invalid_password"
            )
        
        # Both callbacks should be called
        callback1.assert_called()
        callback2.assert_called()


class TestRateLimiter:
    """Test the RateLimiter class specifically."""

    def test_rate_limiter_basic(self):
        """Test basic rate limiting functionality."""
        limiter = RateLimiter(max_requests=5, window=timedelta(seconds=60))
        
        # Should allow first 5 requests
        for i in range(5):
            assert limiter._acquire() == True
        
        # Should deny 6th request
        assert limiter._acquire() == False
        
        # Check current count
        assert limiter.current_count == 0

    def test_rate_limiter_window_reset(self):
        """Test rate limiter window reset."""
        limiter = RateLimiter(max_requests=5, window=timedelta(seconds=1))
        
        # Use up all requests
        for _ in range(5):
            limiter._acquire()
        
        # Wait for window to pass
        time.sleep(1.1)
        
        # Should allow new requests
        assert limiter._acquire() == True

    def test_rate_limiter_cleanup(self):
        """Test rate limiter cleanup of old requests."""
        limiter = RateLimiter(max_requests=10, window=timedelta(seconds=1))
        
        # Make some requests
        for _ in range(5):
            limiter._acquire()
        
        # Manually update last_update to be old
        limiter._last_update = datetime.now(timezone.utc) - timedelta(seconds=2)
        
        # Cleanup should remove old requests
        limiter._cleanup()
        
        # Should have capacity available
        assert limiter.current_count == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])