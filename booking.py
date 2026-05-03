"""
Adaptive Booking Engine for Agent Andrea
State machine: detects current page state, handles modals before they cause errors,
and advances booking step-by-step as context arrives (live receptionist behavior).
"""

import config
from config import logger, API_SECRET, screenshots, html_dumps, BookingState
from session_manager import (
    get_live_session_for_conversation,
    adaptive_modal_scan,
    dismiss_system_modals,
    snap
)
from utils import js_escape, normalize_requested_services
from catalog import scrape_day_availability_from_page
from datetime import datetime
from typing import Any, Optional, Dict
import asyncio


# ─── Page State Detection ─────────────────────────────────────────────

async def detect_page_state(page) -> Dict[str, Any]:
    """Scan the page and figure out what booking phase we're in."""
    info = await page.evaluate("""
        () => {
            const loginVisible = !!document.getElementById('pannello_login') &&
                getComputedStyle(document.getElementById('pannello_login')).display !== 'none';
            const agendaVisible = !!document.querySelector("[pannello='pannello_agenda']");
            const hasMenu = !!document.getElementById('menu');

            // Date grid
            const dateGrid = document.querySelector('.griglia_calendario, .celle');
            const selectedDate = document.querySelector('.data.selezionata, .cella.selezionata');
            let selectedDateInfo = null;
            if (selectedDate) {
                selectedDateInfo = {
                    giorno: selectedDate.getAttribute('giorno'),
                    mese: selectedDate.getAttribute('mese'),
                    anno: selectedDate.getAttribute('anno')
                };
            }

            // Time cells
            const timeCells = document.querySelectorAll('.cella[ora]:not(.assente):not(.occupata)');

            // Customer search modal
            const customerSearchVisible = !!document.querySelector('.cerca_cliente.modale') &&
                getComputedStyle(document.querySelector('.cerca_cliente.modale')).display !== 'none';

            // Phone modal
            const phoneModalVisible = !!document.querySelector('.modale.card.inserisci_cellulare') &&
                getComputedStyle(document.querySelector('.modale.card.inserisci_cellulare')).display !== 'none';

            // Customer form (new customer)
            const customerFormVisible = !!document.querySelector('.form_cliente') &&
                getComputedStyle(document.querySelector('.form_cliente')).display !== 'none';

            // Selected customer info
            let selectedCustomer = null;
            const clienteField = document.querySelector('.appuntamento .cliente_selezionato, .form_cliente .cliente_selezionato');
            if (clienteField) {
                selectedCustomer = clienteField.textContent.trim();
            }
            // Also check for customer name in the form fields
            const nomeInput = document.querySelector('.form_cliente input[name="nome"]');
            const cognomeInput = document.querySelector('.form_cliente input[name="cognome"]');
            if (nomeInput && nomeInput.value) {
                selectedCustomer = `${nomeInput.value} ${cognomeInput?.value || ''}`.trim();
            }

            // Services section
            const servicesSection = document.querySelector('.servizi_selezionati');
            const selectedServiceRows = servicesSection ?
                servicesSection.querySelectorAll('.riga_servizio').length : 0;

            // Service buttons visible
            const serviceButtonsVisible = !!document.querySelector('.pulsanti_tab .servizio');

            // Appointment form save button
            const addButton = document.querySelector('.azioni .button.rimira.primary.aggiungi');
            const addVisible = addButton && getComputedStyle(addButton).display !== 'none';

            // Booking form container
            const bookingFormVisible = !!document.querySelector('.appuntamento') &&
                getComputedStyle(document.querySelector('.appuntamento')).display !== 'none';

            return {
                loginVisible,
                agendaVisible,
                hasMenu,
                hasDateGrid: !!dateGrid,
                selectedDate: selectedDateInfo,
                timeCellCount: timeCells.length,
                customerSearchVisible,
                phoneModalVisible,
                customerFormVisible,
                selectedCustomer,
                selectedServiceCount: selectedServiceRows,
                serviceButtonsVisible,
                addButtonVisible: addVisible,
                bookingFormVisible
            };
        }
    """)

    # Determine phase from page signals
    if info["loginVisible"]:
        phase = "not_logged_in"
    elif not info["agendaVisible"] and not info["hasMenu"]:
        phase = "not_ready"
    elif not info["hasDateGrid"] and not info["bookingFormVisible"]:
        phase = "idle"
    elif info["customerSearchVisible"] or info["customerFormVisible"]:
        # Customer modal is open = time was clicked
        phase = "time_selected"
    elif info["phoneModalVisible"]:
        phase = "customer_selected"
    elif info["bookingFormVisible"] and info["selectedCustomer"]:
        if info["selectedServiceCount"] > 0:
            if info["addButtonVisible"]:
                phase = "ready_to_confirm"
            else:
                phase = "confirmed"
        else:
            phase = "phone_confirmed"
    elif info["timeCellCount"] > 0 and info["selectedDate"]:
        phase = "date_selected"
    elif info["hasDateGrid"]:
        phase = "idle"
    else:
        phase = "idle"

    return {"phase": phase, "info": info}


# ─── Safe Action Wrapper ──────────────────────────────────────────────

async def safe_action(page, action_fn, label=""):
    """Execute an action with adaptive modal scanning before and after.
    Logs any modals found, attempts dismissal, and retries if needed.
    Returns (result, modal_report) or raises with modal context."""

    # Scan before action
    pre_modals = await adaptive_modal_scan(page, f"before-{label}")
    if pre_modals["blocking"]:
        logger.error(f"🚧 Blocking modal before '{label}': {pre_modals['modals']}")

    try:
        result = await action_fn()
    except Exception as e:
        # Scan after error — modal may have appeared
        post_modals = await adaptive_modal_scan(page, f"error-{label}", auto_dismiss=True)
        error_ctx = str(e)
        if post_modals["modals"]:
            error_ctx = f" [modals: {post_modals['modals']}] " + str(e)
        logger.error(f"❌ Action '{label}' failed: {error_ctx}")
        await snap(page, f"error-{label}", force=True)
        raise

    # Scan after action
    post_modals = await adaptive_modal_scan(page, f"after-{label}")

    return result, {"pre": pre_modals, "post": post_modals}


# ─── Phase: Select Date ───────────────────────────────────────────────

async def advance_to_date_selected(page, booking_state: BookingState) -> bool:
    """Click the target date on the calendar."""
    if not booking_state.booked_date:
        logger.warning("No date in booking state — cannot advance to date_selected")
        return False

    target = datetime.strptime(booking_state.booked_date, "%Y-%m-%d")
    day, month, year = target.day, target.month, target.year

    def _click_date():
        return page.evaluate(f"""
            () => {{
                const sel = ".data[giorno='{day}'][mese='{month}'][anno='{year}']";
                const el = document.querySelector(sel);
                if (!el) return {{ ok: false, reason: 'not_found' }};
                el.click();
                return {{ ok: true }};
            }}
        """)

    result, _ = await safe_action(page, _click_date, f"click-date-{day}/{month}")

    if not result or not result.get("ok"):
        raise Exception(f"Date {day}/{month}/{year} not found on calendar")

    logger.info(f"✅ Date clicked: {day}/{month}/{year}")

    # Wait for grid to load
    try:
        await page.wait_for_function(
            f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
            timeout=15000
        )
    except Exception:
        logger.warning("Grid didn't load after date click — retrying")
        await _click_date()
        await asyncio.sleep(2)

    await asyncio.sleep(1)
    return True


# ─── Phase: Select Time ───────────────────────────────────────────────

async def advance_to_time_selected(page, booking_state: BookingState) -> bool:
    """Click a time slot. Uses operator preference or 'prima disponibile'."""
    if not booking_state.booked_time:
        logger.warning("No time in booking state — cannot advance to time_selected")
        return False

    target = datetime.strptime(booking_state.booked_date, "%Y-%m-%d")
    day, month, year = target.day, target.month, target.year

    raw_hour = int(booking_state.booked_time.split(":")[0])
    raw_minute = int(booking_state.booked_time.split(":")[1]) if ":" in booking_state.booked_time else 0
    rounded_minute = (raw_minute // 15) * 15
    hour = str(raw_hour)
    minute = str(rounded_minute)

    # Get operator map
    operator_map = await page.evaluate("""
        () => {
            const map = {};
            document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                const id = op.getAttribute('id_operatore');
                const nome = op.querySelector('.nome');
                if (id && nome) map[nome.textContent.trim().toLowerCase()] = id;
            });
            return map;
        }
    """)

    # Determine operator
    preferred_op_id = None
    operator_pref = (booking_state.operator_preference or "prima disponibile").strip().lower()
    if operator_pref != "prima disponibile":
        for name, op_id in operator_map.items():
            if operator_pref in name:
                preferred_op_id = op_id
                break

    # Try to click
    def _try_click(op_id=None, h=None, m=None):
        h = h or hour
        m = m or minute
        base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}'][minuto='{m}']"
        if op_id:
            base += f"[id_operatore='{op_id}']"
        base += ":not(.assente)"
        return f"""
            () => {{
                const cell = document.querySelector("{base}");
                if (!cell) {{
                    // Debug: find what's actually available
                    const all = document.querySelectorAll(".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}']");
                    const available = Array.from(all).filter(c => !c.classList.contains('assente'));
                    return {{ ok: false, reason: 'not_found', selector: "{base}", available_count: available.length, debug: available.map(c => c.getAttribute('id_operatore') + ':' + c.getAttribute('minuto')).join(',') }};
                }}
                cell.click();
                return {{ ok: true, op: cell.getAttribute('id_operatore'), minuto: cell.getAttribute('minuto') }};
            }}
        """

    clicked = False
    actual_time = f"{hour}:{minute}"
    clicked_operator_id = preferred_op_id
    last_result = None

    def _hour_selector(op_id=None, h=None):
        h = h or hour
        base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}']"
        if op_id:
            base += f"[id_operatore='{op_id}']"
        base += ":not(.assente)"
        return base

    if preferred_op_id:
        # Try exact slot
        for m_try in [minute, "0", "15", "30", "45"]:
            result, _ = await safe_action(page, lambda: page.evaluate(_try_click(op_id=preferred_op_id, m=m_try)), f"click-time-{hour}:{m_try}")
            if result and result.get("ok"):
                clicked = True
                actual_time = f"{hour}:{result.get('minuto', m_try)}"
                clicked_operator_id = preferred_op_id
                break

        if not clicked:
            # Try next hours
            for try_hour in range(raw_hour + 1, 20):
                result, _ = await safe_action(page, lambda h=str(try_hour): page.evaluate(_try_click(op_id=preferred_op_id, h=h)), f"click-time-{try_hour}")
                if result and result.get("ok"):
                    clicked = True
                    actual_time = f"{try_hour}:{result.get('minuto', '0')}"
                    break
    else:
        # Prima disponibile
        last_result = None
        for m_try in [minute, "0", "15", "30", "45"]:
            result, _ = await safe_action(page, lambda m=m_try: page.evaluate(_try_click(m=m)), f"click-time-{hour}:{m_try}")
            last_result = result
            logger.info(f" Trying minute {m_try}: result={result}")
            if result and result.get("ok"):
                clicked = True
                actual_time = f"{hour}:{result.get('minuto', m_try)}"
                clicked_operator_id = result.get("op")
                break

        if not clicked:
            for try_hour in range(raw_hour + 1, 20):
                result, _ = await safe_action(page, lambda h=str(try_hour): page.evaluate(_try_click(h=h)), f"click-time-{try_hour}")
                last_result = result
                if result and result.get("ok"):
                    clicked = True
                    actual_time = f"{try_hour}:{result.get('minuto', '0')}"
                    clicked_operator_id = result.get("op")
                    break

    if not clicked:
        logger.error(f"❌ Time click failed - hour={hour}, minute={minute}, last_result={last_result}")
        raise Exception(f"No available time slot on {booking_state.booked_date}")

    booking_state.booked_time = actual_time
    booking_state.booked_operator = clicked_operator_id
    logger.info(f"✅ Time selected: {actual_time} | operator={clicked_operator_id}")
    await asyncio.sleep(2)
    return True


# ─── Phase: Find/Select Customer ──────────────────────────────────────

async def advance_to_customer_selected(page, booking_state: BookingState) -> bool:
    """Search for customer by name/phone, or create new if not found."""
    if not booking_state.customer_name:
        logger.warning("No customer name — cannot advance to customer_selected")
        return False

    name_parts = booking_state.customer_name.strip().split()
    first_name = name_parts[0] if name_parts else ""
    last_name = name_parts[-1] if len(name_parts) > 1 else ""
    first_safe = js_escape(first_name.lower())
    last_safe = js_escape(last_name.lower())

    search_phone = booking_state.customer_phone or ""
    if search_phone.startswith("+39"):
        search_phone = search_phone[3:]
    elif search_phone.startswith("0039"):
        search_phone = search_phone[4:]
    phone_safe = js_escape(search_phone)

    # Customer modal should be open after time click — scan for modals
    await adaptive_modal_scan(page, "customer-entry")

    # Wait for customer search modal
    try:
        await page.wait_for_selector(".cerca_cliente.modale input[name='cerca_cliente']", timeout=10000)
    except Exception:
        html_content = await page.content()
        html_dumps["customer_modal_error"] = html_content
        logger.warning(f"💾 Saved HTML dump to /html-dumps (customer modal error)")
        raise Exception("Customer search modal did not open after time selection")

    logger.info(f"🔍 Searching customer: '{booking_state.customer_name}'")

    # Search strategies - always pick first match if any exist
    match_js = f"""
        () => {{
            const first = '{first_safe}';
            const last = '{last_safe}';
            const rows = document.querySelectorAll('.tabella_clienti tbody tr[id]');
            const results = [];
            for (const row of rows) {{
                const p = row.querySelector('p.cliente');
                if (!p) continue;
                const text = p.textContent.toLowerCase().trim();
                const phone = row.querySelector('td span')?.textContent?.trim() || '';
                results.push({{
                    id: row.id,
                    name: p.textContent.trim(),
                    hasFirst: text.includes(first),
                    hasLast: last ? text.includes(last) : false,
                    hasPhone: '{search_phone}' && phone.includes('{search_phone}')
                }});
            }}
            // Priority: phone match > full name > first name > first result
            if ('{search_phone}') {{
                for (const r of results) {{
                    if (r.hasPhone) {{
                        document.getElementById(r.id).click();
                        return {{ found: true, id: r.id, name: r.name, method: 'phone' }};
                    }}
                }}
            }}
            if (last) {{
                for (const r of results) {{
                    if (r.hasFirst && r.hasLast) {{
                        document.getElementById(r.id).click();
                        return {{ found: true, id: r.id, name: r.name, method: 'both_names' }};
                    }}
                }}
            }}
            if (!last) {{
                for (const r of results) {{
                    if (r.hasFirst) {{
                        document.getElementById(r.id).click();
                        return {{ found: true, id: r.id, name: r.name, method: 'first_only' }};
                    }}
                }}
            }}
            // Fallback: just pick the first result if any exist
            if (results.length > 0) {{
                document.getElementById(results[0].id).click();
                return {{ found: true, id: results[0].id, name: results[0].name, method: 'first_fallback' }};
            }}
            return {{ found: false, count: 0 }};
        }}
    """

    customer_found = False

    # Search 1: full name
    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", booking_state.customer_name)
    await asyncio.sleep(1.5)
    match = await page.evaluate(match_js)
    if match and match.get("found"):
        customer_found = True
        booking_state.customer_id = match.get("id")
        logger.info(f"✅ Customer found: {match}")

    # Search 2: first name
    if not customer_found:
        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
        await asyncio.sleep(1.5)
        match = await page.evaluate(match_js)
        if match and match.get("found"):
            customer_found = True
            booking_state.customer_id = match.get("id")
            logger.info(f"✅ Customer found by first name: {match}")

    # Search 3: last name
    if not customer_found and last_name:
        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", last_name)
        await asyncio.sleep(1.5)
        match = await page.evaluate(match_js)
        if match and match.get("found"):
            customer_found = True
            booking_state.customer_id = match.get("id")
            logger.info(f"✅ Customer found by last name: {match}")

    # Search 4: phone
    if not customer_found and search_phone:
        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", search_phone)
        await asyncio.sleep(1.5)
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
        if match and match.get("found"):
            customer_found = True
            booking_state.customer_id = match.get("id")
            logger.info(f"✅ Customer found by phone: {match}")

    # Create new customer if not found
    if not customer_found:
        logger.info(f"  Creating new customer: {first_name} {last_name}")
        await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", "")
        await asyncio.sleep(0.5)

        await page.evaluate("""
            () => {
                const btn = document.querySelector('.cerca_cliente .pulsanti .button.rimira.primary.aggiungi');
                if (btn) btn.click();
            }
        """)
        await asyncio.sleep(2)

        await page.evaluate(f"""
            () => {{
                const nome = document.querySelector('.form_cliente input[name="nome"]');
                if (nome) {{ nome.value = '{js_escape(first_name)}'; nome.dispatchEvent(new Event('input', {{bubbles:true}})); }}
                const cognome = document.querySelector('.form_cliente input[name="cognome"]');
                if (cognome) {{ cognome.value = '{js_escape(last_name)}'; cognome.dispatchEvent(new Event('input', {{bubbles:true}})); }}
                const cel = document.querySelector('.form_cliente input[name="cellulare"]');
                if (cel) {{ cel.value = '{phone_safe}'; cel.dispatchEvent(new Event('input', {{bubbles:true}})); }}
            }}
        """)

        saved = await page.evaluate("""
            () => {
                const btn = document.querySelector('.form_cliente .modale_footer .button.rimira.primary.aggiungi');
                if (btn) { btn.click(); return { clicked: true }; }
                return { clicked: false };
            }
        """)

        if saved and saved.get("clicked"):
            logger.info("✅ New customer created")
        else:
            logger.warning("⚠️ Could not create new customer")

        await asyncio.sleep(2)

    # Scan for any modals after customer selection (e.g., customer ID error)
    await adaptive_modal_scan(page, "after-customer-select")

    return True


# ─── Phase: Handle Phone Modal ────────────────────────────────────────

async def advance_to_phone_confirmed(page, booking_state: BookingState) -> bool:
    """Handle the phone input modal if it appears."""
    phone_safe = js_escape(booking_state.customer_phone or "")

    result = await page.evaluate(f"""
        () => {{
            const m = document.querySelector('.modale.card.inserisci_cellulare');
            if (!m || getComputedStyle(m).display === 'none') return {{ visible: false }};
            const inp = m.querySelector('input[name="cellulare"]');
            if (inp && '{phone_safe}') {{
                inp.value = '{phone_safe}';
                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
            }}
            const btn = m.querySelector('.button.rimira.primary.conferma') || m.querySelector('.button.conferma');
            if (btn) {{ btn.click(); return {{ visible: true, filled: true, confirmed: true }}; }}
            return {{ visible: true, filled: !!inp, confirmed: false }};
        }}
    """)

    if result.get("visible"):
        logger.info(f"📱 Phone modal handled: {result}")
    else:
        logger.info("📱 No phone modal")

    # Scan for any modals after phone confirmation (customer ID error, etc.)
    await adaptive_modal_scan(page, "after-phone-confirm")

    await asyncio.sleep(1)
    return True


# ─── Phase: Select Services ───────────────────────────────────────────

async def advance_to_services_selected(page, booking_state: BookingState) -> bool:
    """Add requested services to the appointment."""
    services = booking_state.services or []
    if not services:
        logger.warning("No services — cannot advance to services_selected")
        return False

    # Normalize services
    requested = [s.strip() for s in services if s and s.strip()]
    if not requested:
        return False

    logger.info(f"🛠️ Selecting services: {requested}")

    # Check initial state
    initial_rows = await page.evaluate("""
        () => document.querySelectorAll('.servizi_selezionati .riga_servizio').length
    """)

    for svc in requested:
        svc_kw = js_escape(svc.lower())
        logger.info(f"  Service: {svc}")

        # Try to find and click
        selected = await page.evaluate(f"""
            () => {{
                const kw = '{svc_kw}';
                const all = document.querySelectorAll('.pulsanti_tab .servizio');
                for (const s of all) {{
                    const nome = (s.getAttribute('nome') || '').toLowerCase();
                    if (nome === kw || nome.startsWith(kw) || nome.includes(kw) || (nome.length > 2 && kw.includes(nome))) {{
                        s.click();
                        return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id }};
                    }}
                }}
                // Text fallback
                for (const s of all) {{
                    const txt = (s.querySelector('.nome')?.textContent || s.textContent || '').toLowerCase().trim();
                    if (txt === kw || txt.includes(kw) || kw.includes(txt)) {{
                        s.click();
                        return {{ ok: 1, nome: s.getAttribute('nome') || txt, id: s.id }};
                    }}
                }}
                return {{ ok: 0 }};
            }}
        """)

        if not selected or not selected.get("ok"):
            # Try search
            try:
                await page.fill(".pulsanti_tab input[name='cerca_servizio']", svc)
                await asyncio.sleep(1.5)
                clicked = await page.evaluate("""
                    () => {
                        for (const s of document.querySelectorAll('.pulsanti_tab .servizio')) {
                            if (getComputedStyle(s).display !== 'none') {
                                s.click();
                                return { ok: 1, nome: s.getAttribute('nome') || '' };
                            }
                        }
                        return { ok: 0 };
                    }
                """)
                selected = clicked
            except Exception:
                pass

        if not selected or not selected.get("ok"):
            raise Exception(f"Service not found: {svc}")

        logger.info(f"  ✅ {selected}")
        await asyncio.sleep(1)

        # Clear search
        await page.fill(".pulsanti_tab input[name='cerca_servizio']", "")
        await asyncio.sleep(0.3)

    # Scan for modals after service selection
    await adaptive_modal_scan(page, "after-services")

    return True


# ─── Phase: Select Operator (if specified) ────────────────────────────

async def advance_to_operator_selected(page, booking_state: BookingState) -> bool:
    """Select specific operator in the appointment form."""
    if not booking_state.operator_preference or booking_state.operator_preference.lower() == "prima disponibile":
        return True  # Not required

    op_safe = js_escape(booking_state.operator_preference.lower())
    logger.info(f"👤 Selecting operator: {booking_state.operator_preference}")

    result = await page.evaluate(f"""
        () => {{
            const kw = '{op_safe}';
            const ops = document.querySelectorAll('.pulsanti_tab .operatori .operatore');
            for (const op of ops) {{
                if (op.classList.contains('assente')) continue;
                const n = op.querySelector('span.nome');
                if (n && n.textContent.toLowerCase().trim().includes(kw)) {{
                    op.click();
                    return {{ ok: 1, name: n.textContent.trim(), id: op.id }};
                }}
            }}
            return {{ ok: 0 }};
        }}
    """)

    logger.info(f"Operator selection: {result}")
    await asyncio.sleep(0.5)
    return True


# ─── Phase: Confirm/Save Booking ──────────────────────────────────────

async def advance_to_confirmed(page, booking_state: BookingState) -> bool:
    """Click the save button and verify the booking was created."""
    logger.info("💾 Saving appointment...")

    added = await page.evaluate("""
        () => {
            const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
            if (btn && getComputedStyle(btn).display !== 'none' && btn.getBoundingClientRect().width > 0) {
                btn.click();
                return 'clicked';
            }
            return null;
        }
    """)

    if not added:
        raise Exception("Save button not found — cannot confirm booking")

    logger.info(f"✅ Save clicked: {added}")
    await asyncio.sleep(3)

    # Scan for any post-save modals
    await adaptive_modal_scan(page, "post-save")
    await asyncio.sleep(1)

    # Verify success
    form_gone = await page.evaluate("""
        () => {
            const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
            return !btn || getComputedStyle(btn).display === 'none';
        }
    """)

    has_error = await page.evaluate("""
        () => {
            const m = document.getElementById('modale_dialog');
            return m && getComputedStyle(m).display !== 'none';
        }
    """)

    on_agenda = await page.evaluate("""
        () => {
            const a = document.getElementById('pannello_agenda');
            return a && getComputedStyle(a).display !== 'none';
        }
    """)

    success = form_gone and not has_error
    logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'} | form_gone={form_gone} has_error={has_error} on_agenda={on_agenda}")
    return success


# ─── Back Navigation: Reset to idle ───────────────────────────────────

async def reset_booking_to_idle(page) -> bool:
    """Navigate back to the idle state by closing any open booking form."""
    logger.info("🔄 Resetting booking to idle state...")

    # Try to find and click cancel/close buttons
    cancelled = await page.evaluate("""
        () => {
            // Try cancel buttons in booking form
            const cancelBtns = document.querySelectorAll('.button.annulla, .button.chiudi, .button.indietro');
            for (const btn of cancelBtns) {
                if (getComputedStyle(btn).display !== 'none') {
                    btn.click();
                    return { clicked: true, type: btn.textContent.trim() };
                }
            }
            // Try to click agenda button to reset
            const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
            if (agendaBtn) {
                agendaBtn.click();
                return { clicked: true, type: 'agenda-reset' };
            }
            return { clicked: false };
        }
    """)

    await asyncio.sleep(2)

    if cancelled.get("clicked"):
        logger.info(f"✅ Reset via: {cancelled['type']}")
    else:
        # Force hide any overlays and click agenda
        await page.evaluate("""
            () => {
                document.querySelectorAll('.modale, .modale_overlay, .overlay_modale, .overlay').forEach(el => {
                    el.style.display = 'none';
                });
            }
        """)
        await asyncio.sleep(1)

    # Dismiss any system modals
    await dismiss_system_modals(page, "after-reset")

    return True


# ─── Main: Adaptive Booking Engine ────────────────────────────────────

async def run_adaptive_booking(request: 'BookingRequest') -> dict:
    """Main adaptive booking entry point.

    Detects current page state, advances through booking phases,
    and handles any modals or errors adaptively.
    """
    if not request.conversation_id:
        raise Exception("conversation_id is required for live booking")

    session = await get_live_session_for_conversation(request.conversation_id)

    async with session.lock:
        page = session.page
        if not page or page.is_closed():
            raise Exception("Session page not available")

        # Verify session is ready
        state_ok = await page.evaluate("""() => {
            const loginPanel = document.getElementById('pannello_login');
            const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
            const menu = document.getElementById('menu');
            return !(loginPanel && getComputedStyle(loginPanel).display !== 'none') && (!!agendaBtn || !!menu);
        }""")

        if not state_ok:
            raise Exception("Assigned pool session is not ready for booking")

        session.last_used_at = datetime.utcnow()
        screenshots.clear()

        # Build booking state from request
        services = [s.strip() for s in (request.services or []) if s and s.strip()]
        if not services and request.service:
            services = [request.service.strip()]
        if not services:
            raise Exception("No service provided")

        phone = request.caller_phone or ""
        if phone.startswith("+39"):
            phone = phone[3:]
        elif phone.startswith("0039"):
            phone = phone[4:]

        new_state = BookingState(
            phase="idle",
            booked_date=request.preferred_date,
            booked_time=request.preferred_time,
            customer_name=request.customer_name,
            customer_phone=phone,
            services=services,
            operator_preference=request.operator_preference or "prima disponibile"
        )

        # Check if context changed — if so, reset
        if session.booking_state and new_state.changed_from(session.booking_state):
            logger.info("🔄 Context changed — resetting booking state")
            await reset_booking_to_idle(page)
            session.booking_state = BookingState(
                booked_date=request.preferred_date,
                booked_time=request.preferred_time,
                customer_name=request.customer_name,
                customer_phone=phone,
                services=services,
                operator_preference=request.operator_preference or "prima disponibile"
            )

        if not session.booking_state:
            session.booking_state = new_state

        bs = session.booking_state

        # Detect current page state
        page_state = await detect_page_state(page)
        logger.info(f"📊 Current page state: {page_state['phase']}")

        # Phase progression: advance from current page state to completion
        # Each phase only runs if not already completed
        try:
            # Phase 1: Date
            if page_state["phase"] == "idle" or page_state["phase"] < "date_selected":
                await advance_to_date_selected(page, bs)
                bs.phase = "date_selected"
                await snap(page, "05_date")

            # Phase 2: Time
            if page_state["phase"] in ("date_selected",) or bs.phase == "date_selected":
                # Re-detect to see if we're past date already
                page_state = await detect_page_state(page)
                if page_state["phase"] not in ("time_selected", "customer_selected", "phone_confirmed", "services_selected", "ready_to_confirm"):
                    await advance_to_time_selected(page, bs)
                bs.phase = "time_selected"
                await snap(page, "06_time")

            # Phase 3: Customer
            if page_state["phase"] in ("time_selected",) or bs.phase == "time_selected":
                page_state = await detect_page_state(page)
                if page_state["phase"] not in ("customer_selected", "phone_confirmed", "services_selected", "ready_to_confirm"):
                    await advance_to_customer_selected(page, bs)
                bs.phase = "customer_selected"

            # Phase 4: Phone modal
            if page_state["phase"] in ("customer_selected",) or bs.phase == "customer_selected":
                page_state = await detect_page_state(page)
                if page_state["phase"] not in ("phone_confirmed", "services_selected", "ready_to_confirm"):
                    await advance_to_phone_confirmed(page, bs)
                bs.phase = "phone_confirmed"

            # Phase 5: Services
            if page_state["phase"] in ("phone_confirmed",) or bs.phase == "phone_confirmed":
                page_state = await detect_page_state(page)
                if page_state["phase"] not in ("services_selected", "ready_to_confirm"):
                    await advance_to_services_selected(page, bs)
                bs.phase = "services_selected"
                await snap(page, "09_services")

            # Phase 6: Operator preference
            await advance_to_operator_selected(page, bs)
            await snap(page, "10_operator")
            bs.phase = "ready_to_confirm"

            # Phase 7: Confirm
            success = await advance_to_confirmed(page, bs)
            bs.phase = "confirmed" if success else "ready_to_confirm"

            # Refresh availability cache
            if success:
                try:
                    refreshed = await scrape_day_availability_from_page(page, bs.booked_date, "prima disponibile")
                    if refreshed and refreshed.get("is_open"):
                        from utils import set_cached_day
                        await set_cached_day(bs.booked_date, refreshed)
                except Exception as e:
                    logger.warning(f"Cache refresh failed: {e}")

            await snap(page, "12_final")

            return {
                "success": success,
                "customer_name": bs.customer_name,
                "customer_id": bs.customer_id,
                "services": bs.services,
                "date": bs.booked_date,
                "time": bs.booked_time,
                "operator": bs.operator_preference,
                "message": "✅ Appuntamento creato" if success else "⚠️ Non confermato — verifica Wegest",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }

        except Exception as e:
            logger.error(f"❌ Booking failed: {e}")
            await snap(page, "ERROR", force=True)

            # Scan for modals one more time to capture the error context
            await adaptive_modal_scan(page, "final-error-report")

            return {
                "success": False,
                "error": str(e),
                "message": f"❌ {e}",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }


# ─── Incremental: Sync booking context without advancing ──────────────

async def sync_booking_context(session, context_update: dict) -> dict:
    """Update booking context on the session. If context changed significantly,
    resets the booking state so next booking run starts fresh.

    Returns: {changed: bool, current_phase: str, can_advance: bool, next_phase: str}
    """
    bs = session.booking_state
    if not bs:
        bs = BookingState()
        session.booking_state = bs

    old_hash = bs.context_hash()

    # Apply updates
    if "date" in context_update and context_update["date"]:
        bs.booked_date = context_update["date"]
    if "time" in context_update and context_update["time"]:
        bs.booked_time = context_update["time"]
    if "customer_name" in context_update and context_update["customer_name"]:
        bs.customer_name = context_update["customer_name"]
    if "customer_phone" in context_update and context_update["customer_phone"]:
        bs.customer_phone = context_update["customer_phone"]
    if "services" in context_update and context_update["services"]:
        bs.services = context_update["services"]
    if "operator_preference" in context_update and context_update["operator_preference"]:
        bs.operator_preference = context_update["operator_preference"]

    changed = bs.context_hash() != old_hash

    # Determine next phase we could advance to
    can_advance = False
    next_phase = "idle"

    if bs.booked_date and bs.phase == "idle":
        can_advance = True
        next_phase = "date_selected"
    elif bs.booked_time and bs.phase == "date_selected":
        can_advance = True
        next_phase = "time_selected"
    elif bs.customer_name and bs.phase == "time_selected":
        can_advance = True
        next_phase = "customer_selected"
    elif bs.phase == "customer_selected":
        can_advance = True
        next_phase = "phone_confirmed"
    elif bs.services and bs.phase == "phone_confirmed":
        can_advance = True
        next_phase = "services_selected"
    elif bs.phase == "services_selected":
        can_advance = True
        next_phase = "ready_to_confirm"
    elif bs.phase == "ready_to_confirm":
        can_advance = True
        next_phase = "confirmed"

    return {
        "changed": changed,
        "current_phase": bs.phase,
        "can_advance": can_advance,
        "next_phase": next_phase,
        "booking_data": {
            "date": bs.booked_date,
            "time": bs.booked_time,
            "customer": bs.customer_name,
            "phone": bs.customer_phone,
            "services": bs.services,
            "operator": bs.operator_preference
        }
    }


# ─── Legacy compatibility ─────────────────────────────────────────────

async def run_wegest_booking(request: 'BookingRequest') -> dict:
    """Legacy entry point — delegates to adaptive engine."""
    return await run_adaptive_booking(request)
