#!/usr/bin/env python3
"""
Test middleware configuration to debug session access issues
"""

from hermes.web.app import create_app
from hermes.core.config import load_config
from fastapi.testclient import TestClient

def test_middleware():
    print("Loading config...")
    config = load_config()
    print("Config loaded successfully")
    
    print("Creating app...")
    app = create_app(config)
    print("App created successfully")
    
    print("Creating test client...")
    client = TestClient(app)
    print("Test client created")
    
    print("Testing login endpoint...")
    response = client.post("/auth/login", json={"username": "admin", "password": "password"})
    print(f"Response status: {response.status_code}")
    print(f"Response content: {response.text}")
    
    return response

if __name__ == "__main__":
    test_middleware()