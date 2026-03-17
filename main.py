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
    # Read Authorization header directly from request
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    logger.info(f"Auth header received: '{auth}'")
    logger.info(f"Expected: 'Bearer {API_SECRET}'")
    logger.info(f"All headers: {dict(request.headers)}")
    
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
            logger.info("Step 1: Loading login page...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30000)
            await page.wait_for_timeout(2000)

            # Log all inputs for debugging
            inputs = await page.query_selector_all("input")
            field_info = []
            for inp in inputs:
                name  = await inp.get_attribute("name") or ""
                type_ = await inp.get_attribute("type") or ""
                id_   = await inp.get_attribute("id") or ""
                field_info.append(f"name={name} type={type_} id={id_}")
            logger.info(f"Login form inputs: {field_info}")

            username_selectors = [
                "input[name='username']", "input[name='user']",
                "input[name='email']",    "input[name='login']",
                "input[name='utente']",   "input[type='email']",
                "input[type='text']",
            ]
            password_selectors = [
                "input[name='password']", "input[name='pass']",
                "input[name='pwd']",      "input[type='password']",
            ]

            username_field = None
            for sel in username_selectors:
                el = await page.query_selector(sel)
                if el:
                    username_field = el
                    logger.info(f"Username field: {sel}")
                    break

            password_field = None
            for sel in password_selectors:
                el = await page.query_selector(sel)
                if el:
                    password_field = el
                    logger.info(f"Password field: {sel}")
                    break

            if not username_field or not password_field:
                raise Exception(f"Login fields not found. Inputs found: {field_info}")

            await username_field.fill(WEGEST_USER)
            await password_field.fill(WEGEST_PASSWORD)

            submit_selectors = [
                "button[type='submit']", "input[type='submit']",
                "button:has-text('Accedi')", "button:has-text('Login')",
                "input[value='Accedi']",  ".btn-login",
            ]
            for sel in submit_selectors:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        await el.click()
                        logger.info(f"Login submitted: {sel}")
                        break
                except:
                    continue
            else:
                await password_field.press("Enter")

            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.wait_for_timeout(3000)
            logger.info(f"After login URL: {page.url}")

            # Navigate to Agenda
            agenda_selectors = [
                "[pannello='pannello_agenda']",
                ".pulsante_menu[pannello='pannello_agenda']",
            ]
            for sel in agenda_selectors:
                try:
                    await page.click(sel, timeout=5000)
                    logger.info(f"Agenda clicked: {sel}")
                    break
                except:
                    continue

            await page.wait_for_timeout(2000)

            # Select date
            target_date = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day = target_date.day
            month = target_date.month
            year = target_date.year

            date_selectors = [
                f".data[giorno='{day}'][mese='{month}'][anno='{year}']",
                f"[giorno='{day}'][mese='{month}'][anno='{year}']",
            ]
            for sel in date_selectors:
                try:
                    await page.click(sel, timeout=5000)
                    logger.info(f"Date selected: {request.preferred_date}")
                    break
                except:
                    continue

            await page.wait_for_timeout(2000)

            # Click time slot
            time_selectors = [
                f"[data-ora='{request.preferred_time}']",
                f"[data-time='{request.preferred_time}']",
            ]
            for sel in time_selectors:
                try:
                    await page.click(sel, timeout=3000)
                    logger.info(f"Time slot clicked: {request.preferred_time}")
                    break
                except:
                    continue

            await page.wait_for_timeout(1500)

            # Search customer
            search_input = await page.query_selector(
                "input[name='cerca_cliente'], .cerca_cliente input, .modale_header input[type='text']"
            )
            if search_input:
                first_name = request.customer_name.strip().split()[0]
                await search_input.fill(first_name)
                await page.wait_for_timeout(1500)

                customer_results = await page.query_selector_all(".modale_body button.rimira, .modale_body .button")
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

                if not customer_found:
                    new_btn = await page.query_selector(".pulsanti button.primary, button:has-text('Nuovo Cliente'), button.aggiungi")
                    if new_btn:
                        await new_btn.click()
                        await page.wait_for_timeout(1500)
                        name_parts    = request.customer_name.strip().split(" ", 1)
                        nome_field    = await page.query_selector("input[name='Nome']")
                        cognome_field = await page.query_selector("input[name='Cognome']")
                        cell_field    = await page.query_selector("input[name='Cellulare1'], input[name='cellulare']")
                        if nome_field:    await nome_field.fill(name_parts[0])
                        if cognome_field and len(name_parts) > 1: await cognome_field.fill(name_parts[1])
                        if cell_field:   await cell_field.fill(request.caller_phone)
                        save_btn = await page.query_selector("button.primary:has-text('Salva'), button.rimira.primary")
                        if save_btn:
                            await save_btn.click()
                            await page.wait_for_timeout(1500)

            await page.wait_for_timeout(1000)

            # Select service
            service_keywords = request.service.lower().split()
            service_elements = await page.query_selector_all(".pulsanti_tab .servizi button, .servizi .button")
            for el in service_elements:
                try:
                    text = (await el.inner_text()).lower()
                    if any(kw in text for kw in service_keywords):
                        await el.click()
                        logger.info(f"Service selected: {text.strip()}")
                        break
                except:
                    continue

            await page.wait_for_timeout(1000)

            # Select operator
            if request.operator_preference.lower() != "prima disponibile":
                op_elements = await page.query_selector_all(".pulsanti_tab .operatori button, .operatori .button")
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

            # Add appointment
            add_selectors = [
                ".form_appuntamento .pulsanti button.aggiungi",
                "button.aggiungi:has-text('Aggiungi')",
                "button.primary:has-text('Aggiungi appuntamento')",
            ]
            appointment_added = False
            for sel in add_selectors:
                try:
                    await page.click(sel, timeout=5000)
                    appointment_added = True
                    logger.info(f"Appointment added: {sel}")
                    break
                except:
                    continue

            await page.wait_for_load_state("networkidle", timeout=15000)
            await page.wait_for_timeout(2000)

            content = (await page.content()).lower()
            success = request.customer_name.lower().split()[0] in content or appointment_added

            await browser.close()
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
            return {"success": False, "error": str(e), "message": "Booking automation failed"}