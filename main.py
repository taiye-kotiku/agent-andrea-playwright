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
            # ═══════════════════════════════════════════
            target = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day, month, year = target.day, target.month, target.year
            logger.info(f"Step 5: Date {day}/{month}/{year}...")

            await dismiss_system_modals(page, "before-date")

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            try:
                await page.click(date_selector, timeout=10000)
                logger.info(f"✅ Date clicked")
            except Exception as e:
                raise Exception(f"Date {day}/{month}/{year} not visible on calendar")

            logger.info("Waiting for grid...")
            try:
                await page.wait_for_function(
                    f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
                    timeout=15000
                )
            except:
                await page.click(date_selector, timeout=5000)
                await page.wait_for_timeout(5000)

            await page.wait_for_timeout(2000)
            await dismiss_system_modals(page, "after-date")
            await snap(page, "05_date")

            # ═══════════════════════════════════════════
            # STEP 6: Click time slot (15-min grid)
            # ═══════════════════════════════════════════
            raw_hour = int(request.preferred_time.split(":")[0])
            raw_minute = int(request.preferred_time.split(":")[1]) if ":" in request.preferred_time else 0
            rounded_minute = (raw_minute // 15) * 15
            hour = str(raw_hour)
            minute = str(rounded_minute)
            logger.info(f"Step 6: Time {hour}:{minute}...")

            base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{hour}']"
            exact_sel = f"{base}[minuto='{minute}']:not(.assente):not(.occupata)"
            hour_sel = f"{base}:not(.assente):not(.occupata)"

            time_clicked = False
            actual_time = f"{hour}:{minute}"

            # Try exact
            try:
                count = await page.evaluate(f"() => document.querySelectorAll(\"{exact_sel}\").length")
                if count > 0:
                    await page.click(exact_sel, timeout=5000)
                    time_clicked = True
                    logger.info(f"✅ Exact slot: {hour}:{minute}")
            except:
                pass

            # Try any minute at this hour
            if not time_clicked:
                try:
                    count = await page.evaluate(f"() => document.querySelectorAll(\"{hour_sel}\").length")
                    if count > 0:
                        actual_min = await page.evaluate(f"() => document.querySelector(\"{hour_sel}\")?.getAttribute('minuto')")
                        await page.click(hour_sel, timeout=5000)
                        actual_time = f"{hour}:{actual_min or '0'}"
                        time_clicked = True
                        logger.info(f"✅ Hour fallback: {actual_time}")
                except:
                    pass

            # Try next hours
            if not time_clicked:
                for try_hour in range(raw_hour + 1, 20):
                    try_sel = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{try_hour}']:not(.assente):not(.occupata)"
                    try:
                        count = await page.evaluate(f"() => document.querySelectorAll(\"{try_sel}\").length")
                        if count > 0:
                            actual_min = await page.evaluate(f"() => document.querySelector(\"{try_sel}\")?.getAttribute('minuto')")
                            await page.click(try_sel, timeout=5000)
                            actual_time = f"{try_hour}:{actual_min or '0'}"
                            time_clicked = True
                            logger.info(f"✅ Next available: {actual_time}")
                            break
                    except:
                        continue

            if not time_clicked:
                raise Exception(f"No available slot on {day}/{month}/{year}")

            await page.wait_for_timeout(3000)
            await snap(page, "06_time")

            # ═══════════════════════════════════════════
            # STEP 7: Customer search & selection
            # Search: full name → first → last → phone
            # Match: requires BOTH first AND last name
            # No match: create new customer
            # ═══════════════════════════════════════════
            logger.info(f"Step 7: Customer '{request.customer_name}'...")
            customer_found = False

            name_parts = request.customer_name.strip().split()
            first_name = name_parts[0] if name_parts else ""
            last_name = name_parts[-1] if len(name_parts) > 1 else ""
            first_safe = js_escape(first_name.lower())
            last_safe = js_escape(last_name.lower())

            search_phone = request.caller_phone
            if search_phone.startswith("+39"):
                search_phone = search_phone[3:]
            elif search_phone.startswith("0039"):
                search_phone = search_phone[4:]
            phone_safe = js_escape(search_phone)

            try:
                await page.wait_for_selector(
                    ".cerca_cliente.modale input[name='cerca_cliente']",
                    timeout=10000
                )
                logger.info("✅ Customer search modal open")

                # Match logic: BOTH first AND last name required
                match_js = f"""
                    () => {{
                        const first = '{first_safe}';
                        const last = '{last_safe}';
                        const rows = document.querySelectorAll(
                            '.tabella_clienti tbody tr[id]'
                        );
                        const results = [];
                        for (const row of rows) {{
                            const p = row.querySelector('p.cliente');
                            if (!p) continue;
                            const text = p.textContent.toLowerCase().trim();
                            results.push({{
                                id: row.id,
                                name: p.textContent.trim(),
                                hasFirst: text.includes(first),
                                hasLast: last ? text.includes(last) : false
                            }});
                        }}
                        if (last) {{
                            for (const r of results) {{
                                if (r.hasFirst && r.hasLast) {{
                                    document.getElementById(r.id).click();
                                    return {{ found: true, ...r, method: 'both_names' }};
                                }}
                            }}
                        }}
                        if (!last) {{
                            for (const r of results) {{
                                if (r.hasFirst) {{
                                    document.getElementById(r.id).click();
                                    return {{ found: true, ...r, method: 'first_only' }};
                                }}
                            }}
                        }}
                        return {{
                            found: false,
                            count: results.length,
                            candidates: results.map(r => r.name).slice(0, 5)
                        }};
                    }}
                """

                # Search 1: Full name
                logger.info(f"  Search 1: '{request.customer_name}'")
                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", request.customer_name)
                await page.wait_for_timeout(3000)
                await snap(page, "07a_full")
                match = await page.evaluate(match_js)
                if match and match.get('found'):
                    customer_found = True
                    logger.info(f"✅ Match: {match}")

                # Search 2: First name
                if not customer_found:
                    logger.info(f"  Search 2: '{first_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07b_first")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                # Search 3: Last name
                if not customer_found and last_name:
                    logger.info(f"  Search 3: '{last_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", last_name)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07c_last")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                # Search 4: Phone
                if not customer_found and search_phone:
                    logger.info(f"  Search 4: phone '{search_phone}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", search_phone)
                    await page.wait_for_timeout(3000)
                    await snap(page, "07d_phone")
                    match = await page.evaluate("""
                        () => {
                            const rows = document.querySelectorAll('.tabella_clienti tbody tr[id]');
                            if (rows.length === 1) {
                                rows[0].click();
                                const p = rows[0].querySelector('p.cliente');
                                return { found: true, id: rows[0].id, name: p ? p.textContent.trim() : '?', method: 'phone' };
                            }
                            return { found: false, count: rows.length };
                        }
                    """)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Phone: {match}")

                # ── CREATE NEW CUSTOMER ────────────────────
                if not customer_found:
                    logger.info("  ❌ Not found → creating new customer")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", "")
                    await page.wait_for_timeout(500)

                    # Click "New Customer" in search modal
                    await page.click(".cerca_cliente .pulsanti .button.rimira.primary.aggiungi")
                    await page.wait_for_timeout(3000)
                    await snap(page, "07e_new_form")

                    # Fill Nome
                    await page.evaluate(f"""
                        () => {{
                            const inputs = document.querySelectorAll('input[name="nome"]');
                            for (const inp of inputs) {{
                                if (inp.offsetParent === null) continue;
                                inp.value = '{js_escape(first_name)}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                            return false;
                        }}
                    """)

                    # Fill Cognome
                    await page.evaluate(f"""
                        () => {{
                            const inputs = document.querySelectorAll('input[name="cognome"]');
                            for (const inp of inputs) {{
                                if (inp.offsetParent === null) continue;
                                inp.value = '{js_escape(last_name)}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                            return false;
                        }}
                    """)

                    # Fill Cellulare (skip inserisci_cellulare modal)
                    await page.evaluate(f"""
                        () => {{
                            const inputs = document.querySelectorAll('input[name="cellulare"]');
                            for (const inp of inputs) {{
                                if (inp.offsetParent === null) continue;
                                if (inp.closest('.inserisci_cellulare')) continue;
                                inp.value = '{phone_safe}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                                return true;
                            }}
                            return false;
                        }}
                    """)

                    logger.info(f"  Filled: {first_name} {last_name} / {search_phone}")
                    await snap(page, "07f_filled")

                    # Click "Add customer" in modale_footer
                    # NOT .azioni (that's "Add appointment")
                    # Click "Add customer" in modale_footer
                    # Use Playwright click — more reliable than JS evaluate
                    try:
                        await page.click(
                            ".modale_footer .button.rimira.primary.aggiungi",
                            timeout=5000
                        )
                        customer_found = True
                        logger.info("✅ New customer created (playwright click)")
                    except Exception as click_err:
                        logger.warning(f"Playwright click failed: {click_err}")
                        # JS fallback with better visibility check
                        saved = await page.evaluate("""
                            () => {
                                const btns = document.querySelectorAll(
                                    '.modale_footer .button.rimira.primary.aggiungi'
                                );
                                for (const btn of btns) {
                                    const s = getComputedStyle(btn);
                                    if (s.display === 'none' || s.visibility === 'hidden')
                                        continue;
                                    if (s.pointerEvents === 'none') continue;
                                    btn.click();
                                    return true;
                                }
                                // Broader: any visible primary aggiungi NOT in .azioni
                                const all = document.querySelectorAll(
                                    '.button.rimira.primary.aggiungi'
                                );
                                for (const btn of all) {
                                    if (btn.closest('.azioni')) continue;
                                    if (btn.closest('.cerca_cliente')) continue;
                                    const s = getComputedStyle(btn);
                                    if (s.display === 'none') continue;
                                    btn.click();
                                    return true;
                                }
                                return false;
                            }
                        """)
                        if saved:
                            customer_found = True
                            logger.info("✅ New customer created (JS fallback)")
                        else:
                            logger.warning("⚠️ Could not click Add customer!")

                    await page.wait_for_timeout(4000)
                    await snap(page, "07g_saved")
                    await dismiss_system_modals(page, "after-new-customer")

            except Exception as e:
                logger.warning(f"Customer error: {e}")
                await snap(page, "07_ERROR")

            await page.wait_for_timeout(2000)

            # ═══════════════════════════════════════════
            # STEP 7.5: Phone number modal
            # ═══════════════════════════════════════════
                    # Click "Add customer" in modale_footer
                    # Use Playwright click — more reliable than JS evaluate
            try:
                await page.click(
                    ".modale_footer .button.rimira.primary.aggiungi",
                    timeout=5000
                        )
                customer_found = True
                logger.info("✅ New customer created (playwright click)")
            except Exception as click_err:
                logger.warning(f"Playwright click failed: {click_err}")
                        # JS fallback with better visibility check
                saved = await page.evaluate("""
                            () => {
                                const btns = document.querySelectorAll(
                                    '.modale_footer .button.rimira.primary.aggiungi'
                                );
                                for (const btn of btns) {
                                    const s = getComputedStyle(btn);
                                    if (s.display === 'none' || s.visibility === 'hidden')
                                        continue;
                                    if (s.pointerEvents === 'none') continue;
                                    btn.click();
                                    return true;
                                }
                                // Broader: any visible primary aggiungi NOT in .azioni
                                const all = document.querySelectorAll(
                                    '.button.rimira.primary.aggiungi'
                                );
                                for (const btn of all) {
                                    if (btn.closest('.azioni')) continue;
                                    if (btn.closest('.cerca_cliente')) continue;
                                    const s = getComputedStyle(btn);
                                    if (s.display === 'none') continue;
                                    btn.click();
                                    return true;
                                }
                                return false;
                            }
                        """)
                if saved:
                    customer_found = True
                    logger.info("✅ New customer created (JS fallback)")
                else:
                    logger.warning("⚠️ Could not click Add customer!")

            await page.wait_for_timeout(4000)
            await snap(page, "07g_saved")
            await dismiss_system_modals(page, "after-new-customer")

            # ═══════════════════════════════════════════
            # STEP 8: Select service
            # ═══════════════════════════════════════════
            logger.info(f"Step 8: Service '{request.service}'...")
            svc_kw = js_escape(request.service.lower())

            svc_result = await page.evaluate(f"""
                () => {{
                    const kw = '{svc_kw}';
                    const all = document.querySelectorAll('.pulsanti_tab .servizio');
                    for (const s of all) {{
                        if ((s.getAttribute('nome') || '').toLowerCase() === kw) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'exact' }};
                        }}
                    }}
                    for (const s of all) {{
                        if ((s.getAttribute('nome') || '').toLowerCase().startsWith(kw)) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'starts' }};
                        }}
                    }}
                    for (const s of all) {{
                        if ((s.getAttribute('nome') || '').toLowerCase().includes(kw)) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'contains' }};
                        }}
                    }}
                    for (const s of all) {{
                        const n = (s.getAttribute('nome') || '').toLowerCase();
                        if (n.length > 2 && kw.includes(n)) {{
                            s.click(); return {{ ok:1, nome: s.getAttribute('nome'), id: s.id, m:'reverse' }};
                        }}
                    }}
                    const avail = [];
                    all.forEach(s => avail.push(s.getAttribute('nome')));
                    return {{ ok:0, available: avail }};
                }}
            """)

            if svc_result and svc_result.get('ok'):
                logger.info(f"✅ Service: {svc_result}")
            else:
                logger.warning(f"⚠️ Service not found. Trying search... {svc_result}")
                try:
                    await page.fill(".pulsanti_tab input[name='cerca_servizio']", request.service)
                    await page.wait_for_timeout(2000)
                    await page.evaluate("""
                        () => {
                            const svcs = document.querySelectorAll('.pulsanti_tab .servizio');
                            for (const s of svcs) {
                                if (getComputedStyle(s).display !== 'none') { s.click(); return; }
                            }
                        }
                    """)
                except:
                    pass

            await page.wait_for_timeout(1500)
            await snap(page, "09_service")

            # ═══════════════════════════════════════════
            # STEP 9: Select operator
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
                    if (btn && btn.offsetParent !== null) {
                        btn.click();
                        return 'azioni-aggiungi';
                    }
                    return null;
                }
            """)

            if added:
                logger.info(f"✅ Add clicked: {added}")
            else:
                logger.warning("⚠️ Add button not found — Playwright fallback")
                try:
                    await page.click(".azioni .button.rimira.primary.aggiungi", timeout=5000)
                    added = "playwright"
                except:
                    await snap(page, "10_ERROR")

            await page.wait_for_timeout(5000)
            await snap(page, "11_saved")
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

            success = bool(added) and form_gone and not has_error and not is_processing

            await snap(page, "12_final")
            await browser.close()

            logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "customer_found_in_db": customer_found,
                "service": request.service,
                "date": request.preferred_date,
                "time": actual_time,
                "time_requested": request.preferred_time,
                "operator": request.operator_preference,
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