"""
Availability check logic for Agent Andrea
"""

import config
from config import logger, API_SECRET, service_catalog, SERVICE_DURATION_FALLBACK
from session_manager import get_live_session_for_conversation, get_assigned_pool_session, dismiss_system_modals, snap
from utils import (
    normalize_requested_services,
    compute_valid_start_times,
    quarter_time_to_minutes,
    minutes_to_quarter_time,
    ceil_to_quarter
)
from catalog import scrape_day_availability_from_page
from typing import Any, Optional
from datetime import datetime, timedelta
import asyncio


async def run_availability_check(request: 'AvailabilityRequest') -> dict:
    requested_services = normalize_requested_services(request.service, request.services)

    assigned_session = None
    if request.conversation_id:
        assigned_session = await get_assigned_pool_session(request.conversation_id)

    # 1. If warm live session is assigned, prefer live
    if assigned_session:
        logger.info(f"🟢 Assigned warm session available — bypassing cache for {request.preferred_date}")
        fresh = await run_live_availability_check(request)

        if fresh and fresh.get("is_open") is True and "operators" in fresh:
            from utils import set_cached_day
            await set_cached_day(request.preferred_date, fresh)
            from config import availability_cache_ttl
            availability_cache_ttl[request.preferred_date] = datetime.utcnow()

        return {
            **fresh,
            "source": "live"
        }

    # 2. Otherwise use cache if available
    from utils import get_cached_day
    from config import availability_cache_ttl, AVAILABILITY_CACHE_TTL_SECONDS
    now = datetime.utcnow()

    # Check if cached entry is still within TTL
    cached_ts = availability_cache_ttl.get(request.preferred_date)
    if cached_ts and (now - cached_ts).total_seconds() > AVAILABILITY_CACHE_TTL_SECONDS:
        availability_cache_ttl.pop(request.preferred_date, None)
        logger.info(f"⏰ Availability cache TTL expired for {request.preferred_date}")

    cached = await get_cached_day(request.preferred_date)

    if cached:
        logger.info(f"⚡ Availability cache HIT for {request.preferred_date}")

        operator_pref = (request.operator_preference or "prima disponibile").lower().strip()

        catalog_services = service_catalog.get("services", {})
        required_operator_minutes = 0
        missing_service_durations = []

        for svc in requested_services:
            svc_l = svc.lower().strip()
            matched_duration = None

            # 1. exact from service catalog
            if svc_l in catalog_services:
                matched_duration = int(catalog_services[svc_l].get("tempo_operatore", 0) or 0)

            # 2. fuzzy from service catalog
            if matched_duration is None or matched_duration == 0:
                for known_name, info in catalog_services.items():
                    if svc_l in known_name or known_name in svc_l:
                        matched_duration = int(info.get("tempo_operatore", 0) or 0)
                        if matched_duration > 0:
                            break

            # 3. exact fallback
            if matched_duration is None or matched_duration == 0:
                if svc_l in SERVICE_DURATION_FALLBACK:
                    matched_duration = int(SERVICE_DURATION_FALLBACK[svc_l])

            # 4. fuzzy fallback
            if matched_duration is None or matched_duration == 0:
                for known_name, dur in SERVICE_DURATION_FALLBACK.items():
                    if svc_l in known_name or known_name in svc_l:
                        matched_duration = int(dur)
                        if matched_duration > 0:
                            break

            if matched_duration is None or matched_duration == 0:
                missing_service_durations.append(svc)
            else:
                required_operator_minutes += int(matched_duration)

        logger.info(f"Cache-hit requested services: {requested_services}")
        logger.info(f"Cache-hit required operator minutes: {required_operator_minutes}")
        if missing_service_durations:
            logger.warning(f"Cache-hit missing durations for services: {missing_service_durations}")

        filtered_ops = []
        all_times = set()
        all_valid_times = set()

        for op in cached.get("operators", []):
            name = op.get("name", "")

            is_any_pref = operator_pref in ("prima disponibile", "any", "anyone", "any available", "any available stylist", "any stylist", "chiunque", "indifferente")

            if not is_any_pref:
                if operator_pref not in name.lower().strip():
                    continue

            raw_slots = op.get("available_slots", [])
            valid_start_times = compute_valid_start_times(raw_slots, required_operator_minutes)

            for t in raw_slots:
                all_times.add(t)

            for t in valid_start_times:
                all_valid_times.add(t)

            filtered_ops.append({
                **op,
                "valid_start_times": valid_start_times
            })

        sorted_times = sorted(all_times)
        sorted_valid_times = sorted(all_valid_times)

        hourly = {}
        for t in sorted_times:
            h = t.split(":")[0]
            hourly.setdefault(h, []).append(t)

        valid_hourly = {}
        for t in sorted_valid_times:
            h = t.split(":")[0]
            valid_hourly.setdefault(h, []).append(t)

        present_ops = [op for op in filtered_ops if op.get("present")]
        total_slots = len(sorted_times)
        total_valid_start_times = len(sorted_valid_times)

        if requested_services:
            if total_valid_start_times > 0:
                first_time = sorted_valid_times[0]
                last_time = sorted_valid_times[-1]
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
                summary = (
                    f"✅ {total_slots} slot disponibili con {len(present_ops)} operatori, "
                    f"dalle {first_time} alle {last_time}"
                )
            else:
                summary = "❌ Nessuno slot disponibile per questa data"

        return {
            **cached,
            "requested_services": requested_services,
            "required_operator_minutes": required_operator_minutes,
            "operators": filtered_ops,
            "active_operators": [
                {
                    "name": op["name"],
                    "id": op["id"],
                    "present": op["present"]
                }
                for op in filtered_ops
                if op.get("present")
            ],
            "all_available_times": sorted_times,
            "all_valid_start_times": sorted_valid_times,
            "hourly_summary": hourly,
            "valid_hourly_summary": valid_hourly,
            "total_available_slots": total_slots,
            "total_valid_start_times": total_valid_start_times,
            "total_operators_present": len(present_ops),
            "summary": summary,
            "source": "cache"
        }

    # 3. No assigned warm session and no cache -> no live pool session path
    logger.info(f"🐢 Availability cache MISS for {request.preferred_date}")
    return {
        "success": False,
        "date": request.preferred_date,
        "is_open": False,
        "message": "No warm live session available and no cached availability available",
        "available_slots": [],
        "operators": [],
        "source": "none"
    }


async def run_live_availability_check(request: 'AvailabilityRequest') -> dict:
    if not request.conversation_id:
        raise Exception("conversation_id is required for live availability checks")

    session = await get_live_session_for_conversation(request.conversation_id)

    async with session.lock:
        try:
            page = session.page

            # verify session still healthy
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
                raise Exception("Assigned pool session is not ready")

            session.last_used_at = datetime.utcnow()

            result = await scrape_day_availability_from_page(
                page,
                request.preferred_date,
                request.operator_preference,
                services=request.services,
                service=request.service
            )

            return result

        except Exception as e:
            logger.error(f"❌ Availability error for {request.conversation_id}: {e}")
            try:
                await snap(session.page, "avail_ERROR", force=True)
            except Exception:
                pass

            return {
                "date": request.preferred_date,
                "is_open": False,
                "error": str(e),
                "message": f"❌ Errore: {e}",
                "available_slots": [],
                "operators": [],
                "screenshots_url": "https://agent-andrea-playwright-production.up.railway.app/screenshots"
            }


async def refresh_availability_cache_forever():
    await asyncio.sleep(10)  # let app boot first

    while True:
        try:
            logger.info("🔄 Background availability refresh starting...")

            today = datetime.utcnow().date()
            dates_to_refresh = []

            # Today + tomorrow
            for i in range(2):
                d = today + timedelta(days=i)
                dates_to_refresh.append(d.strftime("%Y-%m-%d"))

            # Next 7 days
            for i in range(2, 9):
                d = today + timedelta(days=i)
                dates_to_refresh.append(d.strftime("%Y-%m-%d"))

            for date_str in dates_to_refresh:
                try:
                    from api_models import AvailabilityRequest
                    req = AvailabilityRequest(
                        preferred_date=date_str,
                        operator_preference="prima disponibile"
                    )
                    fresh = await run_live_availability_check(req)

                    if fresh and fresh.get("is_open") is True and "operators" in fresh:
                        from utils import set_cached_day
                        await set_cached_day(date_str, fresh)
                        logger.info(f"✅ Refreshed cache for {date_str}")
                    else:
                        logger.info(f"ℹ️ Skipped cache for {date_str} (closed/no data)")
                except Exception as e:
                    logger.warning(f"Failed refreshing {date_str}: {e}")

            logger.info("✅ Background availability refresh complete")

            # Sleep 30 minutes
            await asyncio.sleep(1800)

        except Exception as e:
            logger.error(f"Background refresh loop error: {e}")
            await asyncio.sleep(300)


async def delayed_refresh_start():
    await asyncio.sleep(120)
    await refresh_availability_cache_forever()
