"""
Agent Andrea - Wegest Direct Booking Service
Fixed: exact selectors for customer search, new customer form, time cells
Fixed: click ANNULLA on chiusura cassa modal
Fixed: handle stacked modals
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
    Dismiss Wegest modals (modale_dialog only).
    - Chiusura cassa: click ANNULLA (div.button.avviso)
    - Warnings: click OK/conferma (div.button.conferma)
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

        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;

                const testo1 = modal.querySelector('.testo1');
                const modalText = testo1 ? testo1.textContent.toLowerCase() : '';

                // Chiusura cassa modal → click ANNULLA
                if (modalText.includes('cassa') || modalText.includes('passaggio')) {
                    const annulla = modal.querySelector('.button.avviso');
                    if (annulla && window.getComputedStyle(annulla).display !== 'none') {
                        annulla.click();
                        return 'annulla-cassa';
                    }
                }

                // Other warnings → click conferma/OK
                const conferma = modal.querySelector('.button.conferma');
                if (conferma && window.getComputedStyle(conferma).display !== 'none') {
                    conferma.click();
                    return 'conferma-warning';
                }

                // Close button
                const chiudi = modal.querySelector('.button.chiudi');
                if (chiudi && window.getComputedStyle(chiudi).display !== 'none') {
                    chiudi.click();
                    return 'chiudi';
                }

                // Annulla fallback
                const annulla = modal.querySelector('.button.avviso');
                if (annulla && window.getComputedStyle(annulla).display !== 'none') {
                    annulla.click();
                    return 'annulla-fallback';
                }

                modal.style.display = 'none';
                return 'force-hidden';
            }
        """)

        if clicked:
            logger.info(f"  ✅ Dismissed via: {clicked}")

        await page.wait_for_timeout(2500)

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

            # ── STEP 3.5: Dismiss chiusura cassa modal ───────
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

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            logger.info(f"Date selector: {date_selector}")

            await dismiss_all_modals(page, "before-date")

            date_clicked = False
            date_exists = await page.evaluate(f"""
                () => {{
                    const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                    return !!el;
                }}
            """)
            logger.info(f"Date element exists: {date_exists}")

            if date_exists:
                try:
                    await page.click(date_selector, timeout=5000)
                    date_clicked = True
                    logger.info("✅ Date clicked normally")
                except:
                    try:
                        await page.click(date_selector, force=True, timeout=5000)
                        date_clicked = True
                        logger.info("✅ Date clicked (force)")
                    except:
                        pass

            if not date_clicked:
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
                    logger.error(f"❌ Date not found: {day}/{month}/{year}")

            await page.wait_for_timeout(3000)
            await screenshot(page, "06_date_selected")
            await dismiss_all_modals(page, "after-date")
            await page.wait_for_timeout(1000)

            # Verify cells loaded
            cells_loaded = await page.evaluate(f"""
                () => {{
                    return document.querySelectorAll(".cella[giorno='{day}'][mese='{month}'][anno='{year}']").length;
                }}
            """)
            logger.info(f"Cells loaded for {day}/{month}/{year}: {cells_loaded}")

            if cells_loaded == 0:
                logger.warning("No cells — retrying date click")
                await dismiss_all_modals(page, "retry-date")
                await page.evaluate(f"""
                    () => {{
                        const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                        if (el) el.click();
                    }}
                """)
                await page.wait_for_timeout(3000)
                await dismiss_all_modals(page, "retry-date-after")
                await screenshot(page, "06b_date_retry")

            # ── STEP 6: Click time slot ───────────────────────
            logger.info(f"Step 6: Time slot {request.preferred_time}...")
            time_parts = request.preferred_time.split(":")
            hour = str(int(time_parts[0]))
            minute = str(int(time_parts[1])) if len(time_parts) > 1 else "0"

            logger.info(f"Looking for: .cella[ora='{hour}'][minuto='{minute}'][giorno='{day}'][mese='{month}'][anno='{year}']")

            time_result = await page.evaluate(f"""
                () => {{
                    const cells = document.querySelectorAll(
                        ".cella[ora='{hour}'][minuto='{minute}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                    );

                    for (const cell of cells) {{
                        if (cell.classList.contains('assente')) continue;
                        if (cell.classList.contains('occupata')) continue;
                        cell.click();
                        return {{
                            clicked: true,
                            operatore: cell.getAttribute('id_operatore'),
                            text: cell.textContent.trim()
                        }};
                    }}

                    // Fallback: any cell at this hour
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
                            adjusted: cell.getAttribute('minuto')
                        }};
                    }}

                    return {{ clicked: false, total: cells.length }};
                }}
            """)

            if time_result and time_result.get('clicked'):
                logger.info(f"✅ Time clicked — operator: {time_result.get('operatore')}")
            else:
                logger.warning(f"⚠️ Could not click time. Result: {time_result}")

            await page.wait_for_timeout(3000)
            await screenshot(page, "07_time_slot_clicked")
            await dismiss_all_modals(page, "after-time")

            # ── STEP 7: Customer search & selection ───────────
            # After clicking a time cell, Wegest opens the customer search modal
            # div.cerca_cliente.modale with input[name='cerca_cliente']
            logger.info(f"Step 7: Customer {request.customer_name}...")
            customer_found = False

            try:
                # Wait for the customer search modal to appear
                await page.wait_for_selector(".cerca_cliente.modale input[name='cerca_cliente']", timeout=8000)
                logger.info("Customer search modal appeared")

                first_name = request.customer_name.strip().split()[0]
                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                logger.info(f"Typed '{first_name}' in search")

                # Wait for search results to load
                await page.wait_for_timeout(2000)
                await screenshot(page, "08_customer_search")

                # Check for results in the modale_body
                # Results appear as clickable elements in .modale_body
                results = await page.evaluate(f"""
                    () => {{
                        const body = document.querySelector('.cerca_cliente .modale_body');
                        if (!body) return {{ found: false, count: 0 }};

                        const items = body.querySelectorAll('div[id_cliente], .cliente, .risultato, .riga');
                        const searchName = '{first_name.lower()}';

                        for (const item of items) {{
                            const text = item.textContent.toLowerCase();
                            if (text.includes(searchName)) {{
                                item.click();
                                return {{ found: true, text: text.trim().substring(0, 50) }};
                            }}
                        }}

                        // Also try any clickable element in results
                        const allClickable = body.querySelectorAll('div, span, a');
                        for (const el of allClickable) {{
                            const text = el.textContent.toLowerCase();
                            if (text.includes(searchName) && el.offsetHeight > 0) {{
                                el.click();
                                return {{ found: true, text: text.trim().substring(0, 50) }};
                            }}
                        }}

                        return {{ found: false, count: items.length }};
                    }}
                """)

                if results and results.get('found'):
                    customer_found = True
                    logger.info(f"✅ Customer found and selected: {results.get('text', '')}")
                else:
                    logger.info(f"Customer not found in results (count: {results.get('count', 0)}) — creating new...")

                    # Click "Nuovo Cliente" button in the search modal
                    # It's: div.button.rimira.primary.aggiungi inside .cerca_cliente
                    await page.evaluate("""
                        () => {
                            const modal = document.querySelector('.cerca_cliente.modale');
                            if (!modal) return;
                            const btn = modal.querySelector('.button.primary.aggiungi');
                            if (btn) btn.click();
                        }
                    """)
                    logger.info("Clicked 'Nuovo Cliente'")
                    await page.wait_for_timeout(2000)
                    await screenshot(page, "08b_new_customer_form")

                    # Now the new customer form (div.form_cliente) should be visible
                    # Fill: nome, cognome, cellulare
                    parts = request.customer_name.strip().split(" ", 1)
                    nome = parts[0]
                    cognome = parts[1] if len(parts) > 1 else ""

                    # Strip country prefix from phone for the cellulare field
                    phone = request.caller_phone
                    if phone.startswith("+39"):
                        phone = phone[3:]
                    elif phone.startswith("0039"):
                        phone = phone[4:]

                    await page.evaluate(f"""
                        () => {{
                            const form = document.querySelector('.form_cliente');
                            if (!form) return;

                            const nomeInput = form.querySelector("input[name='nome']");
                            const cognomeInput = form.querySelector("input[name='cognome']");
                            const cellInput = form.querySelector("input[name='cellulare']");

                            if (nomeInput) {{
                                nomeInput.value = '{nome}';
                                nomeInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                nomeInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                            if (cognomeInput) {{
                                cognomeInput.value = '{cognome}';
                                cognomeInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                cognomeInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                            if (cellInput) {{
                                cellInput.value = '{phone}';
                                cellInput.dispatchEvent(new Event('input', {{ bubbles: true }}));
                                cellInput.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            }}
                        }}
                    """)
                    logger.info(f"Filled form: nome={nome}, cognome={cognome}, cell={phone}")
                    await page.wait_for_timeout(1000)
                    await screenshot(page, "08c_form_filled")

                    # Click "Aggiungi cliente" in the form footer
                    # It's: .form_cliente .modale_footer div.button.rimira.primary.aggiungi
                    await page.evaluate("""
                        () => {
                            const form = document.querySelector('.form_cliente');
                            if (!form) return;
                            const footer = form.querySelector('.modale_footer');
                            if (!footer) return;
                            const addBtn = footer.querySelector('.button.primary.aggiungi');
                            if (addBtn) addBtn.click();
                        }
                    """)
                    logger.info("Clicked 'Aggiungi cliente'")
                    customer_found = True
                    await page.wait_for_timeout(3000)
                    await screenshot(page, "08d_customer_added")

            except Exception as e:
                logger.warning(f"Customer step issue: {e}")
                await screenshot(page, "08_error")

            await screenshot(page, "09_customer_done")
            await page.wait_for_timeout(1000)

            # After selecting/creating customer, Wegest should show the appointment form
            # with service selection, operator selection, etc.
            await screenshot(page, "09b_appointment_form")

            # ── STEP 8: Select service ────────────────────────
            logger.info(f"Step 8: Service: {request.service}...")
            keywords = request.service.lower().split()
            service_selected = False

            # Look for service buttons/elements
            service_selected = await page.evaluate(f"""
                () => {{
                    const keywords = {list(keywords)};

                    // Try buttons with service-related classes
                    const selectors = [
                        '.servizi button', '.servizi div.button', 'button.servizio',
                        'div.button.servizio', '.lista_servizi button', '.lista_servizi div',
                        '.pulsanti_tab .servizi button', '.pulsanti_tab .servizi div.button'
                    ];

                    for (const selector of selectors) {{
                        const els = document.querySelectorAll(selector);
                        for (const el of els) {{
                            const text = el.textContent.toLowerCase().trim();
                            const style = window.getComputedStyle(el);
                            if (style.display === 'none' || style.visibility === 'hidden') continue;
                            for (const kw of keywords) {{
                                if (text.includes(kw)) {{
                                    el.click();
                                    return true;
                                }}
                            }}
                        }}
                    }}
                    return false;
                }}
            """)

            if service_selected:
                logger.info("✅ Service selected")
            else:
                logger.warning("⚠️ Could not find service button")

            await page.wait_for_timeout(1000)
            await screenshot(page, "10_service_selected")

            # ── STEP 9: Select operator ───────────────────────
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Operator: {request.operator_preference}...")
                op_name = request.operator_preference.lower()
                await page.evaluate(f"""
                    () => {{
                        const els = document.querySelectorAll(
                            '.operatori button, .operatori div.button, button.operatore, div.operatore'
                        );
                        for (const el of els) {{
                            const text = el.textContent.toLowerCase().trim();
                            if (text.includes('{op_name}')) {{
                                el.click();
                                return;
                            }}
                        }}
                    }}
                """)
            await page.wait_for_timeout(1000)

            # ── STEP 10: Add/confirm appointment ─────────────
            logger.info("Step 10: Adding appointment...")
            added = await page.evaluate("""
                () => {
                    // Look for add/save/confirm buttons
                    const selectors = [
                        '.form_appuntamento .button.aggiungi',
                        '.form_appuntamento .button.primary',
                        '.form_appuntamento .button.conferma',
                    ];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && window.getComputedStyle(el).display !== 'none') {
                            el.click();
                            return 'form_appuntamento';
                        }
                    }

                    // Broader search
                    const els = document.querySelectorAll('button, div.button');
                    for (const el of els) {
                        const text = el.textContent.toLowerCase().trim();
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (text.includes('aggiungi appuntamento') || text.includes('salva appuntamento')) {
                            el.click();
                            return 'text-match: ' + text;
                        }
                    }

                    // Even broader
                    for (const el of els) {
                        const text = el.textContent.toLowerCase().trim();
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        // Avoid clicking navigation buttons
                        if (el.closest('#menu') || el.closest('.cerca_cliente')) continue;
                        if (text === 'aggiungi' || text.includes('conferma')) {
                            el.click();
                            return 'broad-match: ' + text;
                        }
                    }

                    return null;
                }
            """)

            if added:
                logger.info(f"✅ Appointment button clicked: {added}")
            else:
                logger.warning("⚠️ Could not find appointment add button")

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

            logger.info(f"Verify — agenda: {on_agenda} | customer: {has_customer_on_page} | error: {has_error_modal} | added: {added}")

            success = on_agenda and not has_error_modal and (has_customer_on_page or bool(added))

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