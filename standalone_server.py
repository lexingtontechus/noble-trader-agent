#!/usr/bin/env python3
"""
Standalone FastAPI server for Hermes dashboard
"""

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(
    title="Hermes Trading Platform",
    description="Development server for Hermes dashboard",
    version="0.1.0-dev"
)

# Add CORS middleware
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

class LoginResponse(BaseModel):
    success: bool
    message: str
    username: Optional[str] = None
    role: Optional[str] = None

@app.get("/")
async def root():
    return {
        "message": "Hermes Trading Platform - Dev Server",
        "status": "running"
    }

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "0.1.0-dev",
        "checked_at": "2026-07-07T00:00:00Z",
        "message": "Hermes backend is running"
    }

@app.post("/auth/login")
async def login(request: LoginRequest):
    """Simple login for development"""
    if request.username == "admin" and request.password == "admin":
        return LoginResponse(
            success=True,
            message="Login successful",
            username="admin",
            role="admin"
        )
    else:
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials"
        )

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
    print("Starting standalone Hermes server on port 8080")
    uvicorn.run("standalone_server:app", host="0.0.0.0", port=8080, reload=False)