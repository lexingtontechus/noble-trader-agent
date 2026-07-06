"""
Automated Security Tests for Hermes Trading Platform

Run with: python -m pytest tests/test_security.py -v
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

# Import the modules we're testing
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from hermes.web.app import _get_auth_settings
from hermes.agent.decision_tree import HermesDecisionTree
from hermes.portfolio.state import PortfolioStateService, PortfolioPosition
from hermes.portfolio.cb_manager import CircuitBreakerManager
from hermes.execution.slippage import SlippageModeler
from hermes.ops.security_monitor import SecurityMonitor, SecurityEvent, RateLimiter


class TestAuthenticationSecurity:
    """Test authentication and authorization security."""

    def test_auth_settings_requires_username(self):
        """Test that auth settings validation requires admin username."""
        with patch('hermes.web.app.get_config') as mock_get_config:
            mock_get_config.return_value = MagicMock(
                auth={},  # Empty auth config
                log_level='INFO',
                environment='test',
                portfolio=MagicMock(initial_symbols=[]),
                venues={},
                upstream={},
                data_sources={},
                account={},
                asset={},
                signal={},
                entry={},
                execution={},
                position_management={},
                circuit_breakers={},
                autonomy={},
                meta_regime={},
                renko={},
                duckdb={},
                hermes_redis={},
                notifications={},
                logging={}
            )
            
            with patch('hermes.web.app.get_secret_or_none') as mock_secret:
                mock_secret.return_value = None
                
                with pytest.raises(RuntimeError) as exc_info:
                    _get_auth_settings()
                
                assert "HERMES_ADMIN_USERNAME must be configured" in str(exc_info.value)

    def test_auth_settings_requires_password(self):
        """Test that auth settings validation requires admin password."""
        with patch('hermes.web.app.get_config') as mock_get_config:
            mock_get_config.return_value = MagicMock(
                auth={'admin_username': 'testuser'},
                log_level='INFO',
                environment='test',
                portfolio=MagicMock(initial_symbols=[]),
                venues={},
                upstream={},
                data_sources={},
                account={},
                asset={},
                signal={},
                entry={},
                execution={},
                position_management={},
                circuit_breakers={},
                autonomy={},
                meta_regime={},
                renko={},
                duckdb={},
                hermes_redis={},
                notifications={},
                logging={}
            )
            
            with patch('hermes.web.app.get_secret_or_none') as mock_secret:
                mock_secret.return_value = None
                
                with pytest.raises(RuntimeError) as exc_info:
                    _get_auth_settings()
                
                assert "HERMES_ADMIN_PASSWORD must be configured" in str(exc_info.value)

    def test_auth_settings_rejects_default_credentials(self):
        """Test that auth settings validation rejects default credentials."""
        with patch('hermes.web.app.get_config') as mock_get_config:
            mock_get_config.return_value = MagicMock(
                auth={
                    'admin_username': 'admin',
                    'admin_password': 'change-me'
                },
                log_level='INFO',
                environment='test',
                portfolio=MagicMock(initial_symbols=[]),
                venues={},
                upstream={},
                data_sources={},
                account={},
                asset={},
                signal={},
                entry={},
                execution={},
                position_management={},
                circuit_breakers={},
                autonomy={},
                meta_regime={},
                renko={},
                duckdb={},
                hermes_redis={},
                notifications={},
                logging={}
            )
            
            with patch('hermes.web.app.get_secret_or_none') as mock_secret:
                mock_secret.return_value = None
                
                with pytest.raises(RuntimeError) as exc_info:
                    _get_auth_settings()
                
                assert "Default credentials detected" in str(exc_info.value)

    def test_auth_settings_accepts_valid_credentials(self):
        """Test that auth settings validation accepts valid credentials."""
        with patch('hermes.web.app.get_config') as mock_get_config:
            mock_get_config.return_value = MagicMock(
                auth={
                    'admin_username': 'secure-user',
                    'admin_password': 'strong-password-123456',
                    'session_secret': 'valid-session-secret-1234567890abcdef',
                    'agent_token': 'valid-agent-token-1234567890abcdef'
                },
                log_level='INFO',
                environment='test',
                portfolio=MagicMock(initial_symbols=[]),
                venues={},
                upstream={},
                data_sources={},
                account={},
                asset={},
                signal={},
                entry={},
                execution={},
                position_management={},
                circuit_breakers={},
                autonomy={},
                meta_regime={},
                renko={},
                duckdb={},
                hermes_redis={},
                notifications={},
                logging={}
            )
            
            with patch('hermes.web.app.get_secret_or_none') as mock_secret:
                mock_secret.return_value = None
                
                # This should not raise an exception
                settings = _get_auth_settings()
                assert settings['admin_username'] == 'secure-user'
                assert settings['admin_password'] == 'strong-password-123456'


class TestDivisionByZeroProtection:
    """Test protection against division by zero."""

    def test_decision_tree_zero_risk_amount(self):
        """Test that decision tree handles zero risk_amount safely."""
        tree = HermesDecisionTree()
        
        position = PortfolioPosition(
            position_id='test-001',
            symbol='BTC/USD',
            venue='hyperliquid',
            direction='long',
            qty=1.0,
            entry_price=50000.0,
            current_price=50000.0,
            stop_price=49000.0,
            target_price=51000.0,
            opened_at=datetime.now(timezone.utc),
            risk_amount=0.0
        )
        
        decision = tree.evaluate_existing_position(
            position=position,
            signal=None,
            current_price=50000.0,
            adverse_brick_count=0
        )
        
        assert decision is not None
        assert decision.r_multiple == 0.0

    def test_decision_tree_negative_risk_amount(self):
        """Test that decision tree handles negative risk_amount safely."""
        tree = HermesDecisionTree()
        
        position = PortfolioPosition(
            position_id='test-002',
            symbol='BTC/USD',
            venue='hyperliquid',
            direction='long',
            qty=1.0,
            entry_price=50000.0,
            current_price=50000.0,
            stop_price=49000.0,
            target_price=51000.0,
            opened_at=datetime.now(timezone.utc),
            risk_amount=-100.0
        )
        
        decision = tree.evaluate_existing_position(
            position=position,
            signal=None,
            current_price=50000.0,
            adverse_brick_count=0
        )
        
        assert decision is not None
        assert decision.r_multiple == 0.0

    def test_slippage_calculator_zero_adv(self):
        """Test that slippage calculator handles zero ADV safely."""
        modeler = SlippageModeler()
        
        slip_bps = modeler.estimate_market_slippage_bps(
            order_size_usd=1000.0,
            annualized_vol=0.60,
            adv_usd=0.0
        )
        
        assert isinstance(slip_bps, float)
        assert slip_bps >= 0

    def test_portfolio_state_zero_equity(self):
        """Test that portfolio state handles zero equity safely."""
        state = PortfolioStateService(initial_equity=0.0)
        
        metrics = state.get_metrics()
        
        assert metrics.leverage_gross == 0.0
        assert metrics.leverage_net == 0.0
        assert metrics.drawdown_pct == 0.0


class TestThreadSafety:
    """Test thread safety of critical components."""

    def test_circuit_breaker_thread_safety(self):
        """Test that circuit breaker manager is thread-safe."""
        manager = CircuitBreakerManager.from_config()
        errors = []
        results = []
        
        def check_portfolio_exposure(thread_id):
            try:
                for i in range(100):
                    trips = manager.check_portfolio_exposure(
                        gross_exposure_usd=85000 + thread_id,
                        equity=100000.0
                    )
                    results.append((thread_id, len(trips)))
            except Exception as e:
                errors.append((thread_id, str(e)))
        
        threads = []
        for i in range(10):
            thread = threading.Thread(target=check_portfolio_exposure, args=(i,))
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        assert len(errors) == 0, f"Thread safety errors: {errors}"
        assert len(results) == 1000

    def test_circuit_breaker_concurrent_trips(self):
        """Test concurrent trip creation and checking."""
        manager = CircuitBreakerManager.from_config()
        errors = []
        
        def create_and_check_trips(thread_id):
            try:
                for i in range(50):
                    manager.check_portfolio_exposure(gross_exposure_usd=95000, equity=100000)
                    manager.check_daily_loss(daily_loss_usd=-15000)
                    
                    manager.is_any_tripped()
                    manager.get_active_trips()
                    manager.get_blocking_action()
                    manager.get_size_multiplier()
            except Exception as e:
                errors.append((thread_id, str(e)))

        threads = []
        for i in range(5):
            thread = threading.Thread(target=create_and_check_trips, args=(i,))
            threads.append(thread)
            thread.start()

        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Concurrent trip errors: {errors}"


class TestRateLimiting:
    """Test rate limiting functionality."""

    def test_rate_limiter_class_exists(self):
        """Test RateLimiter class exists."""
        assert hasattr(SecurityMonitor, 'RateLimiter')

    def test_security_monitor_rate_limit_config(self):
        """Test rate limiting configuration."""
        monitor = SecurityMonitor()

        assert hasattr(monitor, 'RATE_LIMIT_WINDOW')
        assert hasattr(monitor, 'RATE_LIMIT_MAX_REQUESTS')
        assert hasattr(monitor, 'RATE_LIMIT_WARNING_THRESHOLD')

        assert monitor.RATE_LIMIT_MAX_REQUESTS == 100
        assert monitor.RATE_LIMIT_WINDOW == timedelta(seconds=60)

    def test_security_monitor_rate_limiting(self):
        """Test security monitor rate limit tracking."""
        monitor = SecurityMonitor()

        # Simulate many auth failures (all should be tracked)
        for i in range(150):
            monitor.log_auth_failure(
                username="testuser",
                ip_address=f"192.168.1.{i % 255}",
                reason="invalid_password"
            )

        # Check metrics
        metrics = monitor.get_security_metrics()
        assert metrics["rate_limiting"]["csrf_token_requests"] == 150
        # Should have triggered warnings
        assert metrics["rate_limiting"]["warning_count"] >= 50


class TestCSRFProtection:
    """Test CSRF token protection."""

    def test_security_monitor_csrf_config(self):
        """Test CSRF configuration in SecurityMonitor."""
        monitor = SecurityMonitor()

        assert hasattr(monitor, 'CSRF_TOKEN_TTL')
        assert hasattr(monitor, 'CSRF_MAX_TOKENS_PER_SESSION')
        assert hasattr(monitor, 'CSRF_TOKEN_LENGTH')

        assert monitor.CSRF_TOKEN_TTL == timedelta(hours=1)
        assert monitor.CSRF_MAX_TOKENS_PER_SESSION == 10
        assert monitor.CSRF_TOKEN_LENGTH == 64

    def test_security_monitor_csrf_methods(self):
        """Test CSRF token management methods exist."""
        monitor = SecurityMonitor()

        assert hasattr(monitor, 'generate_csrf_token')
        assert hasattr(monitor, 'validate_csrf_token')
        assert hasattr(monitor, 'get_csrf_tokens_for_session')
        assert hasattr(monitor, 'cleanup_expired_csrf_tokens')

    def test_csrf_token_generation(self):
        """Test CSRF token generation."""
        monitor = SecurityMonitor()
        session_id = "test_session_123"

        token = monitor.generate_csrf_token(session_id)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) == 128  # 64 bytes = 128 hex characters

    def test_csrf_token_validation_valid(self):
        """Test valid CSRF token validation."""
        monitor = SecurityMonitor()
        session_id = "test_session_456"

        # Generate token
        token = monitor.generate_csrf_token(session_id)

        # Validate token
        assert monitor.validate_csrf_token(session_id, token) is True

    def test_csrf_token_validation_invalid(self):
        """Test invalid CSRF token validation."""
        monitor = SecurityMonitor()
        session_id = "test_session_789"

        # Generate token
        token = monitor.generate_csrf_token(session_id)

        # Validate with wrong session
        assert monitor.validate_csrf_token("wrong_session", token) is False

        # Validate with wrong token
        assert monitor.validate_csrf_token(session_id, "wrong_token") is False

        # Validate with empty token
        assert monitor.validate_csrf_token(session_id, "") is False

    def test_csrf_token_one_time_use(self):
        """Test CSRF token is consumed after validation."""
        monitor = SecurityMonitor()
        session_id = "test_session_abc"

        token = monitor.generate_csrf_token(session_id)

        # First validation should succeed
        assert monitor.validate_csrf_token(session_id, token) is True

        # Second validation should fail (token consumed)
        assert monitor.validate_csrf_token(session_id, token) is False

    def test_csrf_token_metrics(self):
        """Test CSRF token metrics tracking."""
        monitor = SecurityMonitor()

        # Generate tokens
        monitor.generate_csrf_token("session1")
        monitor.generate_csrf_token("session1")
        monitor.generate_csrf_token("session2")

        metrics = monitor.get_security_metrics()
        assert metrics["csrf_tokens"]["total_tokens"] == 3
        assert metrics["csrf_tokens"]["total_sessions"] == 2
        assert metrics["csrf_tokens"]["max_per_session"] == 10


class TestSecurityMetrics:
    """Test security metrics collection."""

    def test_security_monitor_initialization(self):
        """Test security monitor initialization."""
        monitor = SecurityMonitor()

        assert monitor._initialized is False
        assert len(monitor._auth_events) == 0
        assert len(monitor._authz_events) == 0
        assert len(monitor._security_events) == 0

    def test_security_metrics_empty_state(self):
        """Test security metrics with no events."""
        monitor = SecurityMonitor()
        metrics = monitor.get_security_metrics()

        assert metrics["authentication"]["total_attempts"] == 0
        assert metrics["authentication"]["successful_attempts"] == 0
        assert metrics["authentication"]["failed_attempts"] == 0
        assert metrics["authorization"]["total_checks"] == 0
        assert metrics["rate_limiting"]["current_requests"] == 100
        assert metrics["rate_limiting"]["max_requests"] == 100

    def test_security_metrics_auth_events(self):
        """Test security metrics with auth events."""
        monitor = SecurityMonitor()
        monitor.log_auth_success("admin", ip_address="127.0.0.1")
        monitor.log_auth_failure("admin", ip_address="127.0.0.1", reason="invalid")
        monitor.log_auth_failure("test", ip_address="127.0.0.2", reason="invalid")

        metrics = monitor.get_security_metrics()

        assert metrics["authentication"]["total_attempts"] == 3
        assert metrics["authentication"]["successful_attempts"] == 1
        assert metrics["authentication"]["failed_attempts"] == 2

    def test_security_metrics_authz_events(self):
        """Test security metrics with authz events."""
        monitor = SecurityMonitor()
        monitor.log_authz_success(None, "dashboard", "view")
        monitor.log_authz_success(None, "dashboard", "edit")
        monitor.log_authz_failure(None, "dashboard", "delete", reason="permission_denied")

        metrics = monitor.get_security_metrics()

        assert metrics["authorization"]["total_checks"] == 3
        assert metrics["authorization"]["failed_checks"] == 1

    def test_security_metrics_rate_limiting(self):
        """Test security metrics with rate limiting."""
        monitor = SecurityMonitor()

        # Simulate many requests
        for _ in range(150):
            monitor.log_auth_failure("admin", ip_address="127.0.0.1")

        metrics = monitor.get_security_metrics()

        assert metrics["rate_limiting"]["csrf_token_requests"] == 150
        assert metrics["rate_limiting"]["warning_count"] >= 50

    def test_security_metrics_uptime(self):
        """Test uptime calculation."""
        import time
        from datetime import datetime, timezone

        monitor = SecurityMonitor()
        time.sleep(0.1)  # Small delay

        metrics = monitor.get_security_metrics()

        assert "uptime" in metrics
        assert "since" in metrics["uptime"]
        assert "duration_seconds" in metrics["uptime"]

        # Should be approximately 0.1 seconds
        assert 0.05 < metrics["uptime"]["duration_seconds"] < 1.0


class TestSecurityMonitoringIntegration:
    """Integration tests for security monitoring system."""

    def test_full_security_workflow(self):
        """Test complete security monitoring workflow."""
        monitor = SecurityMonitor()
        monitor.init()

        # Track authentication events
        monitor.log_auth_success("admin", ip_address="127.0.0.1")
        monitor.log_auth_failure("admin", ip_address="127.0.0.1")
        monitor.log_auth_success("user", ip_address="192.168.1.1")

        # Track authorization events
        monitor.log_authz_success(None, "dashboard", "view")
        monitor.log_authz_failure(None, "dashboard", "delete")

        # Track CSRF tokens
        monitor.generate_csrf_token("session123")
        monitor.generate_csrf_token("session456")

        # Get metrics
        metrics = monitor.get_security_metrics()

        # Verify all components are tracked
        assert metrics["authentication"]["total_attempts"] == 3
        assert metrics["authentication"]["successful_attempts"] == 2
        assert metrics["authentication"]["failed_attempts"] == 1
        assert metrics["authorization"]["total_checks"] == 2
        assert metrics["rate_limiting"]["csrf_token_requests"] == 2
        assert metrics["csrf_tokens"]["total_tokens"] == 2

    def test_security_event_types(self):
        """Test all security event types are defined."""
        # Check that the enum has the required attributes (using uppercase names)
        assert hasattr(SecurityEvent, 'AUTH_SUCCESS')
        assert hasattr(SecurityEvent, 'AUTH_FAILURE')
        assert hasattr(SecurityEvent, 'AUTH_LOGOUT')
        assert hasattr(SecurityEvent, 'AUTH_SESSION_EXPIRED')
        assert hasattr(SecurityEvent, 'AUTH_SESSION_CREATED')
        assert hasattr(SecurityEvent, 'AUTHZ_SUCCESS')
        assert hasattr(SecurityEvent, 'AUTHZ_FAILURE')
        assert hasattr(SecurityEvent, 'RATE_LIMIT_HIT')
        assert hasattr(SecurityEvent, 'RATE_LIMIT_WARNING')
        assert hasattr(SecurityEvent, 'INPUT_VALIDATION_FAILURE')
        assert hasattr(SecurityEvent, 'INPUT_SANITIZATION')
        assert hasattr(SecurityEvent, 'SECURITY_CONFIG_CHANGE')
        assert hasattr(SecurityEvent, 'SECURITY_HEADER_MISSING')
        assert hasattr(SecurityEvent, 'SUSPICIOUS_LOGIN_ATTEMPT')
        assert hasattr(SecurityEvent, 'SUSPICIOUS_REQUEST_PATTERN')
        assert hasattr(SecurityEvent, 'BRUTE_FORCE_DETECTED')
        assert hasattr(SecurityEvent, 'SECURITY_MODULE_INITIALIZED')
        assert hasattr(SecurityEvent, 'SECURITY_AUDIT_STARTED')
        assert hasattr(SecurityEvent, 'SECURITY_AUDIT_COMPLETED')


if __name__ == "__main__":
    pytest.main([__file__, "-v"])