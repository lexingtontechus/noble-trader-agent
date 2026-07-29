"""Tests for credentials_validator module."""

import pytest

from hermes.core.credentials_validator import (
    VALID_PLAN_PREFIXES,
    PLAN_TO_STREAM,
    parse_redis_url,
    extract_plan_prefix,
    validate_plan_prefix,
    get_stream_name,
    derive_key,
)


class TestParseRedisUrl:
    """Tests for parse_redis_url function."""

    def test_parse_standard_url(self):
        url = "rediss://pp-sub-a1b2c3d4e5f6@redis.example.com:6379/0"
        result = parse_redis_url(url)
        
        assert result["scheme"] == "rediss"
        assert result["host"] == "redis.example.com"
        assert result["port"] == 6379
        assert result["username"] == "pp-sub-a1b2c3d4e5f6"
        assert result["password"] == ""
        assert result["database"] == "0"

    def test_parse_url_with_password(self):
        url = "rediss://user:password@redis.example.com:6380/1"
        result = parse_redis_url(url)
        
        assert result["username"] == "user"
        assert result["password"] == "password"
        assert result["port"] == 6380
        assert result["database"] == "1"

    def test_parse_redis_sub_url(self):
        url = "redis://sub_a1b2c3d4e5f678901234567890123456@nt-redis-host:36624/0"
        result = parse_redis_url(url)
        
        assert result["scheme"] == "redis"
        assert result["username"] == "sub_a1b2c3d4e5f678901234567890123456"


class TestExtractPlanPrefix:
    """Tests for extract_plan_prefix function."""

    def test_extract_from_legacy_format(self):
        result = extract_plan_prefix("pp-sub-a1b2c3d4e5f6")
        assert result == "pp"

    def test_extract_from_signal_scout(self):
        # Legacy format: ps-sub-<32 hex chars>
        result = extract_plan_prefix("ps-sub-a1b2c3d4e5f678901234567890123456")
        assert result == "ps"

    def test_extract_from_new_format_returns_none(self):
        # New format doesn't contain plan prefix
        result = extract_plan_prefix("sub_a1b2c3d4e5f678901234567890123456")
        assert result is None

    def test_extract_from_invalid_format(self):
        result = extract_plan_prefix("invalid")
        assert result is None

    def test_extract_from_empty_string(self):
        result = extract_plan_prefix("")
        assert result is None

    def test_extract_from_none(self):
        result = extract_plan_prefix(None)
        assert result is None


class TestValidatePlanPrefix:
    """Tests for validate_plan_prefix function."""

    def test_valid_prefix_pp(self):
        is_valid, error = validate_plan_prefix("pp")
        assert is_valid is True
        assert error == ""

    def test_valid_prefix_ps(self):
        is_valid, error = validate_plan_prefix("ps")
        assert is_valid is True
        assert error == ""

    def test_invalid_prefix(self):
        is_valid, error = validate_plan_prefix("invalid")
        assert is_valid is False
        assert "Unknown plan prefix" in error

    def test_none_prefix(self):
        is_valid, error = validate_plan_prefix(None)
        assert is_valid is False
        assert "No plan prefix" in error


class TestGetStreamName:
    """Tests for get_stream_name function."""

    def test_precision_pro_stream(self):
        result = get_stream_name("pp")
        assert result == "signals:precision_pro"

    def test_signal_scout_stream(self):
        result = get_stream_name("ps")
        assert result == "signals:signal_scout"

    def test_unknown_prefix_returns_default(self):
        result = get_stream_name("unknown")
        assert result == "signals:precision_pro"

    def test_none_prefix_returns_default(self):
        result = get_stream_name(None)
        assert result == "signals:precision_pro"


class TestDeriveKey:
    """Tests for derive_key function."""

    def test_derive_key_returns_bytes(self):
        result = derive_key("test_password")
        assert isinstance(result, bytes)

    def test_derive_key_length(self):
        result = derive_key("test_password")
        # Fernet key is 44 bytes (32 bytes base64 encoded)
        assert len(result) == 44

    def test_derive_key_deterministic(self):
        result1 = derive_key("password")
        result2 = derive_key("password")
        assert result1 == result2

    def test_derive_key_different_passwords(self):
        result1 = derive_key("password1")
        result2 = derive_key("password2")
        assert result1 != result2