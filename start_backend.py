#!/usr/bin/env python3
"""
Simple script to start the Hermes FastAPI backend server
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from hermes.web.app import create_app
from hermes.core.config import load_config
import uvicorn

if __name__ == "__main__":
    print("Loading Hermes config...")
    config = load_config()
    app = create_app(config)
    print("Starting Hermes backend server on http://127.0.0.1:8080")
    uvicorn.run("start_backend:app", host="127.0.0.1", port=8080, reload=True)