#!/usr/bin/env python3
"""
Minimal test server
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/test")
def test():
    return {"message": "Test endpoint works!"}

@app.get("/health")
async def health():
    from datetime import datetime, timezone
    return {
        "status": "healthy",
        "version": "0.1.0-dev",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "message": "Hermes backend is running"
    }

@app.post("/auth/login")
async def login():
    """Simple login for development"""
    return {
        "success": True,
        "message": "Login successful",
        "username": "admin",
        "role": "admin"
    }

@app.post("/auth/logout")
async def logout():
    """Simple logout for development"""
    return {
        "success": True,
        "message": "Logged out successfully"
    }

@app.get("/auth/me")
async def get_user_info():
    """Get current user info"""
    return {
        "username": "admin",
        "authenticated": True,
        "role": "admin"
    }

if __name__ == "__main__":
    uvicorn.run("minimal_test:app", host="0.0.0.0", port=8080, reload=False)