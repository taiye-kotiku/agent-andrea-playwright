"""
Agent Andrea - Wegest Direct Booking Service
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

    logger.info(f"Using username: '{WEGEST_USER}' (password length: {len(WEGEST_PASSWORD)})")

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

            # Make sure codice hidden field is set to 1
            await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")
            logger.info("Credentials filled, codice set to 1")

            # ── STEP 3: Submit login ──────────────────────────
            logger.info("Step 3: Submitting login...")

            # Try multiple submission methods
            # Method A: Click the div.button
            try:
                await page.click("div.button", timeout=3000)
                logger.info("Clicked div.button")
            except:
                logger.warning("div.button click failed, trying alternatives...")

            # Wait and check if we moved away from login
            await page.wait_for_timeout(3000)
            url_after_click = page.url
            logger.info(f"URL after click: {url_after_click}")

            # If still on login page, try JavaScript form submit
            if "login" in url_after_click.lower():
                logger.info("Still on login — trying JS form submit...")
                await page.evaluate("""
                    () => {
                        const form = document.querySelector('form.pannello_login_form');
                        if (form) {
                            // Try clicking the button via JS
                            const btn = document.querySelector('div.button');
                            if (btn) btn.click();
                        }
                    }
                """)
                await page.wait_for_timeout(3000)
                logger.info(f"URL after JS click: {page.url}")

            # If still on login, try dispatching click event
            if "login" in page.url.lower():
                logger.info("Still on login — dispatching click event...")
                await page.evaluate("""
                    () => {
                        const btn = document.querySelector('div.button');
                        if (btn) {
                            btn.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                        }
                    }
                """)
                await page.wait_for_timeout(3000)
                logger.info(f"URL after dispatch: {page.url}")

            # If still on login, try pressing Enter
            if "login" in page.url.lower():
                logger.info("Trying Enter key...")
                await page.press("input[name='password']", "Enter")
                await page.wait_for_load_state("networkidle", timeout=10000)
                await page.wait_for_timeout(2000)
                logger.info(f"URL after Enter: {page.url}")

            # Final check
            if "login" in page.url.lower():
                # Log what's on the page to diagnose
                page_text = await page.inner_text("body")
                logger.error(f"Still on login page. Page text: {page_text[:500]}")
                raise Exception(
                    f"Login failed — credentials may be wrong. "
                    f"Username: '{WEGEST_USER}', "
                    f"Password length: {len(WEGEST_PASSWORD)}. "
                    f"URL: {page.url}"
                )

            logger.info(f"Login SUCCESS — URL: {page.url}")

            # ── STEP 4: Navigate to Agenda ────────────────────
            logger.info("Step 4: Navigating to Agenda...")
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
            logger.info(f"Step 7: Customer search: {request.customer_name}...")
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
                logger.warning(f"Customer step issue: {e}")

            await page.wait_for_timeout(1000)

            # ── STEP 8: Select service ────────────────────────
            logger.info(f"Step 8: Selecting service: {request.service}...")
            keywords = request.service.lower().split()
            els = await page.query_selector_all(".pulsanti_tab .servizi button, .servizi button")
            for el in els:
                text = (await el.inner_text()).lower()
                if any(k in text for k in keywords):
                    await el.click()
                    logger.info(f"Service: {text.strip()}")
                    break

            await page.wait_for_timeout(1000)

            # ── STEP 9: Select operator ───────────────────────
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Selecting operator: {request.operator_preference}...")
                ops = await page.query_selector_all(".pulsanti_tab .operatori button, .operatori button")
                for op in ops:
                    text = (await op.inner_text()).lower()
                    if request.operator_preference.lower() in text:
                        await op.click()
                        logger.info(f"Operator: {text.strip()}")
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