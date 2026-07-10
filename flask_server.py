#!/usr/bin/env python3
"""
Simple Flask development server
"""

from flask import Flask, request, jsonify
import os
import secrets

app = Flask(__name__)

# Simple in-memory session store
sessions = {}

# Development credentials
DEV_USERNAME = os.getenv("HERMES_ADMIN_USERNAME", "admin")
DEV_PASSWORD = os.getenv("HERMES_ADMIN_PASSWORD", "admin")

@app.route('/')
def root():
    """Root endpoint"""
    return jsonify({"message": "Hermes Trading Platform - Dev Server"})

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "version": "0.1.0-dev",
        "checked_at": "2026-07-07T00:00:00Z",
        "message": "Hermes backend is running"
    })

@app.route('/auth/login', methods=['POST'])
def login():
    """Simple login for development"""
    data = request.get_json()
    username = data.get('username')
    password = data.get('password')
    
    print(f"Login attempt: {username}")
    
    # Simple comparison for development
    if username == DEV_USERNAME and password == DEV_PASSWORD:
        # Create simple session token
        session_token = f"dev_session_{username}"
        sessions[session_token] = {
            "username": username,
            "created_at": "2026-07-07T00:00:00Z"
        }
        
        print(f"Login successful: {username}")
        return jsonify({
            "success": True,
            "message": "Login successful",
            "token": session_token
        })
    else:
        print(f"Login failed: {username}")
        return jsonify({"success": False, "message": "Invalid credentials"}), 401

@app.route('/auth/logout', methods=['POST'])
def logout():
    """Simple logout for development"""
    data = request.get_json()
    token = data.get('token')
    
    print(f"Logout attempt: {token}")
    
    if token and token in sessions:
        del sessions[token]
        print(f"Logout successful: {token}")
        return jsonify({"success": True, "message": "Logged out successfully"})
    else:
        print(f"Logout failed - no session found: {token}")
        return jsonify({"success": False, "message": "No active session found"})

@app.route('/auth/me', methods=['GET'])
def get_user_info():
    """Get current user info"""
    data = request.get_json()
    token = data.get('token')
    
    print(f"Get user info: {token}")
    
    if token and token in sessions:
        user_info = sessions[token]
        print(f"User info: {user_info['username']}")
        return jsonify({"username": user_info["username"], "authenticated": True})
    else:
        print(f"No session found: {token}")
        return jsonify({"username": None, "authenticated": False})

if __name__ == '__main__':
    print("Starting Hermes dev server on port 8080")
    app.run(host='0.0.0.0', port=8080, debug=True)