"""
Agent Andrea - Wegest Direct Booking Service
All selectors verified against actual Wegest HTML (March 2025)
"""

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright
from datetime import datetime
import os
import base64
import logging
import dotenv
import json
import asyncio
from pathlib import Path
from datetime import timedelta
from dataclasses import dataclass
from typing import Optional, Any


playwright_lock = asyncio.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI(title="Agent Andrea - Wegest Booking Service")
DEBUG_SCREENSHOTS = os.environ.get("DEBUG_SCREENSHOTS", "false").lower() == "true"
screenshots = {}

CACHE_FILE = Path("availability_cache.json")

call_states: dict[str, dict[str, Any]] = {}
call_states_lock = asyncio.Lock()
CALL_STATE_TTL_SECONDS = 60 * 60  # 1 hour

wegest_pool: dict[str, WegestPoolSession] = {}
conversation_to_pool_session: dict[str, str] = {}

POOL_SIZE = 2
pool_lock = asyncio.Lock()



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

availability_cache = {
    "updated_at": None,
    "days": {}
}

cache_lock = asyncio.Lock()

wegest_sessions: dict[str, WegestSession] = {}
wegest_sessions_lock = asyncio.Lock()
MAX_CONCURRENT_SESSIONS = 3
SESSION_IDLE_TTL_SECONDS = 60 * 15  # 15 minutes

def js_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")

class BookingRequest(BaseModel):
    customer_name: str
    caller_phone: str
    service: str | None = None
    services: list[str] = []
    operator_preference: str = "prima disponibile"
    preferred_date: str
    preferred_time: str
    conversation_id: str | None = None

class UpdateBookingContextRequest(BaseModel):
    conversation_id: str
    services: list[str] = []
    service: str | None = None
    operator_preference: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    customer_name: str | None = None
    caller_phone: str | None = None

class FinalizeBookingRequest(BaseModel):
    conversation_id: str

class GetBookingContextRequest(BaseModel):
    conversation_id: str

class AvailabilityRequest(BaseModel):
    preferred_date: str
    operator_preference: str = "prima disponibile"
    service: str | None = None
    services: list[str] = []
    conversation_id: str | None = None

class CheckBookingOptionsRequest(BaseModel):
    conversation_id: str

class PrepareLiveSessionRequest(BaseModel):
    conversation_id: str | None = None

class ServiceDurationRequest(BaseModel):
    service: str | None = None
    services: list[str] = []


API_SECRET = os.environ.get("API_SECRET", "changeme")
OPERATOR_CATALOG_FILE = Path("operator_catalog.json")
SERVICE_CATALOG_FILE = Path("service_catalog.json")
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



operator_catalog = {
    "updated_at": None,
    "operators": {}
}

service_catalog = {
    "updated_at": None,
    "services": {}
}



def load_operator_catalog():
    global operator_catalog
    try:
        if OPERATOR_CATALOG_FILE.exists():
            operator_catalog = json.loads(OPERATOR_CATALOG_FILE.read_text(encoding="utf-8"))
            logger.info("👥 Operator catalog loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load operator catalog: {e}")

def save_operator_catalog():
    try:
        OPERATOR_CATALOG_FILE.write_text(
            json.dumps(operator_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Operator catalog saved")
    except Exception as e:
        logger.warning(f"Failed to save operator catalog: {e}")

def load_service_catalog():
    global service_catalog
    try:
        if SERVICE_CATALOG_FILE.exists():
            service_catalog = json.loads(SERVICE_CATALOG_FILE.read_text(encoding="utf-8"))
            logger.info("🧴 Service catalog loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load service catalog: {e}")

def parse_optional_time_to_minutes(t: str | None) -> int | None:
    if not t:
        return None
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def build_operator_time_suggestions(
    operators: list[dict[str, Any]],
    requested_time: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns:
      - exact operators available at requested time
      - nearest operator/time alternatives if no exact match
    """
    requested_minutes = parse_optional_time_to_minutes(requested_time)
    if requested_minutes is None:
        return [], []

    exact_matches = []
    nearest = []

    for op in operators:
        valid_times = op.get("valid_start_times") or op.get("available_slots") or []
        for t in valid_times:
            mins = quarter_time_to_minutes(t)
            delta = abs(mins - requested_minutes)

            if mins == requested_minutes:
                exact_matches.append({
                    "name": op["name"],
                    "id": op["id"],
                    "time": t,
                    "delta_minutes": 0
                })
            else:
                nearest.append({
                    "name": op["name"],
                    "id": op["id"],
                    "time": t,
                    "delta_minutes": delta
                })

    nearest.sort(key=lambda x: (x["delta_minutes"], x["time"], x["name"]))

    # Keep only closest 5 alternatives
    if exact_matches:
        return exact_matches, []

    return [], nearest[:5]


def save_service_catalog():
    try:
        SERVICE_CATALOG_FILE.write_text(
            json.dumps(service_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Service catalog saved")
    except Exception as e:
        logger.warning(f"Failed to save service catalog: {e}")

def get_missing_booking_fields(state: dict[str, Any]) -> list[str]:
    missing = []

    services = state.get("services") or []
    if not services:
        missing.append("services")

    if not state.get("operator_preference"):
        missing.append("operator_preference")

    if not state.get("preferred_date"):
        missing.append("preferred_date")

    if not state.get("preferred_time"):
        missing.append("preferred_time")

    if not state.get("customer_name"):
        missing.append("customer_name")

    if not state.get("caller_phone"):
        missing.append("caller_phone")

    return missing

def load_cache_from_disk():
    global availability_cache
    try:
        if CACHE_FILE.exists():
            availability_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            logger.info("📦 Availability cache loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load cache from disk: {e}")

def normalize_requested_services(service: str | None, services: list[str]) -> list[str]:
    out = [s.strip() for s in (services or []) if s and s.strip()]
    if not out and service:
        out = [service.strip()]
    return out

def ceil_to_quarter(minutes: int) -> int:
    if minutes <= 0:
        return 0
    return ((minutes + 14) // 15) * 15

def quarter_time_to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)

def minutes_to_quarter_time(total: int) -> str:
    h = str(total // 60).zfill(2)
    m = str(total % 60).zfill(2)
    return f"{h}:{m}"

def normalize_date_to_iso(date_str: str | None) -> str | None:
    if not date_str:
        return date_str

    date_str = date_str.strip()

    # already ISO
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    # dd-MM-yyyy
    try:
        dt = datetime.strptime(date_str, "%d-%m-%Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return date_str
    
def compute_valid_start_times(available_slots: list[str], required_operator_minutes: int) -> list[str]:
    if required_operator_minutes <= 0:
        return sorted(set(available_slots))

    required_block = ceil_to_quarter(required_operator_minutes)
    required_steps = required_block // 15

    available_set = set(available_slots)
    valid = []

    for slot in sorted(available_slots):
        start = quarter_time_to_minutes(slot)
        ok = True
        for i in range(required_steps):
            t = minutes_to_quarter_time(start + i * 15)
            if t not in available_set:
                ok = False
                break
        if ok:
            valid.append(slot)

    return valid

async def extract_service_operator_durations_from_page(page) -> dict:
    durations = await page.evaluate("""
        () => {
            const map = {};
            document.querySelectorAll('.pulsanti_tab .servizio').forEach(s => {
                const nome = (s.getAttribute('nome') || '').toLowerCase().trim();
                const tempoOperatore = parseInt(s.getAttribute('tempo_operatore') || '0', 10);
                if (nome) {
                    map[nome] = tempoOperatore;
                }
            });
            return map;
        }
    """)
    return durations or {}

def save_cache_to_disk():
    try:
        CACHE_FILE.write_text(
            json.dumps(availability_cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Availability cache saved to disk")
    except Exception as e:
        logger.warning(f"Failed to save cache to disk: {e}")

async def update_operator_catalog_from_page(page):
    global operator_catalog

    try:
        found = await page.evaluate("""
            () => {
                const result = {};
                document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                    const id = op.getAttribute('id_operatore');
                    if (!id || id === '0') return;

                    const nome = op.querySelector('.nome');
                    if (!nome) return;

                    result[id] = {
                        name: nome.textContent.trim(),
                        active: !op.classList.contains('assente')
                    };
                });
                return result;
            }
        """)

        if found and isinstance(found, dict):
            for op_id, info in found.items():
                operator_catalog["operators"][op_id] = info

            operator_catalog["updated_at"] = datetime.utcnow().isoformat()
            save_operator_catalog()
            logger.info(f"👥 Operator catalog updated: {list(found.values())}")

    except Exception as e:
        logger.warning(f"Failed to update operator catalog from page: {e}")

async def update_service_catalog_from_page(page):
    global service_catalog

    try:
        found = await page.evaluate("""
            () => {
                const result = {};
                document.querySelectorAll('.pulsanti_tab .servizio[nome]').forEach(s => {
                    const nome = (s.getAttribute('nome') || '').trim();
                    if (!nome) return;

                    const key = nome.toLowerCase();
                    result[key] = {
                        id: s.id || '',
                        nome: nome,
                        tempo_operatore: parseInt(s.getAttribute('tempo_operatore') || '0', 10),
                        tempo_cliente: parseInt(s.getAttribute('tempo_cliente') || '0', 10)
                    };
                });
                return result;
            }
        """)

        if found and isinstance(found, dict):
            for key, info in found.items():
                service_catalog["services"][key] = info

            service_catalog["updated_at"] = datetime.utcnow().isoformat()
            save_service_catalog()
            logger.info(f"🧴 Service catalog updated: {list(found.keys())[:10]}")

    except Exception as e:
        logger.warning(f"Failed to update service catalog from page: {e}")

async def get_call_state(conversation_id: str) -> dict[str, Any]:
    async with call_states_lock:
        state = call_states.get(conversation_id)
        if not state:
            state = {
                "conversation_id": conversation_id,
                "services": [],
                "operator_preference": None,
                "preferred_date": None,
                "preferred_time": None,
                "customer_name": None,
                "caller_phone": None,
                "last_availability_result": None,
                "booking_confirmed": False,
                "updated_at": datetime.utcnow().isoformat()
            }
            call_states[conversation_id] = state
        return state.copy()

async def update_call_state(conversation_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    async with call_states_lock:
        state = call_states.get(conversation_id)
        if not state:
            state = {
                "conversation_id": conversation_id,
                "services": [],
                "operator_preference": None,
                "preferred_date": None,
                "preferred_time": None,
                "customer_name": None,
                "caller_phone": None,
                "last_availability_result": None,
                "booking_confirmed": False,
                "updated_at": datetime.utcnow().isoformat()
            }

        state.update(updates)
        state["updated_at"] = datetime.utcnow().isoformat()
        call_states[conversation_id] = state
        return state.copy()

async def clear_call_state(conversation_id: str):
    async with call_states_lock:
        if conversation_id in call_states:
            del call_states[conversation_id]
            logger.info(f"🧹 Cleared call state for {conversation_id}")

async def get_or_create_wegest_session(conversation_id: str) -> WegestSession:
    async with wegest_sessions_lock:
        session = wegest_sessions.get(conversation_id)
        if session:
            return session

        active_count = len(wegest_sessions)
        if active_count >= MAX_CONCURRENT_SESSIONS:
            raise Exception(f"Maximum concurrent live sessions reached ({MAX_CONCURRENT_SESSIONS})")

        session = WegestSession()
        wegest_sessions[conversation_id] = session
        logger.info(f"🆕 Created Wegest session for {conversation_id}")
        return session

async def reset_wegest_session(conversation_id: str):
    async with wegest_sessions_lock:
        session = wegest_sessions.get(conversation_id)
        if not session:
            return

    try:
        if session.page:
            await session.page.close()
    except Exception:
        pass
    try:
        if session.context:
            await session.context.close()
    except Exception:
        pass
    try:
        if session.browser:
            await session.browser.close()
    except Exception:
        pass
    try:
        if session.playwright:
            await session.playwright.stop()
    except Exception:
        pass

    async with wegest_sessions_lock:
        wegest_sessions.pop(conversation_id, None)

    logger.info(f"♻️ Wegest session reset for {conversation_id}")

async def cleanup_expired_call_states():
    async with call_states_lock:
        now = datetime.utcnow()
        expired = []

        for conversation_id, state in call_states.items():
            try:
                updated_at = datetime.fromisoformat(state["updated_at"])
                age = (now - updated_at).total_seconds()
                if age > CALL_STATE_TTL_SECONDS:
                    expired.append(conversation_id)
            except Exception:
                expired.append(conversation_id)

        for conversation_id in expired:
            del call_states[conversation_id]

        if expired:
            logger.info(f"🧹 Cleaned {len(expired)} expired call states")

async def set_cached_day(date_str: str, payload: dict):
    async with cache_lock:
        availability_cache["days"][date_str] = payload
        availability_cache["updated_at"] = datetime.utcnow().isoformat()
        save_cache_to_disk()

async def is_wegest_session_alive(conversation_id: str) -> bool:
    try:
        session = await get_or_create_wegest_session(conversation_id)
        if not session.page:
            return False
        if session.page.is_closed():
            return False

        state = await session.page.evaluate("""() => {
            const loginPanel = document.getElementById('pannello_login');
            const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
            const menu = document.getElementById('menu');

            return {
                loginVisible: loginPanel ? getComputedStyle(loginPanel).display !== 'none' : false,
                hasAgendaButton: !!agendaBtn,
                hasMenu: !!menu
            };
        }""")

        # Session is valid only if login is NOT visible
        # and the app shell/menu or agenda button exists
        return (
            not state.get("loginVisible", False)
            and (state.get("hasAgendaButton", False) or state.get("hasMenu", False))
        )

    except Exception:
        return False


async def get_assigned_pool_session(conversation_id: str) -> WegestPoolSession | None:
    async with pool_lock:
        pool_id = conversation_to_pool_session.get(conversation_id)
        if not pool_id:
            return None
        return wegest_pool.get(pool_id)


async def assign_idle_pool_session_to_conversation(conversation_id: str) -> WegestPoolSession:
    async with pool_lock:
        # If already assigned, return existing
        existing_pool_id = conversation_to_pool_session.get(conversation_id)
        if existing_pool_id and existing_pool_id in wegest_pool:
            return wegest_pool[existing_pool_id]

        # Find idle session
        for pool_id, session in wegest_pool.items():
            if not session.in_use:
                session.in_use = True
                session.assigned_conversation_id = conversation_id
                session.last_used_at = datetime.utcnow()
                conversation_to_pool_session[conversation_id] = pool_id
                logger.info(f"🔗 Assigned {pool_id} to conversation {conversation_id}")
                return session

    raise Exception("No warm session available in pool")
    

async def ensure_wegest_browser(conversation_id: str):
    session = await get_or_create_wegest_session(conversation_id)

    if session.page and not session.page.is_closed():
        return session

    await reset_wegest_session(conversation_id)
    session = await get_or_create_wegest_session(conversation_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    session.playwright = p
    session.browser = browser
    session.context = context
    session.page = page
    session.logged_in = False
    session.agenda_open = False
    session.last_used_at = datetime.utcnow()

    logger.info(f"🌐 Wegest browser session created for {conversation_id}")
    return session

async def ensure_wegest_logged_in(conversation_id: str):
    WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

    session = await ensure_wegest_browser(conversation_id)

    if await is_wegest_session_alive(conversation_id):
        session.logged_in = True
        session.last_used_at = datetime.utcnow()
        logger.info(f"✅ Reusing existing logged-in session for {conversation_id}")
        return session

    page = session.page

    logger.info(f"🔐 Logging into Wegest session for {conversation_id}...")
    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(5000)

    await page.fill("input[name='username']", WEGEST_USER)
    await page.fill("input[name='password']", WEGEST_PASSWORD)
    await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")

    await page.click("div.button")

    try:
        await page.wait_for_function(
            """() => {
                const lp = document.getElementById('pannello_login');
                return lp && getComputedStyle(lp).display === 'none';
            }""",
            timeout=60000
        )
    except Exception:
        pass

    await page.wait_for_timeout(30000)

    login_visible = await page.evaluate("""() => {
        const el = document.getElementById('pannello_login');
        return el ? getComputedStyle(el).display !== 'none' : false;
    }""")

    if login_visible:
        raise Exception("Login failed — panel still visible")

    session.logged_in = True
    session.agenda_open = False
    session.last_used_at = datetime.utcnow()

    logger.info(f"🎉 Wegest session login successful for {conversation_id}")

    await dismiss_system_modals(page, "post-login")
    await page.wait_for_timeout(2000)

    return session

async def ensure_wegest_agenda_open(conversation_id: str):
    session = await ensure_wegest_logged_in(conversation_id)
    page = session.page

    agenda_visible = await page.evaluate("""() => {
        const a = document.getElementById('pannello_agenda');
        return a ? getComputedStyle(a).display !== 'none' : false;
    }""")

    if agenda_visible:
        session.agenda_open = True
        session.last_used_at = datetime.utcnow()
        logger.info(f"📅 Agenda already open for {conversation_id}")
        await update_operator_catalog_from_page(page)
        return session

    agenda_button_exists = await page.evaluate("""() => {
        return !!document.querySelector("[pannello='pannello_agenda']");
    }""")

    if not agenda_button_exists:
        logger.warning(f"Agenda button not found for {conversation_id}, resetting session...")
        await reset_wegest_session(conversation_id)
        session = await ensure_wegest_logged_in(conversation_id)
        page = session.page

    logger.info(f"📅 Opening agenda in existing session for {conversation_id}...")
    await page.click("[pannello='pannello_agenda']")
    await page.wait_for_timeout(5000)
    await dismiss_system_modals(page, "after-agenda")
    await page.wait_for_timeout(2000)

    session.agenda_open = True
    session.last_used_at = datetime.utcnow()
    await update_operator_catalog_from_page(page)

    return session

async def get_cached_day(date_str: str):
    async with cache_lock:
        return availability_cache["days"].get(date_str)

async def invalidate_cached_day(date_str: str):
    async with cache_lock:
        if date_str in availability_cache["days"]:
            del availability_cache["days"][date_str]
            availability_cache["updated_at"] = datetime.utcnow().isoformat()
            save_cache_to_disk()
            logger.info(f"🗑️ Invalidated cache for {date_str}")

async def reset_pool_session(pool_id: str):
    async with pool_lock:
        session = wegest_pool.get(pool_id)
        if not session:
            return

    try:
        if session.page:
            await session.page.close()
    except Exception:
        pass
    try:
        if session.context:
            await session.context.close()
    except Exception:
        pass
    try:
        if session.browser:
            await session.browser.close()
    except Exception:
        pass
    try:
        if session.playwright:
            await session.playwright.stop()
    except Exception:
        pass

    async with pool_lock:
        wegest_pool.pop(pool_id, None)

    logger.info(f"♻️ Pool session reset: {pool_id}")


async def create_and_warm_pool_session(pool_id: str):
    WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

    session = WegestPoolSession(id=pool_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    context = await browser.new_context(
        viewport={"width": 1280, "height": 900},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
    )
    page = await context.new_page()

    session.playwright = p
    session.browser = browser
    session.context = context
    session.page = page
    session.last_used_at = datetime.utcnow()

    logger.info(f"🔥 Warming pool session {pool_id}...")

    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
    await page.wait_for_timeout(5000)

    await page.fill("input[name='username']", WEGEST_USER)
    await page.fill("input[name='password']", WEGEST_PASSWORD)
    await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")

    await page.click("div.button")

    try:
        await page.wait_for_function(
            """() => {
                const lp = document.getElementById('pannello_login');
                return lp && getComputedStyle(lp).display === 'none';
            }""",
            timeout=60000
        )
    except Exception:
        pass

    await page.wait_for_timeout(30000)

    login_visible = await page.evaluate("""() => {
        const el = document.getElementById('pannello_login');
        return el ? getComputedStyle(el).display !== 'none' : false;
    }""")
    if login_visible:
        raise Exception(f"Pool session {pool_id} login failed")

    session.logged_in = True
    logger.info(f"✅ Pool session {pool_id} logged in")

    await dismiss_system_modals(page, "post-login")
    await page.wait_for_timeout(2000)

    await page.click("[pannello='pannello_agenda']")
    await page.wait_for_timeout(5000)
    await dismiss_system_modals(page, "after-agenda")
    await page.wait_for_timeout(2000)

    session.agenda_open = True
    logger.info(f"📅 Pool session {pool_id} agenda ready")

    async with pool_lock:
        wegest_pool[pool_id] = session

async def get_live_session_for_conversation(conversation_id: str) -> WegestPoolSession:
    session = await get_assigned_pool_session(conversation_id)
    if session:
        return session

    # Fallback: assign one now if available
    return await assign_idle_pool_session_to_conversation(conversation_id)
    

async def warm_pool_on_startup():
    await asyncio.sleep(5)

    for i in range(POOL_SIZE):
        pool_id = f"pool_{i+1}"
        try:
            await create_and_warm_pool_session(pool_id)
        except Exception as e:
            logger.warning(f"Failed to warm {pool_id}: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "Agent Andrea Wegest Booking"}

@app.get("/screenshots", response_class=HTMLResponse)
async def view_screenshots():
    if not screenshots:
        return "<h2>No screenshots yet — run a booking first</h2>"
    html = "<html><body style='background:#111;color:#fff;font-family:sans-serif;padding:20px'>"
    html += "<h1>🎬 Playwright Screenshots</h1>"
    for name, data in screenshots.items():
        html += f"<h3>📸 {name}</h3>"
        html += f"<img src='data:image/png;base64,{data}' style='max-width:100%;border:2px solid #555;margin-bottom:30px;display:block'><br>"
    html += "</body></html>"
    return html

async def snap(page, name: str, force: bool = False):
    if not DEBUG_SCREENSHOTS and not force:
        return
    try:
        data = await page.screenshot(type="png", full_page=True)
        screenshots[name] = base64.b64encode(data).decode()
        logger.info(f"📸 {name}")
    except Exception as e:
        logger.warning(f"Screenshot failed ({name}): {e}")

async def dismiss_system_modals(page, label=""):
    logger.info(f"🔍 Modal sweep: {label}")
    for attempt in range(5):
        modal_visible = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return false;
                const style = window.getComputedStyle(modal);
                return style.display !== 'none' && style.visibility !== 'hidden';
            }
        """)
        if not modal_visible:
            break
        logger.info(f"  ⚠️ System modal (attempt {attempt + 1})")
        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;
                const testo1 = modal.querySelector('.testo1');
                const txt = testo1 ? testo1.textContent.toLowerCase() : '';
                if (txt.includes('cassa') || txt.includes('passaggio')) {
                    const b = modal.querySelector('.button.avviso');
                    if (b) { b.click(); return 'annulla-cassa'; }
                }
                const c = modal.querySelector('.button.conferma');
                if (c && getComputedStyle(c).display !== 'none') { c.click(); return 'conferma'; }
                const x = modal.querySelector('.button.chiudi');
                if (x && getComputedStyle(x).display !== 'none') { x.click(); return 'chiudi'; }
                const a = modal.querySelector('.button.avviso');
                if (a && getComputedStyle(a).display !== 'none') { a.click(); return 'avviso'; }
                modal.style.display = 'none';
                return 'force-hidden';
            }
        """)
        logger.info(f"  → {clicked}")
        await page.wait_for_timeout(2500)
    await page.evaluate("""
        () => document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay').forEach(el => {
            if (getComputedStyle(el).display !== 'none') el.style.display = 'none';
        })
    """)

@app.post("/book")
async def book_appointment(request: Request, booking: BookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    screenshots.clear()
    logger.info(f"📅 Booking: {booking.customer_name} | {booking.service or booking.services} | {booking.preferred_date} {booking.preferred_time}")

    if booking.conversation_id:
        await update_call_state(booking.conversation_id, {
            "customer_name": booking.customer_name,
            "caller_phone": booking.caller_phone,
            "preferred_date": booking.preferred_date,
            "preferred_time": booking.preferred_time,
            "operator_preference": booking.operator_preference,
            "services": normalize_requested_services(booking.service, booking.services),
            "booking_confirmed": True
        })
        logger.info(f"🧠 Updated call state from booking for {booking.conversation_id}")

    result = await run_wegest_booking(booking)

    if booking.conversation_id and result.get("success"):
        await clear_call_state(booking.conversation_id)

    return result

@app.post("/check-availability")
async def check_availability(request: Request, avail: AvailabilityRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    screenshots.clear()
    logger.info(f"🔍 Availability check: {avail.preferred_date}")

    result = await run_availability_check(avail)

    if avail.conversation_id:
        await update_call_state(avail.conversation_id, {
            "preferred_date": avail.preferred_date,
            "operator_preference": avail.operator_preference,
            "services": normalize_requested_services(avail.service, avail.services),
            "last_availability_result": result
        })
        logger.info(f"🧠 Updated call state from availability for {avail.conversation_id}")

    return result

async def run_wegest_booking(request: BookingRequest) -> dict:
    if not request.conversation_id:
        raise Exception("conversation_id is required for live booking")

    session = await get_live_session_for_conversation(request.conversation_id)

    async with session.lock:
        page = None

        try:
            page = session.page

            state_ok = await page.evaluate("""() => {
                const loginPanel = document.getElementById('pannello_login');
                const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
                const menu = document.getElementById('menu');

                return (
                    !(loginPanel && getComputedStyle(loginPanel).display !== 'none') &&
                    (!!agendaBtn || !!menu)
                );
            }""")

            if not state_ok:
                raise Exception("Assigned pool session is not ready for booking")

            session.last_used_at = datetime.utcnow()

            screenshots.clear()
            logger.info(f"📅 Booking: {request.customer_name} | {request.service or request.services} | {request.preferred_date} {request.preferred_time}")

            requested_services = [s.strip() for s in request.services if s and s.strip()]
            if not requested_services and request.service:
                requested_services = [request.service.strip()]
            if not requested_services:
                raise Exception("No service provided")

            logger.info(f"Requested services: {requested_services}")

            # STEP 5: Click date
            target = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day, month, year = target.day, target.month, target.year
            logger.info(f"Step 5: Date {day}/{month}/{year}...")

            await dismiss_system_modals(page, "before-date")

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            try:
                await page.click(date_selector, timeout=10000)
                logger.info("✅ Date clicked")
            except Exception:
                raise Exception(f"Date {day}/{month}/{year} not visible on calendar")

            logger.info("Waiting for grid...")
            try:
                await page.wait_for_function(
                    f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
                    timeout=15000
                )
            except Exception:
                await page.click(date_selector, timeout=5000)
                await page.wait_for_timeout(5000)

            await page.wait_for_timeout(2000)
            await dismiss_system_modals(page, "after-date")
            await snap(page, "05_date")

            # STEP 6: Click time slot
            raw_hour = int(request.preferred_time.split(":")[0])
            raw_minute = int(request.preferred_time.split(":")[1]) if ":" in request.preferred_time else 0
            rounded_minute = (raw_minute // 15) * 15
            hour = str(raw_hour)
            minute = str(rounded_minute)

            logger.info(f"Step 6: Time {hour}:{minute} | operator pref: {request.operator_preference}")

            operator_map = await page.evaluate("""
                () => {
                    const map = {};
                    document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                        const id = op.getAttribute('id_operatore');
                        const nome = op.querySelector('.nome');
                        if (id && nome) {
                            map[nome.textContent.trim().toLowerCase()] = id;
                        }
                    });
                    return map;
                }
            """)
            logger.info(f"Operator map: {operator_map}")

            preferred_op_id = None
            operator_pref = request.operator_preference.strip().lower()

            if operator_pref != "prima disponibile":
                for name, op_id in operator_map.items():
                    if operator_pref in name:
                        preferred_op_id = op_id
                        break
                logger.info(f"Preferred operator id: {preferred_op_id}")

            time_clicked = False
            actual_time = f"{hour}:{minute}"
            clicked_operator_id = preferred_op_id

            def exact_selector(op_id=None, h=None, m=None):
                h = h if h is not None else hour
                m = m if m is not None else minute
                base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}'][minuto='{m}']"
                if op_id:
                    base += f"[id_operatore='{op_id}']"
                base += ":not(.assente):not(.occupata)"
                return base

            def hour_selector(op_id=None, h=None):
                h = h if h is not None else hour
                base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}']"
                if op_id:
                    base += f"[id_operatore='{op_id}']"
                base += ":not(.assente):not(.occupata)"
                return base

            if preferred_op_id:
                logger.info(f"Trying specific operator slot for id_operatore={preferred_op_id}")

                try:
                    sel = exact_selector(op_id=preferred_op_id)
                    count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                    logger.info(f"Specific op exact count: {count}")
                    if count > 0:
                        await page.click(sel, timeout=5000)
                        time_clicked = True
                        clicked_operator_id = preferred_op_id
                        logger.info(f"✅ Clicked exact slot for preferred operator {preferred_op_id}")
                except Exception as e:
                    logger.warning(f"Specific operator exact click failed: {e}")

                if not time_clicked:
                    try:
                        sel = hour_selector(op_id=preferred_op_id)
                        count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                        logger.info(f"Specific op hour count: {count}")
                        if count > 0:
                            actual_min = await page.evaluate(f"""
                                () => {{
                                    const cell = document.querySelector("{sel}");
                                    return cell ? cell.getAttribute('minuto') : null;
                                }}
                            """)
                            await page.click(sel, timeout=5000)
                            actual_time = f"{hour}:{actual_min or '0'}"
                            time_clicked = True
                            clicked_operator_id = preferred_op_id
                            logger.info(f"✅ Clicked same-hour fallback for preferred operator: {actual_time}")
                    except Exception as e:
                        logger.warning(f"Specific operator hour fallback failed: {e}")

                if not time_clicked:
                    logger.info("Trying next available hour for preferred operator...")
                    for try_hour in range(raw_hour + 1, 20):
                        try:
                            sel = hour_selector(op_id=preferred_op_id, h=str(try_hour))
                            count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                            if count > 0:
                                actual_min = await page.evaluate(f"""
                                    () => {{
                                        const cell = document.querySelector("{sel}");
                                        return cell ? cell.getAttribute('minuto') : '0';
                                    }}
                                """)
                                await page.click(sel, timeout=5000)
                                actual_time = f"{try_hour}:{actual_min}"
                                time_clicked = True
                                clicked_operator_id = preferred_op_id
                                logger.info(f"✅ Clicked next available for preferred operator: {actual_time}")
                                break
                        except Exception:
                            continue

                if not time_clicked:
                    raise Exception(
                        f"No available slot for operator '{request.operator_preference}' on {request.preferred_date} around {request.preferred_time}"
                    )

            else:
                logger.info("Using prima disponibile logic")

                try:
                    sel = exact_selector()
                    count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                    logger.info(f"Any-op exact count: {count}")
                    if count > 0:
                        clicked_operator_id = await page.evaluate(f"""
                            () => {{
                                const cell = document.querySelector("{sel}");
                                return cell ? cell.getAttribute('id_operatore') : null;
                            }}
                        """)
                        await page.click(sel, timeout=5000)
                        time_clicked = True
                        logger.info(f"✅ Clicked exact slot for first available operator {clicked_operator_id}")
                except Exception as e:
                    logger.warning(f"Any-op exact click failed: {e}")

                if not time_clicked:
                    try:
                        sel = hour_selector()
                        count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                        logger.info(f"Any-op hour count: {count}")
                        if count > 0:
                            result = await page.evaluate(f"""
                                () => {{
                                    const cell = document.querySelector("{sel}");
                                    if (!cell) return null;
                                    return {{
                                        minuto: cell.getAttribute('minuto'),
                                        op: cell.getAttribute('id_operatore')
                                    }};
                                }}
                            """)
                            await page.click(sel, timeout=5000)
                            actual_time = f"{hour}:{result['minuto'] if result else '0'}"
                            clicked_operator_id = result['op'] if result else None
                            time_clicked = True
                            logger.info(f"✅ Clicked same-hour first available: {actual_time} | op={clicked_operator_id}")
                    except Exception as e:
                        logger.warning(f"Any-op hour fallback failed: {e}")

                if not time_clicked:
                    logger.info("Trying next available hour for any operator...")
                    for try_hour in range(raw_hour + 1, 20):
                        try:
                            sel = hour_selector(h=str(try_hour))
                            count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                            if count > 0:
                                result = await page.evaluate(f"""
                                    () => {{
                                        const cell = document.querySelector("{sel}");
                                        if (!cell) return null;
                                        return {{
                                            minuto: cell.getAttribute('minuto'),
                                            op: cell.getAttribute('id_operatore')
                                        }};
                                    }}
                                """)
                                await page.click(sel, timeout=5000)
                                actual_time = f"{try_hour}:{result['minuto'] if result else '0'}"
                                clicked_operator_id = result['op'] if result else None
                                time_clicked = True
                                logger.info(f"✅ Clicked next available: {actual_time} | op={clicked_operator_id}")
                                break
                        except Exception:
                            continue

            if not time_clicked:
                raise Exception(f"No available slot on {day}/{month}/{year}")

            logger.info(f"Final clicked slot: {actual_time} | id_operatore={clicked_operator_id}")
            await page.wait_for_timeout(3000)
            await snap(page, "06_time")

            # STEP 7: Customer search & selection
            logger.info(f"Step 7: Customer '{request.customer_name}'...")
            customer_found = False

            name_parts = request.customer_name.strip().split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[-1] if len(name_parts) > 1 else ""
            first_safe = js_escape(first_name.lower())
            last_safe = js_escape(last_name.lower())

            search_phone = request.caller_phone
            if search_phone.startswith("+39"):
                search_phone = search_phone[3:]
            elif search_phone.startswith("0039"):
                search_phone = search_phone[4:]
            phone_safe = js_escape(search_phone)

            try:
                await page.wait_for_selector(
                    ".cerca_cliente.modale input[name='cerca_cliente']",
                    timeout=10000
                )
                logger.info("✅ Customer search modal open")

                match_js = f"""
                    () => {{
                        const first = '{first_safe}';
                        const last = '{last_safe}';
                        const rows = document.querySelectorAll('.tabella_clienti tbody tr[id]');
                        const results = [];
                        for (const row of rows) {{
                            const p = row.querySelector('p.cliente');
                            if (!p) continue;
                            const text = p.textContent.toLowerCase().trim();
                            results.push({{
                                id: row.id,
                                name: p.textContent.trim(),
                                hasFirst: text.includes(first),
                                hasLast: last ? text.includes(last) : false
                            }});
                        }}
                        if (last) {{
                            for (const r of results) {{
                                if (r.hasFirst && r.hasLast) {{
                                    document.getElementById(r.id).click();
                                    return {{ found: true, id: r.id, name: r.name, method: 'both_names' }};
                                }}
                            }}
                        }}
                        if (!last) {{
                            for (const r of results) {{
                                if (r.hasFirst) {{
                                    document.getElementById(r.id).click();
                                    return {{ found: true, id: r.id, name: r.name, method: 'first_only' }};
                                }}
                            }}
                        }}
                        return {{
                            found: false,
                            count: results.length,
                            candidates: results.map(r => r.name).slice(0, 5)
                        }};
                    }}
                """

                logger.info(f"  Search 1: '{request.customer_name}'")
                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", request.customer_name)
                await page.wait_for_timeout(3000)
                await snap(page, "07a_full")
                match = await page.evaluate(match_js)
                if match and match.get('found'):
                    customer_found = True
                    logger.info(f"✅ Match: {match}")

                if not customer_found:
                    logger.info(f"  Search 2: '{first_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07b_first")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                if not customer_found and last_name:
                    logger.info(f"  Search 3: '{last_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", last_name)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07c_last")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                if not customer_found and search_phone:
                    logger.info(f"  Search 4: phone '{search_phone}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", search_phone)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07d_phone")
                    match = await page.evaluate("""
                        () => {
                            const rows = document.querySelectorAll('.tabella_clienti tbody tr[id]');
                            if (rows.length === 1) {
                                rows[0].click();
                                const p = rows[0].querySelector('p.cliente');
                                return { found: true, id: rows[0].id, name: p ? p.textContent.trim() : '?', method: 'phone' };
                            }
                            return { found: false, count: rows.length };
                        }
                    """)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Phone: {match}")

                if not customer_found:
                    logger.info("  ❌ Not found → creating new customer")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", "")
                    await page.wait_for_timeout(500)

                    await page.evaluate("""
                        () => {
                            const btn = document.querySelector(
                                '.cerca_cliente .pulsanti .button.rimira.primary.aggiungi'
                            );
                            if (btn) btn.click();
                        }
                    """)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07e_new_form")

                    await page.evaluate(f"""
                        () => {{
                            const inp = document.querySelector('.form_cliente input[name="nome"]');
                            if (inp) {{
                                inp.value = '{js_escape(first_name)}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }}
                        }}
                    """)

                    await page.evaluate(f"""
                        () => {{
                            const inp = document.querySelector('.form_cliente input[name="cognome"]');
                            if (inp) {{
                                inp.value = '{js_escape(last_name)}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }}
                        }}
                    """)

                    await page.evaluate(f"""
                        () => {{
                            const inp = document.querySelector('.form_cliente input[name="cellulare"]');
                            if (inp) {{
                                inp.value = '{phone_safe}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }}
                        }}
                    """)

                    logger.info(f"  Filled: {first_name} {last_name} / {search_phone}")
                    await snap(page, "07f_filled")

                    saved = await page.evaluate("""
                        () => {
                            const btn = document.querySelector(
                                '.form_cliente .modale_footer .button.rimira.primary.aggiungi'
                            );
                            if (btn) {
                                btn.click();
                                return { clicked: true, method: 'form_cliente' };
                            }
                            return { clicked: false };
                        }
                    """)

                    logger.info(f"  Save: {saved}")

                    if saved and saved.get('clicked'):
                        customer_found = True
                        logger.info("✅ New customer created")
                    else:
                        logger.warning("⚠️ Could not click Add customer!")

                    await page.wait_for_timeout(4000)
                    await snap(page, "07g_saved")
                    await dismiss_system_modals(page, "after-new-customer")

            except Exception as e:
                logger.warning(f"Customer error: {e}")
                await snap(page, "07_ERROR")

            await page.wait_for_timeout(2000)

            # STEP 7.5: Phone modal
            phone_handled = await page.evaluate(f"""
                () => {{
                    const m = document.querySelector('.modale.card.inserisci_cellulare');
                    if (!m) return {{ visible: false }};
                    if (getComputedStyle(m).display === 'none') return {{ visible: false }};
                    const inp = m.querySelector('input[name="cellulare"]');
                    if (inp) {{
                        inp.value = '{phone_safe}';
                        inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                        inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                    }}
                    const btn = m.querySelector('.button.rimira.primary.conferma');
                    if (btn) {{
                        btn.click();
                        return {{ visible: true, filled: true, confirmed: true }};
                    }}
                    return {{ visible: true, filled: !!inp, confirmed: false }};
                }}
            """)
            if phone_handled and phone_handled.get('visible'):
                logger.info(f"📱 Phone modal: {phone_handled}")
                await page.wait_for_timeout(2000)
            else:
                logger.info("📱 No phone modal")

            await snap(page, "08_form_ready")

            # STEP 8: Select services
            logger.info(f"Step 8: Services {requested_services}...")

            initial_rows = await page.evaluate("""
                () => document.querySelectorAll('.servizi_selezionati .riga_servizio').length
            """)

            selected_services = []

            for index, requested_service in enumerate(requested_services, start=1):
                service_kw = js_escape(requested_service.lower())
                logger.info(f"Selecting service {index}/{len(requested_services)}: {requested_service}")

                service_selected = await page.evaluate(f"""
                    () => {{
                        const kw = '{service_kw}';
                        const all = document.querySelectorAll('.pulsanti_tab .servizio');

                        for (const s of all) {{
                            if ((s.getAttribute('nome') || '').toLowerCase() === kw) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'exact' }};
                            }}
                        }}

                        for (const s of all) {{
                            const nome = (s.getAttribute('nome') || '').toLowerCase();
                            if (nome.startsWith(kw)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'starts' }};
                            }}
                        }}

                        for (const s of all) {{
                            const nome = (s.getAttribute('nome') || '').toLowerCase();
                            if (nome.includes(kw)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'contains' }};
                            }}
                        }}

                        for (const s of all) {{
                            const nome = (s.getAttribute('nome') || '').toLowerCase();
                            if (nome.length > 2 && kw.includes(nome)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'reverse' }};
                            }}
                        }}

                        for (const s of all) {{
                            const txt = (s.querySelector('.nome')?.textContent || s.textContent || '').toLowerCase().trim();
                            if (txt === kw || txt.includes(kw) || kw.includes(txt)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome') || txt, id: s.id, method: 'text' }};
                            }}
                        }}

                        return {{ ok: 0 }};
                    }}
                """)

                if not service_selected or not service_selected.get("ok"):
                    logger.warning(f"⚠️ Service '{requested_service}' not found directly, trying search...")
                    try:
                        await page.fill(".pulsanti_tab input[name='cerca_servizio']", requested_service)
                        await page.wait_for_timeout(1500)

                        clicked_search = await page.evaluate("""
                            () => {
                                const svcs = document.querySelectorAll('.pulsanti_tab .servizio');
                                for (const s of svcs) {
                                    if (getComputedStyle(s).display !== 'none') {
                                        s.click();
                                        return {
                                            ok: 1,
                                            nome: s.getAttribute('nome') || '',
                                            id: s.id,
                                            method: 'search'
                                        };
                                    }
                                }
                                return { ok: 0 };
                            }
                        """)
                        service_selected = clicked_search
                    except Exception:
                        pass

                if not service_selected or not service_selected.get("ok"):
                    raise Exception(f"Service not found: {requested_service}")

                logger.info(f"✅ Service selected: {service_selected}")

                expected_rows = initial_rows + len(selected_services) + 1
                try:
                    await page.wait_for_function(
                        f"() => document.querySelectorAll('.servizi_selezionati .riga_servizio').length >= {expected_rows}",
                        timeout=5000
                    )
                except Exception:
                    logger.warning(f"⚠️ Did not detect new row for service {requested_service} by count")

                await page.wait_for_timeout(1000)

                selected_row = await page.evaluate(f"""
                    () => {{
                        const rows = document.querySelectorAll('.servizi_selezionati .riga_servizio');
                        const kw = '{service_kw}';
                        for (const row of rows) {{
                            const txt = (row.querySelector('.dettaglio p')?.textContent || row.textContent || '').toLowerCase().trim();
                            if (txt.includes(kw) || kw.includes(txt)) {{
                                return {{
                                    found: true,
                                    text: txt,
                                    id_servizio: row.getAttribute('id_servizio'),
                                    row_id: row.getAttribute('id')
                                }};
                            }}
                        }}
                        return {{ found: false, count: rows.length }};
                    }}
                """)

                logger.info(f"Selected row verification: {selected_row}")

                if selected_row and selected_row.get("found"):
                    selected_services.append({
                        "requested": requested_service,
                        "selected": service_selected.get("nome"),
                        "row": selected_row
                    })
                else:
                    logger.warning(f"⚠️ Could not verify selected row for {requested_service}")

                await page.fill(".pulsanti_tab input[name='cerca_servizio']", "")
                await page.wait_for_timeout(300)

            logger.info(f"✅ Total selected services: {selected_services}")
            await snap(page, "09_services_selected")

            # STEP 9: Select operator in appointment form
            if request.operator_preference.lower() != "prima disponibile":
                op_safe = js_escape(request.operator_preference.lower())
                logger.info(f"Step 9: Operator '{request.operator_preference}'...")
                op_result = await page.evaluate(f"""
                    () => {{
                        const kw = '{op_safe}';
                        const ops = document.querySelectorAll('.pulsanti_tab .operatori .operatore');
                        for (const op of ops) {{
                            if (op.classList.contains('assente')) continue;
                            const n = op.querySelector('span.nome');
                            if (n && n.textContent.toLowerCase().trim().includes(kw)) {{
                                op.click();
                                return {{ ok:1, name: n.textContent.trim(), id: op.id }};
                            }}
                        }}
                        const avail = [];
                        ops.forEach(o => {{
                            const n = o.querySelector('span.nome');
                            avail.push({{
                                name: n ? n.textContent.trim() : '?',
                                id: o.id,
                                absent: o.classList.contains('assente')
                            }});
                        }});
                        return {{ ok:0, available: avail }};
                    }}
                """)
                logger.info(f"Operator: {op_result}")
            else:
                logger.info("Step 9: Default operator")

            await page.wait_for_timeout(1000)
            await snap(page, "10_operator")

            # STEP 10: Add appointment
            logger.info("Step 10: Adding appointment...")

            added = await page.evaluate("""
                () => {
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (btn) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            btn.click();
                            return 'azioni-aggiungi';
                        }
                    }
                    return null;
                }
            """)

            if added:
                logger.info(f"✅ Add clicked: {added}")
            else:
                logger.warning("⚠️ Add button not found — Playwright fallback")
                try:
                    await page.click(".azioni .button.rimira.primary.aggiungi", timeout=5000)
                    added = "playwright"
                except Exception:
                    await snap(page, "10_ERROR")

            await page.wait_for_timeout(5000)
            await snap(page, "11_saved")
            await dismiss_system_modals(page, "post-save")
            await page.wait_for_timeout(2000)

            # VERIFY
            on_agenda = await page.evaluate("""
                () => {
                    const a = document.getElementById('pannello_agenda');
                    return a ? getComputedStyle(a).display !== 'none' : false;
                }
            """)
            has_error = await page.evaluate("""
                () => {
                    const m = document.getElementById('modale_dialog');
                    return m ? getComputedStyle(m).display !== 'none' : false;
                }
            """)
            form_gone = await page.evaluate("""
                () => {
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (!btn) return true;
                    return getComputedStyle(btn).display === 'none';
                }
            """)
            is_processing = await page.evaluate("""
                () => {
                    const s = document.querySelector('.azioni .elaborazione');
                    return s ? getComputedStyle(s).display !== 'none' : false;
                }
            """)

            success = bool(added) and form_gone and not has_error and not is_processing

            if success:
                try:
                    logger.info(f"🔄 Refreshing cache in same session for {request.preferred_date}")
                    refreshed_day = await scrape_day_availability_from_page(
                        page,
                        request.preferred_date,
                        "prima disponibile"
                    )
                    if refreshed_day and refreshed_day.get("is_open") is True:
                        await set_cached_day(request.preferred_date, refreshed_day)
                        logger.info(f"✅ Cache refreshed in same session for {request.preferred_date}")
                    else:
                        await invalidate_cached_day(request.preferred_date)
                        logger.info(f"🗑️ Cache invalidated for {request.preferred_date} (refresh returned no open data)")
                except Exception as refresh_err:
                    logger.warning(f"Same-session cache refresh failed: {refresh_err}")
                    await invalidate_cached_day(request.preferred_date)

            await snap(page, "12_final")
            session.last_used_at = datetime.utcnow()

            logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "customer_found_in_db": customer_found,
                "service": request.service,
                "services": requested_services,
                "date": request.preferred_date,
                "time": actual_time,
                "time_requested": request.preferred_time,
                "operator": request.operator_preference,
                "form_dismissed": form_gone,
                "message": "✅ Appuntamento creato" if success else "⚠️ Non confermato — verifica Wegest",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

        except Exception as e:
            logger.error(f"❌ {e}")
            if page:
                await snap(page, "ERROR", force=True)

            return {
                "success": False,
                "error": str(e),
                "message": f"❌ {e}",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

async def run_availability_check(request: AvailabilityRequest) -> dict:
    requested_services = normalize_requested_services(request.service, request.services)

    assigned_session = None
    if request.conversation_id:
        assigned_session = await get_assigned_pool_session(request.conversation_id)

    # 1. If warm live session is assigned, prefer live
    if assigned_session:
        logger.info(f"🟢 Assigned warm session available — bypassing cache for {request.preferred_date}")
        fresh = await run_live_availability_check(request)

        if fresh and fresh.get("is_open") is True and "operators" in fresh:
            await set_cached_day(request.preferred_date, fresh)

        return {
            **fresh,
            "source": "live"
        }

    # 2. Otherwise use cache if available
    cached = await get_cached_day(request.preferred_date)

    if cached:
        logger.info(f"⚡ Availability cache HIT for {request.preferred_date}")

        operator_pref = (request.operator_preference or "prima disponibile").lower().strip()

        catalog_services = service_catalog.get("services", {})
        required_operator_minutes = 0
        missing_service_durations = []

        for svc in requested_services:
            svc_l = svc.lower().strip()
            matched_duration = None

            # 1. exact from service catalog
            if svc_l in catalog_services:
                matched_duration = int(catalog_services[svc_l].get("tempo_operatore", 0) or 0)

            # 2. fuzzy from service catalog
            if matched_duration is None or matched_duration == 0:
                for known_name, info in catalog_services.items():
                    if svc_l in known_name or known_name in svc_l:
                        matched_duration = int(info.get("tempo_operatore", 0) or 0)
                        if matched_duration > 0:
                            break

            # 3. exact fallback
            if matched_duration is None or matched_duration == 0:
                if svc_l in SERVICE_DURATION_FALLBACK:
                    matched_duration = int(SERVICE_DURATION_FALLBACK[svc_l])

            # 4. fuzzy fallback
            if matched_duration is None or matched_duration == 0:
                for known_name, dur in SERVICE_DURATION_FALLBACK.items():
                    if svc_l in known_name or known_name in svc_l:
                        matched_duration = int(dur)
                        if matched_duration > 0:
                            break

            if matched_duration is None or matched_duration == 0:
                missing_service_durations.append(svc)
            else:
                required_operator_minutes += int(matched_duration)

        logger.info(f"Cache-hit requested services: {requested_services}")
        logger.info(f"Cache-hit required operator minutes: {required_operator_minutes}")
        if missing_service_durations:
            logger.warning(f"Cache-hit missing durations for services: {missing_service_durations}")

        filtered_ops = []
        all_times = set()
        all_valid_times = set()

        for op in cached.get("operators", []):
            name = op.get("name", "")

            if operator_pref != "prima disponibile":
                if operator_pref not in name.lower().strip():
                    continue

            raw_slots = op.get("available_slots", [])
            valid_start_times = compute_valid_start_times(raw_slots, required_operator_minutes)

            for t in raw_slots:
                all_times.add(t)

            for t in valid_start_times:
                all_valid_times.add(t)

            filtered_ops.append({
                **op,
                "valid_start_times": valid_start_times
            })

        sorted_times = sorted(all_times)
        sorted_valid_times = sorted(all_valid_times)

        hourly = {}
        for t in sorted_times:
            h = t.split(":")[0]
            hourly.setdefault(h, []).append(t)

        valid_hourly = {}
        for t in sorted_valid_times:
            h = t.split(":")[0]
            valid_hourly.setdefault(h, []).append(t)

        present_ops = [op for op in filtered_ops if op.get("present")]
        total_slots = len(sorted_times)
        total_valid_start_times = len(sorted_valid_times)

        if requested_services:
            if total_valid_start_times > 0:
                first_time = sorted_valid_times[0]
                last_time = sorted_valid_times[-1]
                summary = (
                    f"✅ {total_valid_start_times} orari di inizio validi per {', '.join(requested_services)} "
                    f"con {len(present_ops)} operatori, dalle {first_time} alle {last_time}"
                )
            else:
                summary = f"❌ Nessun orario di inizio valido per {', '.join(requested_services)} in questa data"
        else:
            if total_slots > 0:
                first_time = sorted_times[0]
                last_time = sorted_times[-1]
                summary = (
                    f"✅ {total_slots} slot disponibili con {len(present_ops)} operatori, "
                    f"dalle {first_time} alle {last_time}"
                )
            else:
                summary = "❌ Nessuno slot disponibile per questa data"

        return {
            **cached,
            "requested_services": requested_services,
            "required_operator_minutes": required_operator_minutes,
            "operators": filtered_ops,
            "active_operators": [
                {
                    "name": op["name"],
                    "id": op["id"],
                    "present": op["present"]
                }
                for op in filtered_ops
                if op.get("present")
            ],
            "all_available_times": sorted_times,
            "all_valid_start_times": sorted_valid_times,
            "hourly_summary": hourly,
            "valid_hourly_summary": valid_hourly,
            "total_available_slots": total_slots,
            "total_valid_start_times": total_valid_start_times,
            "total_operators_present": len(present_ops),
            "summary": summary,
            "source": "cache"
        }

    # 3. No assigned warm session and no cache -> no live pool session path
    logger.info(f"🐢 Availability cache MISS for {request.preferred_date}")
    return {
        "success": False,
        "date": request.preferred_date,
        "is_open": False,
        "message": "No warm live session available and no cached availability available",
        "available_slots": [],
        "operators": [],
        "source": "none"
    }


async def refresh_availability_cache_forever():
    await asyncio.sleep(10)  # let app boot first

    while True:
        try:
            logger.info("🔄 Background availability refresh starting...")

            today = datetime.utcnow().date()
            dates_to_refresh = []

            # Today + tomorrow
            for i in range(2):
                d = today + timedelta(days=i)
                dates_to_refresh.append(d.strftime("%Y-%m-%d"))

            # Next 7 days
            for i in range(2, 9):
                d = today + timedelta(days=i)
                dates_to_refresh.append(d.strftime("%Y-%m-%d"))

            for date_str in dates_to_refresh:
                try:
                    req = AvailabilityRequest(
                        preferred_date=date_str,
                        operator_preference="prima disponibile"
                    )
                    fresh = await run_live_availability_check(req)

                    if fresh and fresh.get("is_open") is True and "operators" in fresh:
                        await set_cached_day(date_str, fresh)
                        logger.info(f"✅ Refreshed cache for {date_str}")
                    else:
                        logger.info(f"ℹ️ Skipped cache for {date_str} (closed/no data)")
                except Exception as e:
                    logger.warning(f"Failed refreshing {date_str}: {e}")

            logger.info("✅ Background availability refresh complete")

            # Sleep 30 minutes
            await asyncio.sleep(1800)

        except Exception as e:
            logger.error(f"Background refresh loop error: {e}")
            await asyncio.sleep(300)

@app.post("/invalidate-cache")
async def invalidate_cache(request: Request):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    date_str = body.get("preferred_date")
    if not date_str:
        raise HTTPException(status_code=400, detail="preferred_date required")

    await invalidate_cached_day(date_str)
    return {"ok": True, "invalidated": date_str}


async def run_live_availability_check(request: AvailabilityRequest) -> dict:
    if not request.conversation_id:
        raise Exception("conversation_id is required for live availability checks")

    session = await get_live_session_for_conversation(request.conversation_id)

    async with session.lock:
        try:
            page = session.page

            # verify session still healthy
            state_ok = await page.evaluate("""() => {
                const loginPanel = document.getElementById('pannello_login');
                const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
                const menu = document.getElementById('menu');

                return (
                    !(loginPanel && getComputedStyle(loginPanel).display !== 'none') &&
                    (!!agendaBtn || !!menu)
                );
            }""")

            if not state_ok:
                raise Exception("Assigned pool session is not ready")

            session.last_used_at = datetime.utcnow()

            result = await scrape_day_availability_from_page(
                page,
                request.preferred_date,
                request.operator_preference,
                services=request.services,
                service=request.service
            )

            return result

        except Exception as e:
            logger.error(f"❌ Availability error for {request.conversation_id}: {e}")
            try:
                await snap(session.page, "avail_ERROR", force=True)
            except Exception:
                pass

            return {
                "date": request.preferred_date,
                "is_open": False,
                "error": str(e),
                "message": f"❌ Errore: {e}",
                "available_slots": [],
                "operators": [],
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

async def cleanup_idle_wegest_sessions():
    now = datetime.utcnow()
    to_remove = []

    async with wegest_sessions_lock:
        items = list(wegest_sessions.items())

    for conversation_id, session in items:
        if not session.last_used_at:
            continue
        age = (now - session.last_used_at).total_seconds()
        if age > SESSION_IDLE_TTL_SECONDS:
            to_remove.append(conversation_id)

    for conversation_id in to_remove:
        await reset_wegest_session(conversation_id)

    if to_remove:
        logger.info(f"🧹 Cleaned {len(to_remove)} idle Wegest sessions")
               
async def delayed_refresh_start():
    await asyncio.sleep(120)
    await refresh_availability_cache_forever()

async def extract_service_operator_durations_from_page(page) -> dict:
    """
    Reads visible service buttons from Wegest and builds:
    { 'taglio': 25, 'colore': 30, ... }
    using tempo_operatore from .pulsanti_tab .servizio[nome]
    """
    durations = await page.evaluate("""
        () => {
            const map = {};
            document.querySelectorAll('.pulsanti_tab .servizio').forEach(s => {
                const nome = (s.getAttribute('nome') || '').toLowerCase().trim();
                const tempoOperatore = parseInt(s.getAttribute('tempo_operatore') || '0', 10);
                if (nome) {
                    map[nome] = tempoOperatore;
                }
            });
            return map;
        }
    """)
    return durations or {}

async def cleanup_wegest_sessions_forever():
    while True:
        try:
            await cleanup_idle_wegest_sessions()
        except Exception as e:
            logger.warning(f"Wegest session cleanup failed: {e}")
        await asyncio.sleep(300)

async def scrape_day_availability_from_page(
    page,
    preferred_date: str,
    operator_preference: str = "prima disponibile",
    services: list[str] | None = None,
    service: str | None = None
) -> dict:
    target = datetime.strptime(preferred_date, "%Y-%m-%d")
    day, month, year = target.day, target.month, target.year
    day_name = target.strftime("%A")

    requested_services = normalize_requested_services(service, services or [])

    logger.info(f"Scraping date in existing session: {day}/{month}/{year} ({day_name})")
    logger.info(f"Requested services for availability: {requested_services}")

    await dismiss_system_modals(page, "before-date")

    date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"

    date_info = await page.evaluate(f"""
        () => {{
            const el = document.querySelector("{date_selector}");
            if (!el) return {{ exists: false }};
            return {{
                exists: true,
                classes: el.className,
                isOpen: el.classList.contains('aperto'),
                isClosed: el.classList.contains('chiuso')
            }};
        }}
    """)

    if not date_info or not date_info.get("exists"):
        return {
            "date": preferred_date,
            "day_name": day_name,
            "is_open": False,
            "message": "❌ Data non visibile nel calendario",
            "operators": []
        }

    if date_info.get("isClosed"):
        return {
            "date": preferred_date,
            "day_name": day_name,
            "is_open": False,
            "message": f"❌ Il salone è chiuso il {day_name}",
            "operators": []
        }

    await page.click(date_selector, timeout=10000)

    try:
        await page.wait_for_function(
            f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
            timeout=15000
        )
    except Exception:
        await page.click(date_selector, timeout=5000)
        await page.wait_for_timeout(3000)

    await page.wait_for_timeout(1500)
    await dismiss_system_modals(page, "after-date")

    # Operator names from real header
    op_names = await page.evaluate("""
        () => {
            const names = {};
            document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                const id = op.getAttribute('id_operatore');
                if (!id || id === '0') return;
                const nome = op.querySelector('.nome');
                if (nome) names[id] = nome.textContent.trim();
            });
            return names;
        }
    """)

    logger.info(f"Operator names found: {op_names}")

    # Read grid + appointment overlays
    grid_data = await page.evaluate(f"""
        () => {{
            const day = '{day}';
            const month = '{month}';
            const year = '{year}';

            const operators = [];

            const toMinutes = (h, m) => parseInt(h, 10) * 60 + parseInt(m, 10);

            const formatTime = (mins) => {{
                const h = Math.floor(mins / 60).toString().padStart(2, '0');
                const m = (mins % 60).toString().padStart(2, '0');
                return `${{h}}:${{m}}`;
            }};

            const expandQuarterHours = (startH, startM, endH, endM) => {{
                const out = [];
                let start = toMinutes(startH, startM);
                const end = toMinutes(endH, endM);

                start = Math.floor(start / 15) * 15;

                for (let t = start; t < end; t += 15) {{
                    out.push(formatTime(t));
                }}

                return out;
            }};

            const occupiedByOperator = {{}};

            document.querySelectorAll('.appuntamento[id_operatore]').forEach(app => {{
                const opId = app.getAttribute('id_operatore');
                if (!opId) return;

                const giorno = app.getAttribute('giorno_inizio');
                const mese = app.getAttribute('mese_inizio');
                const anno = app.getAttribute('anno_inizio');

                if (giorno !== String(day) || mese !== String(month).padStart(2, '0') || anno !== String(year)) {{
                    return;
                }}

                const h1 = app.getAttribute('ora_inizio');
                const m1 = app.getAttribute('minuto_inizio');
                const h2 = app.getAttribute('ora_fine_operatore');
                const m2 = app.getAttribute('minuto_fine_operatore');

                if (!h1 || !m1 || !h2 || !m2) return;

                const slots = expandQuarterHours(h1, m1, h2, m2);

                if (!occupiedByOperator[opId]) occupiedByOperator[opId] = new Set();
                slots.forEach(s => occupiedByOperator[opId].add(s));
            }});

            const columns = document.querySelectorAll('.operatore_orari[id_operatore]');

            for (const col of columns) {{
                const opId = col.getAttribute('id_operatore');
                if (opId === '0') continue;

                const isPresent = col.classList.contains('presente');

                const cells = col.querySelectorAll(
                    ".cella[giorno='" + day + "'][mese='" + month + "'][anno='" + year + "']"
                );

                const available = [];
                const occupied = [];
                const absent = [];

                const bookedSet = occupiedByOperator[opId] || new Set();

                for (const cell of cells) {{
                    const ora = cell.getAttribute('ora');
                    const minuto = cell.getAttribute('minuto');
                    const timeStr = ora.padStart(2, '0') + ':' + minuto.padStart(2, '0');

                    if (cell.classList.contains('assente')) {{
                        absent.push(timeStr);
                    }} else if (cell.classList.contains('occupata')) {{
                        occupied.push(timeStr);
                    }} else if (bookedSet.has(timeStr)) {{
                        occupied.push(timeStr);
                    }} else {{
                        available.push(timeStr);
                    }}
                }}

                operators.push({{
                    id: opId,
                    present: isPresent,
                    available_slots: available,
                    occupied_slots: occupied,
                    absent_slots: absent,
                    total_available: available.length,
                    total_occupied: occupied.length
                }});
            }}

            return operators;
        }}
    """)

    # ═══════════════════════════════════════════
    # SERVICE DURATION LOOKUP
    # Priority:
    #   1. self-updating service_catalog
    #   2. live DOM extraction
    #   3. hardcoded fallback
    # ═══════════════════════════════════════════
    live_service_durations = await extract_service_operator_durations_from_page(page)

    logger.info(f"Service catalog durations: {service_catalog.get('services', {})}")
    logger.info(f"Live scraped service durations: {live_service_durations}")

    required_operator_minutes = 0
    missing_service_durations = []

    for svc in requested_services:
        svc_l = svc.lower().strip()
        matched_duration = None

        # 1. exact from service_catalog
        catalog_services = service_catalog.get("services", {})
        if svc_l in catalog_services:
            matched_duration = int(catalog_services[svc_l].get("tempo_operatore", 0) or 0)

        # 2. fuzzy from service_catalog
        if matched_duration is None or matched_duration == 0:
            for known_name, info in catalog_services.items():
                if svc_l in known_name or known_name in svc_l:
                    matched_duration = int(info.get("tempo_operatore", 0) or 0)
                    if matched_duration > 0:
                        break

        # 3. exact from live DOM
        if matched_duration is None or matched_duration == 0:
            if svc_l in live_service_durations:
                matched_duration = int(live_service_durations[svc_l])

        # 4. fuzzy from live DOM
        if matched_duration is None or matched_duration == 0:
            for known_name, dur in live_service_durations.items():
                if svc_l in known_name or known_name in svc_l:
                    matched_duration = int(dur)
                    if matched_duration > 0:
                        break

        # 5. exact fallback map
        if matched_duration is None or matched_duration == 0:
            if svc_l in SERVICE_DURATION_FALLBACK:
                matched_duration = int(SERVICE_DURATION_FALLBACK[svc_l])

        # 6. fuzzy fallback map
        if matched_duration is None or matched_duration == 0:
            for known_name, dur in SERVICE_DURATION_FALLBACK.items():
                if svc_l in known_name or known_name in svc_l:
                    matched_duration = int(dur)
                    if matched_duration > 0:
                        break

        if matched_duration is None or matched_duration == 0:
            missing_service_durations.append(svc)
        else:
            required_operator_minutes += int(matched_duration)

    logger.info(f"Requested services: {requested_services}")
    logger.info(f"Required operator minutes: {required_operator_minutes}")
    if missing_service_durations:
        logger.warning(f"Missing durations for services: {missing_service_durations}")

    logger.info(f"Service operator durations: {live_service_durations}")
    logger.info(f"Required operator minutes: {required_operator_minutes}")
    if missing_service_durations:
        logger.warning(f"Missing durations for services: {missing_service_durations}")

    all_available = set()
    all_valid_start_times = set()
    operator_list = []

    for op in grid_data:
        op_id = op["id"]
        name = op_names.get(op_id, f"Operatore_{op_id}")

        if operator_preference.lower() != "prima disponibile":
            op_pref = operator_preference.lower().strip()
            if op_pref not in name.lower().strip():
                continue

        raw_slots = op["available_slots"]
        valid_start_times = compute_valid_start_times(raw_slots, required_operator_minutes)

        for slot in raw_slots:
            all_available.add(slot)

        for slot in valid_start_times:
            all_valid_start_times.add(slot)

        operator_list.append({
            "name": name,
            "id": op_id,
            "present": op["present"],
            "available_slots": raw_slots,
            "valid_start_times": valid_start_times,
            "occupied_slots": op["occupied_slots"],
            "total_available": op["total_available"],
            "total_occupied": op["total_occupied"]
        })

    sorted_times = sorted(all_available)
    sorted_valid_start_times = sorted(all_valid_start_times)

    hourly = {}
    for t in sorted_times:
        h = t.split(":")[0]
        hourly.setdefault(h, []).append(t)

    valid_hourly = {}
    for t in sorted_valid_start_times:
        h = t.split(":")[0]
        valid_hourly.setdefault(h, []).append(t)

    present_ops = [op for op in operator_list if op["present"]]
    total_slots = len(sorted_times)
    total_valid_start_times = len(sorted_valid_start_times)

    if requested_services:
        if total_valid_start_times > 0:
            first_time = sorted_valid_start_times[0]
            last_time = sorted_valid_start_times[-1]
            summary = (
                f"✅ {total_valid_start_times} orari di inizio validi per {', '.join(requested_services)} "
                f"con {len(present_ops)} operatori, dalle {first_time} alle {last_time}"
            )
        else:
            summary = f"❌ Nessun orario di inizio valido per {', '.join(requested_services)} in questa data"
    else:
        if total_slots > 0:
            first_time = sorted_times[0]
            last_time = sorted_times[-1]
            summary = f"✅ {total_slots} slot disponibili con {len(present_ops)} operatori, dalle {first_time} alle {last_time}"
        else:
            summary = "❌ Nessuno slot disponibile per questa data"

    return {
        "date": preferred_date,
        "day_name": day_name,
        "is_open": True,
        "requested_services": requested_services,
        "required_operator_minutes": required_operator_minutes,
        "operators": operator_list,
                "active_operators": [
            {
                "name": op["name"],
                "id": op["id"],
                "present": op["present"]
            }
            for op in operator_list
            if op.get("present")
        ],
        "all_available_times": sorted_times,
        "all_valid_start_times": sorted_valid_start_times,
        "hourly_summary": hourly,
        "valid_hourly_summary": valid_hourly,
        "total_available_slots": total_slots,
        "total_valid_start_times": total_valid_start_times,
        "total_operators_present": len(present_ops),
        "summary": summary
    }

async def cleanup_call_states_forever():
    while True:
        try:
            await cleanup_expired_call_states()
        except Exception as e:
            logger.warning(f"Call state cleanup failed: {e}")
        await asyncio.sleep(300)  # every 5 minutes


@app.post("/get-service-duration")
async def get_service_duration_endpoint(request: Request, payload: ServiceDurationRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    requested_services = normalize_requested_services(payload.service, payload.services)

    if not requested_services:
        return {
            "success": False,
            "message": "No service provided",
            "services": []
        }

    catalog_services = service_catalog.get("services", {})
    results = []

    for svc in requested_services:
        svc_l = svc.lower().strip()
        matched = None

        # exact from catalog
        if svc_l in catalog_services:
            matched = catalog_services[svc_l]

        # fuzzy from catalog
        if matched is None:
            for known_name, info in catalog_services.items():
                if svc_l in known_name or known_name in svc_l:
                    matched = info
                    break

        # fallback if catalog misses
        if matched is None:
            fallback_duration = SERVICE_DURATION_FALLBACK.get(svc_l)
            if fallback_duration:
                matched = {
                    "nome": svc,
                    "tempo_operatore": fallback_duration,
                    "tempo_cliente": fallback_duration
                }

        if matched:
            results.append({
                "requested_service": svc,
                "resolved_service": matched.get("nome", svc),
                "tempo_operatore": matched.get("tempo_operatore", 0),
                "tempo_cliente": matched.get("tempo_cliente", 0)
            })
        else:
            results.append({
                "requested_service": svc,
                "resolved_service": None,
                "tempo_operatore": None,
                "tempo_cliente": None
            })

    # Build spoken summaries
    if len(results) == 1:
        r = results[0]
        if r["tempo_operatore"] is not None:
            spoken_summary_it = (
                f"Il servizio {r['resolved_service']} richiede circa "
                f"{r['tempo_operatore']} minuti di lavoro operatore"
            )
            spoken_summary_en = (
                f"The service {r['resolved_service']} requires about "
                f"{r['tempo_operatore']} minutes of operator time"
            )
        else:
            spoken_summary_it = f"Non sono riuscita a trovare la durata del servizio {r['requested_service']}"
            spoken_summary_en = f"I couldn't find the duration for the service {r['requested_service']}"
    else:
        known = [r for r in results if r["tempo_operatore"] is not None]
        total_operator = sum(r["tempo_operatore"] for r in known)
        service_names = ", ".join(r["resolved_service"] or r["requested_service"] for r in results)

        spoken_summary_it = (
            f"I servizi {service_names} richiedono circa {total_operator} minuti totali di lavoro operatore"
        )
        spoken_summary_en = (
            f"The services {service_names} require about {total_operator} total minutes of operator time"
        )

    return {
        "success": True,
        "services": results,
        "spoken_summary_it": spoken_summary_it,
        "spoken_summary_en": spoken_summary_en
    }

    
@app.post("/update-booking-context")
async def update_booking_context_endpoint(request: Request, payload: UpdateBookingContextRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    normalized_services = normalize_requested_services(payload.service, payload.services)

    updates = {}

    if normalized_services:
        updates["services"] = normalized_services

    if payload.operator_preference is not None:
        updates["operator_preference"] = payload.operator_preference

    if payload.preferred_date is not None:
        updates["preferred_date"] = normalize_date_to_iso(payload.preferred_date)

    if payload.preferred_time is not None:
        updates["preferred_time"] = payload.preferred_time

    if payload.customer_name is not None:
        updates["customer_name"] = payload.customer_name

    if payload.caller_phone is not None:
        updates["caller_phone"] = payload.caller_phone

    state = await update_call_state(payload.conversation_id, updates)
    missing_fields = get_missing_booking_fields(state)

    next_action = "ask_missing_fields" if missing_fields else "ready_for_availability_or_confirmation"

    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "booking_context": state,
        "missing_fields": missing_fields,
        "next_action": next_action
    }

@app.post("/get-booking-context")
async def get_booking_context_endpoint(request: Request, payload: GetBookingContextRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    state = await get_call_state(payload.conversation_id)
    missing_fields = get_missing_booking_fields(state)

    if not state.get("preferred_date"):
        next_action = "ask_date"
    elif not state.get("preferred_time"):
        next_action = "check_availability_or_ask_time"
    elif missing_fields:
        next_action = "ask_missing_fields"
    else:
        next_action = "ready_for_confirmation_or_booking"

    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "booking_context": state,
        "missing_fields": missing_fields,
        "next_action": next_action
    }

@app.post("/check-booking-options")
async def check_booking_options_endpoint(request: Request, payload: CheckBookingOptionsRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    state = await get_call_state(payload.conversation_id)

    services = state.get("services") or []
    operator_preference = state.get("operator_preference") or "prima disponibile"
    preferred_date = state.get("preferred_date")
    preferred_time = state.get("preferred_time")

    if not preferred_date:
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "booking_context": state,
            "missing_fields": ["preferred_date"],
            "next_action": "ask_date",
            "message": "Preferred date is missing"
        }

    # Build an AvailabilityRequest from stored state
    avail_request = AvailabilityRequest(
        preferred_date=preferred_date,
        operator_preference=operator_preference,
        services=services,
        service=None,
        conversation_id=payload.conversation_id
    )

    availability_result = await run_availability_check(avail_request)

    # Save latest availability result back to call state
    updated_state = await update_call_state(payload.conversation_id, {
        "last_availability_result": availability_result
    })

    exact_operator_matches = []
    closest_operator_options = []

    preferred_time = state.get("preferred_time")
    operator_preference = state.get("operator_preference") or "prima disponibile"

    if preferred_time and operator_preference.lower() == "prima disponibile":
        exact_operator_matches, closest_operator_options = build_operator_time_suggestions(
            availability_result.get("operators", []),
            preferred_time
        )


    # Decide next action
    if not availability_result.get("is_open", False):
        next_action = "choose_day"
    elif availability_result.get("requested_services"):
        valid_times = availability_result.get("all_valid_start_times", [])
        if valid_times:
            next_action = "choose_time"
        else:
            next_action = "choose_operator_or_day"
    else:
        available_times = availability_result.get("all_available_times", [])
        if available_times:
            next_action = "choose_time"
        else:
            next_action = "choose_operator_or_day"

    # Build spoken summary fallback
    requested_services = availability_result.get("requested_services", [])
    all_valid_start_times = availability_result.get("all_valid_start_times", [])
    all_available_times = availability_result.get("all_available_times", [])

    if requested_services:
        times_for_speech = all_valid_start_times[:3]
    else:
        times_for_speech = all_available_times[:3]

    # Special case: requested time + first available operator
    if preferred_time and operator_preference.lower() == "prima disponibile":
        if exact_operator_matches:
            names = ", ".join([m["name"] for m in exact_operator_matches])
            spoken_summary_it = (
                f"Alle {preferred_time} sono disponibili {names}. Vuoi prenotare con uno di loro?"
            )
            spoken_summary_en = (
                f"At {preferred_time}, {names} are available. Would you like to book with one of them?"
            )
            next_action = "choose_operator_or_confirm_time"
        elif closest_operator_options:
            opts = []
            for opt in closest_operator_options[:3]:
                opts.append(f"{opt['name']} alle {opt['time']}")
            opts_str = ", ".join(opts)

            spoken_summary_it = (
                f"Nessun operatore è disponibile esattamente alle {preferred_time}. "
                f"Le alternative più vicine sono: {opts_str}. Quale preferisci?"
            )
            spoken_summary_en = (
                f"No operator is available exactly at {preferred_time}. "
                f"The closest alternatives are: {opts_str}. Which would you prefer?"
            )
            next_action = "choose_operator_or_time"
        else:
            spoken_summary_it = (
                f"Non abbiamo disponibilità intorno alle {preferred_time}. Vuoi provare un altro orario o un altro giorno?"
            )
            spoken_summary_en = (
                f"We don't have availability around {preferred_time}. Would you like to try another time or a different day?"
            )
            next_action = "choose_time_or_day"

    else:
        if requested_services:
            if times_for_speech:
                spoken_summary_it = (
                    f"Abbiamo disponibilità per {', '.join(requested_services)} "
                    f"il {preferred_date} alle {', '.join(times_for_speech)}. Quale orario preferisci?"
                )
                spoken_summary_en = (
                    f"We have availability for {', '.join(requested_services)} "
                    f"on {preferred_date} at {', '.join(times_for_speech)}. Which time would you prefer?"
                )
            else:
                spoken_summary_it = (
                    f"Non abbiamo disponibilità per {', '.join(requested_services)} "
                    f"in quella data. Vuoi provare un altro giorno o un altro operatore?"
                )
                spoken_summary_en = (
                    f"We don't have availability for {', '.join(requested_services)} "
                    f"on that date. Would you like to try another day or another operator?"
                )
        else:
            if times_for_speech:
                spoken_summary_it = (
                    f"Abbiamo disponibilità il {preferred_date} alle {', '.join(times_for_speech)}. "
                    f"Quale orario preferisci?"
                )
                spoken_summary_en = (
                    f"We have availability on {preferred_date} at {', '.join(times_for_speech)}. "
                    f"Which time would you prefer?"
                )
            else:
                spoken_summary_it = (
                    f"Non abbiamo disponibilità in quella data. Vuoi provare un altro giorno o un altro operatore?"
                )
                spoken_summary_en = (
                    f"We don't have availability on that date. Would you like to try another day or another operator?"
                )

    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "booking_context": updated_state,
        "availability": availability_result,
        "operators_available_at_requested_time": exact_operator_matches,
        "closest_operator_options": closest_operator_options,
        "spoken_summary_it": spoken_summary_it,
        "spoken_summary_en": spoken_summary_en,
        "next_action": next_action
    }

@app.post("/finalize-booking")
async def finalize_booking_endpoint(request: Request, payload: FinalizeBookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    state = await get_call_state(payload.conversation_id)
    missing_fields = get_missing_booking_fields(state)

    if missing_fields:
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "booking_context": state,
            "missing_fields": missing_fields,
            "next_action": "ask_missing_fields",
            "message": "Cannot finalize booking because required fields are missing"
        }

    booking_request = BookingRequest(
        customer_name=state["customer_name"],
        caller_phone=state["caller_phone"],
        service=None,
        services=state.get("services") or [],
        operator_preference=state.get("operator_preference") or "prima disponibile",
        preferred_date=state["preferred_date"],
        preferred_time=state["preferred_time"],
        conversation_id=payload.conversation_id
    )

    result = await run_wegest_booking(booking_request)

    if result.get("success"):
        await clear_call_state(payload.conversation_id)
        return {
            "success": True,
            "conversation_id": payload.conversation_id,
            "message": "Appointment booked successfully",
            "booking_result": result,
            "next_action": "booking_complete"
        }

    return {
        "success": False,
        "conversation_id": payload.conversation_id,
        "message": result.get("message", "Booking failed"),
        "booking_result": result,
        "next_action": "retry_or_apologize"
    }

@app.post("/prepare-live-session")
async def prepare_live_session_endpoint(request: Request, payload: PrepareLiveSessionRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not payload.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id required")

    try:
        session = await assign_idle_pool_session_to_conversation(payload.conversation_id)

        async with session.lock:
            # Verify still alive
            try:
                if not session.page or session.page.is_closed():
                    raise Exception("Pool session page is closed")

                state = await session.page.evaluate("""() => {
                    const loginPanel = document.getElementById('pannello_login');
                    const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
                    const menu = document.getElementById('menu');

                    return {
                        loginVisible: loginPanel ? getComputedStyle(loginPanel).display !== 'none' : false,
                        hasAgendaButton: !!agendaBtn,
                        hasMenu: !!menu
                    };
                }""")

                if state.get("loginVisible", False) or not (state.get("hasAgendaButton", False) or state.get("hasMenu", False)):
                    raise Exception("Pool session is no longer ready")

                session.last_used_at = datetime.utcnow()

                await update_call_state(payload.conversation_id, {
                    "session_prepared": True
                })

                return {
                    "success": True,
                    "conversation_id": payload.conversation_id,
                    "session_ready": True,
                    "message": "Live Wegest session is ready"
                }

            except Exception as session_err:
                logger.warning(f"Assigned pool session failed health check: {session_err}")
                raise session_err

    except Exception as e:
        logger.error(f"❌ Session warm-up failed for {payload.conversation_id}: {e}")
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "session_ready": False,
            "message": f"No live session available right now: {e}"
        }

@app.on_event("startup")
async def startup_event():
    load_cache_from_disk()
    load_operator_catalog()
    load_service_catalog()
    asyncio.create_task(cleanup_call_states_forever())
    asyncio.create_task(cleanup_wegest_sessions_forever())
    asyncio.create_task(warm_pool_on_startup())
    logger.info("🚀 App started (background refresh disabled, warm pool starting)")

