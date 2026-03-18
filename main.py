"""
Agent Andrea - Wegest Direct Booking Service
Fixed: dismiss post-login "chiusura cassa" modal
Fixed: handle modal blocking agenda interactions
Fixed: date navigation and time slot clicking
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


async def dismiss_all_modals(page, label=""):
    """Aggressively dismiss any modals/overlays blocking the page."""
    logger.info(f"🔍 Checking for modals to dismiss... ({label})")
    dismissed = False

    # Attempt 1: Click "OK, PROCEDI" button
    for selector in [
        "button:has-text('OK, PROCEDI')",
        "button:has-text('Ok, procedi')",
        "button:has-text('PROCEDI')",
        "button:has-text('Procedi')",
    ]:
        try:
            btn = page.locator(selector).first
            if await btn.is_visible(timeout=1000):
                await btn.click()
                logger.info(f"✅ Clicked: {selector}")
                dismissed = True
                await page.wait_for_timeout(2000)
                break
        except:
            continue

    # Attempt 2: Click any ANNULLA (cancel) button
    if not dismissed:
        try:
            annulla = page.locator("button:has-text('ANNULLA'), a:has-text('ANNULLA')").first
            if await annulla.is_visible(timeout=1000):
                await annulla.click()
                logger.info("✅ Clicked ANNULLA")
                dismissed = True
                await page.wait_for_timeout(2000)
        except:
            pass

    # Attempt 3: Click any close button on modals
    if not dismissed:
        try:
            close_btns = page.locator(".modale .chiudi, .modale_close, .close, [class*='close']")
            count = await close_btns.count()
            for i in range(count):
                btn = close_btns.nth(i)
                if await btn.is_visible(timeout=500):
                    await btn.click()
                    logger.info(f"✅ Clicked close button {i}")
                    dismissed = True
                    await page.wait_for_timeout(1500)
                    break
        except:
            pass

    # Attempt 4: Force-remove any modal overlays via JS
    try:
        removed = await page.evaluate("""
            () => {
                let removed = 0;
                // Remove modal overlays
                document.querySelectorAll('.modale_overlay, .overlay, .modal-backdrop').forEach(el => {
                    el.style.display = 'none';
                    removed++;
                });
                // Hide modal containers
                document.querySelectorAll('.modale, .modal, [class*="modale"]').forEach(el => {
                    if (el.style.display !== 'none' && window.getComputedStyle(el).display !== 'none') {
                        el.style.display = 'none';
                        removed++;
                    }
                });
                return removed;
            }
        """)
        if removed > 0:
            logger.info(f"✅ Force-removed {removed} modal elements via JS")
            dismissed = True
            await page.wait_for_timeout(1000)
    except Exception as e:
        logger.warning(f"JS modal removal failed: {e}")

    if not dismissed:
        logger.info("No modals found to dismiss")

    return dismissed


@app.post("/book")
async def book_appointment(request: Request, booking: BookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")
    screenshots.clear()
    logger.info(f"📅 Booking: {booking.customer_name} | {booking.service} | {booking.preferred_date} {booking.preferred_time}")
    return await run_wegest_booking(booking)


async def run_wegest_booking(request: BookingRequest) -> dict:
    WEGEST_USER = os.environ.get("WEGEST_USERNAME", "")
    WEGEST_PASSWORD = os.environ.get("WEGEST_PASSWORD", "")
    LOGIN_URL = "https://www.i-salon.eu/login/default.asp?login=&"

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
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
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

            try:
                await page.wait_for_function(
                    """() => {
                        const loginPanel = document.getElementById('pannello_login');
                        const menu = document.getElementById('menu');
                        
                        const loginHidden = loginPanel && 
                            window.getComputedStyle(loginPanel).display === 'none';
                        const menuVisible = menu && 
                            window.getComputedStyle(menu).display !== 'none';
                        const contents = document.querySelector('.wrapper_contents');
                        const hasContents = contents && 
                            window.getComputedStyle(contents).display !== 'none';
                        
                        return loginHidden || menuVisible || hasContents;
                    }""",
                    timeout=60000
                )
                logger.info("✅ Dashboard loaded — login successful!")
            except Exception as e:
                logger.warning(f"DOM wait timeout: {e}")
                await screenshot(page, "03_login_timeout")

            await page.wait_for_timeout(10000)
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
                    f"Login failed. Username='{WEGEST_USER}'. Check /screenshots."
                )

            logger.info("🎉 LOGIN SUCCESS!")
            await screenshot(page, "04_dashboard")

            # ── STEP 3.5: Dismiss post-login modals ──────────
            await dismiss_all_modals(page, "post-login")
            await screenshot(page, "04b_after_modal_dismiss")

            # ── STEP 4: Click Agenda ──────────────────────────
            logger.info("Step 4: Opening Agenda...")
            await page.wait_for_selector("[pannello='pannello_agenda']", timeout=10000)
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(5000)
            await screenshot(page, "05_agenda")
            logger.info("Agenda opened")

            # Dismiss modals again — they can reappear after navigation
            await dismiss_all_modals(page, "after-agenda")
            await page.wait_for_timeout(2000)
            await screenshot(page, "05b_agenda_clean")

            # ── STEP 5: Select date ───────────────────────────
            logger.info(f"Step 5: Selecting date {request.preferred_date}...")
            target_date = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day = target_date.day
            month = target_date.month
            year = target_date.year

            # Try clicking the date — use force:true to bypass overlay issues
            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            logger.info(f"Looking for date element: {date_selector}")

            try:
                await page.wait_for_selector(date_selector, timeout=10000)
                # Use force click to bypass any remaining overlays
                await page.click(date_selector, force=True)
                logger.info(f"Date clicked (force): {request.preferred_date}")
            except Exception as e:
                logger.warning(f"Direct date click failed: {e}")
                # Fallback: click via JavaScript
                clicked = await page.evaluate(f"""
                    () => {{
                        const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                        if (el) {{
                            el.click();
                            return true;
                        }}
                        return false;
                    }}
                """)
                if clicked:
                    logger.info(f"Date clicked via JS: {request.preferred_date}")
                else:
                    logger.warning("Could not find date element at all")

            await page.wait_for_timeout(3000)
            await screenshot(page, "06_date_selected")

            # Dismiss modals again after date selection
            await dismiss_all_modals(page, "after-date")
            await page.wait_for_timeout(1000)

            # ── STEP 6: Click time slot ───────────────────────
            logger.info(f"Step 6: Time slot {request.preferred_time}...")

            # Parse time for multiple formats
            time_parts = request.preferred_time.split(":")
            hour = time_parts[0]
            minute = time_parts[1] if len(time_parts) > 1 else "00"
            time_padded = f"{hour}:{minute}"
            time_no_pad = f"{int(hour)}:{minute}"

            time_clicked = False
            for sel in [
                f"[data-ora='{time_padded}']",
                f"[data-ora='{time_no_pad}']",
                f"[data-time='{time_padded}']",
                f"[data-time='{time_no_pad}']",
                f"td[ora='{time_padded}']",
                f"td[ora='{time_no_pad}']",
            ]:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible(timeout=2000):
                        await el.click(force=True)
                        logger.info(f"✅ Time clicked: {sel}")
                        time_clicked = True
                        break
                except:
                    continue

            if not time_clicked:
                # Fallback: try clicking on the agenda grid by coordinates
                logger.info("Time selectors failed — trying JS click on time cells...")
                time_clicked = await page.evaluate(f"""
                    () => {{
                        const cells = document.querySelectorAll('td[ora], [data-ora], [data-time]');
                        for (const cell of cells) {{
                            const ora = cell.getAttribute('ora') || cell.getAttribute('data-ora') || cell.getAttribute('data-time') || '';
                            if (ora === '{time_padded}' || ora === '{time_no_pad}') {{
                                cell.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}
                """)
                if time_clicked:
                    logger.info(f"✅ Time clicked via JS")
                else:
                    logger.warning("⚠️ Could not find time slot")

            await page.wait_for_timeout(3000)
            await screenshot(page, "07_time_slot_clicked")

            # Dismiss any modal that appeared
            await dismiss_all_modals(page, "after-time")

            # ── STEP 7: Customer search ───────────────────────
            logger.info(f"Step 7: Customer {request.customer_name}...")
            try:
                # Wait for the appointment form / customer search to appear
                customer_input = None
                for sel in [
                    "input[name='cerca_cliente']",
                    "input[placeholder*='cliente']",
                    "input[placeholder*='Cliente']",
                    "input[placeholder*='cerca']",
                    "#cerca_cliente",
                ]:
                    try:
                        await page.wait_for_selector(sel, timeout=5000)
                        customer_input = sel
                        break
                    except:
                        continue

                if customer_input:
                    first_name = request.customer_name.strip().split()[0]
                    await page.fill(customer_input, first_name)
                    await page.wait_for_timeout(2000)
                    await screenshot(page, "08_customer_search")

                    # Look for search results
                    results = await page.query_selector_all(
                        ".modale_body button.rimira, .risultati_ricerca button, .lista_clienti button, .cliente_risultato"
                    )
                    found = False
                    for r in results:
                        text = (await r.inner_text()).lower()
                        if first_name.lower() in text:
                            await r.click()
                            found = True
                            logger.info(f"✅ Customer found: {text.strip()}")
                            break

                    if not found:
                        logger.info("Customer not found — creating new...")
                        new_btn = await page.query_selector(
                            ".pulsanti button.primary, button:has-text('Nuovo'), button:has-text('nuovo')"
                        )
                        if new_btn:
                            await new_btn.click()
                            await page.wait_for_timeout(2000)
                            parts = request.customer_name.strip().split(" ", 1)
                            nome = await page.query_selector("input[name='Nome']")
                            cognome = await page.query_selector("input[name='Cognome']")
                            cell = await page.query_selector("input[name='Cellulare1']")
                            if nome:
                                await nome.fill(parts[0])
                            if cognome and len(parts) > 1:
                                await cognome.fill(parts[1])
                            if cell:
                                await cell.fill(request.caller_phone)
                            save = await page.query_selector(
                                ".pulsanti button.primary, button:has-text('Salva'), button:has-text('salva')"
                            )
                            if save:
                                await save.click()
                            await page.wait_for_timeout(2000)
                            logger.info("✅ New customer created")
                else:
                    logger.warning("Could not find customer search input")

            except Exception as e:
                logger.warning(f"Customer step issue: {e}")

            await screenshot(page, "09_customer_done")
            await page.wait_for_timeout(1000)

            # ── STEP 8: Select service ────────────────────────
            logger.info(f"Step 8: Service: {request.service}...")
            keywords = request.service.lower().split()
            service_selected = False

            els = await page.query_selector_all(
                ".pulsanti_tab .servizi button, .servizi button, button.servizio, .lista_servizi button"
            )
            logger.info(f"Found {len(els)} service buttons")
            for el in els:
                try:
                    text = (await el.inner_text()).lower()
                    if any(k in text for k in keywords):
                        await el.click()
                        logger.info(f"✅ Service selected: {text.strip()}")
                        service_selected = True
                        break
                except:
                    continue

            if not service_selected:
                logger.warning("Could not find service button — trying JS...")
                service_kw = request.service.lower()
                await page.evaluate(f"""
                    () => {{
                        const buttons = document.querySelectorAll('button');
                        for (const btn of buttons) {{
                            if (btn.textContent.toLowerCase().includes('{service_kw}')) {{
                                btn.click();
                                return;
                            }}
                        }}
                    }}
                """)

            await page.wait_for_timeout(1000)
            await screenshot(page, "10_service_selected")

            # ── STEP 9: Select operator ───────────────────────
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Operator: {request.operator_preference}...")
                ops = await page.query_selector_all(
                    ".pulsanti_tab .operatori button, .operatori button, button.operatore"
                )
                for op in ops:
                    try:
                        text = (await op.inner_text()).lower()
                        if request.operator_preference.lower() in text:
                            await op.click()
                            logger.info(f"✅ Operator: {text.strip()}")
                            break
                    except:
                        continue
            await page.wait_for_timeout(1000)

            # ── STEP 10: Add appointment ──────────────────────
            logger.info("Step 10: Adding appointment...")
            added = False
            for sel in [
                "button.aggiungi",
                ".form_appuntamento .pulsanti button.aggiungi",
                "button:has-text('Aggiungi appuntamento')",
                "button:has-text('Aggiungi')",
                "button:has-text('Salva')",
                "button:has-text('Conferma')",
                ".pulsanti button.primary",
            ]:
                try:
                    btn = page.locator(sel).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        added = True
                        logger.info(f"✅ Appointment added via: {sel}")
                        break
                except:
                    continue

            if not added:
                # JS fallback
                logger.info("Trying JS fallback for add button...")
                added = await page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll('button');
                        for (const btn of buttons) {
                            const text = btn.textContent.toLowerCase();
                            if (text.includes('aggiungi') || text.includes('salva') || text.includes('conferma')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)

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