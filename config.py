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
LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

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
booking_lock = asyncio.Lock()

# Global state
screenshots = {}

CACHE_FILE = Path("availability_cache.json")

call_states: dict[str, dict[str, Any]] = {}
CALL_STATE_TTL_SECONDS = 60 * 60  # 1 hour

availability_cache = {
    "updated_at": None,
    "days": {}
}

availability_cache_ttl: dict[str, datetime] = {}
AVAILABILITY_CACHE_TTL_SECONDS = 60

wegest_sessions: dict[str, 'WegestSession'] = {}
MAX_CONCURRENT_SESSIONS = 3
SESSION_IDLE_TTL_SECONDS = 60 * 15  # 15 minutes

wegest_pool: dict[str, 'WegestPoolSession'] = {}
conversation_to_pool_session: dict[str, str] = {}

POOL_SIZE = 1

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
class BookingState:
    phase: str = "idle"  # idle, date_selected, time_selected, customer_selected, phone_confirmed, services_selected, ready_to_confirm, confirmed
    booked_date: str | None = None
    booked_time: str | None = None
    booked_operator: str | None = None
    customer_name: str | None = None
    customer_id: str | None = None
    customer_phone: str | None = None
    services: list = None
    operator_preference: str | None = None
    last_context_hash: str | None = None

    def __post_init__(self):
        if self.services is None:
            self.services = []

    def context_hash(self) -> str:
        return str(hash((self.booked_date, self.booked_time, self.customer_name, self.customer_phone, tuple(self.services), self.operator_preference)))

    def changed_from(self, other: 'BookingState') -> bool:
        if other is None:
            return True
        return (self.booked_date != other.booked_date or
                self.booked_time != other.booked_time or
                self.customer_name != other.customer_name or
                self.customer_phone != other.customer_phone or
                self.services != other.services or
                self.operator_preference != other.operator_preference)


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
    booking_state: Optional['BookingState'] = None
    previous_booking_state: Optional['BookingState'] = None

    def __post_init__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()
        if self.booking_state is None:
            self.booking_state = BookingState()
