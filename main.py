"""
Agent Andrea - Wegest Direct Booking Service
All selectors verified against actual Wegest HTML (March 2025)
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


def js_escape(s: str) -> str:
    """Escape string for safe use in JS template literals."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")


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


async def take_screenshot(page, name: str):
    try:
        data = await page.screenshot(type="png", full_page=True)
        screenshots[name] = base64.b64encode(data).decode()
        logger.info(f"📸 Screenshot: {name}")
    except Exception as e:
        logger.warning(f"Screenshot failed ({name}): {e}")


async def dismiss_system_modals(page, label=""):
    """Dismiss system warning/cassa modals only — NOT appointment form modals."""
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
        logger.info(f"  ⚠️ System modal detected (attempt {attempt + 1})")
        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;
                const testo1 = modal.querySelector('.testo1');
                const modalText = testo1 ? testo1.textContent.toLowerCase() : '';

                // Cassa/passaggio warnings — click annulla
                if (modalText.includes('cassa') || modalText.includes('passaggio')) {
                    const annulla = modal.querySelector('.button.avviso');
                    if (annulla && window.getComputedStyle(annulla).display !== 'none') {
                        annulla.click();
                        return 'annulla-cassa';
                    }
                }
                // Generic confirm
                const conferma = modal.querySelector('.button.conferma');
                if (conferma && window.getComputedStyle(conferma).display !== 'none') {
                    conferma.click();
                    return 'conferma';
                }
                // Close
                const chiudi = modal.querySelector('.button.chiudi');
                if (chiudi && window.getComputedStyle(chiudi).display !== 'none') {
                    chiudi.click();
                    return 'chiudi';
                }
                // Avviso fallback
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

    # Clean up any remaining overlays
    await page.evaluate("""
        () => {
            document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay').forEach(el => {
                if (window.getComputedStyle(el).display !== 'none') el.style.display = 'none';
            });
        }
    """)


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
            # ═══════════════════════════════════════════════════
            # STEP 1: Load login page
            # ═══════════════════════════════════════════════════
            logger.info("Step 1: Loading login page...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            await take_screenshot(page, "01_login_page")

            # ═══════════════════════════════════════════════════
            # STEP 2: Fill credentials
            # Verified: input[name='username'], input[name='password'], input[name='codice']
            # ═══════════════════════════════════════════════════
            logger.info("Step 2: Filling credentials...")
            await page.fill("input[name='username']", WEGEST_USER)
            await page.fill("input[name='password']", WEGEST_PASSWORD)
            await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")
            await take_screenshot(page, "02_credentials_filled")

            # ═══════════════════════════════════════════════════
            # STEP 3: Click login
            # Verified: div.button triggers login
            # ═══════════════════════════════════════════════════
            logger.info("Step 3: Clicking login...")
            await page.click("div.button")
            try:
                await page.wait_for_function(
                    """() => {
                        const lp = document.getElementById('pannello_login');
                        const menu = document.getElementById('menu');
                        return (lp && window.getComputedStyle(lp).display === 'none') ||
                               (menu && window.getComputedStyle(menu).display !== 'none');
                    }""",
                    timeout=60000
                )
            except:
                pass

            await page.wait_for_timeout(30000)
            await take_screenshot(page, "03_after_login")

            login_visible = await page.evaluate("""() => {
                const el = document.getElementById('pannello_login');
                return el ? window.getComputedStyle(el).display !== 'none' : false;
            }""")
            if login_visible:
                raise Exception("Login failed — login panel still visible")

            logger.info("🎉 LOGIN SUCCESS!")

            # ═══════════════════════════════════════════════════
            # STEP 3.5: Dismiss post-login system modals
            # ═══════════════════════════════════════════════════
            await dismiss_system_modals(page, "post-login")
            await page.wait_for_timeout(2000)
            await take_screenshot(page, "04_modals_cleared")

            # ═══════════════════════════════════════════════════
            # STEP 4: Open Agenda
            # Verified: [pannello='pannello_agenda']
            # ═══════════════════════════════════════════════════
            logger.info("Step 4: Opening Agenda...")
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(5000)
            await dismiss_system_modals(page, "after-agenda")
            await page.wait_for_timeout(2000)
            await take_screenshot(page, "05_agenda")

            # ═══════════════════════════════════════════════════
            # STEP 5: Select date
            # Verified: .data[giorno='D'][mese='M'][anno='Y']
            # ═══════════════════════════════════════════════════
            target = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day, month, year = target.day, target.month, target.year
            logger.info(f"Step 5: Date {day}/{month}/{year}...")

            await dismiss_system_modals(page, "before-date")

            date_clicked = await page.evaluate(f"""
                () => {{
                    const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                    if (el) {{ el.click(); return true; }}
                    return false;
                }}
            """)
            logger.info(f"Date clicked: {date_clicked}")
            if not date_clicked:
                raise Exception(f"Date not found on calendar: {day}/{month}/{year}")

            await page.wait_for_timeout(4000)
            await dismiss_system_modals(page, "after-date")
            await take_screenshot(page, "06_date_selected")

            # Verify time cells loaded for this date
            cells = await page.evaluate(f"""
                () => document.querySelectorAll(".cella[giorno='{day}'][mese='{month}'][anno='{year}']").length
            """)
            logger.info(f"Time cells for date: {cells}")

            if cells == 0:
                # Retry date click
                await page.evaluate(f"""
                    () => {{
                        const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                        if (el) el.click();
                    }}
                """)
                await page.wait_for_timeout(4000)
                await dismiss_system_modals(page, "retry-date")

            # ═══════════════════════════════════════════════════
            # STEP 6: Click time slot
            # Verified: .cella[ora='H'][minuto='M'][giorno][mese][anno]
            # Skip cells with class 'assente' or 'occupata'
            # ═══════════════════════════════════════════════════
            hour = str(int(request.preferred_time.split(":")[0]))
            minute = str(int(request.preferred_time.split(":")[1])) if ":" in request.preferred_time else "0"
            logger.info(f"Step 6: Time ora={hour} minuto={minute}...")

            time_result = await page.evaluate(f"""
                () => {{
                    // Exact match first
                    const exact = document.querySelectorAll(
                        ".cella[ora='{hour}'][minuto='{minute}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                    );
                    for (const cell of exact) {{
                        if (cell.classList.contains('assente') || cell.classList.contains('occupata')) continue;
                        cell.click();
                        return {{ clicked: true, ora: '{hour}', minuto: '{minute}', op: cell.getAttribute('id_operatore') }};
                    }}
                    // Fallback: any available slot at this hour
                    const hourCells = document.querySelectorAll(
                        ".cella[ora='{hour}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                    );
                    for (const cell of hourCells) {{
                        if (cell.classList.contains('assente') || cell.classList.contains('occupata')) continue;
                        cell.click();
                        return {{ clicked: true, ora: '{hour}', minuto: cell.getAttribute('minuto'), op: cell.getAttribute('id_operatore'), adjusted: true }};
                    }}
                    return {{ clicked: false }};
                }}
            """)
            logger.info(f"Time result: {time_result}")

            if not time_result or not time_result.get('clicked'):
                raise Exception(f"No available time slot at {hour}:{minute} on {day}/{month}/{year}")

            await page.wait_for_timeout(3000)
            await take_screenshot(page, "07_time_clicked")

            # ═══════════════════════════════════════════════════
            # STEP 7: Customer search & selection
            # Verified: .cerca_cliente.modale opens after clicking cell
            # Search: input[name='cerca_cliente']
            # Results: .tabella_clienti tbody tr[id] with p.cliente for name
            # New customer: .cerca_cliente .pulsanti .button.rimira.primary.aggiungi
            # ═══════════════════════════════════════════════════
            logger.info(f"Step 7: Customer '{request.customer_name}'...")
            customer_found = False

            try:
                await page.wait_for_selector(".cerca_cliente.modale input[name='cerca_cliente']", timeout=10000)
                logger.info("✅ Customer search modal visible")

                first_name = request.customer_name.strip().split()[0]
                first_name_safe = js_escape(first_name)
                full_name_safe = js_escape(request.customer_name)

                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                await page.wait_for_timeout(3000)
                await take_screenshot(page, "08_customer_search")

                # Search results are <tr id="CUSTOMER_ID"> rows
                results = await page.evaluate(f"""
                    () => {{
                        const keyword = '{first_name_safe}'.toLowerCase();
                        const fullName = '{full_name_safe}'.toLowerCase();
                        const rows = document.querySelectorAll('.cerca_cliente .tabella_clienti tbody tr[id]');

                        // Best match: full name
                        for (const row of rows) {{
                            const nameEl = row.querySelector('p.cliente');
                            if (nameEl && nameEl.textContent.toLowerCase().includes(fullName)) {{
                                row.click();
                                return {{ found: true, id: row.id, name: nameEl.textContent.trim(), method: 'fullname' }};
                            }}
                        }}

                        // Good match: first name
                        for (const row of rows) {{
                            const nameEl = row.querySelector('p.cliente');
                            if (nameEl && nameEl.textContent.toLowerCase().includes(keyword)) {{
                                row.click();
                                return {{ found: true, id: row.id, name: nameEl.textContent.trim(), method: 'firstname' }};
                            }}
                        }}

                        // Only one result: click it
                        if (rows.length === 1) {{
                            rows[0].click();
                            const n = rows[0].querySelector('p.cliente');
                            return {{ found: true, id: rows[0].id, name: n ? n.textContent.trim() : '?', method: 'only-result' }};
                        }}

                        return {{ found: false, count: rows.length }};
                    }}
                """)

                if results and results.get('found'):
                    customer_found = True
                    logger.info(f"✅ Customer selected: {results}")
                else:
                    logger.info(f"Customer not found ({results}). Creating new customer...")

                    # Click "New Customer" button in search modal
                    await page.click(".cerca_cliente .pulsanti .button.rimira.primary.aggiungi")
                    await page.wait_for_timeout(2000)
                    await take_screenshot(page, "08b_new_customer_form")

                    parts = request.customer_name.strip().split(" ", 1)
                    nome = js_escape(parts[0])
                    cognome = js_escape(parts[1]) if len(parts) > 1 else ""

                    phone = request.caller_phone
                    if phone.startswith("+39"):
                        phone = phone[3:]
                    elif phone.startswith("0039"):
                        phone = phone[4:]
                    phone_safe = js_escape(phone)

                    await page.evaluate(f"""
                        () => {{
                            const setVal = (name, val) => {{
                                const inputs = document.querySelectorAll('input[name="' + name + '"]');
                                for (const inp of inputs) {{
                                    if (window.getComputedStyle(inp).display === 'none') continue;
                                    inp.value = val;
                                    inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                    inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                                    return;
                                }}
                            }};
                            setVal('nome', '{nome}');
                            setVal('cognome', '{cognome}');
                            setVal('cellulare', '{phone_safe}');
                        }}
                    """)
                    await take_screenshot(page, "08c_form_filled")

                    # Click save/confirm in the new customer form
                    await page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('.button.rimira.primary');
                            for (const btn of btns) {
                                const style = window.getComputedStyle(btn);
                                if (style.display === 'none') continue;
                                const text = btn.textContent.toLowerCase();
                                // Skip the appointment "aggiungi" button
                                if (btn.closest('.azioni')) continue;
                                if (text.includes('salva') || text.includes('conferma') || text.includes('aggiungi')) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    customer_found = True
                    await page.wait_for_timeout(3000)
                    await take_screenshot(page, "08d_customer_created")

            except Exception as e:
                logger.warning(f"Customer step error: {e}")
                await take_screenshot(page, "08_error")

            await page.wait_for_timeout(2000)

            # ═══════════════════════════════════════════════════
            # STEP 7.5: Handle phone number modal
            # Verified: .modale.card.inserisci_cellulare
            # Input: .inserisci_cellulare input[name='cellulare']
            # Confirm: .inserisci_cellulare .button.rimira.primary.conferma
            # ═══════════════════════════════════════════════════
            phone_modal = await page.evaluate("""
                () => {
                    const m = document.querySelector('.modale.card.inserisci_cellulare');
                    if (!m) return false;
                    return window.getComputedStyle(m).display !== 'none';
                }
            """)
            if phone_modal:
                logger.info("📱 Phone number modal detected — filling phone...")
                phone = request.caller_phone
                if phone.startswith("+39"):
                    phone = phone[3:]
                elif phone.startswith("0039"):
                    phone = phone[4:]

                await page.fill(".inserisci_cellulare input[name='cellulare']", phone)
                await page.wait_for_timeout(500)
                await page.click(".inserisci_cellulare .button.rimira.primary.conferma")
                logger.info(f"✅ Phone entered: {phone}")
                await page.wait_for_timeout(2000)
                await take_screenshot(page, "08e_phone_entered")

            await take_screenshot(page, "09_appointment_form")

            # ═══════════════════════════════════════════════════
            # STEP 8: Select service
            # Verified: .pulsanti_tab .servizio[nome="SERVICE_NAME"]
            # The nome attribute contains the Italian service name
            # Services tab (tab="servizi") is active by default with ALL services
            # ═══════════════════════════════════════════════════
            logger.info(f"Step 8: Service '{request.service}'...")
            service_kw = js_escape(request.service.lower())

            service_selected = await page.evaluate(f"""
                () => {{
                    const keyword = '{service_kw}';
                    const allServices = document.querySelectorAll('.pulsanti_tab .servizio');

                    // 1. Exact match on nome attribute
                    for (const svc of allServices) {{
                        const nome = (svc.getAttribute('nome') || '').toLowerCase();
                        if (nome === keyword) {{
                            svc.click();
                            return {{ selected: true, nome: svc.getAttribute('nome'), id: svc.id, method: 'exact-nome' }};
                        }}
                    }}

                    // 2. nome starts with keyword
                    for (const svc of allServices) {{
                        const nome = (svc.getAttribute('nome') || '').toLowerCase();
                        if (nome.startsWith(keyword)) {{
                            svc.click();
                            return {{ selected: true, nome: svc.getAttribute('nome'), id: svc.id, method: 'starts-with' }};
                        }}
                    }}

                    // 3. nome contains keyword
                    for (const svc of allServices) {{
                        const nome = (svc.getAttribute('nome') || '').toLowerCase();
                        if (nome.includes(keyword)) {{
                            svc.click();
                            return {{ selected: true, nome: svc.getAttribute('nome'), id: svc.id, method: 'contains' }};
                        }}
                    }}

                    // 4. keyword contains nome (e.g., "taglio donna" matches "TAGLIO")
                    for (const svc of allServices) {{
                        const nome = (svc.getAttribute('nome') || '').toLowerCase();
                        if (nome.length > 2 && keyword.includes(nome)) {{
                            svc.click();
                            return {{ selected: true, nome: svc.getAttribute('nome'), id: svc.id, method: 'reverse-contains' }};
                        }}
                    }}

                    // Debug: list all available services
                    const available = [];
                    allServices.forEach(s => available.push(s.getAttribute('nome')));
                    return {{ selected: false, available: available }};
                }}
            """)

            if service_selected and service_selected.get('selected'):
                logger.info(f"✅ Service: {service_selected}")
            else:
                logger.warning(f"⚠️ Service not found by name. Trying search box... Available: {service_selected}")
                try:
                    await page.fill(".pulsanti_tab input[name='cerca_servizio']", request.service)
                    await page.wait_for_timeout(2000)
                    clicked_search = await page.evaluate("""
                        () => {
                            const svcs = document.querySelectorAll('.pulsanti_tab .servizio');
                            for (const svc of svcs) {
                                if (window.getComputedStyle(svc).display !== 'none' &&
                                    window.getComputedStyle(svc.parentElement).display !== 'none') {
                                    svc.click();
                                    return svc.getAttribute('nome');
                                }
                            }
                            return null;
                        }
                    """)
                    if clicked_search:
                        logger.info(f"✅ Service via search: {clicked_search}")
                except Exception as e:
                    logger.warning(f"Service search failed: {e}")

            await page.wait_for_timeout(1500)
            await take_screenshot(page, "10_service_selected")

            # ═══════════════════════════════════════════════════
            # STEP 9: Select operator (optional)
            # Verified: .pulsanti_tab .operatori .operatore
            # Name in span.nome, absent operators have class .assente
            # ═══════════════════════════════════════════════════
            if request.operator_preference.lower() != "prima disponibile":
                op_name_safe = js_escape(request.operator_preference.lower())
                logger.info(f"Step 9: Operator '{request.operator_preference}'...")

                op_result = await page.evaluate(f"""
                    () => {{
                        const keyword = '{op_name_safe}';
                        const ops = document.querySelectorAll('.pulsanti_tab .operatori .operatore');
                        for (const op of ops) {{
                            if (op.classList.contains('assente')) continue;
                            const nome = op.querySelector('span.nome');
                            if (nome && nome.textContent.toLowerCase().trim().includes(keyword)) {{
                                op.click();
                                return {{ selected: true, name: nome.textContent.trim(), id: op.id }};
                            }}
                        }}
                        const available = [];
                        ops.forEach(op => {{
                            const n = op.querySelector('span.nome');
                            available.push({{
                                name: n ? n.textContent.trim() : '?',
                                id: op.id,
                                absent: op.classList.contains('assente')
                            }});
                        }});
                        return {{ selected: false, available: available }};
                    }}
                """)
                logger.info(f"Operator result: {op_result}")
            else:
                logger.info("Step 9: Using default operator (prima disponibile)")

            await page.wait_for_timeout(1000)
            await take_screenshot(page, "11_operator")

            # ═══════════════════════════════════════════════════
            # STEP 10: Click "Add appointment"
            # Verified: .azioni .button.rimira.primary.aggiungi
            # (NOT .cerca_cliente or .inserisci_cellulare versions)
            # ═══════════════════════════════════════════════════
            logger.info("Step 10: Clicking 'Add appointment'...")

            added = await page.evaluate("""
                () => {
                    // Primary target: the exact button in .azioni
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (btn) {
                        const style = window.getComputedStyle(btn);
                        if (style.display !== 'none' && style.visibility !== 'hidden') {
                            btn.click();
                            return 'azioni-aggiungi';
                        }
                    }

                    // Fallback: any visible primary aggiungi NOT in search/phone modals
                    const allBtns = document.querySelectorAll('.button.rimira.primary.aggiungi');
                    for (const b of allBtns) {
                        const style = window.getComputedStyle(b);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (b.closest('.cerca_cliente')) continue;
                        if (b.closest('.inserisci_cellulare')) continue;
                        if (b.closest('.form_cliente')) continue;
                        b.click();
                        return 'fallback-aggiungi';
                    }

                    return null;
                }
            """)

            if added:
                logger.info(f"✅ 'Add appointment' clicked: {added}")
            else:
                logger.warning("⚠️ Could not find 'Add appointment' button")
                await take_screenshot(page, "10_ERROR_no_button")

            await page.wait_for_timeout(5000)
            await take_screenshot(page, "12_after_save")

            # Handle any post-save modals
            await dismiss_system_modals(page, "post-save")
            await page.wait_for_timeout(2000)

            # ═══════════════════════════════════════════════════
            # VERIFY
            # ═══════════════════════════════════════════════════
            first_name_lower = js_escape(request.customer_name.lower().split()[0])

            on_agenda = await page.evaluate("""
                () => {
                    const a = document.getElementById('pannello_agenda');
                    return a ? window.getComputedStyle(a).display !== 'none' : false;
                }
            """)

            has_error_modal = await page.evaluate("""
                () => {
                    const m = document.getElementById('modale_dialog');
                    if (!m) return false;
                    return window.getComputedStyle(m).display !== 'none';
                }
            """)

            # Check if appointment form is GONE (means it was submitted)
            form_gone = await page.evaluate("""
                () => {
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (!btn) return true;
                    return window.getComputedStyle(btn).display === 'none';
                }
            """)

            # Check if appointment block appeared on the grid
            has_appointment = await page.evaluate(f"""
                () => {{
                    const blocks = document.querySelectorAll('.appuntamento, .evento, .prenotazione, .cella.occupata');
                    for (const b of blocks) {{
                        if (b.textContent.toLowerCase().includes('{first_name_lower}')) return true;
                    }}
                    return false;
                }}
            """)

            # Check for "elaborazione" spinner (still processing)
            is_processing = await page.evaluate("""
                () => {
                    const spin = document.querySelector('.azioni .elaborazione');
                    if (!spin) return false;
                    return window.getComputedStyle(spin).display !== 'none';
                }
            """)

            logger.info(f"Verify — agenda:{on_agenda} error:{has_error_modal} form_gone:{form_gone} appointment:{has_appointment} processing:{is_processing} added:{added}")

            success = bool(added) and form_gone and not has_error_modal and not is_processing

            await take_screenshot(page, "13_final")
            await browser.close()

            logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "service": request.service,
                "date": request.preferred_date,
                "time": request.preferred_time,
                "operator": request.operator_preference,
                "appointment_visible": has_appointment,
                "form_dismissed": form_gone,
                "message": "✅ Appuntamento creato in Wegest" if success else "⚠️ Non confermato — verifica in Wegest",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

        except Exception as e:
            logger.error(f"❌ Error: {str(e)}")
            await take_screenshot(page, "ERROR")
            try:
                await browser.close()
            except:
                pass
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ Errore: {str(e)} — controlla /screenshots",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }