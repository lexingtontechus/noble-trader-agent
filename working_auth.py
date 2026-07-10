#!/usr/bin/env python3
"""
Working FastAPI server with authentication
"""

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import os

app = FastAPI()

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple in-memory session store
sessions = {}

# Development credentials
DEV_USERNAME = os.getenv("HERMES_ADMIN_USERNAME", "admin")
DEV_PASSWORD = os.getenv("HERMES_ADMIN_PASSWORD", "admin")

class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    success: bool
    message: str
    token: str = ""

@app.get("/")
async def root():
    return {"message": "Hermes Trading Platform - Dev Server"}

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "0.1.0-dev",
        "checked_at": "2026-07-07T00:00:00Z",
        "message": "Hermes backend is running"
    }

@app.post("/auth/login")
async def login(login_data: LoginRequest):
    """Simple login for development"""
    if login_data.username == DEV_USERNAME and login_data.password == DEV_PASSWORD:
        # Create simple session token
        session_token = f"dev_session_{login_data.username}"
        sessions[session_token] = {
            "username": login_data.username,
            "created_at": "2026-07-07T00:00:00Z"
        }
        return LoginResponse(
            success=True,
            message="Login successful",
            token=session_token
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

@app.post("/auth/logout")
async def logout():
    """Simple logout for development"""
    # For now, just acknowledge logout
    # In a real app, you'd extract token from body/headers
    return {"success": True, "message": "Logged out successfully"}

@app.get("/auth/me")
async def get_user_info():
    """Get current user info"""
    # For now, return default user
    # In a real app, you'd verify token and return user info
    return {
        "username": DEV_USERNAME,
        "authenticated": True,
        "role": "admin"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("working_auth:app", host="0.0.0.0", port=8080, reload=True)