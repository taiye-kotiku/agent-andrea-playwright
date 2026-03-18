"""
Agent Andrea - Wegest Direct Booking Service
Fixed: longer waits after login for AJAX dashboard to load
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from playwright.async_api import async_playwright
from datetime import datetime
import os
import base64
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Andrea - Wegest Booking Service")

screenshots = {}

class BookingRequest(BaseModel):
    customer_name: str
    caller_phone: str
    service: str
    operator_preference: str = "prima disponibile"
    preferred_date: str
    preferred_time: str

API_SECRET = os.environ.get("API_SECRET", "changeme")

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

async def screenshot(page, name: str):
    try:
        data = await page.screenshot(type="png", full_page=True)
        screenshots[name] = base64.b64encode(data).decode()
        logger.info(f"📸 Screenshot: {name}")
    except Exception as e:
        logger.warning(f"Screenshot failed ({name}): {e}")

@app.post("/book")
async def book_appointment(request: Request, booking: BookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    screenshots.clear()
    logger.info(f"📅 Booking: {booking.customer_name} | {booking.service} | {booking.preferred_date} {booking.preferred_time}")
    return await run_wegest_booking(booking)


async def run_wegest_booking(request: BookingRequest) -> dict:
    WEGEST_USER     = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL       = "https://www.i-salon.eu/login/default.asp?login=&"

    logger.info(f"🔑 Username: '{WEGEST_USER}' | Password length: {len(WEGEST_PASSWORD)}")

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
            # ── STEP 1: Load login page ───────────────────────
            logger.info("Step 1: Loading login page...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)
            await screenshot(page, "01_login_page")

            # ── STEP 2: Fill credentials ──────────────────────
            logger.info("Step 2: Filling credentials...")
            await page.fill("input[name='username']", WEGEST_USER)
            await page.fill("input[name='password']", WEGEST_PASSWORD)
            await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")
            await screenshot(page, "02_credentials_filled")
            logger.info("Credentials filled")

            # ── STEP 3: Click login ───────────────────────────
            logger.info("Step 3: Clicking login button...")
            await page.click("div.button")
            logger.info("Login button clicked — waiting for dashboard to load...")

            # ── KEY FIX: Wait much longer for AJAX login ──────
            # Wegest does AJAX login which takes several seconds
            # We wait for the login panel to disappear OR menu to appear
            # with a generous 20 second timeout
            try:
                await page.wait_for_function(
                    """() => {
                        const loginPanel = document.getElementById('pannello_login');
                        const menu = document.getElementById('menu');
                        
                        // Login successful if:
                        // 1. Login panel is hidden
                        const loginHidden = loginPanel && 
                            window.getComputedStyle(loginPanel).display === 'none';
                        
                        // 2. OR menu is visible  
                        const menuVisible = menu && 
                            window.getComputedStyle(menu).display !== 'none';
                        
                        // 3. OR wrapper_contents has content
                        const contents = document.querySelector('.wrapper_contents');
                        const hasContents = contents && 
                            window.getComputedStyle(contents).display !== 'none';
                        
                        return loginHidden || menuVisible || hasContents;
                    }""",
                    timeout=20000  # Give it 20 seconds
                )
                logger.info("✅ Dashboard loaded — login successful!")
            except Exception as e:
                logger.warning(f"DOM wait timeout after 20s: {e}")
                # Still take screenshot to see current state
                await screenshot(page, "03_login_timeout")

            # Extra buffer for dashboard to fully render
            await page.wait_for_timeout(3000)
            await screenshot(page, "03_after_login")

            # Verify login state
            login_visible = await page.evaluate("""
                () => {
                    const el = document.getElementById('pannello_login');
                    if (!el) return false;
                    return window.getComputedStyle(el).display !== 'none';
                }
            """)
            menu_visible = await page.evaluate("""
                () => {
                    const el = document.getElementById('menu');
                    if (!el) return false;
                    return window.getComputedStyle(el).display !== 'none';
                }
            """)
            logger.info(f"Login panel visible: {login_visible} | Menu visible: {menu_visible}")

            if login_visible and not menu_visible:
                raise Exception(
                    f"Login failed after 20s wait. "
                    f"Username='{WEGEST_USER}'. "
                    f"Check /screenshots — screenshot 03 shows current state."
                )

            logger.info("🎉 LOGIN SUCCESS!")
            await screenshot(page, "04_dashboard")

            # ── STEP 4: Click Agenda ──────────────────────────
            logger.info("Step 4: Opening Agenda...")
            await page.wait_for_selector("[pannello='pannello_agenda']", timeout=10000)
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(3000)
            await screenshot(page, "05_agenda")
            logger.info("Agenda opened")

            # ── STEP 5: Select date ───────────────────────────
            logger.info(f"Step 5: Selecting date {request.preferred_date}...")
            target_date = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day   = target_date.day
            month = target_date.month
            year  = target_date.year

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            await page.wait_for_selector(date_selector, timeout=10000)
            await page.click(date_selector)
            await page.wait_for_timeout(2000)
            await screenshot(page, "06_date_selected")
            logger.info(f"Date selected: {request.preferred_date}")

            # ── STEP 6: Click time slot ───────────────────────
            logger.info(f"Step 6: Time slot {request.preferred_time}...")
            for sel in [
                f"[data-ora='{request.preferred_time}']",
                f"[data-time='{request.preferred_time}']",
            ]:
                try:
                    await page.click(sel, timeout=3000)
                    logger.info(f"Time clicked: {sel}")
                    break
                except:
                    continue
            await page.wait_for_timeout(2000)
            await screenshot(page, "07_time_slot_clicked")

            # ── STEP 7: Customer search ───────────────────────
            logger.info(f"Step 7: Customer {request.customer_name}...")
            try:
                await page.wait_for_selector("input[name='cerca_cliente']", timeout=8000)
                first_name = request.customer_name.strip().split()[0]
                await page.fill("input[name='cerca_cliente']", first_name)
                await page.wait_for_timeout(1500)
                await screenshot(page, "08_customer_search")

                results = await page.query_selector_all(".modale_body button.rimira")
                found = False
                for r in results:
                    text = (await r.inner_text()).lower()
                    if first_name.lower() in text:
                        await r.click()
                        found = True
                        logger.info(f"Customer found: {text.strip()}")
                        break

                if not found:
                    logger.info("Customer not found — creating new...")
                    new_btn = await page.query_selector(".pulsanti button.primary")
                    if new_btn:
                        await new_btn.click()
                        await page.wait_for_timeout(1500)
                        parts = request.customer_name.strip().split(" ", 1)
                        nome    = await page.query_selector("input[name='Nome']")
                        cognome = await page.query_selector("input[name='Cognome']")
                        cell    = await page.query_selector("input[name='Cellulare1']")
                        if nome:    await nome.fill(parts[0])
                        if cognome and len(parts) > 1: await cognome.fill(parts[1])
                        if cell:    await cell.fill(request.caller_phone)
                        save = await page.query_selector(".pulsanti button.primary")
                        if save: await save.click()
                        await page.wait_for_timeout(1500)
                        logger.info("New customer created")

            except Exception as e:
                logger.warning(f"Customer step: {e}")

            await screenshot(page, "09_customer_done")
            await page.wait_for_timeout(1000)

            # ── STEP 8: Select service ────────────────────────
            logger.info(f"Step 8: Service: {request.service}...")
            keywords = request.service.lower().split()
            els = await page.query_selector_all(".pulsanti_tab .servizi button, .servizi button")
            logger.info(f"Found {len(els)} service buttons")
            for el in els:
                text = (await el.inner_text()).lower()
                if any(k in text for k in keywords):
                    await el.click()
                    logger.info(f"✅ Service selected: {text.strip()}")
                    break
            await page.wait_for_timeout(1000)
            await screenshot(page, "10_service_selected")

            # ── STEP 9: Select operator ───────────────────────
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Operator: {request.operator_preference}...")
                ops = await page.query_selector_all(".pulsanti_tab .operatori button, .operatori button")
                for op in ops:
                    text = (await op.inner_text()).lower()
                    if request.operator_preference.lower() in text:
                        await op.click()
                        logger.info(f"✅ Operator: {text.strip()}")
                        break
            await page.wait_for_timeout(1000)

            # ── STEP 10: Add appointment ──────────────────────
            logger.info("Step 10: Adding appointment...")
            added = False
            for sel in [
                "button.aggiungi",
                ".form_appuntamento .pulsanti button.aggiungi",
                "button:has-text('Aggiungi appuntamento')",
            ]:
                try:
                    await page.click(sel, timeout=5000)
                    added = True
                    logger.info(f"✅ Appointment added via: {sel}")
                    break
                except:
                    continue

            await page.wait_for_timeout(3000)
            await screenshot(page, "11_final_result")

            content = (await page.content()).lower()
            success = request.customer_name.lower().split()[0] in content or added

            await browser.close()
            logger.info(f"🏁 Result: {'✅ SUCCESS' if success else '⚠️ UNCERTAIN — check Wegest'}")
            logger.info("👁️ View screenshots at /screenshots")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "service": request.service,
                "date": request.preferred_date,
                "time": request.preferred_time,
                "operator": request.operator_preference,
                "message": "✅ Appointment created in Wegest" if success else "⚠️ Submitted — verify in Wegest",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

        except Exception as e:
            logger.error(f"❌ Error: {str(e)}")
            await screenshot(page, "ERROR")
            try:
                await browser.close()
            except:
                pass
            return {
                "success": False,
                "error": str(e),
                "message": "❌ Failed — check /screenshots",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }