#!/usr/bin/env python3
"""
Standalone Flask authentication server with no imports from Hermes
"""

from flask import Flask, request, jsonify, make_response
import os

# Create Flask app without any imports from Hermes
app = Flask(__name__)

# Configure app to avoid any conflicts
app.config.update(
    SECRET_KEY='dev-only-secret-for-testing',
    SESSION_COOKIE_SECURE=False,
    SESSION_COOKIE_HTTPONLY=False
)

@app.route('/')
def root():
    return jsonify({
        "message": "Hermes Trading Platform - Dev Server",
        "status": "running"
    })

@app.route('/health')
def health():
    return jsonify({
        "status": "healthy",
        "version": "0.1.0-dev",
        "checked_at": "2026-07-07T00:00:00Z",
        "message": "Auth backend is running"
    })

@app.route('/auth/login', methods=['POST'])
def login():
    """Simple login for development"""
    print("Login request received")
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    print(f"Login attempt: {username}")
    
    # Use simple credentials for development
    admin_username = os.getenv("HERMES_ADMIN_USERNAME", "admin")
    admin_password = os.getenv("HERMES_ADMIN_PASSWORD", "admin")
    
    print(f"Expected credentials: {admin_username}/***")
    
    if username == admin_username and password == admin_password:
        # Create session cookie
        response = make_response(jsonify({
            "success": True,
            "message": "Login successful",
            "username": username,
            "role": "admin"
        }))
        
        # Set session cookie
        response.set_cookie('auth_session', f'user_{username}', 
                           max_age=3600, secure=False, httponly=False)
        
        print(f"Login successful: {username}")
        return response
    else:
        print(f"Login failed: {username}")
        return jsonify({
            "success": False,
            "message": "Invalid credentials"
        }), 401

@app.route('/auth/logout', methods=['POST'])
def logout():
    """Simple logout for development"""
    print("Logout request received")
    
    response = make_response(jsonify({
        "success": True,
        "message": "Logged out successfully"
    }))
    
    # Clear session cookie
    response.set_cookie('auth_session', '', max_age=0)
    
    print("Logout successful")
    return response

@app.route('/auth/me', methods=['GET'])
def get_user_info():
    """Get current user info from session"""
    session_cookie = request.cookies.get('auth_session')
    
    print(f"Get user info request: {session_cookie}")
    
    if session_cookie:
        # Simple parsing for development
        if session_cookie.startswith('user_'):
            username = session_cookie.replace('user_', '')
            print(f"User authenticated: {username}")
            return jsonify({
                "username": username,
                "authenticated": True,
                "role": "admin"
            })
    
    print("No session found")
    return jsonify({
        "username": None,
        "authenticated": False
    })

if __name__ == '__main__':
    print("Starting standalone Hermes auth server on port 8080")
    print("This server has NO imports from the original Hermes codebase")
    app.run(host='0.0.0.0', port=8080, debug=True, use_reloader=False)