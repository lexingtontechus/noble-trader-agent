#!/usr/bin/env python3
"""
Server module that uvicorn can import properly
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from hermes.web.app import create_app
from hermes.core.config import load_config

# Create the app instance
config = load_config()
app = create_app(config)