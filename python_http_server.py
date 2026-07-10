#!/usr/bin/env python3

import http.server
import socketserver
import json
import urllib.parse
from http.cookies import SimpleCookie
import threading
import time

PORT = 8080

class AuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "message": "Hermes Trading Platform - Dev Server"
            }).encode())
        
        elif self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "healthy",
                "version": "0.1.0-dev",
                "checked_at": "2026-07-07T00:00:00Z",
                "message": "Hermes backend is running"
            }).encode())
        
        elif self.path == '/auth/me':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({
                "username": "admin",
                "authenticated": True,
                "role": "admin"
            }).encode())
        
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"error": "Not found"}).encode())
    
    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        post_data = self.rfile.read(content_length)
        
        if self.path == '/auth/login':
            try:
                data = json.loads(post_data.decode('utf-8'))
                username = data.get('username')
                password = data.get('password')
                
                if username == "admin" and password == "admin":
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Set-Cookie', 'auth_session=user_admin; Path=/; Max-Age=3600')
                    self.end_headers()
                    
                    response = {
                        "success": True,
                        "message": "Login successful",
                        "username": username,
                        "role": "admin"
                    }
                    self.wfile.write(json.dumps(response).encode())
                else:
                    self.send_response(401)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    response = {
                        "success": False,
                        "message": "Invalid credentials"
                    }
                    self.wfile.write(json.dumps(response).encode())
                    
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                response = {
                    "success": False,
                    "message": f"Server error: {str(e)}"
                }
                self.wfile.write(json.dumps(response).encode())
        
        elif self.path == '/auth/logout':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Set-Cookie', 'auth_session=; Path=/; Max-Age=0')
            self.end_headers()
            
            response = {
                "success": True,
                "message": "Logged out successfully"
            }
            self.wfile.write(json.dumps(response).encode())
        
        else:
            self.send_response(404)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            response = {"error": "Not found"}
            self.wfile.write(json.dumps(response).encode())

def start_server():
    with socketserver.TCPServer(("", PORT), AuthHandler) as httpd:
        print(f"Starting HTTP server on port {PORT}")
        print("Endpoints:")
        print("  GET  /health")
        print("  POST /auth/login - admin/admin")
        print("  POST /auth/logout")
        print("  GET  /auth/me")
        httpd.serve_forever()

if __name__ == '__main__':
    # Start server in a thread
    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Wait for server to start
    time.sleep(1)
    
    print("Server is running! You can now test the dashboard.")
    print("Endpoints:")
    print("  GET  /health")
    print("  POST /auth/login - admin/admin")
    print("  POST /auth/logout")
    print("  GET  /auth/me")
    print("Press Ctrl+C to stop")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Server stopped")