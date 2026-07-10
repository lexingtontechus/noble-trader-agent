#!/usr/bin/env python3
"""
Proper server startup script that initializes the Hermes app
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from hermes.web.app import create_app
from hermes.core.config import load_config
import uvicorn

# Create the app instance
config = load_config()
app = create_app(config)

if __name__ == "__main__":
    print("Starting Hermes backend server on http://127.0.0.1:8080")
    uvicorn.run(app, host="127.0.0.1", port=8080, reload=True)
    # For uvicorn reload, we need to set the module correctly
    import uvicorn
    uvicorn.run("run_server:app", host="127.0.0.1", port=8080, reload=True)