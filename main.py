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
    logger.info(f"Auth received: '{auth}' | Expected: 'Bearer {API_SECRET}'")

    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    logger.info(f"Booking: {booking.customer_name} | {booking.service} | {booking.preferred_date} {booking.preferred_time}")
    result = await run_wegest_booking(booking)
    return result


async def run_wegest_booking(request: BookingRequest) -> dict:
    WEGEST_USER     = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL       = "https://www.i-salon.eu/login/default.asp?login=&"

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

            # ── STEP 2: Fill login form ───────────────────────
            logger.info("Step 2: Filling login form...")

            # From HTML: input[name='username'] and input[name='password']
            await page.fill("input[name='username']", WEGEST_USER)
            await page.fill("input[name='password']", WEGEST_PASSWORD)
            logger.info("Credentials filled")

            # ── STEP 3: Click login button ────────────────────
            # From HTML: <div class="button">Accedi</div> — NOT a <button> tag!
            logger.info("Step 3: Clicking login button...")
            await page.click("div.button")
            logger.info("Login button clicked")

            # Wait for redirect after login
            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.wait_for_timeout(3000)
            logger.info(f"After login URL: {page.url}")
            logger.info(f"Page title: {await page.title()}")

            # Check if login succeeded
            current_url = page.url
            if "login" in current_url.lower():
                # Still on login page — login failed
                raise Exception(f"Login failed — still on login page. Check credentials. URL: {current_url}")

            logger.info("Login successful!")

            # ── STEP 4: Click Agenda in sidebar ──────────────
            logger.info("Step 4: Navigating to Agenda...")

            # From Wegest HTML: <div class="pulsante_menu" pannello="pannello_agenda">
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

            # From Wegest HTML: <div class="data Martedi aperto" giorno="17" mese="3" anno="2026">
            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            await page.wait_for_selector(date_selector, timeout=10000)
            await page.click(date_selector)
            await page.wait_for_timeout(2000)
            logger.info(f"Date selected: {request.preferred_date}")

            # ── STEP 6: Click time slot on agenda grid ────────
            logger.info(f"Step 6: Clicking time slot {request.preferred_time}...")

            # Parse time
            time_parts = request.preferred_time.split(":")
            hour   = int(time_parts[0])
            minute = int(time_parts[1]) if len(time_parts) > 1 else 0

            # Try clicking directly on a grid cell at the right time
            # Wegest renders the agenda as a grid — we look for cells by time
            time_selectors = [
                f"[data-ora='{request.preferred_time}']",
                f"[data-ora='{hour:02d}:{minute:02d}']",
                f".griglia_orario [ora='{hour:02d}:{minute:02d}']",
                f".operatori_orari [data-time='{request.preferred_time}']",
            ]

            slot_clicked = False
            for sel in time_selectors:
                try:
                    await page.click(sel, timeout=3000)
                    slot_clicked = True
                    logger.info(f"Time slot clicked: {sel}")
                    break
                except:
                    continue

            if not slot_clicked:
                logger.warning("Could not find time slot by data attribute — agenda grid may use different structure")

            await page.wait_for_timeout(2000)

            # ── STEP 7: Handle customer search modal ──────────
            logger.info(f"Step 7: Searching for customer: {request.customer_name}...")

            # Wegest opens cerca_cliente modal after clicking time slot
            # From HTML: input[name='cerca_cliente'] placeholder="Search by customer name or mobile phone"
            try:
                await page.wait_for_selector(
                    "input[name='cerca_cliente']",
                    timeout=8000
                )
                search_input = await page.query_selector("input[name='cerca_cliente']")

                if search_input:
                    first_name = request.customer_name.strip().split()[0]
                    await search_input.fill(first_name)
                    await page.wait_for_timeout(1500)
                    logger.info(f"Searching for: {first_name}")

                    # Look for customer in results
                    customer_results = await page.query_selector_all(
                        ".modale_body button.rimira, "
                        ".modale_body .button.rimira"
                    )

                    customer_found = False
                    for result in customer_results:
                        try:
                            text = (await result.inner_text()).lower()
                            if first_name.lower() in text:
                                await result.click()
                                customer_found = True
                                logger.info(f"Customer selected: {text.strip()}")
                                break
                        except:
                            continue

                    # Create new customer if not found
                    if not customer_found:
                        logger.info("Customer not found — creating new...")

                        # From HTML: button.primary "New Customer" / "Nuovo Cliente"
                        new_btn = await page.query_selector(
                            ".pulsanti button.primary, "
                            "button:has-text('Nuovo Cliente'), "
                            "button:has-text('New Customer')"
                        )
                        if new_btn:
                            await new_btn.click()
                            await page.wait_for_timeout(1500)

                            name_parts = request.customer_name.strip().split(" ", 1)

                            # Wegest new customer fields
                            nome_field    = await page.query_selector("input[name='Nome']")
                            cognome_field = await page.query_selector("input[name='Cognome']")
                            cell_field    = await page.query_selector(
                                "input[name='Cellulare1'], input[name='cellulare']"
                            )

                            if nome_field:
                                await nome_field.fill(name_parts[0])
                                logger.info(f"First name filled: {name_parts[0]}")
                            if cognome_field and len(name_parts) > 1:
                                await cognome_field.fill(name_parts[1])
                                logger.info(f"Last name filled: {name_parts[1]}")
                            if cell_field:
                                await cell_field.fill(request.caller_phone)
                                logger.info(f"Phone filled: {request.caller_phone}")

                            # Save new customer
                            save_btn = await page.query_selector(
                                ".pulsanti button.primary, "
                                "button.rimira.primary"
                            )
                            if save_btn:
                                await save_btn.click()
                                await page.wait_for_timeout(1500)
                                logger.info("New customer saved")

            except Exception as e:
                logger.warning(f"Customer search modal issue: {str(e)}")

            await page.wait_for_timeout(1000)

            # ── STEP 8: Select service ────────────────────────
            logger.info(f"Step 8: Selecting service: {request.service}...")
            service_keywords = request.service.lower().split()

            # From Wegest HTML: services are in .pulsanti_tab .servizi
            service_elements = await page.query_selector_all(
                ".pulsanti_tab .servizi button, "
                ".servizi button"
            )

            service_selected = False
            for el in service_elements:
                try:
                    text = (await el.inner_text()).lower()
                    if any(kw in text for kw in service_keywords):
                        await el.click()
                        service_selected = True
                        logger.info(f"Service selected: {text.strip()}")
                        break
                except:
                    continue

            if not service_selected:
                logger.warning(f"Service '{request.service}' not matched — check exact service names in Wegest")

            await page.wait_for_timeout(1000)

            # ── STEP 9: Select operator ───────────────────────
            logger.info(f"Step 9: Selecting operator: {request.operator_preference}...")

            if request.operator_preference.lower() != "prima disponibile":
                op_elements = await page.query_selector_all(
                    ".pulsanti_tab .operatori button, "
                    ".operatori button"
                )
                for el in op_elements:
                    try:
                        text = (await el.inner_text()).lower()
                        if request.operator_preference.lower() in text:
                            await el.click()
                            logger.info(f"Operator selected: {text.strip()}")
                            break
                    except:
                        continue

            await page.wait_for_timeout(1000)

            # ── STEP 10: Confirm appointment ──────────────────
            logger.info("Step 10: Confirming appointment...")

            # From Wegest HTML: button.aggiungi "Aggiungi appuntamento"
            add_selectors = [
                ".form_appuntamento .pulsanti button.aggiungi",
                "button.aggiungi",
                ".pulsanti .aggiungi",
                "button:has-text('Aggiungi appuntamento')",
                "button:has-text('Add appointment')",
            ]

            appointment_added = False
            for sel in add_selectors:
                try:
                    await page.click(sel, timeout=5000)
                    appointment_added = True
                    logger.info(f"Appointment confirmed: {sel}")
                    break
                except:
                    continue

            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(2000)

            # ── STEP 11: Verify success ───────────────────────
            content = (await page.content()).lower()
            first_name_lower = request.customer_name.lower().split()[0]
            success = first_name_lower in content or appointment_added

            await browser.close()
            logger.info(f"Result: {'SUCCESS' if success else 'UNCERTAIN — check Wegest'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "service": request.service,
                "date": request.preferred_date,
                "time": request.preferred_time,
                "operator": request.operator_preference,
                "message": "Appointment created in Wegest agenda" if success else "Submitted — please verify in Wegest"
            }

        except Exception as e:
            logger.error(f"Automation error: {str(e)}")
            try:
                await browser.close()
            except:
                pass
            return {
                "success": False,
                "error": str(e),
                "message": "Wegest booking automation failed"
            }