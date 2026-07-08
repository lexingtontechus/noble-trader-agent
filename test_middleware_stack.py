#!/usr/bin/env python3
"""
Test middleware configuration to debug session access issues
"""

from hermes.web.app import create_app
from hermes.core.config import load_config

def test_middleware_stack():
    print("Loading config...")
    config = load_config()
    print("Config loaded successfully")
    
    print("Creating app...")
    app = create_app(config)
    print("App created successfully")
    
    print("Middleware stack:")
    for i, middleware in enumerate(app.user_middleware):
        print(f"  {i}: {middleware.cls.__name__}")
    
    print("\nRouter stack:")
    for route in app.routes:
        print(f"  {route.methods} {route.path} -> {route.endpoint}")
    
    return app

if __name__ == "__main__":
    test_middleware_stack()