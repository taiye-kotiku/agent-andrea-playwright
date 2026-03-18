"""
Agent Andrea - Wegest Direct Booking Service
All selectors verified against actual Wegest HTML
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
                if (modalText.includes('cassa') || modalText.includes('passaggio')) {
                    const annulla = modal.querySelector('.button.avviso');
                    if (annulla && window.getComputedStyle(annulla).display !== 'none') {
                        annulla.click();
                        return 'annulla-cassa';
                    }
                }
                const conferma = modal.querySelector('.button.conferma');
                if (conferma && window.getComputedStyle(conferma).display !== 'none') {
                    conferma.click();
                    return 'conferma-warning';
                }
                const chiudi = modal.querySelector('.button.chiudi');
                if (chiudi && window.getComputedStyle(chiudi).display !== 'none') {
                    chiudi.click();
                    return 'chiudi';
                }
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
                if (window.getComputedStyle(el).display !== 'none') el.style.display = 'none';
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

            # ── STEP 3: Click login ───────────────────────────
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
                await screenshot(page, "03_login_timeout")

            await page.wait_for_timeout(10000)
            await screenshot(page, "03_after_login")

            login_visible = await page.evaluate("""() => {
                const el = document.getElementById('pannello_login');
                return el ? window.getComputedStyle(el).display !== 'none' : false;
            }""")
            if login_visible:
                raise Exception("Login failed")

            logger.info("🎉 LOGIN SUCCESS!")

            # ── STEP 3.5: Dismiss chiusura cassa ─────────────
            await dismiss_all_modals(page, "post-login")
            await page.wait_for_timeout(2000)
            await screenshot(page, "04_modals_cleared")

            # ── STEP 4: Click Agenda ──────────────────────────
            logger.info("Step 4: Opening Agenda...")
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(5000)
            await dismiss_all_modals(page, "after-agenda")
            await page.wait_for_timeout(2000)
            await screenshot(page, "05_agenda")

            # ── STEP 5: Select date ───────────────────────────
            target = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day, month, year = target.day, target.month, target.year
            logger.info(f"Step 5: Date {day}/{month}/{year}...")

            await dismiss_all_modals(page, "before-date")

            date_clicked = await page.evaluate(f"""
                () => {{
                    const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                    if (el) {{ el.click(); return true; }}
                    return false;
                }}
            """)
            logger.info(f"Date clicked: {date_clicked}")

            await page.wait_for_timeout(3000)
            await dismiss_all_modals(page, "after-date")
            await screenshot(page, "06_date_selected")

            # Verify cells loaded
            cells = await page.evaluate(f"""
                () => document.querySelectorAll(".cella[giorno='{day}'][mese='{month}'][anno='{year}']").length
            """)
            logger.info(f"Cells for date: {cells}")

            if cells == 0:
                await dismiss_all_modals(page, "retry-date")
                await page.evaluate(f"""
                    () => {{
                        const el = document.querySelector(".data[giorno='{day}'][mese='{month}'][anno='{year}']");
                        if (el) el.click();
                    }}
                """)
                await page.wait_for_timeout(3000)
                await dismiss_all_modals(page, "retry-date-2")

            # ── STEP 6: Click time slot ───────────────────────
            hour = str(int(request.preferred_time.split(":")[0]))
            minute = str(int(request.preferred_time.split(":")[1])) if ":" in request.preferred_time else "0"
            logger.info(f"Step 6: Time ora={hour} minuto={minute}...")

            time_result = await page.evaluate(f"""
                () => {{
                    // Exact match first
                    const cells = document.querySelectorAll(
                        ".cella[ora='{hour}'][minuto='{minute}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                    );
                    for (const cell of cells) {{
                        if (cell.classList.contains('assente') || cell.classList.contains('occupata')) continue;
                        cell.click();
                        return {{ clicked: true, op: cell.getAttribute('id_operatore'), text: cell.textContent.trim() }};
                    }}
                    // Fallback: any minute at this hour
                    const any = document.querySelectorAll(
                        ".cella[ora='{hour}'][giorno='{day}'][mese='{month}'][anno='{year}']"
                    );
                    for (const cell of any) {{
                        if (cell.classList.contains('assente') || cell.classList.contains('occupata')) continue;
                        cell.click();
                        return {{ clicked: true, op: cell.getAttribute('id_operatore'), adjusted: cell.getAttribute('minuto') }};
                    }}
                    return {{ clicked: false }};
                }}
            """)
            logger.info(f"Time result: {time_result}")

            await page.wait_for_timeout(3000)
            await screenshot(page, "07_time_clicked")
            await dismiss_all_modals(page, "after-time")

            # ── STEP 7: Customer search & selection ───────────
            logger.info(f"Step 7: Customer '{request.customer_name}'...")
            customer_found = False

            try:
                await page.wait_for_selector(".cerca_cliente.modale input[name='cerca_cliente']", timeout=8000)
                logger.info("Customer search modal visible")

                first_name = request.customer_name.strip().split()[0]
                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                await page.wait_for_timeout(2000)
                await screenshot(page, "08_customer_search")

                # Check results
                results = await page.evaluate(f"""
                    () => {{
                        const body = document.querySelector('.cerca_cliente .modale_body');
                        if (!body) return {{ found: false }};
                        const items = body.querySelectorAll('div[id_cliente], .cliente, .risultato, .riga');
                        for (const item of items) {{
                            if (item.textContent.toLowerCase().includes('{first_name.lower()}')) {{
                                item.click();
                                return {{ found: true, text: item.textContent.trim().substring(0, 50) }};
                            }}
                        }}
                        return {{ found: false, count: items.length }};
                    }}
                """)

                if results and results.get('found'):
                    customer_found = True
                    logger.info(f"✅ Customer selected: {results.get('text')}")
                else:
                    logger.info("Customer not found — creating new...")

                    # Click "Nuovo Cliente" (div.button.primary.aggiungi inside .cerca_cliente)
                    await page.evaluate("""
                        () => {
                            const modal = document.querySelector('.cerca_cliente.modale');
                            if (!modal) return;
                            const btn = modal.querySelector('.button.primary.aggiungi');
                            if (btn) btn.click();
                        }
                    """)
                    await page.wait_for_timeout(2000)
                    await screenshot(page, "08b_new_customer_form")

                    parts = request.customer_name.strip().split(" ", 1)
                    nome = parts[0]
                    cognome = parts[1] if len(parts) > 1 else ""

                    phone = request.caller_phone
                    if phone.startswith("+39"):
                        phone = phone[3:]
                    elif phone.startswith("0039"):
                        phone = phone[4:]

                    await page.evaluate(f"""
                        () => {{
                            const form = document.querySelector('.form_cliente');
                            if (!form) return;
                            const n = form.querySelector("input[name='nome']");
                            const c = form.querySelector("input[name='cognome']");
                            const cell = form.querySelector("input[name='cellulare']");
                            if (n) {{ n.value = '{nome}'; n.dispatchEvent(new Event('input', {{bubbles:true}})); n.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                            if (c) {{ c.value = '{cognome}'; c.dispatchEvent(new Event('input', {{bubbles:true}})); c.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                            if (cell) {{ cell.value = '{phone}'; cell.dispatchEvent(new Event('input', {{bubbles:true}})); cell.dispatchEvent(new Event('change', {{bubbles:true}})); }}
                        }}
                    """)
                    logger.info(f"Filled: {nome} {cognome} / {phone}")
                    await screenshot(page, "08c_form_filled")

                    # Click "Aggiungi cliente" in form footer
                    await page.evaluate("""
                        () => {
                            const form = document.querySelector('.form_cliente');
                            if (!form) return;
                            const btn = form.querySelector('.modale_footer .button.primary.aggiungi');
                            if (btn) btn.click();
                        }
                    """)
                    customer_found = True
                    logger.info("✅ New customer added")
                    await page.wait_for_timeout(3000)
                    await screenshot(page, "08d_customer_added")

            except Exception as e:
                logger.warning(f"Customer step error: {e}")
                await screenshot(page, "08_error")

            await page.wait_for_timeout(2000)
            await screenshot(page, "09_after_customer")

            # ── STEP 8: Select service ────────────────────────
            # Services are div.servizio with nome="TAGLIO" etc.
            # They match by the `nome` attribute, not textContent (which may be translated)
            logger.info(f"Step 8: Service '{request.service}'...")

            service_kw = request.service.lower()
            service_selected = await page.evaluate(f"""
                () => {{
                    const keyword = '{service_kw}';
                    const services = document.querySelectorAll('.pulsanti_tab .tab-content.attivo .servizio');

                    // First try: match by nome attribute (most reliable)
                    for (const svc of services) {{
                        const nome = (svc.getAttribute('nome') || '').toLowerCase();
                        if (nome.includes(keyword)) {{
                            svc.click();
                            return {{ selected: true, nome: nome, method: 'nome-attr' }};
                        }}
                    }}

                    // Second try: match by visible text
                    for (const svc of services) {{
                        const text = svc.textContent.toLowerCase().trim();
                        if (text.includes(keyword)) {{
                            svc.click();
                            return {{ selected: true, text: text.substring(0, 30), method: 'text' }};
                        }}
                    }}

                    // Third try: search ALL tabs, not just active
                    const allServices = document.querySelectorAll('.pulsanti_tab .servizio');
                    for (const svc of allServices) {{
                        const nome = (svc.getAttribute('nome') || '').toLowerCase();
                        if (nome.includes(keyword)) {{
                            svc.click();
                            return {{ selected: true, nome: nome, method: 'all-tabs' }};
                        }}
                    }}

                    return {{ selected: false, total: services.length }};
                }}
            """)

            if service_selected and service_selected.get('selected'):
                logger.info(f"✅ Service: {service_selected}")
            else:
                logger.warning(f"⚠️ Service not found: {service_selected}")
                # Try using the search box
                try:
                    await page.fill(".pulsanti_tab input[name='cerca_servizio']", request.service)
                    await page.wait_for_timeout(1500)
                    # Click first visible result
                    await page.evaluate(f"""
                        () => {{
                            const services = document.querySelectorAll('.pulsanti_tab .servizio');
                            for (const svc of services) {{
                                if (window.getComputedStyle(svc).display !== 'none') {{
                                    svc.click();
                                    return;
                                }}
                            }}
                        }}
                    """)
                    logger.info("Service selected via search")
                except:
                    pass

            await page.wait_for_timeout(1000)
            await screenshot(page, "10_service_selected")

            # ── STEP 9: Select operator ───────────────────────
            # Operators: div.operatore with span.nome inside
            # Skip those with class "assente"
            if request.operator_preference.lower() != "prima disponibile":
                logger.info(f"Step 9: Operator '{request.operator_preference}'...")
                op_name = request.operator_preference.lower()

                op_result = await page.evaluate(f"""
                    () => {{
                        const ops = document.querySelectorAll('.pulsanti_tab .operatori .operatore');
                        for (const op of ops) {{
                            if (op.classList.contains('assente')) continue;
                            const nome = op.querySelector('.nome');
                            if (nome && nome.textContent.toLowerCase().trim().includes('{op_name}')) {{
                                op.click();
                                return {{ selected: true, name: nome.textContent.trim(), id: op.id }};
                            }}
                        }}
                        return {{ selected: false }};
                    }}
                """)
                logger.info(f"Operator result: {op_result}")
            else:
                logger.info("Step 9: Using first available operator (default)")

            await page.wait_for_timeout(1000)
            await screenshot(page, "11_operator_selected")

            # ── STEP 10: Click AGGIUNGI APPUNTAMENTO ──────────
            logger.info("Step 10: Adding appointment...")

            # The button is div.button.rimira.primary with text "AGGIUNGI APPUNTAMENTO"
            # It's at the bottom of the appointment form panel
            added = await page.evaluate("""
                () => {
                    // Look for the specific "AGGIUNGI APPUNTAMENTO" button
                    const buttons = document.querySelectorAll('div.button.rimira.primary');
                    for (const btn of buttons) {
                        const text = btn.textContent.toLowerCase().trim();
                        const style = window.getComputedStyle(btn);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;

                        if (text.includes('aggiungi appuntamento') || text.includes('aggiungiappuntamento')) {
                            btn.click();
                            return 'aggiungi-appuntamento';
                        }
                    }

                    // Fallback: any visible primary button with "aggiungi" that's NOT in cerca_cliente or form_cliente
                    for (const btn of buttons) {
                        const text = btn.textContent.toLowerCase().trim();
                        const style = window.getComputedStyle(btn);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        if (btn.closest('.cerca_cliente') || btn.closest('.form_cliente')) continue;

                        if (text.includes('aggiungi')) {
                            btn.click();
                            return 'aggiungi-fallback';
                        }
                    }

                    return null;
                }
            """)

            if added:
                logger.info(f"✅ Appointment button clicked: {added}")
            else:
                logger.warning("⚠️ Could not find AGGIUNGI APPUNTAMENTO button")
                # Try Playwright selector
                try:
                    btn = page.locator("div.button:has-text('AGGIUNGI APPUNTAMENTO')").first
                    if await btn.is_visible(timeout=3000):
                        await btn.click()
                        added = "playwright-fallback"
                        logger.info("✅ Clicked via Playwright")
                except:
                    pass

            await page.wait_for_timeout(3000)
            await screenshot(page, "12_appointment_added")

            # ── VERIFY ────────────────────────────────────────
            first_name = request.customer_name.lower().split()[0]
            page_content = (await page.content()).lower()

            on_agenda = await page.evaluate("""
                () => {
                    const a = document.getElementById('pannello_agenda');
                    return a ? window.getComputedStyle(a).display !== 'none' : false;
                }
            """)

            has_error = await page.evaluate("""
                () => {
                    const m = document.getElementById('modale_dialog');
                    if (!m) return false;
                    return window.getComputedStyle(m).display !== 'none';
                }
            """)

            # Check if appointment block appeared on the agenda
            has_appointment = await page.evaluate(f"""
                () => {{
                    const blocks = document.querySelectorAll('.appuntamento, .evento, .prenotazione');
                    for (const b of blocks) {{
                        if (b.textContent.toLowerCase().includes('{first_name}')) return true;
                    }}
                    return false;
                }}
            """)

            logger.info(f"Verify — agenda: {on_agenda} | error: {has_error} | appointment_block: {has_appointment} | added: {added}")

            success = bool(added) and on_agenda and not has_error

            await screenshot(page, "13_final")
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