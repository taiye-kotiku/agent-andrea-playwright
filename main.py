"""
Agent Andrea - Wegest Direct Booking Service
Main entry point - modularized version
All selectors verified against actual Wegest HTML (March 2025)
"""

# Import config first to set up global state
from config import app, logger

# Import API routes (this registers them with the app)
import api

# The app is ready to run
# Use: uvicorn main:app --host 0.0.0.0 --port 8080
