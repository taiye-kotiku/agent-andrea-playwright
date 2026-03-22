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
from typing import Optional

playwright_lock = asyncio.Lock()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
load_dotenv()

app = FastAPI(title="Agent Andrea - Wegest Booking Service")
DEBUG_SCREENSHOTS = os.environ.get("DEBUG_SCREENSHOTS", "false").lower() == "true"
screenshots = {}

CACHE_FILE = Path("availability_cache.json")

availability_cache = {
    "updated_at": None,
    "days": {}
}

cache_lock = asyncio.Lock()

def js_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")


class BookingRequest(BaseModel):
    customer_name: str
    caller_phone: str
    service: str
    operator_preference: str = "prima disponibile"
    preferred_date: str
    preferred_time: str

class AvailabilityRequest(BaseModel):
    preferred_date: str
    operator_preference: str = "prima disponibile"

API_SECRET = os.environ.get("API_SECRET", "changeme")

def load_cache_from_disk():
    global availability_cache
    try:
        if CACHE_FILE.exists():
            availability_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            logger.info("📦 Availability cache loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load cache from disk: {e}")

@dataclass
class WegestSession:
    browser = None
    context = None
    page = None
    lock: asyncio.Lock = None
    logged_in: bool = False
    agenda_open: bool = False
    last_used_at: Optional[datetime] = None

    def __post_init__(self):
        if self.lock is None:
            self.lock = asyncio.Lock()

wegest_session = WegestSession()


def save_cache_to_disk():
    try:
        CACHE_FILE.write_text(
            json.dumps(availability_cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Availability cache saved to disk")
    except Exception as e:
        logger.warning(f"Failed to save cache to disk: {e}")


async def set_cached_day(date_str: str, payload: dict):
    async with cache_lock:
        availability_cache["days"][date_str] = payload
        availability_cache["updated_at"] = datetime.utcnow().isoformat()
        save_cache_to_disk()

async def reset_wegest_session():
    global wegest_session
    try:
        if wegest_session.page:
            await wegest_session.page.close()
    except Exception:
        pass
    try:
        if wegest_session.context:
            await wegest_session.context.close()
    except Exception:
        pass
    try:
        if wegest_session.browser:
            await wegest_session.browser.close()
    except Exception:
        pass

    wegest_session.browser = None
    wegest_session.context = None
    wegest_session.page = None
    wegest_session.logged_in = False
    wegest_session.agenda_open = False
    wegest_session.last_used_at = None

    logger.info("♻️ Wegest session reset")

async def is_wegest_session_alive() -> bool:
    try:
        if not wegest_session.page:
            return False
        if wegest_session.page.is_closed():
            return False

        login_visible = await wegest_session.page.evaluate("""() => {
            const el = document.getElementById('pannello_login');
            return el ? getComputedStyle(el).display !== 'none' : false;
        }""")

        return not login_visible
    except Exception:
        return False

async def ensure_wegest_browser():
    if wegest_session.page and not wegest_session.page.is_closed():
        return

    await reset_wegest_session()

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

    wegest_session.browser = browser
    wegest_session.context = context
    wegest_session.page = page
    wegest_session.logged_in = False
    wegest_session.agenda_open = False
    wegest_session.last_used_at = datetime.utcnow()

    logger.info("🌐 Wegest browser session created")

async def ensure_wegest_logged_in():
    WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

    await ensure_wegest_browser()

    if await is_wegest_session_alive():
        wegest_session.logged_in = True
        wegest_session.last_used_at = datetime.utcnow()
        logger.info("✅ Reusing existing logged-in session")
        return

    page = wegest_session.page

    logger.info("🔐 Logging into Wegest session...")
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

    wegest_session.logged_in = True
    wegest_session.agenda_open = False
    wegest_session.last_used_at = datetime.utcnow()

    logger.info("🎉 Wegest session login successful")

    await dismiss_system_modals(page, "post-login")
    await page.wait_for_timeout(2000)

async def ensure_wegest_agenda_open():
    await ensure_wegest_logged_in()

    page = wegest_session.page

    agenda_visible = await page.evaluate("""() => {
        const a = document.getElementById('pannello_agenda');
        return a ? getComputedStyle(a).display !== 'none' : false;
    }""")

    if agenda_visible:
        wegest_session.agenda_open = True
        wegest_session.last_used_at = datetime.utcnow()
        logger.info("📅 Agenda already open")
        return

    logger.info("📅 Opening agenda in existing session...")
    await page.click("[pannello='pannello_agenda']")
    await page.wait_for_timeout(5000)
    await dismiss_system_modals(page, "after-agenda")
    await page.wait_for_timeout(2000)

    wegest_session.agenda_open = True
    wegest_session.last_used_at = datetime.utcnow()


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
    logger.info(f"📅 Booking: {booking.customer_name} | {booking.service} | {booking.preferred_date} {booking.preferred_time}")
    return await run_wegest_booking(booking)


@app.post("/check-availability")
async def check_availability(request: Request, avail: AvailabilityRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    screenshots.clear()
    logger.info(f"🔍 Availability check: {avail.preferred_date}")
    return await run_availability_check(avail)


async def run_wegest_booking(request: BookingRequest) -> dict:
    async with playwright_lock:
        WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
        WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
        LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                # ═══════════════════════════════════════════
                # STEP 1: Login page
                # ═══════════════════════════════════════════
                logger.info("Step 1: Loading login...")
                await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(5000)
                await snap(page, "01_login")

                # ═══════════════════════════════════════════
                # STEP 2: Credentials
                # ═══════════════════════════════════════════
                logger.info("Step 2: Credentials...")
                await page.fill("input[name='username']", WEGEST_USER)
                await page.fill("input[name='password']", WEGEST_PASSWORD)
                await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")
                await snap(page, "02_creds")

                # ═══════════════════════════════════════════
                # STEP 3: Login click
                # ═══════════════════════════════════════════
                logger.info("Step 3: Clicking login...")
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
                await snap(page, "03_logged_in")

                login_visible = await page.evaluate("""() => {
                    const el = document.getElementById('pannello_login');
                    return el ? getComputedStyle(el).display !== 'none' : false;
                }""")
                if login_visible:
                    raise Exception("Login failed — panel still visible")
                logger.info("🎉 LOGIN OK")

                # ═══════════════════════════════════════════
                # STEP 3.5: Dismiss post-login modals
                # ═══════════════════════════════════════════
                await dismiss_system_modals(page, "post-login")
                await page.wait_for_timeout(2000)

                # ═══════════════════════════════════════════
                # STEP 4: Open Agenda
                # ═══════════════════════════════════════════
                logger.info("Step 4: Agenda...")
                await page.click("[pannello='pannello_agenda']")
                await page.wait_for_timeout(5000)
                await dismiss_system_modals(page, "after-agenda")
                await page.wait_for_timeout(2000)
                await snap(page, "04_agenda")

                # ═══════════════════════════════════════════
                # STEP 5: Click date
                # ═══════════════════════════════════════════
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

                # ═══════════════════════════════════════════
                # STEP 6: Click time slot
                # FIXED: respect operator preference at cell-click stage
                # Verified:
                #   operator headers: .operatori_nomi .operatore[id_operatore] .nome
                #   cells: .cella[ora][minuto][giorno][mese][anno][id_operatore]
                # ═══════════════════════════════════════════
                raw_hour = int(request.preferred_time.split(":")[0])
                raw_minute = int(request.preferred_time.split(":")[1]) if ":" in request.preferred_time else 0
                rounded_minute = (raw_minute // 15) * 15
                hour = str(raw_hour)
                minute = str(rounded_minute)

                logger.info(f"Step 6: Time {hour}:{minute} | operator pref: {request.operator_preference}")

                # Build operator map from header row
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
                    # Match operator name to id_operatore
                    for name, op_id in operator_map.items():
                        if operator_pref in name:
                            preferred_op_id = op_id
                            break
                    logger.info(f"Preferred operator id: {preferred_op_id}")

                time_clicked = False
                actual_time = f"{hour}:{minute}"
                clicked_operator_id = preferred_op_id

                # Helper: exact minute selector
                def exact_selector(op_id=None, h=None, m=None):
                    h = h if h is not None else hour
                    m = m if m is not None else minute
                    base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}'][minuto='{m}']"
                    if op_id:
                        base += f"[id_operatore='{op_id}']"
                    base += ":not(.assente):not(.occupata)"
                    return base

                # Helper: hour selector
                def hour_selector(op_id=None, h=None):
                    h = h if h is not None else hour
                    base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}']"
                    if op_id:
                        base += f"[id_operatore='{op_id}']"
                    base += ":not(.assente):not(.occupata)"
                    return base

                # ── CASE 1: specific operator requested ──────────
                if preferred_op_id:
                    logger.info(f"Trying specific operator slot for id_operatore={preferred_op_id}")

                    # Exact time for preferred operator
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

                    # Any minute in same hour for preferred operator
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

                    # Next available hour for preferred operator
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

                    # If still no slot for that operator, fail clearly
                    if not time_clicked:
                        raise Exception(
                            f"No available slot for operator '{request.operator_preference}' on {request.preferred_date} around {request.preferred_time}"
                        )

                # ── CASE 2: prima disponibile ────────────────────
                else:
                    logger.info("Using prima disponibile logic")

                    # Exact minute, any operator
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

                    # Any minute in same hour
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

                    # Next available hour
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

                # ═══════════════════════════════════════════
                # STEP 7: Customer search & selection
                # ═══════════════════════════════════════════
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

                    # Match logic: requires BOTH first AND last name
                    match_js = f"""
                        () => {{
                            const first = '{first_safe}';
                            const last = '{last_safe}';
                            const rows = document.querySelectorAll(
                                '.tabella_clienti tbody tr[id]'
                            );
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

                    # Search 1: Full name
                    logger.info(f"  Search 1: '{request.customer_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", request.customer_name)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07a_full")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                    # Search 2: First name
                    if not customer_found:
                        logger.info(f"  Search 2: '{first_name}'")
                        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                        await page.wait_for_timeout(3000)
                        await snap(page, "07b_first")
                        match = await page.evaluate(match_js)
                        if match and match.get('found'):
                            customer_found = True
                            logger.info(f"✅ Match: {match}")

                    # Search 3: Last name
                    if not customer_found and last_name:
                        logger.info(f"  Search 3: '{last_name}'")
                        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", last_name)
                        await page.wait_for_timeout(3000)
                        await snap(page, "07c_last")
                        match = await page.evaluate(match_js)
                        if match and match.get('found'):
                            customer_found = True
                            logger.info(f"✅ Match: {match}")

                    # Search 4: Phone
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

                    # ── CREATE NEW CUSTOMER ────────────────────
                    if not customer_found:
                        logger.info("  ❌ Not found → creating new customer")
                        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", "")
                        await page.wait_for_timeout(500)

                        # Click "New Customer" in search modal
                        # Verified: .cerca_cliente .pulsanti .button.rimira.primary.aggiungi
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

                        # Fill Nome
                        # Verified: input[name='nome'] inside .form_cliente
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

                        # Fill Cognome
                        # Verified: input[name='cognome'] inside .form_cliente
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

                        # Fill Cellulare
                        # Verified: input[name='cellulare'] inside .form_cliente
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

                        # Click "Add customer"
                        # Verified: .form_cliente .modale_footer .button.rimira.primary.aggiungi
                        # (btn #36 of 37 — unique inside .form_cliente container)
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
                    await snap(page, "07_ERROR", force=True)

                await page.wait_for_timeout(2000)

                # ═══════════════════════════════════════════
                # STEP 7.5: Phone number modal (if it appears)
                # All JS evaluate — no page.fill() to avoid timeout
                # Verified: .modale.card.inserisci_cellulare
                # ═══════════════════════════════════════════
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

                # ═══════════════════════════════════════════
                # STEP 8: Select service
                # Verified: .pulsanti_tab .servizio with nome attribute
                # ═══════════════════════════════════════════
                logger.info(f"Step 8: Service '{request.service}'...")
                svc_kw = js_escape(request.service.lower())

                svc_result = await page.evaluate(f"""
                    () => {{
                        const kw = '{svc_kw}';
                        const all = document.querySelectorAll('.pulsanti_tab .servizio');
                        for (const s of all) {{
                            if ((s.getAttribute('nome') || '').toLowerCase() === kw) {{
                                s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'exact' }};
                            }}
                        }}
                        for (const s of all) {{
                            if ((s.getAttribute('nome') || '').toLowerCase().startsWith(kw)) {{
                                s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'starts' }};
                            }}
                        }}
                        for (const s of all) {{
                            if ((s.getAttribute('nome') || '').toLowerCase().includes(kw)) {{
                                s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'contains' }};
                            }}
                        }}
                        for (const s of all) {{
                            const n = (s.getAttribute('nome') || '').toLowerCase();
                            if (n.length > 2 && kw.includes(n)) {{
                                s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'reverse' }};
                            }}
                        }}
                        const avail = [];
                        all.forEach(s => avail.push(s.getAttribute('nome')));
                        return {{ ok:0, available: avail }};
                    }}
                """)

                if svc_result and svc_result.get('ok'):
                    logger.info(f"✅ Service: {svc_result}")
                else:
                    logger.warning(f"⚠️ Service not found. Trying search... {svc_result}")
                    try:
                        await page.fill(".pulsanti_tab input[name='cerca_servizio']", request.service)
                        await page.wait_for_timeout(2000)
                        await page.evaluate("""
                            () => {
                                const svcs = document.querySelectorAll('.pulsanti_tab .servizio');
                                for (const s of svcs) {
                                    if (getComputedStyle(s).display !== 'none') { s.click(); return; }
                                }
                            }
                        """)
                    except Exception:
                        pass

                await page.wait_for_timeout(1500)
                await snap(page, "09_service")

                # ═══════════════════════════════════════════
                # STEP 9: Select operator
                # Verified: .pulsanti_tab .operatori .operatore span.nome
                # ═══════════════════════════════════════════
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

                # ═══════════════════════════════════════════
                # STEP 10: Click "Add appointment"
                # Verified: .azioni .button.rimira.primary.aggiungi
                # ═══════════════════════════════════════════
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
                        await snap(page, "10_ERROR", force=True)

                await page.wait_for_timeout(5000)
                await snap(page, "11_saved")
                await dismiss_system_modals(page, "post-save")
                await page.wait_for_timeout(2000)

                # ═══════════════════════════════════════════
                # VERIFY
                # ═══════════════════════════════════════════
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
                await browser.close()

                logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

                return {
                    "success": success,
                    "customer_name": request.customer_name,
                    "customer_found_in_db": customer_found,
                    "service": request.service,
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
                await snap(page, "ERROR", force=True)
                try:
                    await browser.close()
                except Exception:
                    pass
                return {
                    "success": False,
                    "error": str(e),
                    "message": f"❌ {e}",
                    "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
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

    
async def run_availability_check(request: AvailabilityRequest) -> dict:
    cached = await get_cached_day(request.preferred_date)

    if cached:
        logger.info(f"⚡ Availability cache HIT for {request.preferred_date}")

        operator_pref = (request.operator_preference or "prima disponibile").lower()

        # If no operator preference, return full cached day
        if operator_pref == "prima disponibile":
            return {
                **cached,
                "source": "cache"
            }

        # Filter operators for specific preference
        filtered_ops = []
        all_times = set()

        for op in cached.get("operators", []):
            if operator_pref in op.get("name", "").lower():
                filtered_ops.append(op)
                for t in op.get("available_slots", []):
                    all_times.add(t)

        return {
            **cached,
            "operators": filtered_ops,
            "all_available_times": sorted(all_times),
            "source": "cache"
        }

    logger.info(f"🐢 Availability cache MISS for {request.preferred_date}")
    fresh = await run_live_availability_check(request)

    # Cache only if successful/open
    if fresh and fresh.get("is_open") is True and "operators" in fresh:
        await set_cached_day(request.preferred_date, fresh)

    return {
        **fresh,
        "source": "live"
    }

async def run_live_availability_check(request: AvailabilityRequest) -> dict:
    async with wegest_session.lock:
        try:
            await ensure_wegest_agenda_open()
            page = wegest_session.page

            result = await scrape_day_availability_from_page(
                page,
                request.preferred_date,
                request.operator_preference
            )

            wegest_session.last_used_at = datetime.utcnow()
            return result

        except Exception as e:
            logger.error(f"❌ Availability error: {e}")
            try:
                await snap(wegest_session.page, "avail_ERROR", force=True)
            except Exception:
                pass

            # If session is broken, reset it so next request can recover cleanly
            try:
                await reset_wegest_session()
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
                
async def delayed_refresh_start():
    await asyncio.sleep(120)
    await refresh_availability_cache_forever()

async def scrape_day_availability_from_page(page, preferred_date: str, operator_preference: str = "prima disponibile") -> dict:
    target = datetime.strptime(preferred_date, "%Y-%m-%d")
    day, month, year = target.day, target.month, target.year
    day_name = target.strftime("%A")

    logger.info(f"Scraping date in existing session: {day}/{month}/{year} ({day_name})")

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

    # ── GET OPERATOR NAMES FROM REAL HEADER ─────────
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

    # ── READ GRID + APPOINTMENT OVERLAYS ────────────
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

            // Build occupied slots from appointment overlays
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

    all_available = set()
    operator_list = []

    for op in grid_data:
        op_id = op["id"]
        name = op_names.get(op_id, f"Operatore_{op_id}")

        if operator_preference.lower() != "prima disponibile":
            op_pref = operator_preference.lower().strip()
            if op_pref not in name.lower().strip():
                continue

        for slot in op["available_slots"]:
            all_available.add(slot)

        operator_list.append({
            "name": name,
            "id": op_id,
            "present": op["present"],
            "available_slots": op["available_slots"],
            "occupied_slots": op["occupied_slots"],
            "total_available": op["total_available"],
            "total_occupied": op["total_occupied"]
        })

    sorted_times = sorted(all_available)

    hourly = {}
    for t in sorted_times:
        h = t.split(":")[0]
        hourly.setdefault(h, []).append(t)

    present_ops = [op for op in operator_list if op["present"]]
    total_slots = len(sorted_times)

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
        "operators": operator_list,
        "all_available_times": sorted_times,
        "hourly_summary": hourly,
        "total_available_slots": total_slots,
        "total_operators_present": len(present_ops),
        "summary": summary
    }


@app.on_event("startup")
async def startup_event():
    load_cache_from_disk()
    logger.info("🚀 App started (background refresh disabled)")



