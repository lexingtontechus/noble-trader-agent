#!/usr/bin/env python3
"""
Ultra simple server to test if uvicorn works
"""

from fastapi import FastAPI

app = FastAPI()

@app.get("/simple")
def simple():
    return {"message": "Simple server works!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ultra_simple:app", host="0.0.0.0", port=8080)