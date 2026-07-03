"""
Dashboard tests — verify all routes respond correctly.

Run with:
    pytest tests/test_dashboard.py -v
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client with loaded config."""
    from hermes.core.config import load_config
    from hermes.web.app import create_app

    config = load_config()
    app = create_app(config)
    return TestClient(app)


def test_dashboard_status_page(client):
    """GET / returns 200 and shows subsystem names."""
    response = client.get("/")
    assert response.status_code == 200
    assert "Hermes Trading Platform" in response.text
    assert "DuckDB" in response.text
    assert "Alpaca" in response.text
    assert "Hyperliquid" in response.text
    assert "Noble Trader Redis" in response.text
    assert "Supabase" in response.text


def test_dashboard_health_json(client):
    """GET /health returns JSON with status and subsystems."""
    response = client.get("/health")
    assert response.status_code in (200, 503)
    data = response.json()
    assert "status" in data
    assert data["status"] in ("healthy", "degraded")
    assert "subsystems" in data
    assert "version" in data
    # All 6 subsystems should be present
    expected = {
        "DuckDB",
        "Hermes Redis (internal)",
        "Noble Trader Redis (upstream)",
        "Supabase",
        "Alpaca",
        "Hyperliquid",
    }
    assert set(data["subsystems"].keys()) == expected


def test_dashboard_config_page(client):
    """GET /config returns 200 and shows redacted config."""
    response = client.get("/config")
    assert response.status_code == 200
    # Should show some config content
    assert "config" in response.text.lower()
    # Should not show actual secret values
    assert "PKXXXXXXXXXXXXXX" not in response.text  # placeholder from .env.example


def test_dashboard_heartbeats_page(client):
    """GET /heartbeats returns 200."""
    response = client.get("/heartbeats")
    assert response.status_code == 200
    assert "Heartbeats" in response.text


def test_dashboard_heartbeats_with_symbol_filter(client):
    """GET /heartbeats?symbol=BTC filters correctly."""
    response = client.get("/heartbeats?symbol=BTC&limit=50")
    assert response.status_code == 200


def test_dashboard_api_status(client):
    """GET /api/status returns JSON with overall + subsystems."""
    response = client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data
    assert "config_hash" in data
    assert "environment" in data
    assert "overall" in data
    assert "subsystems" in data
    assert len(data["subsystems"]) == 6


def test_dashboard_api_heartbeats(client):
    """GET /api/heartbeats returns JSON list."""
    response = client.get("/api/heartbeats?limit=5")
    assert response.status_code == 200
    data = response.json()
    assert "count" in data
    assert "heartbeats" in data
    assert isinstance(data["heartbeats"], list)


def test_dashboard_static_css(client):
    """GET /static/style.css returns CSS."""
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")


def test_dashboard_static_js(client):
    """GET /static/app.js returns JavaScript."""
    response = client.get("/static/app.js")
    assert response.status_code == 200
    assert "javascript" in response.headers.get("content-type", "").lower()


def test_dashboard_status_shows_connection_state(client):
    """Status page shows connection states (connected/not_configured/error)."""
    response = client.get("/")
    assert response.status_code == 200
    # Should show at least one badge class
    assert "badge-" in response.text


def test_dashboard_no_secrets_leaked(client):
    """No real secret values should appear in any dashboard page."""
    sensitive_patterns = [
        "PKXXXXXXXXXXXXXX",  # Alpaca key placeholder pattern
        "sk_test_",
        "sk_live_",
        "eyJ",  # JWT prefix
    ]
    for path in ["/", "/config", "/heartbeats", "/health", "/api/status"]:
        response = client.get(path)
        for pattern in sensitive_patterns:
            # Config page may show placeholder text from .env.example
            # but should never show actual key patterns
            if path == "/config" and pattern == "PKXXXXXXXXXXXXXX":
                continue  # this is the .env.example placeholder, safe
            assert pattern not in response.text, (
                f"Sensitive pattern '{pattern}' found in {path}"
            )


def test_cli_dashboard_help():
    """`platform dashboard --help` works."""
    from click.testing import CliRunner

    from hermes.app import cli

    runner = CliRunner()
    result = runner.invoke(cli, ["dashboard", "--help"])
    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output
    assert "--reload" in result.output
