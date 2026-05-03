"""
Booking logic for Agent Andrea
"""

import config
from config import logger, API_SECRET, screenshots
from session_manager import (
    get_live_session_for_conversation,
    dismiss_system_modals,
    snap
)
from utils import js_escape, normalize_requested_services
from catalog import scrape_day_availability_from_page, extract_service_operator_durations_from_page
from datetime import datetime
from typing import Any, Optional
import asyncio


async def run_wegest_booking(request: 'BookingRequest') -> dict:
    if not request.conversation_id:
        raise Exception("conversation_id is required for live booking")

    session = await get_live_session_for_conversation(request.conversation_id)

    async with session.lock:
        page = None

        try:
            page = session.page

            state_ok = await page.evaluate("""() => {
                const loginPanel = document.getElementById('pannello_login');
                const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
                const menu = document.getElementById('menu');

                return (
                    !(loginPanel && getComputedStyle(loginPanel).display !== 'none') &&
                    (!!agendaBtn || !!menu)
                );
            }""")

            if not state_ok:
                raise Exception("Assigned pool session is not ready for booking")

            session.last_used_at = datetime.utcnow()

            screenshots.clear()
            logger.info(f"📅 Booking: {request.customer_name} | {request.service or request.services} | {request.preferred_date} {request.preferred_time}")

            requested_services = [s.strip() for s in request.services if s and s.strip()]
            if not requested_services and request.service:
                requested_services = [request.service.strip()]
            if not requested_services:
                raise Exception("No service provided")

            logger.info(f"Requested services: {requested_services}")

            # STEP 5: Click date
            target = datetime.strptime(request.preferred_date, "%Y-%m-%d")
            day, month, year = target.day, target.month, target.year
            logger.info(f"Step 5: Date {day}/{month}/{year}...")

            await dismiss_system_modals(page, "before-date")

            date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"
            try:
                await page.click(date_selector, timeout=10000)
                logger.info("✅ Date clicked")
            except Exception:
                raise Exception(f"Date {day}/{month}/{year} not visible on calendar")

            logger.info("Waiting for grid...")
            try:
                await page.wait_for_function(
                    f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
                    timeout=15000
                )
            except Exception:
                await page.click(date_selector, timeout=5000)
                await page.wait_for_timeout(2000)

            await page.wait_for_timeout(1000)
            await dismiss_system_modals(page, "after-date")
            await snap(page, "05_date")

            # STEP 6: Click time slot
            raw_hour = int(request.preferred_time.split(":")[0])
            raw_minute = int(request.preferred_time.split(":")[1]) if ":" in request.preferred_time else 0
            rounded_minute = (raw_minute // 15) * 15
            hour = str(raw_hour)
            minute = str(rounded_minute)

            logger.info(f"Step 6: Time {hour}:{minute} | operator pref: {request.operator_preference}")

            operator_map = await page.evaluate("""
                () => {
                    const map = {};
                    document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                        const id = op.getAttribute('id_operatore');
                        const nome = op.querySelector('.nome');
                        if (id && nome) {
                            map[nome.textContent.trim().toLowerCase()] = id;
                        }
                    });
                    return map;
                }
            """)
            logger.info(f"Operator map: {operator_map}")

            preferred_op_id = None
            operator_pref = request.operator_preference.strip().lower()

            if operator_pref != "prima disponibile":
                for name, op_id in operator_map.items():
                    if operator_pref in name:
                        preferred_op_id = op_id
                        break
                logger.info(f"Preferred operator id: {preferred_op_id}")

            time_clicked = False
            actual_time = f"{hour}:{minute}"
            clicked_operator_id = preferred_op_id

            def exact_selector(op_id=None, h=None, m=None):
                h = h if h is not None else hour
                m = m if m is not None else minute
                base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}'][minuto='{m}']"
                if op_id:
                    base += f"[id_operatore='{op_id}']"
                base += ":not(.assente):not(.occupata)"
                return base

            def hour_selector(op_id=None, h=None):
                h = h if h is not None else hour
                base = f".cella[giorno='{day}'][mese='{month}'][anno='{year}'][ora='{h}']"
                if op_id:
                    base += f"[id_operatore='{op_id}']"
                base += ":not(.assente):not(.occupata)"
                return base

            if preferred_op_id:
                logger.info(f"Trying specific operator slot for id_operatore={preferred_op_id}")

                try:
                    sel = exact_selector(op_id=preferred_op_id)
                    count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                    logger.info(f"Specific op exact count: {count}")
                    if count > 0:
                        await page.click(sel, timeout=5000)
                        time_clicked = True
                        clicked_operator_id = preferred_op_id
                        logger.info(f"✅ Clicked exact slot for preferred operator {preferred_op_id}")
                except Exception as e:
                    logger.warning(f"Specific operator exact click failed: {e}")

                if not time_clicked:
                    try:
                        sel = hour_selector(op_id=preferred_op_id)
                        count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                        logger.info(f"Specific op hour count: {count}")
                        if count > 0:
                            actual_min = await page.evaluate(f"""
                                () => {{
                                    const cell = document.querySelector("{sel}");
                                    return cell ? cell.getAttribute('minuto') : null;
                                }}
                            """)
                            await page.click(sel, timeout=5000)
                            actual_time = f"{hour}:{actual_min or '0'}"
                            time_clicked = True
                            clicked_operator_id = preferred_op_id
                            logger.info(f"✅ Clicked same-hour fallback for preferred operator: {actual_time}")
                    except Exception as e:
                        logger.warning(f"Specific operator hour fallback failed: {e}")

                if not time_clicked:
                    logger.info("Trying next available hour for preferred operator...")
                    for try_hour in range(raw_hour + 1, 20):
                        try:
                            sel = hour_selector(op_id=preferred_op_id, h=str(try_hour))
                            count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                            if count > 0:
                                actual_min = await page.evaluate(f"""
                                    () => {{
                                        const cell = document.querySelector("{sel}");
                                        return cell ? cell.getAttribute('minuto') : '0';
                                    }}
                                """)
                                await page.click(sel, timeout=5000)
                                actual_time = f"{try_hour}:{actual_min}"
                                time_clicked = True
                                clicked_operator_id = preferred_op_id
                                logger.info(f"✅ Clicked next available for preferred operator: {actual_time}")
                                break
                        except Exception:
                            continue

                if not time_clicked:
                    raise Exception(
                        f"No available slot for operator '{request.operator_preference}' on {request.preferred_date} around {request.preferred_time}"
                    )

            else:
                logger.info("Using prima disponibile logic")

                try:
                    sel = exact_selector()
                    count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                    logger.info(f"Any-op exact count: {count}")
                    if count > 0:
                        clicked_operator_id = await page.evaluate(f"""
                            () => {{
                                const cell = document.querySelector("{sel}");
                                return cell ? cell.getAttribute('id_operatore') : null;
                            }}
                        """)
                        await page.click(sel, timeout=5000)
                        time_clicked = True
                        logger.info(f"✅ Clicked exact slot for first available operator {clicked_operator_id}")
                except Exception as e:
                    logger.warning(f"Any-op exact click failed: {e}")

                if not time_clicked:
                    try:
                        sel = hour_selector()
                        count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                        logger.info(f"Any-op hour count: {count}")
                        if count > 0:
                            result = await page.evaluate(f"""
                                () => {{
                                    const cell = document.querySelector("{sel}");
                                    if (!cell) return null;
                                    return {{
                                        minuto: cell.getAttribute('minuto'),
                                        op: cell.getAttribute('id_operatore')
                                    }};
                                }}
                            """)
                            await page.click(sel, timeout=5000)
                            actual_time = f"{hour}:{result['minuto'] if result else '0'}"
                            clicked_operator_id = result['op'] if result else None
                            time_clicked = True
                            logger.info(f"✅ Clicked same-hour first available: {actual_time} | op={clicked_operator_id}")
                    except Exception as e:
                        logger.warning(f"Any-op hour fallback failed: {e}")

                if not time_clicked:
                    logger.info("Trying next available hour for any operator...")
                    for try_hour in range(raw_hour + 1, 20):
                        try:
                            sel = hour_selector(h=str(try_hour))
                            count = await page.evaluate(f"() => document.querySelectorAll(\"{sel}\").length")
                            if count > 0:
                                result = await page.evaluate(f"""
                                    () => {{
                                        const cell = document.querySelector("{sel}");
                                        if (!cell) return null;
                                        return {{
                                            minuto: cell.getAttribute('minuto'),
                                            op: cell.getAttribute('id_operatore')
                                        }};
                                    }}
                                """)
                                await page.click(sel, timeout=5000)
                                actual_time = f"{try_hour}:{result['minuto'] if result else '0'}"
                                clicked_operator_id = result['op'] if result else None
                                time_clicked = True
                                logger.info(f"✅ Clicked next available: {actual_time} | op={clicked_operator_id}")
                                break
                        except Exception:
                            continue

            if not time_clicked:
                raise Exception(f"No available slot on {day}/{month}/{year}")

            logger.info(f"Final clicked slot: {actual_time} | id_operatore={clicked_operator_id}")
            await page.wait_for_timeout(2000)
            await snap(page, "06_time")

            # STEP 7: Customer search & selection
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
                        return {{
                            found: false,
                            count: results.length,
                            candidates: results.map(r => r.name).slice(0, 5)
                        }};
                    }}
                """

                logger.info(f"  Search 1: '{request.customer_name}'")
                await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", request.customer_name)
                await page.wait_for_timeout(1500)
                await snap(page, "07a_full")
                match = await page.evaluate(match_js)
                if match and match.get('found'):
                    customer_found = True
                    logger.info(f"✅ Match: {match}")

                if not customer_found:
                    logger.info(f"  Search 2: '{first_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", first_name)
                    await page.wait_for_timeout(1500)
                    await snap(page, "07b_first")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                if not customer_found and last_name:
                    logger.info(f"  Search 3: '{last_name}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", last_name)
                    await page.wait_for_timeout(1500)
                    await snap(page, "07c_last")
                    match = await page.evaluate(match_js)
                    if match and match.get('found'):
                        customer_found = True
                        logger.info(f"✅ Match: {match}")

                if not customer_found and search_phone:
                    logger.info(f"  Search 4: phone '{search_phone}'")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", search_phone)
                    await page.wait_for_timeout(1500)
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

                if not customer_found:
                    logger.info("  ❌ Not found → creating new customer")
                    await page.fill(".cerca_cliente.modale input[name='cerca_cliente']", "")
                    await page.wait_for_timeout(500)

                    await page.evaluate("""
                        () => {
                            const btn = document.querySelector(
                                '.cerca_cliente .pulsanti .button.rimira.primary.aggiungi'
                            );
                            if (btn) btn.click();
                        }
                    """)
                    await page.wait_for_timeout(2000)
                    await snap(page, "07e_new_form")

                    await page.evaluate(f"""
                        () => {{
                            const inp = document.querySelector('.form_cliente input[name="nome"]');
                            if (inp) {{
                                inp.value = '{js_escape(first_name)}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }}
                        }}
                    """)

                    await page.evaluate(f"""
                        () => {{
                            const inp = document.querySelector('.form_cliente input[name="cognome"]');
                            if (inp) {{
                                inp.value = '{js_escape(last_name)}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }}
                        }}
                    """)

                    await page.evaluate(f"""
                        () => {{
                            const inp = document.querySelector('.form_cliente input[name="cellulare"]');
                            if (inp) {{
                                inp.value = '{phone_safe}';
                                inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }}
                        }}
                    """)

                    logger.info(f"  Filled: {first_name} {last_name} / {search_phone}")
                    await snap(page, "07f_filled")

                    saved = await page.evaluate("""
                        () => {
                            const btn = document.querySelector(
                                '.form_cliente .modale_footer .button.rimira.primary.aggiungi'
                            );
                            if (btn) {
                                btn.click();
                                return { clicked: true, method: 'form_cliente' };
                            }
                            return { clicked: false };
                        }
                    """)

                    logger.info(f"  Save: {saved}")

                    if saved and saved.get('clicked'):
                        customer_found = True
                        logger.info("✅ New customer created")
                    else:
                        logger.warning("⚠️ Could not click Add customer!")

                    await page.wait_for_timeout(2000)
                    await snap(page, "07g_saved")
                    await dismiss_system_modals(page, "after-new-customer")

            except Exception as e:
                logger.warning(f"Customer error: {e}")
                await snap(page, "07_ERROR")

            await page.wait_for_timeout(1000)

            # STEP 7.5: Phone modal
            phone_handled = await page.evaluate(f"""
                () => {{
                    const m = document.querySelector('.modale.card.inserisci_cellulare');
                    if (!m) return {{ visible: false }};
                    if (getComputedStyle(m).display === 'none') return {{ visible: false }};
                    const inp = m.querySelector('input[name="cellulare"]');
                    if (inp) {{
                        inp.value = '{phone_safe}';
                        inp.dispatchEvent(new Event('input', {{bubbles:true}}));
                        inp.dispatchEvent(new Event('change', {{bubbles:true}}));
                    }}
                    const btn = m.querySelector('.button.rimira.primary.conferma');
                    if (btn) {{
                        btn.click();
                        return {{ visible: true, filled: true, confirmed: true }};
                    }}
                    return {{ visible: true, filled: !!inp, confirmed: false }};
                }}
            """)
    if phone_handled and phone_handled.get('visible'):
        logger.info(f"📱 Phone modal: {phone_handled}")
        await page.wait_for_timeout(1000)
            else:
                logger.info("📱 No phone modal")

            await snap(page, "08_form_ready")

            # STEP 8: Select services
            logger.info(f"Step 8: Services {requested_services}...")

            initial_rows = await page.evaluate("""
                () => document.querySelectorAll('.servizi_selezionati .riga_servizio').length
            """)

            selected_services = []

            for index, requested_service in enumerate(requested_services, start=1):
                service_kw = js_escape(requested_service.lower())
                logger.info(f"Selecting service {index}/{len(requested_services)}: {requested_service}")

                service_selected = await page.evaluate(f"""
                    () => {{
                        const kw = '{service_kw}';
                        const all = document.querySelectorAll('.pulsanti_tab .servizio');

                        for (const s of all) {{
                            if ((s.getAttribute('nome') || '').toLowerCase() === kw) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'exact' }};
                            }}
                        }}

                        for (const s of all) {{
                            const nome = (s.getAttribute('nome') || '').toLowerCase();
                            if (nome.startsWith(kw)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'starts' }};
                            }}
                        }}

                        for (const s of all) {{
                            const nome = (s.getAttribute('nome') || '').toLowerCase();
                            if (nome.includes(kw)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'contains' }};
                            }}
                        }}

                        for (const s of all) {{
                            const nome = (s.getAttribute('nome') || '').toLowerCase();
                            if (nome.length > 2 && kw.includes(nome)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome'), id: s.id, method: 'reverse' }};
                            }}
                        }}

                        for (const s of all) {{
                            const txt = (s.querySelector('.nome')?.textContent || s.textContent || '').toLowerCase().trim();
                            if (txt === kw || txt.includes(kw) || kw.includes(txt)) {{
                                s.click();
                                return {{ ok: 1, nome: s.getAttribute('nome') || txt, id: s.id, method: 'text' }};
                            }}
                        }}

                        return {{ ok: 0 }};
                    }}
                """)

                if not service_selected or not service_selected.get("ok"):
                    logger.warning(f"⚠️ Service '{requested_service}' not found directly, trying search...")
                    try:
                        await page.fill(".pulsanti_tab input[name='cerca_servizio']", requested_service)
                        await page.wait_for_timeout(1500)

                        clicked_search = await page.evaluate("""
                            () => {
                                const svcs = document.querySelectorAll('.pulsanti_tab .servizio');
                                for (const s of svcs) {
                                    if (getComputedStyle(s).display !== 'none') {
                                        s.click();
                                        return {
                                            ok: 1,
                                            nome: s.getAttribute('nome') || '',
                                            id: s.id,
                                            method: 'search'
                                        };
                                    }
                                }
                                return { ok: 0 };
                            }
                        """)
                        service_selected = clicked_search
                    except Exception:
                        pass

                if not service_selected or not service_selected.get("ok"):
                    raise Exception(f"Service not found: {requested_service}")

                logger.info(f"✅ Service selected: {service_selected}")

                expected_rows = initial_rows + len(selected_services) + 1
                try:
                    await page.wait_for_function(
                        f"() => document.querySelectorAll('.servizi_selezionati .riga_servizio').length >= {expected_rows}",
                        timeout=5000
                    )
                except Exception:
                    logger.warning(f"⚠️ Did not detect new row for service {requested_service} by count")

                await page.wait_for_timeout(1000)

                selected_row = await page.evaluate(f"""
                    () => {{
                        const rows = document.querySelectorAll('.servizi_selezionati .riga_servizio');
                        const kw = '{service_kw}';
                        for (const row of rows) {{
                            const txt = (row.querySelector('.dettaglio p')?.textContent || row.textContent || '').toLowerCase().trim();
                            if (txt.includes(kw) || kw.includes(txt)) {{
                                return {{
                                    found: true,
                                    text: txt,
                                    id_servizio: row.getAttribute('id_servizio'),
                                    row_id: row.getAttribute('id')
                                }};
                            }}
                        }}
                        return {{ found: false, count: rows.length }};
                    }}
                """)

                logger.info(f"Selected row verification: {selected_row}")

                if selected_row and selected_row.get("found"):
                    selected_services.append({
                        "requested": requested_service,
                        "selected": service_selected.get("nome"),
                        "row": selected_row
                    })
                else:
                    logger.warning(f"⚠️ Could not verify selected row for {requested_service}")

                await page.fill(".pulsanti_tab input[name='cerca_servizio']", "")
                await page.wait_for_timeout(300)

            logger.info(f"✅ Total selected services: {selected_services}")
            await snap(page, "09_services_selected")

            # STEP 9: Select operator in appointment form
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
                            avail.push({{
                                name: n ? n.textContent.trim() : '?',
                                id: o.id,
                                absent: o.classList.contains('assente')
                            }});
                        }});
                        return {{ ok:0, available: avail }};
                    }}
                """)
                logger.info(f"Operator: {op_result}")
            else:
                logger.info("Step 9: Default operator")

            await page.wait_for_timeout(500)
            await snap(page, "10_operator")

            # STEP 10: Add appointment
            logger.info("Step 10: Adding appointment...")

            added = await page.evaluate("""
                () => {
                    const btn = document.querySelector('.azioni .button.rimira.primary.aggiungi');
                    if (btn) {
                        const rect = btn.getBoundingClientRect();
                        if (rect.width > 0 && rect.height > 0) {
                            btn.click();
                            return 'azioni-aggiungi';
                        }
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
                except Exception:
                    await snap(page, "10_ERROR")

            await page.wait_for_timeout(3000)
            await snap(page, "11_saved")
            await dismiss_system_modals(page, "post-save")
            await page.wait_for_timeout(1000)

            # VERIFY
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

            if success:
                try:
                    logger.info(f"🔄 Refreshing cache in same session for {request.preferred_date}")
                    refreshed_day = await scrape_day_availability_from_page(
                        page,
                        request.preferred_date,
                        "prima disponibile"
                    )
                    if refreshed_day and refreshed_day.get("is_open") is True:
                        from utils import set_cached_day
                        await set_cached_day(request.preferred_date, refreshed_day)
                        logger.info(f"✅ Cache refreshed in same session for {request.preferred_date}")
                    else:
                        from utils import invalidate_cached_day
                        await invalidate_cached_day(request.preferred_date)
                        logger.info(f"🗑️ Cache invalidated for {request.preferred_date} (refresh returned no open data)")
                except Exception as refresh_err:
                    logger.warning(f"Same-session cache refresh failed: {refresh_err}")
                    from utils import invalidate_cached_day
                    await invalidate_cached_day(request.preferred_date)

            await snap(page, "12_final")
            session.last_used_at = datetime.utcnow()

            logger.info(f"🏁 {'✅ SUCCESS' if success else '⚠️ UNCERTAIN'}")

            return {
                "success": success,
                "customer_name": request.customer_name,
                "customer_found_in_db": customer_found,
                "service": request.service,
                "services": requested_services,
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
            if page:
                await snap(page, "ERROR", force=True)

            return {
                "success": False,
                "error": str(e),
                "message": f"❌ {e}",
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }
