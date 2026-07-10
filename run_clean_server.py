#!/usr/bin/env python3

# Change to a directory that doesn't have the src folder
import os
import tempfile
import shutil

# Create a temporary directory for our server
temp_dir = tempfile.mkdtemp()
server_file = os.path.join(temp_dir, 'temp_server.py')

server_content = '''
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
    uvicorn.run("temp_server:app", host="0.0.0.0", port=8080, reload=False)
'''

with open(server_file, 'w') as f:
    f.write(server_content)

# Change to temp directory and run
os.chdir(temp_dir)
exec(open(server_file).read())

# Cleanup
shutil.rmtree(temp_dir)