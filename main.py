"""
Agent Andrea - Wegest Direct Booking Service
Fixed: time slots are div.cella with ora/minuto attributes
Fixed: click ANNULLA on chiusura cassa modal
Fixed: handle "Selezionare una data" warning
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
    """
    Dismiss all Wegest modals.
    - Chiusura cassa modal: click ANNULLA (div.button.avviso)
    - Warning modals (e.g. "Selezionare una data"): click OK (div.button.conferma)
    - All Wegest buttons are DIVs, not <button> elements.
    """
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
            logger.info(f"  ✅ No modal visible (attempt {attempt + 1})")
            break

        logger.info(f"  ⚠️ Modal detected (attempt {attempt + 1})")

        # Read the modal text to decide which button to click
        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;
                
                const testo1 = modal.querySelector('.testo1');
                const modalText = testo1 ? testo1.textContent.toLowerCase() : '';
                
                // If it's the "chiusura cassa" modal → click ANNULLA
                if (modalText.includes('cassa') || modalText.includes('passaggio')) {
                    const annulla = modal.querySelector('.button.avviso');
                    if (annulla && window.getComputedStyle(annulla).display !== 'none') {
                        annulla.click();
                        return 'annulla-cassa';
                    }
                }
                
                // For any other modal (warnings like "Selezionare una data") → click conferma/OK
                const conferma = modal.querySelector('.button.conferma');
                if (conferma && window.getComputedStyle(conferma).display !== 'none') {
                    conferma.click();
                    return 'conferma-warning';
                }
                
                // Try chiudi (close)
                const chiudi = modal.querySelector('.button.chiudi');
                if (chiudi && window.getComputedStyle(chiudi).display !== 'none') {
                    chiudi.click();
                    return 'chiudi';
                }
                
                // Try annulla as fallback
                const annulla = modal.querySelector('.button.avviso');
                if (annulla && window.getComputedStyle(annulla).display !== 'none') {
                    annulla.click();
                    return 'annulla-fallback';
                }
                
                // Last resort: force hide
                modal.style.display = 'none';
                return 'force-hidden';
            }
        """)

        if clicked:
            logger.info(f"  ✅ Dismissed via: {clicked}")
        else:
            logger.warning(f"  ❌ Could not dismiss modal")

        await page.wait_for_timeout(2500)

    # Remove any overlay backgrounds
    await page.evaluate("""
        () => {
            document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay').forEach(el => {
                if (window.getComputedStyle(el).display !== 'none') {
                    el.style.display = 'none';
                }
            });
        }
    """)

    logger.info(f"  Modal sweep complete: {label}")


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
            logger.info("Login button clicked — waiting for dashboard...")

            try:
                await page.wait_for_function(
                    """() => {
                        const loginPanel = document.getElementById('pannello_login');
                        const menu = document.getElementById('menu');
                        const loginHidden = loginPanel && window.getComputedStyle(loginPanel).display === 'none';
                        const menuVisible = menu && window.getComputedStyle(menu).display !== 'none';
                        const contents = document.querySelector('.wrapper_contents');
                        const hasContents = contents && window.getComputedStyle(contents).display !== 'none';
                        return loginHidden || menuVisible || hasContents;
                    }""",
                    timeout=60000
                )
                logger.info("✅ Dashboard loaded!")
            except Exception as e:
                logger.warning(f"DOM wait timeout: {e}")
                await screenshot(page, "03_login_timeout")

            await page.wait_for_timeout(10000)
            await screenshot(page, "03_after_login")

            # Verify login
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
                raise Exception("Login failed. Check /screenshots.")

            logger.info("🎉 LOGIN SUCCESS!")
            await screenshot(page, "04_dashboard")

            # ══════════════════════════════════════════════════
            # STEP 3.5: CLICK ANNULLA ON CHIUSURA CASSA MODAL
            # ══════════════════════════════════════════════════
            logger.info("Step 3.5: Dismissing chiusura cassa modal...")
            await dismiss_all_modals(page, "post-login")
            await page.wait_for_timeout(2000)
            await screenshot(page, "04b_modals_cleared")

            # ── STEP 4: Click Agenda ──────────────────────────
            logger.info("Step 4: Opening Agenda...")
            await page.wait_for_selector("[pannello='pannello_agenda']", timeout=10000)
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(5000)

            await dismiss_all_modals(page, "after-agenda")
            await page.wait_for_timeout(2000)
            await screenshot(page, "05_agenda_clean")
            logger.info("Agenda opened and clean")

            # ── STEP 5: Select date ───────────────────────────
            logger.info(f"Step 5: Selecting date {request.preferred_date}...")
            target_date = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day = target_date.day
            month = target_date.month
            year = target_date.year

            # The calendar strip at top uses div.data with giorno/mese/anno
            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            logger.info(f"Date selector: {date_selector}")

            await dismiss_all_modals(page, "before-date")

            date_clicked = False

            # First check if the date is already visible in the calendar strip
            date_exists = await page.evaluate(f"""
                () => {{
                    const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                    return !!el;
                }}
            """)
            logger.info(f"Date element exists in DOM: {date_exists}")

            if date_exists:
                try:
                    await page.click(date_selector, timeout=5000)
                    date_clicked = True
                    logger.info("✅ Date clicked normally")
                except Exception as e:
                    logger.warning(f"Normal date click failed: {e}")
                    try:
                        await page.click(date_selector, force=True, timeout=5000)
                        date_clicked = True
                        logger.info("✅ Date clicked (force)")
                    except:
                        pass

            if not date_clicked:
                # JS fallback
                clicked_js = await page.evaluate(f"""
                    () => {{
                        const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                        if (el) {{ el.click(); return true; }}
                        return false;
                    }}
                """)
                if clicked_js:
                    date_clicked = True
                    logger.info("✅ Date clicked via JS")
                else:
                    logger.error(f"❌ Date element not found for {day}/{month}/{year}")

            await page.wait_for_timeout(3000)
            await screenshot(page, "06_date_selected")

            # Dismiss any warning that appeared
            await dismiss_all_modals(page, "after-date")
            await page.wait_for_timeout(1000)

            # Verify the date was actually selected by checking if cells loaded for that date
            cells_loaded = await page.evaluate(f"""
                () => {{
                    const cells = document.querySelectorAll(".cella[giorno='{day}'][mese='{month}'][anno='{year}']");
                    return cells.length;
                }}
            """)
            logger.info(f"Cells loaded for {day}/{month}/{year}: {cells_loaded}")

            if cells_loaded == 0:
                logger.warning("No cells found for selected date — date may not have registered")
                # Try clicking the date one more time
                await dismiss_all_modals(page, "retry-before-date")
                await page.evaluate(f"""
                    () => {{
                        const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                        if (el) el.click();
                    }}
                """)
                await page.wait_for_timeout(3000)
                await dismiss_all_modals(page, "retry-after-date")
                await page.wait_for_timeout(1000)
                await screenshot(page, "06b_date_retry")

            # ── STEP 6: Click time slot ───────────────────────
            # Time cells are: div.cella[ora="10"][minuto="0"][giorno="19"][mese="3"][anno="2026"]
            logger.info(f"Step 6: Time slot {request.preferred_time}...")

            time_parts = request.preferred_time.split(":")
            hour = str(int(time_parts[0]))  # Remove leading zero: "09" -> "9"
            minute = str(int(time_parts[1])) if len(time_parts) > 1 else "0"  # "00" -> "0"

            logger.info(f"Looking for cell: ora='{hour}' minuto='{minute}' giorno='{day}' mese='{month}' anno='{year}'")

            # Click the first available operator's cell at this time
            time_clicked = await page.evaluate(f"""
                () => {{
                    // Find all cells matching this time on this date
                    const cells = document.querySelectorAll(
                        ".cella[ora='{hour}'][minuto='{minute}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                    );
                    
                    console.log('Found ' + cells.length + ' matching cells');
                    
                    for (const cell of cells) {{
                        // Skip cells that are marked as absent
                        if (cell.classList.contains('assente')) continue;
                        
                        // Skip cells that already have an appointment
                        if (cell.classList.contains('occupata')) continue;
                        
                        // Click the first available cell
                        cell.click();
                        return {{
                            clicked: true,
                            operatore: cell.getAttribute('id_operatore'),
                            text: cell.textContent.trim()
                        }};
                    }}
                    
                    // If all cells at exact time are unavailable, try any cell at this hour
                    if (cells.length === 0) {{
                        const anyCells = document.querySelectorAll(
                            ".cella[ora='{hour}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                        );
                        for (const cell of anyCells) {{
                            if (cell.classList.contains('assente')) continue;
                            if (cell.classList.contains('occupata')) continue;
                            cell.click();
                            return {{
                                clicked: true,
                                operatore: cell.getAttribute('id_operatore'),
                                text: cell.textContent.trim(),
                                adjusted_minute: cell.getAttribute('minuto')
                            }};
                        }}
                    }}
                    
                    return {{ clicked: false, total: cells.length }};
                }}
            """)

            if time_clicked and time_clicked.get('clicked'):
                logger.info(f"✅ Time slot clicked — operator: {time_clicked.get('operatore')}, text: {time_clicked.get('text')}")
                if time_clicked.get('adjusted_minute'):
                    logger.info(f"  (adjusted to minute {time_clicked['adjusted_minute']})")
            else:
                logger.warning(f"⚠️ Could not click time slot. Result: {time_clicked}")

            await page.wait_for_timeout(3000)
            await screenshot(page, "07_time_slot_clicked")
            await dismiss_all_modals(page, "after-time")

            # ── STEP 7: Customer search ───────────────────────
            logger.info(f"Step 7: Customer {request.customer_name}...")
            customer_found = False
            try:
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

                    # Wegest uses div.button.rimira for results too
                    results = await page.query_selector_all(
                        ".modale_body button.rimira, .modale_body div.button.rimira, "
                        ".risultati_ricerca button, .risultati_ricerca div.button, "
                        ".lista_clienti button, .lista_clienti div.button, "
                        ".cliente_risultato"
                    )
                    for r in results:
                        text = (await r.inner_text()).lower()
                        if first_name.lower() in text:
                            await r.click()
                            customer_found = True
                            logger.info(f"✅ Customer found: {text.strip()}")
                            break

                    if not customer_found:
                        logger.info("Customer not found — creating new...")
                        # Try clicking "Nuovo cliente" button
                        new_clicked = await page.evaluate("""
                            () => {
                                const buttons = document.querySelectorAll('div.button, button');
                                for (const btn of buttons) {
                                    const text = btn.textContent.toLowerCase().trim();
                                    const style = window.getComputedStyle(btn);
                                    if (style.display === 'none') continue;
                                    if (text.includes('nuovo') || text.includes('crea') || text.includes('aggiungi cliente')) {
                                        btn.click();
                                        return true;
                                    }
                                }
                                return false;
                            }
                        """)

                        if new_clicked:
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

                            # Save new customer
                            await page.evaluate("""
                                () => {
                                    const buttons = document.querySelectorAll('div.button, button');
                                    for (const btn of buttons) {
                                        const text = btn.textContent.toLowerCase().trim();
                                        const style = window.getComputedStyle(btn);
                                        if (style.display === 'none') continue;
                                        if (text.includes('salva') || text.includes('conferma')) {
                                            btn.click();
                                            return;
                                        }
                                    }
                                }
                            """)
                            await page.wait_for_timeout(2000)
                            customer_found = True
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

            # Try all possible service button selectors
            els = await page.query_selector_all(
                ".pulsanti_tab .servizi button, .servizi button, button.servizio, "
                ".lista_servizi button, div.button.servizio, .servizi div.button"
            )
            logger.info(f"Found {len(els)} service buttons")
            for el in els:
                try:
                    text = (await el.inner_text()).lower()
                    if any(k in text for k in keywords):
                        await el.click()
                        service_selected = True
                        logger.info(f"✅ Service selected: {text.strip()}")
                        break
                except:
                    continue

            if not service_selected:
                logger.warning("Service not found via selectors — trying JS...")
                service_kw = request.service.lower()
                service_selected = await page.evaluate(f"""
                    () => {{
                        const els = document.querySelectorAll('button, div.button, div.servizio, span, div');
                        for (const el of els) {{
                            const text = el.textContent.toLowerCase().trim();
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden') continue;
                            if (text === '{service_kw}' || text.includes('{service_kw}')) {{
                                // Make sure it's a clickable element, not a container
                                if (el.classList.contains('button') || el.classList.contains('servizio') || el.tagName === 'BUTTON') {{
                                    el.click();
                                    return true;
                                }}
                            }}
                        }}
                        return false;
                    }}
                """)

            await page.wait_for_timeout(1000)
            await screenshot(page, "10_service_selected")

            # ── STEP 9: Select operator ───────────────────────
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Operator: {request.operator_preference}...")
                op_name = request.operator_preference.lower()
                await page.evaluate(f"""
                    () => {{
                        const els = document.querySelectorAll('button, div.button, div.operatore');
                        for (const el of els) {{
                            const text = el.textContent.toLowerCase().trim();
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none') continue;
                            if (text.includes('{op_name}')) {{
                                el.click();
                                return;
                            }}
                        }}
                    }}
                """)
            await page.wait_for_timeout(1000)

            # ── STEP 10: Add appointment ──────────────────────
            logger.info("Step 10: Adding appointment...")
            added = False

            # Use JS to find and click the add/save button
            added = await page.evaluate("""
                () => {
                    const els = document.querySelectorAll('button, div.button');
                    for (const el of els) {
                        const text = el.textContent.toLowerCase().trim();
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (text.includes('aggiungi') || text.includes('salva appuntamento')) {
                            el.click();
                            return true;
                        }
                    }
                    return false;
                }
            """)

            if added:
                logger.info("✅ Add/Save button clicked")
            else:
                logger.warning("⚠️ Could not find add/save button")
                # Try Playwright selectors as fallback
                for sel in [
                    "button.aggiungi",
                    "div.button.aggiungi",
                    "button:has-text('Aggiungi')",
                    "div.button:has-text('Aggiungi')",
                    "button:has-text('Salva')",
                    "div.button:has-text('Salva')",
                ]:
                    try:
                        btn = page.locator(sel).first
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                            added = True
                            logger.info(f"✅ Added via fallback: {sel}")
                            break
                    except:
                        continue

            await page.wait_for_timeout(3000)
            await screenshot(page, "11_final_result")

            # ── VERIFY ────────────────────────────────────────
            await screenshot(page, "12_verification")

            page_content = (await page.content()).lower()
            first_name = request.customer_name.lower().split()[0]
            has_customer_on_page = first_name in page_content

            on_agenda = await page.evaluate("""
                () => {
                    const agenda = document.getElementById('pannello_agenda');
                    if (!agenda) return false;
                    return window.getComputedStyle(agenda).display !== 'none';
                }
            """)

            has_error_modal = await page.evaluate("""
                () => {
                    const modal = document.getElementById('modale_dialog');
                    if (!modal) return false;
                    const style = window.getComputedStyle(modal);
                    return style.display !== 'none' && style.visibility !== 'hidden';
                }
            """)

            logger.info(f"Verify — agenda: {on_agenda} | customer: {has_customer_on_page} | error_modal: {has_error_modal} | added: {added}")

            success = on_agenda and not has_error_modal and (has_customer_on_page or added)

            await browser.close()
            logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "service": request.service,
                "date": request.preferred_date,
                "time": request.preferred_time,
                "operator": request.operator_preference,
                "message": "✅ Appointment created in Wegest" if success else "⚠️ Could not confirm — verify in Wegest",
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