"""
Agent Andrea - Wegest Direct Booking Service
Main application entry point.
"""

import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
import base64
import asyncio
from datetime import datetime

# Import configurations
from app.core.config import settings

# Import routers
from app.routers.booking import router as booking_router
from app.routers.context import router as context_router
from app.routers.session import router as session_router
from app.routers.admin import router as admin_router

# Import services for startup
from app.services.catalog import load_operator_catalog, load_service_catalog
from app.services.cache import load_cache_from_disk

logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Agent Andrea - Wegest Booking Service",
    description="Automated booking service for Wegest using Playwright",
    version="2.0.0"
)

# Include routers
app.include_router(booking_router)
app.include_router(context_router)
app.include_router(session_router)
app.include_router(admin_router)

# Import screenshots management
from app.core.screenshots import get_screenshots, clear_screenshots, add_screenshot


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "Agent Andrea Wegest Booking", "version": "2.0.0"}


@app.get("/screenshots", response_class=HTMLResponse)
async def view_screenshots(request: Request):
    """View captured screenshots (debug only)."""
    from app.core.auth import verify_api_secret_optional
    is_authed = await verify_api_secret_optional(request)

    if not is_authed and settings.is_production:
        return HTMLResponse("<h1>Unauthorized</h1>", status_code=401)

    screenshots = get_screenshots()
    if not screenshots:
        return "<h2>No screenshots yet — run a booking first</h2>"

    html = "<html><body style='background:#111;color:#fff;font-family:sans-serif;padding:20px'>"
    html += "<h1>🎬 Playwright Screenshots</h1>"
    for name, data in screenshots.items():
        html += f"<h3>📸 {name}</h3>"
        html += f"<img src='data:image/png;base64,{data}' style='max-width:100%;border:2px solid #555;margin-bottom:30px;display:block'><br>"
    html += "</body></html>"
    return html


@app.on_event("startup")
async def startup_event():
    """Initialize services on startup."""
    logger.info("🚀 Starting Agent Andrea Wegest Booking Service")

    # Load cached data
    load_cache_from_disk()
    load_operator_catalog()
    load_service_catalog()

    # Validate configuration
    if not settings.is_production:
        logger.warning("⚠️  API_SECRET is set to default value 'changeme' - NOT SECURE FOR PRODUCTION!")

    # Start background tasks
    from app.services.wegest import (
        cleanup_idle_wegest_sessions_forever,
        cleanup_call_states_forever,
        warm_pool_on_startup
    )

    asyncio.create_task(cleanup_call_states_forever())
    asyncio.create_task(cleanup_idle_wegest_sessions_forever())
    asyncio.create_task(warm_pool_on_startup())

    logger.info("✅ App started (background tasks running)")


# Export screenshots for use in other modules
def get_screenshots():
    return screenshots

def clear_screenshots():
    screenshots.clear()

def add_screenshot(name: str, data: str):
    screenshots[name] = data
