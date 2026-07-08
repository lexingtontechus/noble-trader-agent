#!/usr/bin/env python3
"""
Test script to debug the login endpoint issue
"""

import sys
import os
import json
from pathlib import Path

# Add the src directory to the path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from hermes.core.config import load_config
from hermes.web.app import create_app, _get_auth_settings
from hermes.security.password_utils import verify_password

def test_authentication():
    """Test the authentication logic"""
    print("Testing authentication...")
    
    try:
        # Load config
        config_path = None
        config = load_config(config_path)
        print("Config loaded successfully")
        
        # Create app
        app = create_app(config)
        print("App created successfully")
        
        # Get auth settings
        settings = _get_auth_settings()
        print("Auth settings retrieved successfully")
        print(f"Username: {settings['admin_username']}")
        print(f"Password hash: {settings['admin_password']}")
        
        # Test password verification
        test_password = 'DevPass123!@#'
        result = verify_password(test_password, settings['admin_password'])
        print(f"Password verification result: {result}")
        
        # Test username verification
        import hmac
        username = 'admin'
        user_ok = hmac.compare_digest(username, settings['admin_username'])
        print(f"Username verification result: {user_ok}")
        
        # Test the full authentication
        user_ok = hmac.compare_digest(username, settings['admin_username'])
        pass_ok = verify_password(test_password, settings['admin_password']) if settings['admin_password'] else False
        
        print(f"Full auth check - User: {user_ok}, Pass: {pass_ok}")
        
        if user_ok and pass_ok:
            print("✓ Authentication should work!")
        else:
            print("✗ Authentication would fail")
            
    except Exception as e:
        print(f"Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_authentication()