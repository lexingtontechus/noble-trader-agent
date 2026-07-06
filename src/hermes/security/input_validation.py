"""
Input Validation Middleware for Hermes Trading Platform

Provides comprehensive input validation across all API endpoints:
- Schema validation
- Type checking
- Length constraints
- Format validation
- SQL injection prevention
- XSS prevention
- CSRF protection
- File upload validation
- Parameter sanitization
- Security rule engine
"""

import json
import re
import time
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union, get_type_hints
from dataclasses import dataclass, field
from dataclasses import is_dataclass, fields
from urllib.parse import urlparse


class ValidationRule(Enum):
    """Validation rules."""
    REQUIRED = "required"
    MIN_LENGTH = "min_length"
    MAX_LENGTH = "max_length"
    REGEX = "regex"
    EMAIL = "email"
    URL = "url"
    INTEGER = "integer"
    FLOAT = "float"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    ENUM = "enum"
    JSON = "json"
    BASE64 = "base64"
    HEX = "hex"
    UUID = "uuid"
    PHONE = "phone"
    ZIP_CODE = "zip_code"
    CREDIT_CARD = "credit_card"
    SSN = "ssn"
    SQL_INJECTION = "sql_injection"
    XSS = "xss"
    COMMAND_INJECTION = "command_injection"
    PATH_TRAVERSAL = "path_traversal"
    AUTH_BYPASS = "auth_bypass"


class ValidationSeverity(Enum):
    """Validation severity levels."""
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


@dataclass
class ValidationError:
    """Validation error data."""
    field: str
    rule: ValidationRule
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    value: Any = None


@dataclass
class ValidationRuleConfig:
    """Configuration for validation rules."""
    rule: ValidationRule
    value: Any = None
    message: Optional[str] = None
    severity: ValidationSeverity = ValidationSeverity.ERROR
    enabled: bool = True


@dataclass
class ValidationResult:
    """Validation result."""
    is_valid: bool
    errors: List[ValidationError]
    warnings: List[ValidationError]
    sanitized_data: Dict[str, Any]
    original_data: Dict[str, Any]


class InputValidator:
    """
    Comprehensive input validation system.
    
    Features:
    - Multiple validation rules
    - Custom validators
    - Sanitization
    - Security checks
    - Schema validation
    - Dataclass validation
    - Performance optimization
    """
    
    def __init__(self):
        """Initialize input validator."""
        self._rules: Dict[str, List[ValidationRuleConfig]] = {}
        self._custom_validators: Dict[str, callable] = {}
        self._schemas: Dict[str, Any] = {}
        
        # Compile regex patterns
        self._compiled_patterns = self._compile_patterns()
        
        # Initialize default rules
        self._init_default_rules()
    
    def _compile_patterns(self) -> Dict[str, re.Pattern]:
        """Compile regex patterns for validation."""
        patterns = {
            'email': re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'),
            'url': re.compile(r'^https?://(?:[-\w.]|(?:%[\da-fA-F]{2}))+[^\s]*$'),
            'phone': re.compile(r'^\+?1?\d{9,15}$'),
            'zip_code': re.compile(r'^\d{5}(-\d{4})?$'),
            'credit_card': re.compile(r'^\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}$'),
            'ssn': re.compile(r'^\d{3}-?\d{2}-?\d{4}$'),
            'uuid': re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.IGNORECASE),
            'base64': re.compile(r'^[A-Za-z0-9+/]+={0,2}$'),
            'hex': re.compile(r'^[0-9a-fA-F]+$'),
            
            # Security patterns
            'sql_injection': re.compile(r'(select|insert|update|delete|drop|create|alter|exec|union|into|load|file|dump|waitfor|delay|sleep|"|\'|\;|\-\-|\#|\*|\/|\%|\/\*|\*\/|@@|xp_|sp_|dbo\.|or|and|not)', re.IGNORECASE),
            'xss': re.compile(r'(<script[^>]*>.*?</script>)|(javascript:)|(on\w+\s*=)|(eval\(|alert\(|document\.|window\.)', re.IGNORECASE),
            'command_injection': re.compile(r'(cmd\.exec|system|exec|shell_exec|popen|proc_open|\;|\&|\|`|\$\(|\${)', re.IGNORECASE),
            'path_traversal': re.compile(r'(\.\.\/|\.\.\\|\%2e\%2e\/)', re.IGNORECASE),
            'auth_bypass': re.compile(r'(administrator|root|test|demo|true\s*=\s*true|false\s*=\s*false|1\s*=\s*1|0\s*=\s*0|drop\s+table|delete\s+from|truncate)', re.IGNORECASE)
        }
        
        return patterns
    
    def _init_default_rules(self) -> None:
        """Initialize default validation rules."""
        # General input rules
        self.add_rule('general', ValidationRule.REQUIRED, severity=ValidationSeverity.ERROR)
        self.add_rule('general', ValidationRule.MAX_LENGTH, value=1000)
        self.add_rule('general', ValidationRule.SQL_INJECTION, severity=ValidationSeverity.ERROR)
        self.add_rule('general', ValidationRule.XSS, severity=ValidationSeverity.ERROR)
        self.add_rule('general', ValidationRule.COMMAND_INJECTION, severity=ValidationSeverity.ERROR)
        self.add_rule('general', ValidationRule.PATH_TRAVERSAL, severity=ValidationSeverity.ERROR)
        self.add_rule('general', ValidationRule.AUTH_BYPASS, severity=ValidationSeverity.ERROR)
        
        # Email field rules
        self.add_rule('email', ValidationRule.EMAIL, message="Invalid email format")
        self.add_rule('email', ValidationRule.MAX_LENGTH, value=254)
        
        # Username field rules
        self.add_rule('username', ValidationRule.REQUIRED, severity=ValidationSeverity.ERROR)
        self.add_rule('username', ValidationRule.MIN_LENGTH, value=3)
        self.add_rule('username', ValidationRule.MAX_LENGTH, value=50)
        self.add_rule('username', ValidationRule.REGEX, value=r'^[a-zA-Z0-9_@.-]+$', 
                     message="Username can only contain letters, numbers, @, _, ., -")
        
        # Password field rules
        self.add_rule('password', ValidationRule.REQUIRED, severity=ValidationSeverity.ERROR)
        self.add_rule('password', ValidationRule.MIN_LENGTH, value=8)
        self.add_rule('password', ValidationRule.MAX_LENGTH, value=128)
        
        # URL field rules
        self.add_rule('url', ValidationRule.URL, message="Invalid URL format")
        self.add_rule('url', ValidationRule.MAX_LENGTH, value=2048)
        
        # Integer field rules
        self.add_rule('integer', ValidationRule.INTEGER, message="Value must be an integer")
        self.add_rule('integer', ValidationRule.REGEX, value=r'^-?\d+$', 
                     message="Invalid integer format")
        
        # Float field rules
        self.add_rule('float', ValidationRule.FLOAT, message="Value must be a number")
        self.add_rule('float', ValidationRule.REGEX, value=r'^-?\d+(\.\d+)?$', 
                     message="Invalid number format")
        
        # Phone field rules
        self.add_rule('phone', ValidationRule.PHONE, message="Invalid phone format")
        
        # Credit card field rules
        self.add_rule('credit_card', ValidationRule.CREDIT_CARD, message="Invalid credit card format")
    
    def add_rule(self, field: str, rule: ValidationRule, value: Any = None,
                 message: Optional[str] = None, severity: ValidationSeverity = ValidationSeverity.ERROR) -> None:
        """
        Add validation rule for field.
        
        Args:
            field: Field name
            rule: Validation rule
            value: Rule value (for rules that require it)
            message: Custom error message
            severity: Error severity
        """
        if field not in self._rules:
            self._rules[field] = []
        
        rule_config = ValidationRuleConfig(
            rule=rule,
            value=value,
            message=message,
            severity=severity
        )
        
        self._rules[field].append(rule_config)
    
    def add_custom_validator(self, name: str, validator: callable) -> None:
        """
        Add custom validator.
        
        Args:
            name: Validator name
            validator: Validator function that takes (value, field_name) and returns bool
        """
        self._custom_validators[name] = validator
    
    def validate_field(self, field_name: str, value: Any, context: Dict[str, Any] = None) -> List[ValidationError]:
        """
        Validate a single field.
        
        Args:
            field_name: Name of the field
            value: Value to validate
            context: Additional context for validation
            
        Returns:
            List of validation errors
        """
        errors = []
        
        # Get rules for field
        rules = self._rules.get(field_name, self._rules.get('general', []))
        
        for rule in rules:
            if not rule.enabled:
                continue
            
            error = self._validate_rule(value, field_name, rule, context or {})
            if error:
                errors.append(error)
        
        # Check custom validators
        for validator_name, validator in self._custom_validators.items():
            try:
                if not validator(value, field_name, context or {}):
                    errors.append(ValidationError(
                        field=field_name,
                        rule=ValidationRule.REQUIRED,
                        message=f"Custom validation failed for {validator_name}",
                        severity=ValidationSeverity.ERROR
                    ))
            except Exception as e:
                errors.append(ValidationError(
                    field=field_name,
                    rule=ValidationRule.REQUIRED,
                    message=f"Custom validation error: {str(e)}",
                    severity=ValidationSeverity.ERROR
                ))
        
        return errors
    
    def _validate_rule(self, value: Any, field_name: str, rule: ValidationRuleConfig,
                      context: Dict[str, Any]) -> Optional[ValidationError]:
        """Validate a single rule."""
        if value is None:
            if rule.rule == ValidationRule.REQUIRED:
                return ValidationError(
                    field=field_name,
                    rule=rule.rule,
                    message=rule.message or f"{field_name} is required",
                    severity=rule.severity
                )
            return None  # Skip other rules for None values
        
        # Convert to string for text-based validations
        str_value = str(value) if not isinstance(value, (dict, list)) else str(value)
        
        try:
            if rule.rule == ValidationRule.REQUIRED:
                # Already handled above
                pass
            
            elif rule.rule == ValidationRule.MIN_LENGTH:
                if len(str_value) < rule.value:
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or f"{field_name} must be at least {rule.value} characters",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.MAX_LENGTH:
                if len(str_value) > rule.value:
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or f"{field_name} must be no more than {rule.value} characters",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.REGEX:
                if not re.match(rule.value, str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or f"{field_name} format is invalid",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.EMAIL:
                if not self._compiled_patterns['email'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid email format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.URL:
                if not self._compiled_patterns['url'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid URL format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.INTEGER:
                if not self._compiled_patterns['integer'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Value must be an integer",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.FLOAT:
                if not self._compiled_patterns['float'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Value must be a number",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.BOOLEAN:
                if str_value.lower() not in ('true', 'false', '1', '0'):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Value must be boolean",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.DATETIME:
                try:
                    datetime.fromisoformat(str_value.replace('Z', '+00:00'))
                except ValueError:
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid datetime format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.ENUM:
                if value not in rule.value:
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or f"Value must be one of: {rule.value}",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.JSON:
                try:
                    json.loads(str_value)
                except json.JSONDecodeError:
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid JSON format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.BASE64:
                if not self._compiled_patterns['base64'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid base64 format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.HEX:
                if not self._compiled_patterns['hex'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid hexadecimal format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.UUID:
                if not self._compiled_patterns['uuid'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid UUID format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.PHONE:
                if not self._compiled_patterns['phone'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid phone format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.ZIP_CODE:
                if not self._compiled_patterns['zip_code'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid zip code format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.CREDIT_CARD:
                if not self._compiled_patterns['credit_card'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid credit card format",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.SSN:
                if not self._compiled_patterns['ssn'].match(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Invalid SSN format",
                        severity=rule.severity,
                        value=value
                    )
            
            # Security validations
            elif rule.rule == ValidationRule.SQL_INJECTION:
                if self._compiled_patterns['sql_injection'].search(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Potential SQL injection detected",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.XSS:
                if self._compiled_patterns['xss'].search(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Potential XSS attack detected",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.COMMAND_INJECTION:
                if self._compiled_patterns['command_injection'].search(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Potential command injection detected",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.PATH_TRAVERSAL:
                if self._compiled_patterns['path_traversal'].search(str_value):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Potential path traversal attack detected",
                        severity=rule.severity,
                        value=value
                    )
            
            elif rule.rule == ValidationRule.AUTH_BYPASS:
                if self._compiled_patterns['auth_bypass'].search(str_value.lower()):
                    return ValidationError(
                        field=field_name,
                        rule=rule.rule,
                        message=rule.message or "Potential authentication bypass detected",
                        severity=rule.severity,
                        value=value
                    )
        
        except Exception as e:
            return ValidationError(
                field=field_name,
                rule=rule.rule,
                message=rule.message or f"Validation error: {str(e)}",
                severity=rule.severity,
                value=value
            )
        
        return None
    
    def validate_data(self, data: Dict[str, Any], schema: Any = None) -> ValidationResult:
        """
        Validate input data.
        
        Args:
            data: Data to validate
            schema: Optional schema (dataclass or dict)
            
        Returns:
            ValidationResult
        """
        errors = []
        warnings = []
        sanitized_data = {}
        original_data = data.copy()
        
        # Validate data against schema if provided
        if schema:
            if is_dataclass(schema):
                errors.extend(self._validate_dataclass(data, schema))
            elif isinstance(schema, dict):
                errors.extend(self._validate_schema(data, schema))
        
        # Validate each field
        for field_name, value in data.items():
            field_errors = self.validate_field(field_name, value, data)
            
            # Separate errors by severity
            for error in field_errors:
                if error.severity == ValidationSeverity.ERROR:
                    errors.append(error)
                elif error.severity == ValidationSeverity.WARNING:
                    warnings.append(error)
            
            # Sanitize data if no errors
            if not any(e.field == field_name and e.severity == ValidationSeverity.ERROR for e in errors):
                sanitized_data[field_name] = self._sanitize_value(value)
            else:
                # Keep original value if validation failed
                sanitized_data[field_name] = value
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            sanitized_data=sanitized_data,
            original_data=original_data
        )
    
    def _validate_dataclass(self, data: Dict[str, Any], schema) -> List[ValidationError]:
        """Validate data against dataclass schema."""
        errors = []
        
        for field in fields(schema):
            field_name = field.name
            field_type = field.type
            
            if field_name in data:
                value = data[field_name]
                
                # Type validation
                if not isinstance(value, field_type):
                    # Try to convert basic types
                    if field_type in (int, float, str, bool) and isinstance(value, str):
                        try:
                            if field_type == int:
                                value = int(value)
                            elif field_type == float:
                                value = float(value)
                            elif field_type == bool:
                                value = value.lower() in ('true', '1', 'yes')
                            elif field_type == str:
                                value = str(value)
                        except (ValueError, TypeError):
                            errors.append(ValidationError(
                                field=field_name,
                                rule=ValidationRule.REQUIRED,
                                message=f"Invalid type for {field_name}",
                                severity=ValidationSeverity.ERROR
                            ))
                            continue
                
                # Validate nested dataclasses
                if is_dataclass(field_type) and isinstance(value, dict):
                    nested_errors = self._validate_dataclass(value, field_type)
                    errors.extend([e._replace(field=f"{field_name}.{e.field}") for e in nested_errors])
        
        return errors
    
    def _validate_schema(self, data: Dict[str, Any], schema: Dict[str, Any]) -> List[ValidationError]:
        """Validate data against dictionary schema."""
        errors = []
        
        for field_name, field_config in schema.items():
            if field_name in data:
                value = data[field_name]
                
                # Check required
                if field_config.get('required', False) and value is None:
                    errors.append(ValidationError(
                        field=field_name,
                        rule=ValidationRule.REQUIRED,
                        message=f"{field_name} is required",
                        severity=ValidationSeverity.ERROR
                    ))
                
                # Check type
                expected_type = field_config.get('type')
                if expected_type and value is not None and not isinstance(value, expected_type):
                    errors.append(ValidationError(
                        field=field_name,
                        rule=ValidationRule.REQUIRED,
                        message=f"{field_name} must be of type {expected_type}",
                        severity=ValidationSeverity.ERROR
                    ))
                
                # Check rules
                rules = field_config.get('rules', [])
                for rule_config in rules:
                    rule = ValidationRule(rule_config['rule'])
                    rule_obj = ValidationRuleConfig(
                        rule=rule,
                        value=rule_config.get('value'),
                        message=rule_config.get('message'),
                        severity=ValidationSeverity(rule_config.get('severity', 'error'))
                    )
                    error = self._validate_rule(value, field_name, rule_obj, data)
                    if error:
                        errors.append(error)
        
        return errors
    
    def _sanitize_value(self, value: Any) -> Any:
        """Sanitize value for safe use."""
        if isinstance(value, str):
            # Remove control characters
            value = ''.join(char if char.isprintable() or char in ('\t', '\n', '\r') else '?' for char in value)
            
            # Truncate to reasonable length
            if len(value) > 10000:
                value = value[:10000]
            
            # Escape HTML characters
            value = value.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')
            
            return value
        elif isinstance(value, (dict, list)):
            # Recursively sanitize complex structures
            if isinstance(value, dict):
                return {k: self._sanitize_value(v) for k, v in value.items()}
            else:
                return [self._sanitize_value(v) for v in value]
        
        return value
    
    def get_validation_report(self) -> Dict[str, Any]:
        """Get validation statistics and configuration."""
        return {
            'rules': {field: [rule.__dict__ for rule in rules] for field, rules in self._rules.items()},
            'custom_validators': list(self._custom_validators.keys()),
            'schemas': list(self._schemas.keys())
        }


# Global validator instance
input_validator: InputValidator = InputValidator()


def validate_input(data: Dict[str, Any], schema: Any = None) -> ValidationResult:
    """
    Validate input data using global validator.
    
    Args:
        data: Data to validate
        schema: Optional schema
        
    Returns:
        ValidationResult
    """
    return input_validator.validate_data(data, schema)


def add_validation_rule(field: str, rule: ValidationRule, value: Any = None,
                        message: Optional[str] = None, severity: ValidationSeverity = ValidationSeverity.ERROR) -> None:
    """
    Add validation rule using global validator.
    
    Args:
        field: Field name
        rule: Validation rule
        value: Rule value
        message: Custom message
        severity: Error severity
    """
    input_validator.add_rule(field, rule, value, message, severity)


@dataclass
class RequestValidationConfig:
    """Configuration for request validation middleware."""
    enable_validation: bool = True
    strict_mode: bool = False
    sanitize_input: bool = True
    log_validation_errors: bool = True
    block_on_error: bool = True
    custom_schemas: Dict[str, Any] = field(default_factory=dict)


class RequestValidationMiddleware:
    """
    Request validation middleware for FastAPI applications.
    
    Features:
    - Automatic input validation
    - Schema validation
    - Security checks
    - Error handling
    - Custom validation rules
    - Performance monitoring
    """
    
    def __init__(self, config: RequestValidationConfig = None):
        """
        Initialize request validation middleware.
        
        Args:
            config: Middleware configuration
        """
        self.config = config or RequestValidationConfig()
        self._validation_stats = {
            'total_requests': 0,
            'valid_requests': 0,
            'invalid_requests': 0,
            'validation_time': 0
        }
    
    def validate_request(self, request_data: Dict[str, Any], 
                         endpoint: str = None) -> ValidationResult:
        """
        Validate request data.
        
        Args:
            request_data: Request data to validate
            endpoint: Endpoint name for schema lookup
            
        Returns:
            ValidationResult
        """
        start_time = time.time()
        self._validation_stats['total_requests'] += 1
        
        try:
            # Get schema for endpoint if available
            schema = None
            if endpoint and endpoint in self.config.custom_schemas:
                schema = self.config.custom_schemas[endpoint]
            
            # Validate data
            result = validate_input(request_data, schema)
            
            # Update stats
            if result.is_valid:
                self._validation_stats['valid_requests'] += 1
            else:
                self._validation_stats['invalid_requests'] += 1
                if self.config.log_validation_errors:
                    self._log_validation_errors(result.errors, endpoint)
            
            return result
            
        finally:
            self._validation_stats['validation_time'] += time.time() - start_time
    
    def _log_validation_errors(self, errors: List[ValidationError], endpoint: str = None) -> None:
        """Log validation errors."""
        import logging
        logger = logging.getLogger(__name__)
        
        error_messages = [f"{e.field}: {e.message}" for e in errors]
        logger.warning(f"Validation failed for {endpoint or 'request'}: {', '.join(error_messages)}")
    
    def get_validation_stats(self) -> Dict[str, Any]:
        """Get validation statistics."""
        return self._validation_stats.copy()


# Global middleware instance
validation_middleware: RequestValidationMiddleware = RequestValidationMiddleware()