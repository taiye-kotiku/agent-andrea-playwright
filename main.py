"""
Agent Andrea - Wegest Direct Booking Service
Login uses AJAX - we intercept the response to detect success
"""

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel
from playwright.async_api import async_playwright
from datetime import datetime
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Andrea - Wegest Booking Service")

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

@app.post("/book")
async def book_appointment(request: Request, booking: BookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    logger.info(f"Booking: {booking.customer_name} | {booking.service} | {booking.preferred_date} {booking.preferred_time}")
    result = await run_wegest_booking(booking)
    return result


async def run_wegest_booking(request: BookingRequest) -> dict:
    WEGEST_USER     = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL       = "https://www.i-salon.eu/login/default.asp?login=&"

    logger.info(f"Username: '{WEGEST_USER}' | Password length: {len(WEGEST_PASSWORD)}")

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

            # ── STEP 2: Fill credentials ──────────────────────
            logger.info("Step 2: Filling credentials...")
            await page.fill("input[name='username']", WEGEST_USER)
            await page.fill("input[name='password']", WEGEST_PASSWORD)
            await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")
            logger.info("Credentials filled")

            # ── STEP 3: Submit and wait for AJAX response ─────
            logger.info("Step 3: Submitting login via AJAX...")

            # Listen for network responses to detect login success/failure
            login_response_data = {}

            async def handle_response(response):
                url = response.url
                if "login" in url.lower() or "accedi" in url.lower() or "auth" in url.lower():
                    try:
                        text = await response.text()
                        logger.info(f"Login response URL: {url}")
                        logger.info(f"Login response: {text[:300]}")
                        login_response_data["url"] = url
                        login_response_data["body"] = text
                    except:
                        pass

            page.on("response", handle_response)

            # Click the login div button
            await page.click("div.button")
            logger.info("Login button clicked")

            # Wait for AJAX to complete
            await page.wait_for_timeout(4000)

            # Check if the hidden wrapper_contents/wrapper_menu appeared
            # Wegest shows wrapper_menu after successful login
            wrapper_visible = await page.evaluate("""
                () => {
                    const menu = document.getElementById('wrapper_menu') || 
                                 document.querySelector('.wrapper_menu') ||
                                 document.querySelector('#menu');
                    if (menu) {
                        const style = window.getComputedStyle(menu);
                        return style.display !== 'none';
                    }
                    return false;
                }
            """)

            logger.info(f"Menu visible after login: {wrapper_visible}")
            logger.info(f"Login response data: {login_response_data}")

            # Also check if pannello_login is now hidden
            login_panel_hidden = await page.evaluate("""
                () => {
                    const panel = document.getElementById('pannello_login');
                    if (panel) {
                        const style = window.getComputedStyle(panel);
                        return style.display === 'none';
                    }
                    return true; // panel not found = logged in
                }
            """)

            logger.info(f"Login panel hidden: {login_panel_hidden}")

            # Check cookie/session
            cookies = await context.cookies()
            cookie_names = [c['name'] for c in cookies]
            logger.info(f"Cookies after login: {cookie_names}")

            if not wrapper_visible and not login_panel_hidden:
                # Log the page source for debugging
                html = await page.content()
                logger.error(f"Login may have failed. Page excerpt: {html[1000:2000]}")
                raise Exception(
                    f"Login failed. Menu not visible, login panel still showing. "
                    f"Username: '{WEGEST_USER}'. Check credentials in Railway variables."
                )

            logger.info("Login SUCCESS!")

            # ── STEP 4: Navigate to Agenda ────────────────────
            logger.info("Step 4: Navigating to Agenda...")

            # Wegest is a SPA — click the agenda menu item
            await page.wait_for_selector("[pannello='pannello_agenda']", timeout=10000)
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(2000)
            logger.info("Agenda opened")

            # ── STEP 5: Select target date ────────────────────
            logger.info(f"Step 5: Selecting date {request.preferred_date}...")
            target_date = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day   = target_date.day
            month = target_date.month
            year  = target_date.year

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            await page.wait_for_selector(date_selector, timeout=10000)
            await page.click(date_selector)
            await page.wait_for_timeout(2000)
            logger.info(f"Date selected: {request.preferred_date}")

            # ── STEP 6: Click time slot ───────────────────────
            logger.info(f"Step 6: Clicking time slot {request.preferred_time}...")
            time_selectors = [
                f"[data-ora='{request.preferred_time}']",
                f"[data-time='{request.preferred_time}']",
            ]
            for sel in time_selectors:
                try:
                    await page.click(sel, timeout=3000)
                    logger.info(f"Time slot clicked: {sel}")
                    break
                except:
                    continue
            await page.wait_for_timeout(2000)

            # ── STEP 7: Customer search ───────────────────────
            logger.info(f"Step 7: Customer: {request.customer_name}...")
            try:
                await page.wait_for_selector("input[name='cerca_cliente']", timeout=8000)
                first_name = request.customer_name.strip().split()[0]
                await page.fill("input[name='cerca_cliente']", first_name)
                await page.wait_for_timeout(1500)

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
                    logger.info("Creating new customer...")
                    new_btn = await page.query_selector(".pulsanti button.primary")
                    if new_btn:
                        await new_btn.click()
                        await page.wait_for_timeout(1500)
                        parts = request.customer_name.strip().split(" ", 1)
                        nome = await page.query_selector("input[name='Nome']")
                        cognome = await page.query_selector("input[name='Cognome']")
                        cell = await page.query_selector("input[name='Cellulare1']")
                        if nome: await nome.fill(parts[0])
                        if cognome and len(parts) > 1: await cognome.fill(parts[1])
                        if cell: await cell.fill(request.caller_phone)
                        save = await page.query_selector(".pulsanti button.primary")
                        if save: await save.click()
                        await page.wait_for_timeout(1500)
            except Exception as e:
                logger.warning(f"Customer step: {e}")

            await page.wait_for_timeout(1000)

            # ── STEP 8: Select service ────────────────────────
            logger.info(f"Step 8: Service: {request.service}...")
            keywords = request.service.lower().split()
            els = await page.query_selector_all(".pulsanti_tab .servizi button, .servizi button")
            for el in els:
                text = (await el.inner_text()).lower()
                if any(k in text for k in keywords):
                    await el.click()
                    logger.info(f"Service selected: {text.strip()}")
                    break
            await page.wait_for_timeout(1000)

            # ── STEP 9: Select operator ───────────────────────
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Operator: {request.operator_preference}...")
                ops = await page.query_selector_all(".pulsanti_tab .operatori button, .operatori button")
                for op in ops:
                    text = (await op.inner_text()).lower()
                    if request.operator_preference.lower() in text:
                        await op.click()
                        logger.info(f"Operator selected: {text.strip()}")
                        break
            await page.wait_for_timeout(1000)

            # ── STEP 10: Add appointment ──────────────────────
            logger.info("Step 10: Adding appointment...")
            add_selectors = [
                ".form_appuntamento .pulsanti button.aggiungi",
                "button.aggiungi",
                "button:has-text('Aggiungi appuntamento')",
            ]
            added = False
            for sel in add_selectors:
                try:
                    await page.click(sel, timeout=5000)
                    added = True
                    logger.info(f"Appointment added: {sel}")
                    break
                except:
                    continue

            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(2000)

            content = (await page.content()).lower()
            success = request.customer_name.lower().split()[0] in content or added

            await browser.close()
            logger.info(f"Result: {'SUCCESS' if success else 'UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "service": request.service,
                "date": request.preferred_date,
                "time": request.preferred_time,
                "operator": request.operator_preference,
                "message": "Appointment created in Wegest" if success else "Submitted — verify in Wegest"
            }

        except Exception as e:
            logger.error(f"Error: {str(e)}")
            try:
                await browser.close()
            except:
                pass
            return {"success": False, "error": str(e), "message": "Wegest booking automation failed"}