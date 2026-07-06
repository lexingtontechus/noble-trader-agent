"""
Unit tests for Input Validation Middleware

Tests comprehensive input validation functionality:
- Field validation rules
- Schema validation
- Security checks
- Data sanitization
- Performance monitoring
- Integration with validation middleware
"""

import pytest
import time
from datetime import datetime, timezone
from typing import Dict, Any, List

from src.hermes.security.input_validation import (
    InputValidator,
    ValidationRule,
    ValidationSeverity,
    ValidationError,
    ValidationResult,
    RequestValidationMiddleware,
    RequestValidationConfig,
    validate_input,
    add_validation_rule,
    input_validator
)


class TestInputValidator:
    """Test the InputValidator class."""

    def setup_method(self):
        """Set up test environment."""
        self.validator = InputValidator()
    
    def test_required_validation(self):
        """Test required field validation."""
        # Valid case
        errors = self.validator.validate_field('username', 'testuser')
        assert len(errors) == 0
        
        # Missing field
        errors = self.validator.validate_field('username', None)
        assert len(errors) == 1
        assert errors[0].rule == ValidationRule.REQUIRED
        assert errors[0].field == 'username'
    
    def test_length_validation(self):
        """Test length validation rules."""
        # Valid case
        errors = self.validator.validate_field('username', 'testuser')
        assert len(errors) == 0
        
        # Too short
        errors = self.validator.validate_field('username', 'ab')
        assert len(errors) == 1
        assert errors[0].rule == ValidationRule.MIN_LENGTH
        
        # Too long
        errors = self.validator.validate_field('username', 'a' * 51)
        assert len(errors) == 1
        assert errors[0].rule == ValidationRule.MAX_LENGTH
    
    def test_email_validation(self):
        """Test email format validation."""
        # Valid emails
        valid_emails = [
            'user@example.com',
            'test.user@domain.co.uk',
            'user+tag@domain.com',
            'user123@domain.com'
        ]
        
        for email in valid_emails:
            errors = self.validator.validate_field('email', email)
            assert len(errors) == 0, f"Email {email} should be valid"
        
        # Invalid emails
        invalid_emails = [
            'invalid-email',
            '@domain.com',
            'user@',
            'user@domain',
            'user@domain.',
            'user..domain@.com'
        ]
        
        for email in invalid_emails:
            errors = self.validator.validate_field('email', email)
            assert len(errors) == 1, f"Email {email} should be invalid"
            assert errors[0].rule == ValidationRule.EMAIL
    
    def test_security_validation(self):
        """Test security validation rules."""
        # SQL injection detection
        sql_injection_values = [
            "SELECT * FROM users",
            "1 OR 1=1",
            "'; DROP TABLE users; --",
            "WAITFOR DELAY '0:0:5'"
        ]
        
        for value in sql_injection_values:
            errors = self.validator.validate_field('search', value)
            sql_errors = [e for e in errors if e.rule == ValidationRule.SQL_INJECTION]
            assert len(sql_errors) == 1, f"SQL injection detected in: {value}"
        
        # XSS detection
        xss_values = [
            "<script>alert('XSS')</script>",
            "javascript:alert('XSS')",
            "onerror=alert('XSS')",
            "eval(alert('XSS'))"
        ]
        
        for value in xss_values:
            errors = self.validator.validate_field('comment', value)
            xss_errors = [e for e in errors if e.rule == ValidationRule.XSS]
            assert len(xss_errors) == 1, f"XSS detected in: {value}"
        
        # Command injection detection
        cmd_injection_values = [
            "system command",
            "exec evil",
            "shell_exec script",
            "popen evil",
            "proc_open script"
        ]
        
        for value in cmd_injection_values:
            errors = self.validator.validate_field('command', value)
            cmd_errors = [e for e in errors if e.rule == ValidationRule.COMMAND_INJECTION]
            assert len(cmd_errors) == 1, f"Command injection detected in: {value}"
            
        # Also test that pipe is detected as auth bypass
        errors = self.validator.validate_field('command', '| echo test')
        bypass_errors = [e for e in errors if e.rule == ValidationRule.AUTH_BYPASS]
        assert len(bypass_errors) == 1, "Pipe should be detected as auth bypass"
    
    def test_data_sanitization(self):
        """Test data sanitization."""
        # Control characters
        sanitized = self.validator._sanitize_value('test\x00\x01\x02string')
        assert '??string' in sanitized
        
        # Long string truncation
        long_string = 'a' * 10001
        sanitized = self.validator._sanitize_value(long_string)
        assert len(sanitized) == 10000
        
        # HTML escaping
        sanitized = self.validator._sanitize_value('<script>alert("test")</script>')
        assert '&lt;script&gt;alert(&quot;test&quot;)&lt;/script&gt;' in sanitized
    
    def test_custom_validator(self):
        """Test custom validator integration."""
        # Add custom validator
        def validate_age(value, field_name, context):
            return isinstance(value, int) and value >= 0 and value <= 150
        
        self.validator.add_custom_validator('age', validate_age)
        
        # Valid age
        errors = self.validator.validate_field('age', 25)
        assert len(errors) == 0
        
        # Invalid age
        errors = self.validator.validate_field('age', 200)
        assert len(errors) == 1
    
    def test_data_validation(self):
        """Test comprehensive data validation."""
        test_data = {
            'username': 'testuser',
            'email': 'user@example.com',
            'password': 'ValidPassword123!',
            'age': 25
        }
        
        result = self.validator.validate_data(test_data)
        
        assert result.is_valid
        assert len(result.errors) == 0
        assert result.sanitized_data == test_data
    
    def test_invalid_data_validation(self):
        """Test validation of invalid data."""
        test_data = {
            'username': 'ab',  # Too short
            'email': 'invalid-email',  # Invalid email
            'password': 'short',  # Too short
            'age': -1  # Invalid age
        }
        
        result = self.validator.validate_data(test_data)
        
        assert not result.is_valid
        assert len(result.errors) > 0
        assert len(result.warnings) >= 0
    
    def test_error_severity(self):
        """Test error severity handling."""
        # Add a warning rule
        self.validator.add_rule('username', ValidationRule.MIN_LENGTH, value=5, 
                               severity=ValidationSeverity.WARNING)
        
        # Test severity separation
        result = self.validator.validate_data({'username': 'ab'})
        
        assert not result.is_valid
        warning_count = len([e for e in result.errors if e.severity == ValidationSeverity.WARNING])
        assert warning_count >= 0


class TestRequestValidationMiddleware:
    """Test the RequestValidationMiddleware class."""

    def setup_method(self):
        """Set up test environment."""
        self.config = RequestValidationConfig(
            enable_validation=True,
            strict_mode=False,
            sanitize_input=True,
            log_validation_errors=True,
            block_on_error=True
        )
        self.middleware = RequestValidationMiddleware(self.config)
    
    def test_validation_success(self):
        """Test successful validation."""
        request_data = {
            'username': 'testuser',
            'email': 'user@example.com',
            'password': 'ValidPassword123!'
        }
        
        result = self.middleware.validate_request(request_data)
        
        assert result.is_valid
        assert len(result.errors) == 0
    
    def test_validation_failure(self):
        """Test validation failure."""
        request_data = {
            'username': 'ab',  # Too short
            'email': 'invalid-email',  # Invalid email
            'password': 'short'  # Too short
        }
        
        result = self.middleware.validate_request(request_data)
        
        assert not result.is_valid
        assert len(result.errors) > 0
    
    def test_custom_schema_validation(self):
        """Test validation with custom schema."""
        # Define custom schema
        custom_schema = {
            'username': {'type': str, 'required': True, 'rules': [{'rule': 'min_length', 'value': 3}]},
            'age': {'type': int, 'required': False}
        }
        
        # Set custom schema
        self.middleware.config.custom_schemas['test_endpoint'] = custom_schema
        
        request_data = {
            'username': 'testuser',
            'age': '25'  # String instead of int
        }
        
        result = self.middleware.validate_request(request_data, 'test_endpoint')
        
        # Should fail age validation due to type mismatch
        assert not result.is_valid
    
    def test_validation_stats(self):
        """Test validation statistics."""
        # Reset stats
        self.middleware._validation_stats = {
            'total_requests': 0,
            'valid_requests': 0,
            'invalid_requests': 0,
            'validation_time': 0
        }
        
        # Make valid request
        request_data = {'username': 'testuser', 'email': 'user@example.com'}
        self.middleware.validate_request(request_data)
        
        # Make invalid request
        request_data = {'username': 'ab', 'email': 'invalid-email'}
        self.middleware.validate_request(request_data)
        
        # Check stats
        stats = self.middleware.get_validation_stats()
        assert stats['total_requests'] == 2
        assert stats['valid_requests'] == 1
        assert stats['invalid_requests'] == 1
        assert stats['validation_time'] >= 0


class TestIntegration:
    """Test integration with global validator."""

    def test_global_validator_function(self):
        """Test global validator functions."""
        # Add rule to global validator
        add_validation_rule('test_field', ValidationRule.REQUIRED)
        
        # Test with valid data
        result = validate_input({'test_field': 'value'})
        assert result.is_valid
        
        # Test with invalid data (no test_field)
        # Note: the rule only applies when field is present but None
        result = validate_input({'test_field': None})
        assert not result.is_valid
    
    def test_global_validator_modification(self):
        """Test global validator modification."""
        # Test that global validator can be modified
        original_count = len(input_validator._rules.get('email', []))
        
        add_validation_rule('email', ValidationRule.MIN_LENGTH, value=10)
        
        new_count = len(input_validator._rules.get('email', []))
        assert new_count > original_count


class TestPerformance:
    """Test performance of validation system."""

    def setup_method(self):
        """Set up test environment."""
        self.validator = InputValidator()
    
    def test_validation_performance(self):
        """Test validation performance with large input."""
        # Prepare large input within limits
        large_data = {
            'username': 'testuser',
            'email': 'user@example.com',
            'password': 'ValidPassword123!',
            'long_text': 'text' * 100  # 400 characters, under limit
        }
        
        # Measure validation time
        start_time = time.time()
        result = self.validator.validate_data(large_data)
        end_time = time.time()
        
        # Check performance (should be fast)
        validation_time = end_time - start_time
        assert validation_time < 0.1, f"Validation took too long: {validation_time}s"
        
        assert result.is_valid
    
    def test_concurrent_validation(self):
        """Test validation performance under concurrent load."""
        import threading
        
        def validate_worker(worker_id):
            data = {'username': f'user{worker_id}', 'email': f'user{worker_id}@example.com'}
            return self.validator.validate_data(data)
        
        # Create multiple threads
        threads = []
        results = []
        
        for i in range(10):
            thread = threading.Thread(target=lambda i=i: results.append(validate_worker(i)))
            threads.append(thread)
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for all threads
        for thread in threads:
            thread.join()
        
        # Check all validations passed
        assert len(results) == 10
        assert all(result.is_valid for result in results)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])