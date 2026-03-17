"""
Agent Andrea - Wegest Direct Booking Service
Logs into i-salon.eu (Wegest) and creates appointments directly
Deploy on Railway as a FastAPI app
"""

from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel
from playwright.async_api import async_playwright
from datetime import datetime
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Andrea - Wegest Booking Service")

# ── Request Model ─────────────────────────────────────────────
class BookingRequest(BaseModel):
    customer_name: str
    caller_phone: str
    service: str
    operator_preference: str = "prima disponibile"
    preferred_date: str   # YYYY-MM-DD
    preferred_time: str   # HH:MM

# ── Auth ──────────────────────────────────────────────────────
API_SECRET = os.environ.get("API_SECRET", "changeme")

def verify_auth(authorization: str = Header(None)):
    if not authorization or authorization != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

# ── Health Check ──────────────────────────────────────────────
@app.get("/health")
async def health():
    return {"status": "ok", "service": "Agent Andrea Wegest Booking"}

# ── Main Booking Endpoint ─────────────────────────────────────
@app.post("/book")
async def book_appointment(
    request: BookingRequest,
    authorization: str = Header(None)
):
    verify_auth(authorization)
    logger.info(f"Booking: {request.customer_name} | {request.service} | {request.preferred_date} {request.preferred_time}")
    result = await run_wegest_booking(request)
    return result

# ── Playwright Wegest Automation ──────────────────────────────
async def run_wegest_booking(request: BookingRequest) -> dict:

    WEGEST_USER     = os.environ["WEGEST_USERNAME"]
    WEGEST_PASSWORD = os.environ["WEGEST_PASSWORD"]

    # i-salon.eu is the booking-capable URL per Wegest documentation
    LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:

            # ── STEP 1: Load login page ───────────────────────
            logger.info("Step 1: Loading i-salon.eu login page...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Screenshot login page for debugging
            await page.screenshot(path="/tmp/step1_login_page.png")
            logger.info(f"Page title: {await page.title()}")
            logger.info(f"Page URL: {page.url}")

            # ── STEP 2: Fill login form ───────────────────────
            logger.info("Step 2: Filling login form...")

            # Try multiple possible username/email field selectors
            # We'll detect which one exists
            username_selectors = [
                "input[name='username']",
                "input[name='user']",
                "input[name='email']",
                "input[name='login']",
                "input[name='utente']",
                "input[type='text']:first-of-type",
                "input[type='email']",
            ]

            password_selectors = [
                "input[name='password']",
                "input[name='pass']",
                "input[name='pwd']",
                "input[type='password']",
            ]

            # Find username field
            username_field = None
            for selector in username_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        username_field = el
                        logger.info(f"Username field found: {selector}")
                        break
                except:
                    continue

            # Find password field
            password_field = None
            for selector in password_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        password_field = el
                        logger.info(f"Password field found: {selector}")
                        break
                except:
                    continue

            if not username_field or not password_field:
                # Log all input fields on the page for debugging
                inputs = await page.query_selector_all("input")
                field_info = []
                for inp in inputs:
                    name = await inp.get_attribute("name") or ""
                    type_ = await inp.get_attribute("type") or ""
                    id_ = await inp.get_attribute("id") or ""
                    field_info.append(f"name={name} type={type_} id={id_}")
                logger.error(f"Login form fields found: {field_info}")
                raise Exception(f"Login fields not found. Available inputs: {field_info}")

            await username_field.fill(WEGEST_USER)
            await password_field.fill(WEGEST_PASSWORD)
            await page.screenshot(path="/tmp/step2_filled_login.png")

            # ── STEP 3: Submit login ──────────────────────────
            logger.info("Step 3: Submitting login...")

            submit_selectors = [
                "button[type='submit']",
                "input[type='submit']",
                "button:has-text('Accedi')",
                "button:has-text('Login')",
                "button:has-text('Entra')",
                "input[value='Accedi']",
                "input[value='Login']",
                ".btn-login",
                "#login_button",
            ]

            submitted = False
            for selector in submit_selectors:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        await el.click()
                        submitted = True
                        logger.info(f"Login submitted with: {selector}")
                        break
                except:
                    continue

            if not submitted:
                # Press Enter on password field as fallback
                await password_field.press("Enter")
                logger.info("Login submitted via Enter key")

            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.wait_for_timeout(3000)
            await page.screenshot(path="/tmp/step3_after_login.png")
            logger.info(f"After login URL: {page.url}")

            # ── STEP 4: Navigate to Agenda ────────────────────
            logger.info("Step 4: Navigating to Agenda...")

            # Click Agenda menu item in Wegest sidebar
            # From the HTML we saw: pannello="pannello_agenda"
            agenda_selectors = [
                "[pannello='pannello_agenda']",
                ".pulsante_menu[pannello='pannello_agenda']",
                "div.pulsante_menu:has(.nome:has-text('Agenda'))",
            ]

            agenda_clicked = False
            for selector in agenda_selectors:
                try:
                    await page.click(selector, timeout=5000)
                    agenda_clicked = True
                    logger.info(f"Agenda clicked: {selector}")
                    break
                except:
                    continue

            await page.wait_for_timeout(2000)
            await page.screenshot(path="/tmp/step4_agenda.png")

            # ── STEP 5: Navigate to Target Date ──────────────
            logger.info(f"Step 5: Selecting date {request.preferred_date}...")
            target_date = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day   = target_date.day
            month = target_date.month
            year  = target_date.year

            # From the Wegest HTML we saw exact data attributes:
            # <div class="data Martedi aperto" giorno="17" mese="3" anno="2026">
            date_selectors = [
                f".data[giorno='{day}'][mese='{month}'][anno='{year}']",
                f"[giorno='{day}'][mese='{month}'][anno='{year}']",
                f".data.aperto[giorno='{day}'][mese='{month}'][anno='{year}']",
            ]

            date_clicked = False
            for selector in date_selectors:
                try:
                    await page.click(selector, timeout=5000)
                    date_clicked = True
                    logger.info(f"Date selected: {request.preferred_date}")
                    break
                except:
                    continue

            if not date_clicked:
                logger.warning(f"Could not click date {request.preferred_date}")

            await page.wait_for_timeout(2000)
            await page.screenshot(path="/tmp/step5_date_selected.png")

            # ── STEP 6: Click Time Slot in Agenda Grid ────────
            logger.info(f"Step 6: Clicking time slot {request.preferred_time}...")

            # The Wegest agenda grid — we click the cell at the right time
            # for the right operator column
            time_hour   = request.preferred_time.split(":")[0].zfill(2)
            time_minute = request.preferred_time.split(":")[1] if ":" in request.preferred_time else "00"

            time_selectors = [
                f"[data-ora='{request.preferred_time}']",
                f"[data-time='{request.preferred_time}']",
                f".griglia_orario [ora='{time_hour}:{time_minute}']",
            ]

            slot_clicked = False
            for selector in time_selectors:
                try:
                    await page.click(selector, timeout=3000)
                    slot_clicked = True
                    logger.info(f"Time slot clicked: {request.preferred_time}")
                    break
                except:
                    continue

            await page.wait_for_timeout(1500)

            # ── STEP 7: Search Customer in Modal ──────────────
            logger.info(f"Step 7: Searching for customer: {request.customer_name}...")

            # Wegest opens cerca_cliente modal after clicking a slot
            # From HTML: input[name='cerca_cliente']
            await page.wait_for_selector(
                "input[name='cerca_cliente'], .cerca_cliente input, .modale_header input",
                timeout=8000
            )

            search_input = await page.query_selector(
                "input[name='cerca_cliente'], "
                ".cerca_cliente input, "
                ".modale_header input[type='text']"
            )

            if search_input:
                first_name = request.customer_name.strip().split()[0]
                await search_input.fill(first_name)
                await page.wait_for_timeout(1500)

                # Look for customer in results
                customer_results = await page.query_selector_all(
                    ".modale_body button.rimira, "
                    ".modale_body .button, "
                    "[class*='cliente'] button"
                )

                customer_found = False
                for result in customer_results:
                    try:
                        text = (await result.inner_text()).lower()
                        if first_name.lower() in text:
                            await result.click()
                            customer_found = True
                            logger.info(f"Customer found and selected: {text.strip()}")
                            break
                    except:
                        continue

                # Create new customer if not found
                if not customer_found:
                    logger.info("Customer not found — creating new customer...")
                    new_btn = await page.query_selector(
                        ".pulsanti button.primary, "
                        "button:has-text('Nuovo Cliente'), "
                        "button.aggiungi"
                    )
                    if new_btn:
                        await new_btn.click()
                        await page.wait_for_timeout(1500)

                        # Fill customer details
                        # Wegest new customer form fields
                        name_parts    = request.customer_name.strip().split(" ", 1)
                        nome_field    = await page.query_selector("input[name='Nome']")
                        cognome_field = await page.query_selector("input[name='Cognome']")
                        cell_field    = await page.query_selector(
                            "input[name='Cellulare1'], "
                            "input[name='cellulare']"
                        )

                        if nome_field:
                            await nome_field.fill(name_parts[0])
                        if cognome_field and len(name_parts) > 1:
                            await cognome_field.fill(name_parts[1])
                        if cell_field:
                            await cell_field.fill(request.caller_phone)

                        # Save new customer
                        save_btn = await page.query_selector(
                            "button.primary:has-text('Salva'), "
                            "button.primary:has-text('Aggiungi'), "
                            "button.rimira.primary"
                        )
                        if save_btn:
                            await save_btn.click()
                            await page.wait_for_timeout(1500)
                            logger.info("New customer created")

            await page.wait_for_timeout(1000)
            await page.screenshot(path="/tmp/step7_customer.png")

            # ── STEP 8: Select Service ────────────────────────
            logger.info(f"Step 8: Selecting service: {request.service}...")

            service_keywords = request.service.lower().split()

            # Wegest service buttons are in .pulsanti_tab .servizi
            service_elements = await page.query_selector_all(
                ".pulsanti_tab .servizi button, "
                ".servizi .button, "
                "[class*='servizi'] button"
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
                logger.warning(f"Service '{request.service}' not found — check service names in Wegest")

            await page.wait_for_timeout(1000)

            # ── STEP 9: Select Operator ───────────────────────
            logger.info(f"Step 9: Selecting operator: {request.operator_preference}...")

            if request.operator_preference.lower() != "prima disponibile":
                operator_elements = await page.query_selector_all(
                    ".pulsanti_tab .operatori button, "
                    ".operatori .button, "
                    "[class*='operatori'] button"
                )
                for el in operator_elements:
                    try:
                        text = (await el.inner_text()).lower()
                        if request.operator_preference.lower() in text:
                            await el.click()
                            logger.info(f"Operator selected: {text.strip()}")
                            break
                    except:
                        continue

            await page.wait_for_timeout(1000)
            await page.screenshot(path="/tmp/step9_service_operator.png")

            # ── STEP 10: Add Appointment ──────────────────────
            logger.info("Step 10: Adding appointment...")

            # From the Wegest HTML: button.aggiungi "Aggiungi appuntamento"
            add_selectors = [
                ".form_appuntamento .pulsanti button.aggiungi",
                "button.aggiungi:has-text('Aggiungi')",
                "button.primary:has-text('Aggiungi appuntamento')",
                ".pulsanti .aggiungi",
            ]

            appointment_added = False
            for selector in add_selectors:
                try:
                    await page.click(selector, timeout=5000)
                    appointment_added = True
                    logger.info(f"Appointment added with: {selector}")
                    break
                except:
                    continue

            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(2000)
            await page.screenshot(path="/tmp/step10_final.png")

            # ── STEP 11: Verify ───────────────────────────────
            content = (await page.content()).lower()
            first_name_lower = request.customer_name.lower().split()[0]
            success = first_name_lower in content or appointment_added

            await browser.close()

            logger.info(f"Final result: {'SUCCESS' if success else 'UNCERTAIN - CHECK WEGEST'}")

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
                await page.screenshot(path=f"/tmp/error_{datetime.now().strftime('%H%M%S')}.png")
                await browser.close()
            except:
                pass
            return {
                "success": False,
                "error": str(e),
                "message": "Wegest booking automation failed"
            }