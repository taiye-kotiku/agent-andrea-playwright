"""
Catalog and page extraction functions for Agent Andrea
"""

import config
from datetime import datetime
from config import logger, service_catalog, SERVICE_DURATION_FALLBACK
from playwright.async_api import async_playwright
from session_manager import dismiss_system_modals
from typing import Any, Optional


async def update_operator_catalog_from_page(page):
    try:
        found = await page.evaluate("""
            () => {
                const result = {};
                document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                    const id = op.getAttribute('id_operatore');
                    if (!id || id === '0') return;

                    const nome = op.querySelector('.nome');
                    if (!nome) return;

                    result[id] = {
                        name: nome.textContent.trim(),
                        active: !op.classList.contains('assente')
                    };
                });
                return result;
            }
        """)

        if found and isinstance(found, dict):
            for op_id, info in found.items():
                config.operator_catalog["operators"][op_id] = info

            config.operator_catalog["updated_at"] = datetime.utcnow().isoformat()
            from utils import save_operator_catalog
            save_operator_catalog()
            config.logger.info(f"👥 Operator catalog updated: {list(found.values())}")

    except Exception as e:
        config.logger.warning(f"Failed to update operator catalog from page: {e}")


async def update_service_catalog_from_page(page):
    try:
        found = await page.evaluate("""
            () => {
                const result = {};
                document.querySelectorAll('.pulsanti_tab .servizio[nome]').forEach(s => {
                    const nome = (s.getAttribute('nome') || '').trim();
                    if (!nome) return;

                    const key = nome.toLowerCase();
                    result[key] = {
                        id: s.id || '',
                        nome: nome,
                        tempo_operatore: parseInt(s.getAttribute('tempo_operatore') || '0', 10),
                        tempo_cliente: parseInt(s.getAttribute('tempo_cliente') || '0', 10)
                    };
                });
                return result;
            }
        """)

        if found and isinstance(found, dict):
            for key, info in found.items():
                config.service_catalog["services"][key] = info

            config.service_catalog["updated_at"] = datetime.utcnow().isoformat()
            from utils import save_service_catalog
            save_service_catalog()
            config.logger.info(f"🧴 Service catalog updated: {list(found.keys())[:10]}")

    except Exception as e:
        config.logger.warning(f"Failed to update service catalog from page: {e}")


async def extract_service_operator_durations_from_page(page) -> dict:
    """
    Reads visible service buttons from Wegest and builds:
    { 'taglio': 25, 'colore': 30, ... }
    using tempo_operatore from .pulsanti_tab .servizio[nome]
    """
    durations = await page.evaluate("""
        () => {
            const map = {};
            document.querySelectorAll('.pulsanti_tab .servizio').forEach(s => {
                const nome = (s.getAttribute('nome') || '').toLowerCase().trim();
                const tempoOperatore = parseInt(s.getAttribute('tempo_operatore') || '0', 10);
                if (nome) {
                    map[nome] = tempoOperatore;
                }
            });
            return map;
        }
    """)
    return durations or {}


async def scrape_day_availability_from_page(
    page,
    preferred_date: str,
    operator_preference: str = "prima disponibile",
    services: list[str] | None = None,
    service: str | None = None
) -> dict:
    from datetime import datetime
    from utils import (
        normalize_requested_services,
        compute_valid_start_times,
        quarter_time_to_minutes,
        minutes_to_quarter_time,
        ceil_to_quarter
    )

    target = datetime.strptime(preferred_date, "%Y-%m-%d")
    day, month, year = target.day, target.month, target.year
    day_name = target.strftime("%A")

    requested_services = normalize_requested_services(service, services or [])

    config.logger.info(f"Scraping date in existing session: {day}/{month}/{year} ({day_name})")
    config.logger.info(f"Requested services for availability: {requested_services}")

    await dismiss_system_modals(page, "before-date")

    date_selector = f".data[giorno='{day}'][mese='{month}'][anno='{year}']"

    date_info = await page.evaluate(f"""
        () => {{
            const el = document.querySelector("{date_selector}");
            if (!el) return {{ exists: false }};
            return {{
                exists: true,
                classes: el.className,
                isOpen: el.classList.contains('aperto'),
                isClosed: el.classList.contains('chiuso')
            }};
        }}
    """)

    if not date_info or not date_info.get("exists"):
        return {
            "date": preferred_date,
            "day_name": day_name,
            "is_open": False,
            "message": "❌ Data non visibile nel calendario",
            "operators": []
        }

    if date_info.get("isClosed"):
        return {
            "date": preferred_date,
            "day_name": day_name,
            "is_open": False,
            "message": f"❌ Il salone è chiuso il {day_name}",
            "operators": []
        }

    await page.click(date_selector, timeout=10000)

    try:
        await page.wait_for_function(
            f"() => document.querySelectorAll(\".cella[giorno='{day}'][mese='{month}'][anno='{year}']\").length > 0",
            timeout=15000
        )
    except Exception:
        await page.click(date_selector, timeout=5000)
        await page.wait_for_timeout(3000)

    await page.wait_for_timeout(1500)
    await dismiss_system_modals(page, "after-date")

    # Operator names from real header
    op_names = await page.evaluate("""
        () => {
            const names = {};
            document.querySelectorAll('.operatori_nomi .operatore[id_operatore]').forEach(op => {
                const id = op.getAttribute('id_operatore');
                if (!id || id === '0') return;
                const nome = op.querySelector('.nome');
                if (nome) names[id] = nome.textContent.trim();
            });
            return names;
        }
    """)

    config.logger.info(f"Operator names found: {op_names}")

    # Read grid + appointment overlays
    grid_data = await page.evaluate(f"""
        () => {{
            const day = '{day}';
            const month = '{month}';
            const year = '{year}';

            const operators = [];

            const toMinutes = (h, m) => parseInt(h, 10) * 60 + parseInt(m, 10);

            const formatTime = (mins) => {{
                const h = Math.floor(mins / 60).toString().padStart(2, '0');
                const m = (mins % 60).toString().padStart(2, '0');
                return `${{h}}:${{m}}`;
            }};

            const expandQuarterHours = (startH, startM, endH, endM) => {{
                const out = [];
                let start = toMinutes(startH, startM);
                const end = toMinutes(endH, endM);

                start = Math.floor(start / 15) * 15;

                for (let t = start; t < end; t += 15) {{
                    out.push(formatTime(t));
                }}

                return out;
            }};

            const occupiedByOperator = {{}};

            document.querySelectorAll('.appuntamento[id_operatore]').forEach(app => {{
                const opId = app.getAttribute('id_operatore');
                if (!opId) return;

                const giorno = app.getAttribute('giorno_inizio');
                const mese = app.getAttribute('mese_inizio');
                const anno = app.getAttribute('anno_inizio');

                if (giorno !== String(day) || mese !== String(month).padStart(2, '0') || anno !== String(year)) {{
                    return;
                }}

                const h1 = app.getAttribute('ora_inizio');
                const m1 = app.getAttribute('minuto_inizio');
                const h2 = app.getAttribute('ora_fine_operatore');
                const m2 = app.getAttribute('minuto_fine_operatore');

                if (!h1 || !m1 || !h2 || !m2) return;

                const slots = expandQuarterHours(h1, m1, h2, m2);

                if (!occupiedByOperator[opId]) occupiedByOperator[opId] = new Set();
                slots.forEach(s => occupiedByOperator[opId].add(s));
            }});

            const columns = document.querySelectorAll('.operatore_orari[id_operatore]');

            for (const col of columns) {{
                const opId = col.getAttribute('id_operatore');
                if (opId === '0') continue;

                const isPresent = col.classList.contains('presente');

                const cells = col.querySelectorAll(
                    ".cella[giorno='" + day + "'][mese='" + month + "'][anno='" + year + "']"
                );

                const available = [];
                const occupied = [];
                const absent = [];

                const bookedSet = occupiedByOperator[opId] || new Set();

                for (const cell of cells) {{
                    const ora = cell.getAttribute('ora');
                    const minuto = cell.getAttribute('minuto');
                    const timeStr = ora.padStart(2, '0') + ':' + minuto.padStart(2, '0');

                    if (cell.classList.contains('assente')) {{
                        absent.push(timeStr);
                    }} else if (cell.classList.contains('occupata')) {{
                        occupied.push(timeStr);
                    }} else if (bookedSet.has(timeStr)) {{
                        occupied.push(timeStr);
                    }} else {{
                        available.push(timeStr);
                    }}
                }}

                operators.push({{
                    id: opId,
                    present: isPresent,
                    available_slots: available,
                    occupied_slots: occupied,
                    absent_slots: absent,
                    total_available: available.length,
                    total_occupied: occupied.length
                }});
            }}

            return operators;
        }}
    """)

    # ══════════════════════════════════════
    # SERVICE DURATION LOOKUP
    # Priority:
    #   1. self-updating service_catalog
    #   2. live DOM extraction
    #   3. hardcoded fallback
    # ══════════════════════════════════════
    live_service_durations = await extract_service_operator_durations_from_page(page)

    config.logger.info(f"Service catalog durations: {config.service_catalog.get('services', {})}")
    config.logger.info(f"Live scraped service durations: {live_service_durations}")

    required_operator_minutes = 0
    missing_service_durations = []

    for svc in requested_services:
        svc_l = svc.lower().strip()
        matched_duration = None

        # 1. exact from service_catalog
        catalog_services = config.service_catalog.get("services", {})
        if svc_l in catalog_services:
            matched_duration = int(catalog_services[svc_l].get("tempo_operatore", 0) or 0)

        # 2. fuzzy from service_catalog
        if matched_duration is None or matched_duration == 0:
            for known_name, info in catalog_services.items():
                if svc_l in known_name or known_name in svc_l:
                    matched_duration = int(info.get("tempo_operatore", 0) or 0)
                    if matched_duration > 0:
                        break

        # 3. exact from live DOM
        if matched_duration is None or matched_duration == 0:
            if svc_l in live_service_durations:
                matched_duration = int(live_service_durations[svc_l])

        # 4. fuzzy from live DOM
        if matched_duration is None or matched_duration == 0:
            for known_name, dur in live_service_durations.items():
                if svc_l in known_name or known_name in svc_l:
                    matched_duration = int(dur)
                    if matched_duration > 0:
                        break

        # 5. exact fallback map
        if matched_duration is None or matched_duration == 0:
            if svc_l in config.SERVICE_DURATION_FALLBACK:
                matched_duration = int(config.SERVICE_DURATION_FALLBACK[svc_l])

        # 6. fuzzy fallback map
        if matched_duration is None or matched_duration == 0:
            for known_name, dur in config.SERVICE_DURATION_FALLBACK.items():
                if svc_l in known_name or known_name in svc_l:
                    matched_duration = int(dur)
                    if matched_duration > 0:
                        break

        if matched_duration is None or matched_duration == 0:
            missing_service_durations.append(svc)
        else:
            required_operator_minutes += int(matched_duration)

    config.logger.info(f"Requested services: {requested_services}")
    config.logger.info(f"Required operator minutes: {required_operator_minutes}")
    if missing_service_durations:
        config.logger.warning(f"Missing durations for services: {missing_service_durations}")

    config.logger.info(f"Service operator durations: {live_service_durations}")
    config.logger.info(f"Required operator minutes: {required_operator_minutes}")
    if missing_service_durations:
        config.logger.warning(f"Missing durations for services: {missing_service_durations}")

    all_available = set()
    all_valid_start_times = set()
    operator_list = []

    for op in grid_data:
        op_id = op["id"]
        name = op_names.get(op_id, f"Operatore_{op_id}")

        op_pref = operator_preference.lower().strip()
        is_any_pref = op_pref in ("prima disponibile", "any", "anyone", "any available", "any available stylist", "any stylist", "chiunque", "indifferente")

        if not is_any_pref:
            if op_pref not in name.lower().strip():
                continue

        raw_slots = op["available_slots"]
        valid_start_times = compute_valid_start_times(raw_slots, required_operator_minutes)

        for slot in raw_slots:
            all_available.add(slot)

        for slot in valid_start_times:
            all_valid_start_times.add(slot)

        operator_list.append({
            "name": name,
            "id": op_id,
            "present": op["present"],
            "available_slots": raw_slots,
            "valid_start_times": valid_start_times,
            "occupied_slots": op["occupied_slots"],
            "total_available": op["total_available"],
            "total_occupied": op["total_occupied"]
        })

    sorted_times = sorted(all_available)
    sorted_valid_start_times = sorted(all_valid_start_times)

    hourly = {}
    for t in sorted_times:
        h = t.split(":")[0]
        hourly.setdefault(h, []).append(t)

    valid_hourly = {}
    for t in sorted_valid_start_times:
        h = t.split(":")[0]
        valid_hourly.setdefault(h, []).append(t)

    present_ops = [op for op in operator_list if op["present"]]
    total_slots = len(sorted_times)
    total_valid_start_times = len(sorted_valid_start_times)

    if requested_services:
        if total_valid_start_times > 0:
            first_time = sorted_valid_start_times[0]
            last_time = sorted_valid_start_times[-1]
            summary = (
                f"✅ {total_valid_start_times} orari di inizio validi per {', '.join(requested_services)} "
                f"con {len(present_ops)} operatori, dalle {first_time} alle {last_time}"
            )
        else:
            summary = f"❌ Nessun orario di inizio valido per {', '.join(requested_services)} in questa data"
    else:
        if total_slots > 0:
            first_time = sorted_times[0]
            last_time = sorted_times[-1]
            summary = f"✅ {total_slots} slot disponibili con {len(present_ops)} operatori, dalle {first_time} alle {last_time}"
        else:
            summary = "❌ Nessuno slot disponibile per questa data"

    return {
        "date": preferred_date,
        "day_name": day_name,
        "is_open": True,
        "requested_services": requested_services,
        "required_operator_minutes": required_operator_minutes,
        "operators": operator_list,
        "active_operators": [
            {
                "name": op["name"],
                "id": op["id"],
                "present": op["present"]
            }
            for op in operator_list
            if op.get("present")
        ],
        "all_available_times": sorted_times,
        "all_valid_start_times": sorted_valid_start_times,
        "hourly_summary": hourly,
        "valid_hourly_summary": valid_hourly,
        "total_available_slots": total_slots,
        "total_valid_start_times": total_valid_start_times,
        "total_operators_present": len(present_ops),
        "summary": summary
    }
