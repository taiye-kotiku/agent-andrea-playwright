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


async def snap(page, name: str):
    try:
        data = await page.screenshot(type="png", full_page=True)
        screenshots[name] = base64.b64encode(data).decode()
        logger.info(f"📸 {name}")
    except Exception as e:
        logger.warning(f"Screenshot failed ({name}): {e}")


async def dismiss_system_modals(page, label=""):
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
        logger.info(f"  ⚠️ System modal (attempt {attempt + 1})")
        clicked = await page.evaluate("""
            () => {
                const modal = document.getElementById('modale_dialog');
                if (!modal) return null;
                const testo1 = modal.querySelector('.testo1');
                const txt = testo1 ? testo1.textContent.toLowerCase() : '';
                if (txt.includes('cassa') || txt.includes('passaggio')) {
                    const b = modal.querySelector('.button.avviso');
                    if (b) { b.click(); return 'annulla-cassa'; }
                }
                const c = modal.querySelector('.button.conferma');
                if (c && getComputedStyle(c).display !== 'none') { c.click(); return 'conferma'; }
                const x = modal.querySelector('.button.chiudi');
                if (x && getComputedStyle(x).display !== 'none') { x.click(); return 'chiudi'; }
                const a = modal.querySelector('.button.avviso');
                if (a && getComputedStyle(a).display !== 'none') { a.click(); return 'avviso'; }
                modal.style.display = 'none';
                return 'force-hidden';
            }
        """)
        logger.info(f"  → {clicked}")
        await page.wait_for_timeout(2500)
    await page.evaluate("""
        () => document.querySelectorAll('.modale_overlay, .overlay_modale, .overlay').forEach(el => {
            if (getComputedStyle(el).display !== 'none') el.style.display = 'none';
        })
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
            # ═══════════════════════════════════════════
            # STEP 1: Login page
            # ═══════════════════════════════════════════
            logger.info("Step 1: Loading login...")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            await snap(page, "01_login")

            # ═══════════════════════════════════════════
            # STEP 2: Credentials
            # ═══════════════════════════════════════════
            logger.info("Step 2: Credentials...")
            await page.fill("input[name='username']", WEGEST_USER)
            await page.fill("input[name='password']", WEGEST_PASSWORD)
            await page.evaluate("document.querySelector('input[name=\"codice\"]').value = '1'")
            await snap(page, "02_creds")

            # ═══════════════════════════════════════════
            # STEP 3: Login click
            # ═══════════════════════════════════════════
            logger.info("Step 3: Clicking login...")
            await page.click("div.button")
            try:
                await page.wait_for_function(
                    """() => {
                        const lp = document.getElementById('pannello_login');
                        return lp && getComputedStyle(lp).display === 'none';
                    }""",
                    timeout=60000
                )
            except:
                pass
            await page.wait_for_timeout(30000)
            await snap(page, "03_logged_in")

            login_visible = await page.evaluate("""() => {
                const el = document.getElementById('pannello_login');
                return el ? getComputedStyle(el).display !== 'none' : false;
            }""")
            if login_visible:
                raise Exception("Login failed — panel still visible")
            logger.info("🎉 LOGIN OK")

            # ═══════════════════════════════════════════
            # STEP 3.5: Dismiss post-login modals
            # ═══════════════════════════════════════════
            await dismiss_system_modals(page, "post-login")
            await page.wait_for_timeout(2000)

            # ═══════════════════════════════════════════
            # STEP 4: Open Agenda
            # ═══════════════════════════════════════════
            logger.info("Step 4: Agenda...")
            await page.click("[pannello='pannello_agenda']")
            await page.wait_for_timeout(5000)
            await dismiss_system_modals(page, "after-agenda")
            await page.wait_for_timeout(2000)
            await snap(page, "04_agenda")

            # ═══════════════════════════════════════════
            # STEP 5: Click date
            # Verified: .data[giorno='D'][mese='M'][anno='Y']
            # ═══════════════════════════════════════════
            target = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day, month, year = target.day, target.month, target.year
            logger.info(f"Step 5: Date {day}/{month}/{year}...")

            await dismiss_system_modals(page, "before-date")

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            try:
                await page.click(date_selector, timeout=10000)
                logger.info(f"✅ Date clicked: {date_selector}")
            except Exception as e:
                logger.error(f"❌ Date not found: {date_selector} — {e}")
                await snap(page, "05_ERROR_date")
                raise Exception(f"Date {day}/{month}/{year} not visible on calendar")

            # Wait for grid to load with cells for this date
            logger.info("Waiting for grid to load...")
            try:
                await page.wait_for_function(
                    f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
                    timeout=15000
                )
                logger.info("✅ Grid loaded")
            except:
                logger.warning("Grid load timeout — retrying date click...")
                await page.click(date_selector, timeout=5000)
                await page.wait_for_timeout(5000)

            await page.wait_for_timeout(2000)
            await dismiss_system_modals(page, "after-date")
            await snap(page, "05_date_selected")

            # Count cells for debugging
            cell_count = await page.evaluate(f"""
                () => document.querySelectorAll(".cella[giorno='{day}'][mese='{month}'][anno='{year}']").length
            """)
            logger.info(f"Cells for {day}/{month}/{year}: {cell_count}")

            # ═══════════════════════════════════════════
            # STEP 6: Click time slot
            # Verified: .cella[ora='H'][minuto='M'][giorno][mese][anno]
            # Grid uses 15-min intervals: minuto = 0, 15, 30, 45
            # Cells with class 'assente' = operator absent
            # Using Playwright native click (NOT JS evaluate)
            # ═══════════════════════════════════════════
            raw_hour = int(request.preferred_time.split(":")[0])
            raw_minute = int(request.preferred_time.split(":")[1]) if ":" in request.preferred_time else 0
            # Round to nearest 15-min slot
            rounded_minute = (raw_minute // 15) * 15
            hour = str(raw_hour)
            minute = str(rounded_minute)
            logger.info(f"Step 6: Time {hour}:{minute} (raw: {request.preferred_time})...")

            # Build selector: exact time, not absent, not occupied
            base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{hour}']"
            exact_sel = f"{base}[minuto='{minute}']:not(.assente):not(.occupata)"
            hour_sel = f"{base}:not(.assente):not(.occupata)"

            time_clicked = False
            actual_time = f"{hour}:{minute}"

            # Try 1: Exact minute match
            try:
                count = await page.evaluate(f"() => document.querySelectorAll(\"{exact_sel}\").length")
                logger.info(f"Exact match cells: {count} ({exact_sel})")
                if count > 0:
                    await page.click(exact_sel, timeout=5000)
                    time_clicked = True
                    logger.info(f"✅ Clicked exact slot: {hour}:{minute}")
            except Exception as e:
                logger.warning(f"Exact click failed: {e}")

            # Try 2: Any minute at this hour
            if not time_clicked:
                try:
                    count = await page.evaluate(f"() => document.querySelectorAll(\"{hour_sel}\").length")
                    logger.info(f"Hour fallback cells: {count} ({hour_sel})")
                    if count > 0:
                        # Get the actual minute we'll click
                        actual_min = await page.evaluate(f"""
                            () => {{
                                const cell = document.querySelector("{hour_sel}");
                                return cell ? cell.getAttribute('minuto') : null;
                            }}
                        """)
                        await page.click(hour_sel, timeout=5000)
                        actual_time = f"{hour}:{actual_min or '0'}"
                        time_clicked = True
                        logger.info(f"✅ Clicked hour fallback: {actual_time}")
                except Exception as e:
                    logger.warning(f"Hour fallback failed: {e}")

            # Try 3: Next available hour
            if not time_clicked:
                logger.warning(f"No slot at hour {hour}, trying next hours...")
                for try_hour in range(raw_hour + 1, 20):
                    try_sel = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{try_hour}']:not(.assente):not(.occupata)"
                    try:
                        count = await page.evaluate(f"() => document.querySelectorAll(\"{try_sel}\").length")
                        if count > 0:
                            actual_min = await page.evaluate(f"""
                                () => {{
                                    const cell = document.querySelector("{try_sel}");
                                    return cell ? cell.getAttribute('minuto') : '0';
                                }}
                            """)
                            await page.click(try_sel, timeout=5000)
                            actual_time = f"{try_hour}:{actual_min}"
                            time_clicked = True
                            logger.info(f"✅ Clicked next available: {actual_time}")
                            break
                    except:
                        continue

            if not time_clicked:
                await snap(page, "06_ERROR_no_slot")
                raise Exception(f"No available time slot on {day}/{month}/{year}")

            await page.wait_for_timeout(3000)
            await snap(page, "06_time_clicked")

            # ═══════════════════════════════════════════
            # STEP 7: Customer search & selection
            # Verified: .cerca_cliente.modale opens
            # Search: input[name='cerca_cliente']
            # Results: .tabella_clienti tbody tr[id] → p.cliente
            # New: .cerca_cliente .pulsanti .button.rimira.primary.aggiungi
            # ═══════════════════════════════════════════
            logger.info(f"Step 7: Customer '{request.customer_name}'...")
            customer_found = False

            try:
                await page.wait_for_selector(".cerca_cliente.modale input[name='cerca_cliente']", timeout=10000)
                logger.info("✅ Customer search modal open")

                first_name = request.customer_name.strip().split()[0]
                first_safe = js_escape(first_name)
                full_safe = js_escape(request.customer_name)

                # Type into search
                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                await page.wait_for_timeout(3000)
                await snap(page, "07_customer_search")

                # Check for results in the table
                results = await page.evaluate(f"""
                    () => {{
                        const kw = '{first_safe}'.toLowerCase();
                        const full = '{full_safe}'.toLowerCase();
                        const rows = document.querySelectorAll('.tabella_clienti tbody tr[id]');

                        // Full name match
                        for (const row of rows) {{
                            const p = row.querySelector('p.cliente');
                            if (p && p.textContent.toLowerCase().includes(full)) {{
                                row.click();
                                return {{ found: true, id: row.id, name: p.textContent.trim(), m: 'full' }};
                            }}
                        }}
                        // First name match
                        for (const row of rows) {{
                            const p = row.querySelector('p.cliente');
                            if (p && p.textContent.toLowerCase().includes(kw)) {{
                                row.click();
                                return {{ found: true, id: row.id, name: p.textContent.trim(), m: 'first' }};
                            }}
                        }}
                        // Single result
                        if (rows.length === 1) {{
                            rows[0].click();
                            const p = rows[0].querySelector('p.cliente');
                            return {{ found: true, id: rows[0].id, name: p ? p.textContent.trim() : '?', m: 'only' }};
                        }}
                        return {{ found: false, count: rows.length }};
                    }}
                """)

                if results and results.get('found'):
                    customer_found = True
                    logger.info(f"✅ Customer: {results}")
                else:
                    logger.info(f"Customer not in DB ({results}). Creating...")
                    await page.click(".cerca_cliente .pulsanti .button.rimira.primary.aggiungi")
                    await page.wait_for_timeout(2000)
                    await snap(page, "07b_new_form")

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
                            const set = (n, v) => {{
                                const inputs = document.querySelectorAll('input[name="' + n + '"]');
                                for (const i of inputs) {{
                                    if (getComputedStyle(i).display === 'none') continue;
                                    if (i.closest('.cerca_cliente') && i.name === 'cerca_cliente') continue;
                                    i.value = v;
                                    i.dispatchEvent(new Event('input', {{bubbles:true}}));
                                    i.dispatchEvent(new Event('change', {{bubbles:true}}));
                                    return;
                                }}
                            }};
                            set('nome', '{nome}');
                            set('cognome', '{cognome}');
                            set('cellulare', '{phone_safe}');
                        }}
                    """)
                    await snap(page, "07c_filled")

                    # Click save (avoid clicking the appointment .azioni button)
                    await page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('.button.rimira.primary');
                            for (const b of btns) {
                                if (getComputedStyle(b).display === 'none') continue;
                                if (b.closest('.azioni')) continue;
                                const t = b.textContent.toLowerCase();
                                if (t.includes('salva') || t.includes('conferma') || t.includes('aggiungi')) {
                                    b.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    customer_found = True
                    await page.wait_for_timeout(3000)
                    await snap(page, "07d_created")

            except Exception as e:
                logger.warning(f"Customer error: {e}")
                await snap(page, "07_ERROR")

            await page.wait_for_timeout(2000)

            # ═══════════════════════════════════════════
            # STEP 7.5: Phone number modal
            # Verified: .modale.card.inserisci_cellulare
            # Input: .inserisci_cellulare input[name='cellulare']
            # Confirm: .inserisci_cellulare .button.rimira.primary.conferma
            # ═══════════════════════════════════════════
            phone_modal = await page.evaluate("""
                () => {
                    const m = document.querySelector('.modale.card.inserisci_cellulare');
                    return m ? getComputedStyle(m).display !== 'none' : false;
                }
            """)
            if phone_modal:
                logger.info("📱 Phone modal — filling...")
                phone = request.caller_phone
                if phone.startswith("+39"):
                    phone = phone[3:]
                elif phone.startswith("0039"):
                    phone = phone[4:]
                await page.fill(".inserisci_cellulare input[name='cellulare']", phone)
                await page.wait_for_timeout(500)
                await page.click(".inserisci_cellulare .button.rimira.primary.conferma")
                logger.info(f"✅ Phone: {phone}")
                await page.wait_for_timeout(2000)

            await snap(page, "08_form_ready")

            # ═══════════════════════════════════════════
            # STEP 8: Select service
            # Verified: .pulsanti_tab .servizio
            #   nome attr = Italian name (TAGLIO, COLORE, etc.)
            #   div.nome = visible label (may be translated)
            # ═══════════════════════════════════════════
            logger.info(f"Step 8: Service '{request.service}'...")
            svc_kw = js_escape(request.service.lower())

            svc_result = await page.evaluate(f"""
                () => {{
                    const kw = '{svc_kw}';
                    const all = document.querySelectorAll('.pulsanti_tab .servizio');

                    // 1. Exact nome attr
                    for (const s of all) {{
                        if ((s.getAttribute('nome') || '').toLowerCase() === kw) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'exact' }};
                        }}
                    }}
                    // 2. nome starts with
                    for (const s of all) {{
                        if ((s.getAttribute('nome') || '').toLowerCase().startsWith(kw)) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'starts' }};
                        }}
                    }}
                    // 3. nome contains
                    for (const s of all) {{
                        if ((s.getAttribute('nome') || '').toLowerCase().includes(kw)) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'contains' }};
                        }}
                    }}
                    // 4. kw contains nome
                    for (const s of all) {{
                        const n = (s.getAttribute('nome') || '').toLowerCase();
                        if (n.length > 2 && kw.includes(n)) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'reverse' }};
                        }}
                    }}
                    // List available
                    const avail = [];
                    all.forEach(s => avail.push(s.getAttribute('nome')));
                    return {{ ok:0, available: avail }};
                }}
            """)

            if svc_result and svc_result.get('ok'):
                logger.info(f"✅ Service: {svc_result}")
            else:
                logger.warning(f"⚠️ Service not found by nome. Trying search... {svc_result}")
                try:
                    await page.fill(".pulsanti_tab input[name='cerca_servizio']", request.service)
                    await page.wait_for_timeout(2000)
                    await page.evaluate("""
                        () => {
                            const svcs = document.querySelectorAll('.pulsanti_tab .servizio');
                            for (const s of svcs) {
                                if (getComputedStyle(s).display !== 'none') {
                                    s.click(); return;
                                }
                            }
                        }
                    """)
                except:
                    pass

            await page.wait_for_timeout(1500)
            await snap(page, "09_service")

            # ═══════════════════════════════════════════
            # STEP 9: Select operator
            # Verified: .pulsanti_tab .operatori .operatore
            #   span.nome = operator name
            #   .assente = absent that day
            # ═══════════════════════════════════════════
            if request.operator_preference.lower() != "prima disponibile":
                op_safe = js_escape(request.operator_preference.lower())
                logger.info(f"Step 9: Operator '{request.operator_preference}'...")

                op_result = await page.evaluate(f"""
                    () => {{
                        const kw = '{op_safe}';
                        const ops = document.querySelectorAll('.pulsanti_tab .operatori .operatore');
                        for (const op of ops) {{
                            if (op.classList.contains('assente')) continue;
                            const n = op.querySelector('span.nome');
                            if (n && n.textContent.toLowerCase().trim().includes(kw)) {{
                                op.click();
                                return {{ ok:1, name: n.textContent.trim(), id: op.id }};
                            }}
                        }}
                        const avail = [];
                        ops.forEach(o => {{
                            const n = o.querySelector('span.nome');
                            avail.push({{ name: n?.textContent.trim(), id: o.id, absent: o.classList.contains('assente') }});
                        }});
                        return {{ ok:0, available: avail }};
                    }}
                """)
                logger.info(f"Operator: {op_result}")
            else:
                logger.info("Step 9: Default operator (prima disponibile)")

            await page.wait_for_timeout(1000)
            await snap(page, "10_operator")

            # ═══════════════════════════════════════════
            # STEP 10: Click "Add appointment"
            # Verified: .azioni .button.rimira.primary.aggiungi
            # ═══════════════════════════════════════════
            logger.info("Step 10: Adding appointment...")

            added = await page.evaluate("""
                () => {
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (btn && getComputedStyle(btn).display !== 'none') {
                        btn.click();
                        return 'azioni-aggiungi';
                    }
                    // Fallback
                    const all = document.querySelectorAll('.button.rimira.primary.aggiungi');
                    for (const b of all) {
                        if (getComputedStyle(b).display === 'none') continue;
                        if (b.closest('.cerca_cliente')) continue;
                        if (b.closest('.inserisci_cellulare')) continue;
                        b.click();
                        return 'fallback';
                    }
                    return null;
                }
            """)

            if added:
                logger.info(f"✅ Add clicked: {added}")
            else:
                logger.warning("⚠️ Add button not found — trying Playwright click")
                try:
                    await page.click(".azioni .button.rimira.primary.aggiungi", timeout=5000)
                    added = "playwright"
                    logger.info("✅ Playwright click worked")
                except:
                    await snap(page, "10_ERROR_no_button")

            await page.wait_for_timeout(5000)
            await snap(page, "11_saved")

            # Handle post-save modals
            await dismiss_system_modals(page, "post-save")
            await page.wait_for_timeout(2000)

            # ═══════════════════════════════════════════
            # VERIFY
            # ═══════════════════════════════════════════
            first_lower = js_escape(request.customer_name.lower().split()[0])

            on_agenda = await page.evaluate("""
                () => {
                    const a = document.getElementById('pannello_agenda');
                    return a ? getComputedStyle(a).display !== 'none' : false;
                }
            """)

            has_error = await page.evaluate("""
                () => {
                    const m = document.getElementById('modale_dialog');
                    return m ? getComputedStyle(m).display !== 'none' : false;
                }
            """)

            form_gone = await page.evaluate("""
                () => {
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (!btn) return true;
                    return getComputedStyle(btn).display === 'none';
                }
            """)

            is_processing = await page.evaluate("""
                () => {
                    const s = document.querySelector('.azioni .elaborazione');
                    return s ? getComputedStyle(s).display !== 'none' : false;
                }
            """)

            has_appt = await page.evaluate(f"""
                () => {{
                    const blocks = document.querySelectorAll('.appuntamento, .evento, .prenotazione');
                    for (const b of blocks) {{
                        if (b.textContent.toLowerCase().includes('{first_lower}')) return true;
                    }}
                    return false;
                }}
            """)

            logger.info(f"Verify — agenda:{on_agenda} error:{has_error} form_gone:{form_gone} processing:{is_processing} appt:{has_appt} added:{added}")

            success = bool(added) and form_gone and not has_error and not is_processing

            await snap(page, "12_final")
            await browser.close()

            logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "service": request.service,
                "date": request.preferred_date,
                "time": actual_time,
                "time_requested": request.preferred_time,
                "operator": request.operator_preference,
                "appointment_visible": has_appt,
                "form_dismissed": form_gone,
                "message": "✅ Appuntamento creato" if success else "⚠️ Non confermato — verifica Wegest",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

        except Exception as e:
            logger.error(f"❌ {e}")
            await snap(page, "ERROR")
            try:
                await browser.close()
            except:
                pass
            return {
                "success": False,
                "error": str(e),
                "message": f"❌ {e}",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }