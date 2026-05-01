"""
Agent Andrea - Wegest Direct Booking Service
All selectors verified against actual Wegest HTML (March 2025)
Configuration, data classes, and global state
"""

from dotenv import load_dotenv
from datetime import datetime
from dataclasses import dataclass
from typing import Optional, Any
from pathlib import Path
import os
import asyncio
import logging

load_dotenv()

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# FastAPI app

# FastAPI app
from fastapi import FastAPI
app = FastAPI(title="Agent Andrea - Wegest Booking Service")

# Configuration
API_SECRET = os.environ.get("API_SECRET", "changeme")
DEBUG_SCREENSHOTS = os.environ.get("DEBUG_SCREENSHOTS", "false").lower() == "true"
WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&piattaforma=web"

# Catalog files
OPERATOR_CATALOG_FILE = Path("operator_catalog.json")
SERVICE_CATALOG_FILE = Path("service_catalog.json")

# Service duration fallback
SERVICE_DURATION_FALLBACK = {
    "colore": 30,
    "taglio": 25,
    "piega donna": 35,
    "filler": 15,
    "shampoo": 10,
    "taglio collaboratori": 30,
    "maschera": 5,
    "rituale specific": 30,
    "rigenerazionme": 15,
    "botox": 15,
    "booster": 15,
    "decolorazione": 15,
    "meches": 45,
    "shades": 45,
    "permanente": 30,
    "sfumature basic": 15,
    "sfumatura light": 15,
    "ritocco colore": 15,
    "acconciatura": 20,
    "colore ritocchino": 15,
    "tonalizzante": 15,
    "smooting": 30,
    "acconciatura sposa": 20,
    "manicure": 65,
    "zero crespo": 15,
    "ossigenazione": 40
}

# Locks
playwright_lock = asyncio.Lock()
call_states_lock = asyncio.Lock()
cache_lock = asyncio.Lock()
wegest_sessions_lock = asyncio.Lock()
pool_lock = asyncio.Lock()

# Global state
screenshots = {}

CACHE_FILE = Path("availability_cache.json")

call_states: dict[str, dict[str, Any]] = {}
CALL_STATE_TTL_SECONDS = 60 * 60  # 1 hour

availability_cache = {
    "updated_at": None,
    "days": {}
}

wegest_sessions: dict[str, 'WegestSession'] = {}
MAX_CONCURRENT_SESSIONS = 3
SESSION_IDLE_TTL_SECONDS = 60 * 15  # 15 minutes

wegest_pool: dict[str, 'WegestPoolSession'] = {}
conversation_to_pool_session: dict[str, str] = {}

POOL_SIZE = 2

operator_catalog = {
    "updated_at": None,
    "operators": {}
}

service_catalog = {
    "updated_at": None,
    "services": {}
}


@dataclass
class WegestSession:
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    lock: asyncio.Lock = None
    logged_in: bool = False
    agenda_open: bool = False
    last_used_at: Optional[datetime] = None

    def __post_init__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()


@dataclass
class WegestPoolSession:
    id: str
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    lock: asyncio.Lock = None
    logged_in: bool = False
    agenda_open: bool = False
    in_use: bool = False
    assigned_conversation_id: str | None = None
    last_used_at: Optional[datetime] = None

    def __post_init__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
