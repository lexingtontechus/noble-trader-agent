"""
Pytest configuration and fixtures for Hermes tests.

Provides:
- test client with auth support
- auth token fixture for protected endpoints
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def test_config():
    """Load test configuration."""
    from hermes.core.config import load_config

    return load_config()


@pytest.fixture
def app(test_config):
    """Create test app with loaded config."""
    from hermes.web.app import create_app

    return create_app(test_config)


@pytest.fixture
def client(app):
    """Create test client."""
    return TestClient(app)


@pytest.fixture
def auth_token(test_config):
    """Get auth token from config (for tests that need auth)."""
    from hermes.core.config import get_secret_or_none

    return get_secret_or_none("hermes.agent_token")


@pytest.fixture
def auth_headers(auth_token):
    """Get auth headers for protected API endpoints."""
    if auth_token:
        return {"Authorization": f"Bearer {auth_token}"}
    return {}


@pytest.fixture
def authenticated_client(client, auth_headers):
    """Client with auth headers set."""
    # Set auth headers for all requests
    client.headers.update(auth_headers)
    return client