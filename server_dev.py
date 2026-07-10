#!/usr/bin/env python3
"""
Simple development server with basic authentication
"""

import os
import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

# Configure logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)

app = FastAPI(
    title="Hermes Trading Platform - Dev Server",
    description="Development server with simplified authentication",
    version="0.1.0-dev"
)

# Simple in-memory session store
sessions = {}

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Simple login model
class LoginRequest(BaseModel):
    username: str
    password: str

class LoginResponse(BaseModel):
    success: bool
    message: str
    token: Optional[str] = None

# Development credentials
DEV_USERNAME = os.getenv("HERMES_ADMIN_USERNAME", "admin")
DEV_PASSWORD = os.getenv("HERMES_ADMIN_PASSWORD", "admin")

@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Hermes Trading Platform - Dev Server"}

@app.get("/health")
async def health():
    """Health check endpoint"""
    from datetime import datetime, timezone
    return {
        "status": "healthy",
        "version": "0.1.0-dev",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "message": "Hermes backend is running"
    }

@app.post("/auth/login")
async def login(login_data: LoginRequest):
    """Simple login for development"""
    logger.info("Login attempt", username=login_data.username)
    
    # Simple comparison for development
    if login_data.username == DEV_USERNAME and login_data.password == DEV_PASSWORD:
        # Create simple session token
        session_token = f"dev_session_{login_data.username}"
        sessions[session_token] = {
            "username": login_data.username,
            "created_at": "2026-07-07T00:00:00Z"
        }
        
        logger.info("Login successful", username=login_data.username, session_token=session_token)
        return LoginResponse(
            success=True,
            message="Login successful",
            token=session_token
        )
    else:
        logger.warning("Login failed", username=login_data.username)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

@app.post("/auth/logout")
async def logout(token: str = None):
    """Simple logout for development"""
    logger.info("Logout attempt", token=token)
    
    if token and token in sessions:
        del sessions[token]
        logger.info("Logout successful", token=token)
        return {"success": True, "message": "Logged out successfully"}
    else:
        logger.warning("Logout failed - no session found", token=token)
        return {"success": False, "message": "No active session found"}

@app.get("/auth/me")
async def get_user_info(token: str = None):
    """Get current user info"""
    logger.info("Get user info attempt", token=token)
    
    if token and token in sessions:
        user_info = sessions[token]
        logger.info("User info retrieved", username=user_info["username"])
        return {"username": user_info["username"], "authenticated": True}
    else:
        logger.warning("User info failed - no session", token=token)
        return {"username": None, "authenticated": False}

if __name__ == "__main__":
    logger.info("Starting Hermes dev server", host="0.0.0.0", port=8080)
    uvicorn.run(
        "server_dev:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
        log_level="info"
    )