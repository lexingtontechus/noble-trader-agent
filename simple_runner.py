#!/usr/bin/env python3

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

app = FastAPI(title="Hermes Dev Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class LoginRequest(BaseModel):
    username: str
    password: str

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "0.1.0-dev",
        "message": "Hermes backend is running"
    }

@app.post("/auth/login")
async def login(request: LoginRequest):
    if request.username == "admin" and request.password == "admin":
        return {
            "success": True,
            "message": "Login successful",
            "username": "admin",
            "role": "admin"
        }
    else:
        raise HTTPException(status_code=401, detail="Invalid credentials")

@app.post("/auth/logout")
async def logout():
    return {
        "success": True,
        "message": "Logged out successfully"
    }

@app.get("/auth/me")
async def get_user_info():
    return {
        "username": "admin",
        "authenticated": True,
        "role": "admin"
    }

if __name__ == "__main__":
    # Directly run the server without uvicorn.run
    import threading
    
    def run_server():
        config = uvicorn.Config(app, host="0.0.0.0", port=8080, reload=False)
        server = uvicorn.Server(config)
        server.run()
    
    # Run in a thread so we can test immediately
    server_thread = threading.Thread(target=run_server)
    server_thread.daemon = True
    server_thread.start()
    
    # Wait for server to start
    import time
    time.sleep(2)
    
    print("Server started on port 8080")
    print("Testing endpoints...")
    
    # Test health endpoint
    try:
        import requests
        response = requests.get("http://localhost:8080/health")
        print(f"Health endpoint: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"Health test failed: {e}")
    
    # Test login endpoint
    try:
        response = requests.post(
            "http://localhost:8080/auth/login",
            json={"username": "admin", "password": "admin"}
        )
        print(f"Login endpoint: {response.status_code} - {response.json()}")
    except Exception as e:
        print(f"Login test failed: {e}")
    
    print("Server running... Press Ctrl+C to stop")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Server stopped")