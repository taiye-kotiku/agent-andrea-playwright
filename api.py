"""
FastAPI routes for Agent Andrea
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.gzip import GZipMiddleware
import config
from config import app, API_SECRET, DEBUG_SCREENSHOTS, screenshots, html_dumps, logger, booking_lock

app.add_middleware(GZipMiddleware, minimum_size=1000)
from api_models import (
    BookingRequest,
    UpdateBookingContextRequest,
    FinalizeBookingRequest,
    GetBookingContextRequest,
    AvailabilityRequest,
    CheckBookingOptionsRequest,
    PrepareLiveSessionRequest,
    ServiceDurationRequest
)
from booking import run_wegest_booking
from availability import run_availability_check
from utils import normalize_requested_services, get_missing_booking_fields, load_cache_from_disk
from session_manager import snap, dump_html, warm_pool_on_startup, cleanup_idle_wegest_sessions, cleanup_idle_pool_sessions, dismiss_system_modals, assign_idle_pool_session_to_conversation, adaptive_modal_scan
from utils import cleanup_expired_call_states
from catalog import extract_service_operator_durations_from_page
from datetime import datetime
from typing import Any
import base64
import asyncio


@app.get("/")
async def root():
    return {"status": "ok", "service": "Agent Andrea Wegest Booking"}

@app.get("/health")
async def health():
    return {"status": "ok", "service": "Agent Andrea Wegest Booking"}


@app.post("/booking-status")
async def booking_status(request: Request):
    """Check the current live booking state for a conversation."""
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id required")

    from session_manager import get_assigned_pool_session
    session = await get_assigned_pool_session(conversation_id)
    if not session:
        return {"success": False, "message": "No session assigned", "conversation_id": conversation_id}

    bs = session.booking_state
    from booking import detect_page_state
    page_state = {"phase": "unknown"}
    if session.page and not session.page.is_closed():
        try:
            page_state = await detect_page_state(session.page)
        except Exception as e:
            page_state = {"phase": "error", "error": str(e)}

    return {
        "success": True,
        "conversation_id": conversation_id,
        "booking_state": {
            "phase": bs.phase if bs else "none",
            "date": bs.booked_date if bs else None,
            "time": bs.booked_time if bs else None,
            "customer": bs.customer_name if bs else None,
            "services": bs.services if bs else [],
            "operator": bs.operator_preference if bs else None
        } if bs else None,
        "page_state": page_state
    }


@app.post("/advance-booking")
async def advance_booking_endpoint(request: Request):
    """Advance the live booking one step forward. Call after each /update-booking-context
    to make the bot behave like a real receptionist — advancing as info arrives.

    If context changed (e.g., customer changed date), the booking resets and starts fresh.
    """
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    conversation_id = body.get("conversation_id")
    if not conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id required")

    from session_manager import get_assigned_pool_session, get_live_session_for_conversation
    from booking import sync_booking_context, detect_page_state, advance_to_date_selected, advance_to_time_selected, advance_to_customer_selected, advance_to_phone_confirmed, advance_to_services_selected, advance_to_operator_selected, advance_to_confirmed, BookingState

    session = await get_live_session_for_conversation(conversation_id)
    if not session:
        return {"success": False, "message": "No live session for this conversation"}

    try:
        async with session.lock:
            # Get current context from call state
            from utils import get_call_state
            state = await get_call_state(conversation_id)

            # Build booking state from stored context
            phone = state.get("caller_phone") or ""
            if phone and phone.startswith("+39"):
                phone = phone[3:]
            elif phone and phone.startswith("0039"):
                phone = phone[4:]

            new_state = BookingState(
                phase="idle",
                booked_date=state.get("preferred_date"),
                booked_time=state.get("preferred_time"),
                customer_name=state.get("customer_name"),
                customer_phone=phone,
                services=state.get("services") or [],
                operator_preference=state.get("operator_preference", "prima disponibile")
            )

            # Sync context — detects changes, resets if needed
            sync_result = await sync_booking_context(session, {
                "date": new_state.booked_date,
                "time": new_state.booked_time,
                "customer_name": new_state.customer_name,
                "customer_phone": new_state.customer_phone,
                "services": new_state.services,
                "operator_preference": new_state.operator_preference
            })

            # Detect current page state
            page_state = await detect_page_state(session.page)
            logger.info(f"📊 Page state: {page_state['phase']} | Booking phase: {sync_result['current_phase']}")

            # Scan for any modals before advancing
            modal_report = await adaptive_modal_scan(session.page, "advance-check")
            if modal_report["blocking"]:
                logger.warning(f"🚧 Blocking modals before advance: {modal_report['modals']}")

            # Advance one step if possible
            bs = session.booking_state
            advanced_to = None

            if sync_result["can_advance"]:
                next_phase = sync_result["next_phase"]

                if next_phase == "date_selected" and bs.booked_date:
                    await advance_to_date_selected(session.page, bs)
                    bs.phase = "date_selected"
                    advanced_to = "date_selected"

                elif next_phase == "time_selected" and bs.booked_time:
                    await advance_to_time_selected(session.page, bs)
                    bs.phase = "time_selected"
                    advanced_to = "time_selected"

                elif next_phase == "customer_selected" and bs.customer_name:
                    await advance_to_customer_selected(session.page, bs)
                    bs.phase = "customer_selected"
                    advanced_to = "customer_selected"

                elif next_phase == "phone_confirmed":
                    await advance_to_phone_confirmed(session.page, bs)
                    bs.phase = "phone_confirmed"
                    advanced_to = "phone_confirmed"

                elif next_phase == "services_selected" and bs.services:
                    await advance_to_services_selected(session.page, bs)
                    bs.phase = "services_selected"
                    advanced_to = "services_selected"

                elif next_phase == "ready_to_confirm":
                    await advance_to_operator_selected(session.page, bs)
                    bs.phase = "ready_to_confirm"
                    advanced_to = "ready_to_confirm"

                elif next_phase == "confirmed":
                    success = await advance_to_confirmed(session.page, bs)
                    bs.phase = "confirmed" if success else "ready_to_confirm"
                    advanced_to = "confirmed"

            return {
                "success": True,
                "conversation_id": conversation_id,
                "context_changed": sync_result["changed"],
                "previous_phase": sync_result["current_phase"],
                "current_phase": bs.phase,
                "advanced_to": advanced_to,
                "can_advance_again": bs.phase not in ("confirmed", "ready_to_confirm"),
                "modals_detected": len(modal_report["modals"]),
                "booking_data": sync_result["booking_data"]
            }
    except Exception as e:
        logger.error(f"❌ /advance-booking error: {e}")
        # Dump HTML for debugging
        try:
            if session and session.page:
                await dump_html(session.page, f"advance_booking_error_{conversation_id}")
        except Exception:
            pass
        return {
            "success": False,
            "error": str(e),
            "message": f"Advance booking failed: {e}",
            "conversation_id": conversation_id
        }


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


@app.get("/html-dumps", response_class=HTMLResponse)
async def view_html_dumps():
    if not html_dumps:
        return "<h2>No HTML dumps yet</h2>"
    html = "<html><body style='background:#111;color:#fff;font-family:monospace;padding:20px'>"
    html += "<h1>📄 HTML Dumps</h1>"
    for name, data in html_dumps.items():
        html += f"<h3>📄 {name}</h3>"
        html += f"<pre style='background:#222;padding:15px;overflow-x:auto;white-space:pre-wrap;word-wrap:break-word;border:1px solid #555;max-height:400px;overflow-y:scroll'>{data[:50000]}</pre><br>"
    html += "</body></html>"
    return html


@app.post("/clear-debug")
async def clear_debug():
    screenshots.clear()
    html_dumps.clear()
    return {"success": True, "message": "Debug data cleared"}


@app.post("/book")
async def book_appointment(request: Request, booking: BookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    screenshots.clear()
    logger.info(f"📅 Booking: {booking.customer_name} | {booking.service or booking.services} | {booking.preferred_date} {booking.preferred_time}")

    if booking.conversation_id:
        from utils import update_call_state
        await update_call_state(booking.conversation_id, {
            "customer_name": booking.customer_name,
            "caller_phone": booking.caller_phone,
            "preferred_date": booking.preferred_date,
            "preferred_time": booking.preferred_time,
            "operator_preference": booking.operator_preference,
            "services": normalize_requested_services(booking.service, booking.services),
            "booking_confirmed": True
        })
        logger.info(f"🧠 Updated call state from booking for {booking.conversation_id}")

    result = await run_wegest_booking(booking)

    if booking.conversation_id and result.get("success"):
        from utils import clear_call_state
        await clear_call_state(booking.conversation_id)

    return result


@app.post("/check-availability")
async def check_availability(request: Request, avail: AvailabilityRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    screenshots.clear()
    logger.info(f"🔍 Availability check: {avail.preferred_date}")

    result = await run_availability_check(avail)

    if avail.conversation_id:
        from utils import update_call_state
        await update_call_state(avail.conversation_id, {
            "preferred_date": avail.preferred_date,
            "operator_preference": avail.operator_preference,
            "services": normalize_requested_services(avail.service, avail.services),
            "last_availability_result": result
        })
        logger.info(f"🧠 Updated call state from availability for {avail.conversation_id}")

    return result


@app.post("/invalidate-cache")
async def invalidate_cache(request: Request):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    body = await request.json()
    date_str = body.get("preferred_date")
    if not date_str:
        raise HTTPException(status_code=400, detail="preferred_date required")

    from utils import invalidate_cached_day
    await invalidate_cached_day(date_str)
    return {"ok": True, "invalidated": date_str}


@app.post("/get-service-duration")
async def get_service_duration_endpoint(request: Request, payload: ServiceDurationRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    requested_services = normalize_requested_services(payload.service, payload.services)

    if not requested_services:
        return {
            "success": False,
            "message": "No service provided",
            "services": []
        }

    catalog_services = config.service_catalog.get("services", {})
    results = []

    for svc in requested_services:
        svc_l = svc.lower().strip()
        matched = None

        # exact from catalog
        if svc_l in catalog_services:
            matched = catalog_services[svc_l]

        # fuzzy from catalog
        if matched is None:
            for known_name, info in catalog_services.items():
                if svc_l in known_name or known_name in svc_l:
                    matched = info
                    break

        # fallback if catalog misses
        if matched is None:
            from config import SERVICE_DURATION_FALLBACK
            fallback_duration = SERVICE_DURATION_FALLBACK.get(svc_l)
            if fallback_duration:
                matched = {
                    "nome": svc,
                    "tempo_operatore": fallback_duration,
                    "tempo_cliente": fallback_duration
                }

        if matched:
            results.append({
                "requested_service": svc,
                "resolved_service": matched.get("nome", svc),
                "tempo_operatore": matched.get("tempo_operatore", 0),
                "tempo_cliente": matched.get("tempo_cliente", 0)
            })
        else:
            results.append({
                "requested_service": svc,
                "resolved_service": None,
                "tempo_operatore": None,
                "tempo_cliente": None
            })

    # Build spoken summaries
    if len(results) == 1:
        r = results[0]
        if r["tempo_operatore"] is not None:
            spoken_summary_it = (
                f"Il servizio {r['resolved_service']} richiede circa "
                f"{r['tempo_operatore']} minuti di lavoro operatore"
            )
            spoken_summary_en = (
                f"The service {r['resolved_service']} requires about "
                f"{r['tempo_operatore']} minutes of operator time"
            )
        else:
            spoken_summary_it = f"Non sono riuscita a trovare la durata del servizio {r['requested_service']}"
            spoken_summary_en = f"I couldn't find the duration for the service {r['requested_service']}"
    else:
        known = [r for r in results if r["tempo_operatore"] is not None]
        total_operator = sum(r["tempo_operatore"] for r in known)
        service_names = ", ".join(r["resolved_service"] or r["requested_service"] for r in results)

        spoken_summary_it = (
            f"I servizi {service_names} richiedono circa {total_operator} minuti totali di lavoro operatore"
        )
        spoken_summary_en = (
            f"The services {service_names} require about {total_operator} total minutes of operator time"
        )

    return {
        "success": True,
        "services": results,
        "spoken_summary_it": spoken_summary_it,
        "spoken_summary_en": spoken_summary_en
    }


@app.post("/update-booking-context")
async def update_booking_context_endpoint(request: Request, payload: UpdateBookingContextRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    normalized_services = normalize_requested_services(payload.service, payload.services)

    updates = {}

    if normalized_services:
        updates["services"] = normalized_services

    if payload.operator_preference is not None:
        op_pref = payload.operator_preference.lower().strip()
        any_vals = {"prima disponibile", "any", "anyone", "any available", "any available stylist", "any stylist", "chiunque", "indifferente"}
        updates["operator_preference"] = "prima disponibile" if op_pref in any_vals else payload.operator_preference

    if payload.preferred_date is not None:
        from utils import normalize_date_to_iso
        updates["preferred_date"] = normalize_date_to_iso(payload.preferred_date)

    if payload.preferred_time is not None:
        updates["preferred_time"] = payload.preferred_time

    if payload.customer_name is not None:
        updates["customer_name"] = payload.customer_name

    if payload.caller_phone is not None:
        updates["caller_phone"] = payload.caller_phone

    from utils import update_call_state
    state = await update_call_state(payload.conversation_id, updates)
    missing_fields = get_missing_booking_fields(state)

    next_action = "ask_missing_fields" if missing_fields else "ready_for_availability_or_confirmation"

    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "booking_context": state,
        "missing_fields": missing_fields,
        "next_action": next_action
    }


@app.post("/get-booking-context")
async def get_booking_context_endpoint(request: Request, payload: GetBookingContextRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    from utils import get_call_state
    state = await get_call_state(payload.conversation_id)
    missing_fields = get_missing_booking_fields(state)

    if not state.get("preferred_date"):
        next_action = "ask_date"
    elif not state.get("preferred_time"):
        next_action = "check_availability_or_ask_time"
    elif missing_fields:
        next_action = "ask_missing_fields"
    else:
        next_action = "ready_for_confirmation_or_booking"

    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "booking_context": state,
        "missing_fields": missing_fields,
        "next_action": next_action
    }


@app.post("/check-booking-options")
async def check_booking_options_endpoint(request: Request, payload: CheckBookingOptionsRequest):
    try:
        logger.info(f"🔍 check_booking-options called with conversation_id={payload.conversation_id}")
        auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
        if auth != f"Bearer {API_SECRET}":
            raise HTTPException(status_code=401, detail="Unauthorized")

        from utils import get_call_state, update_call_state
        from session_manager import get_assigned_pool_session
        state = await get_call_state(payload.conversation_id)
        
        # Get session for potential HTML dump on error
        session = await get_assigned_pool_session(payload.conversation_id)
        
        logger.info(f"🔍 State for {payload.conversation_id}: preferred_date={state.get('preferred_date')}, services={state.get('services')}")

        services = state.get("services") or []
        operator_preference = state.get("operator_preference") or "prima disponibile"
        preferred_date = state.get("preferred_date")
        preferred_time = state.get("preferred_time")

        if not preferred_date:
            return {
                "success": False,
                "conversation_id": payload.conversation_id,
                "booking_context": state,
                "missing_fields": ["preferred_date"],
                "next_action": "ask_date",
                "message": "Preferred date is missing"
            }

        # Build an AvailabilityRequest from stored state
        avail_request = AvailabilityRequest(
            preferred_date=preferred_date,
            operator_preference=operator_preference,
            services=services,
            service=None,
            conversation_id=payload.conversation_id
        )

        availability_result = await run_availability_check(avail_request)

        # Save latest availability result back to call state
        updated_state = await update_call_state(payload.conversation_id, {
            "last_availability_result": availability_result
        })

        from utils import build_operator_time_suggestions
        exact_operator_matches = []
        closest_operator_options = []

        preferred_time = state.get("preferred_time")
        operator_preference = state.get("operator_preference") or "prima disponibile"

        if preferred_time and operator_preference.lower() == "prima disponibile":
            exact_operator_matches, closest_operator_options = build_operator_time_suggestions(
                availability_result.get("operators", []),
                preferred_time
            )

        # Decide next action
        if not availability_result.get("is_open", False):
            next_action = "choose_day"
        elif availability_result.get("requested_services"):
            valid_times = availability_result.get("all_valid_start_times", [])
            if valid_times:
                next_action = "choose_time"
            else:
                next_action = "choose_operator_or_day"
        else:
            available_times = availability_result.get("all_available_times", [])
            if available_times:
                next_action = "choose_time"
            else:
                next_action = "choose_operator_or_day"

        # Build spoken summary fallback
        requested_services = availability_result.get("requested_services", [])
        all_valid_start_times = availability_result.get("all_valid_start_times", [])
        all_available_times = availability_result.get("all_available_times", [])

        if requested_services:
            times_for_speech = all_valid_start_times[:3]
        else:
            times_for_speech = all_available_times[:3]

        # Special case: requested time + first available operator
        if preferred_time and operator_preference.lower() == "prima disponibile":
            if exact_operator_matches:
                names = ", ".join([m["name"] for m in exact_operator_matches])
                spoken_summary_it = (
                    f"Alle {preferred_time} sono disponibili {names}. Vuoi prenotare con uno di loro?"
                )
                spoken_summary_en = (
                    f"At {preferred_time}, {names} are available. Would you like to book with one of them?"
                )
                next_action = "choose_operator_or_confirm_time"
            elif closest_operator_options:
                opts = []
                for opt in closest_operator_options[:3]:
                    opts.append(f"{opt['name']} alle {opt['time']}")
                opts_str = ", ".join(opts)

                spoken_summary_it = (
                    f"Nessun operatore è disponibile esattamente alle {preferred_time}. "
                    f"Le alternative più vicine sono: {opts_str}. Quale preferisci?"
                )
                spoken_summary_en = (
                    f"No operator is available exactly at {preferred_time}. "
                    f"The closest alternatives are: {opts_str}. Which would you prefer?"
                )
                next_action = "choose_operator_or_time"
            else:
                spoken_summary_it = (
                    f"Non abbiamo disponibilità intorno alle {preferred_time}. Vuoi provare un altro orario o un altro giorno?"
                )
                spoken_summary_en = (
                    f"We don't have availability around {preferred_time}. Would you like to try another time or a different day?"
                )
                next_action = "choose_time_or_day"

        else:
            if requested_services:
                if times_for_speech:
                    spoken_summary_it = (
                        f"Abbiamo disponibilità per {', '.join(requested_services)} "
                        f"il {preferred_date} alle {', '.join(times_for_speech)}. Quale orario preferisci?"
                    )
                    spoken_summary_en = (
                        f"We have availability for {', '.join(requested_services)} "
                        f"on {preferred_date} at {', '.join(times_for_speech)}. Which time would you prefer?"
                    )
                else:
                    spoken_summary_it = (
                        f"Non abbiamo disponibilità per {', '.join(requested_services)} "
                        f"in quella data. Vuoi provare un altro giorno o un altro operatore?"
                    )
                    spoken_summary_en = (
                        f"We don't have availability for {', '.join(requested_services)} "
                        f"on that date. Would you like to try another day or another operator?"
                    )
            else:
                if times_for_speech:
                    spoken_summary_it = (
                        f"Abbiamo disponibilità il {preferred_date} alle {', '.join(times_for_speech)}. "
                        f"Quale orario preferisci?"
                    )
                    spoken_summary_en = (
                        f"We have availability on {preferred_date} at {', '.join(times_for_speech)}. "
                        f"Which time would you prefer?"
                    )
                else:
                    spoken_summary_it = (
                        f"Non abbiamo disponibilità in quella data. Vuoi provare un altro giorno o un altro operatore?"
                    )
                    spoken_summary_en = (
                        f"We don't have availability on that date. Would you like to try another day or another operator?"
                    )

        return {
            "success": True,
            "conversation_id": payload.conversation_id,
            "booking_context": updated_state,
            "availability": availability_result,
            "operators_available_at_requested_time": exact_operator_matches,
            "closest_operator_options": closest_operator_options,
            "spoken_summary_it": spoken_summary_it,
            "spoken_summary_en": spoken_summary_en,
            "next_action": next_action
        }
    except Exception as e:
        logger.error(f"❌ Error in check_booking_options: {e}")
        # Dump HTML for debugging
        try:
            if session and session.page:
                await dump_html(session.page, f"check_options_error_{payload.conversation_id}")
        except Exception:
            pass
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "error": str(e),
            "message": f"Error: {e}",
            "next_action": "retry_or_apologize"
        }


@app.post("/finalize-booking")
async def finalize_booking_endpoint(request: Request, payload: FinalizeBookingRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    from utils import get_call_state, get_missing_booking_fields, clear_call_state
    state = await get_call_state(payload.conversation_id)
    missing_fields = get_missing_booking_fields(state)

    if missing_fields:
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "booking_context": state,
            "missing_fields": missing_fields,
            "next_action": "ask_missing_fields",
            "message": "Cannot finalize booking because required fields are missing"
        }

    async with booking_lock:
        # Sync context to session before booking (handles changes, resets if needed)
        from booking import sync_booking_context
        from session_manager import get_assigned_pool_session
        session = await get_assigned_pool_session(payload.conversation_id)
        if session:
            phone = state.get("caller_phone") or ""
            if phone and phone.startswith("+39"):
                phone = phone[3:]
            elif phone and phone.startswith("0039"):
                phone = phone[4:]
            await sync_booking_context(session, {
                "date": state.get("preferred_date"),
                "time": state.get("preferred_time"),
                "customer_name": state.get("customer_name"),
                "customer_phone": phone,
                "services": state.get("services") or [],
                "operator_preference": state.get("operator_preference", "prima disponibile")
            })

        booking_request = BookingRequest(
            customer_name=state["customer_name"],
            caller_phone=state["caller_phone"],
            service=None,
            services=state.get("services") or [],
            operator_preference=state.get("operator_preference") or "prima disponibile",
            preferred_date=state["preferred_date"],
            preferred_time=state["preferred_time"],
            conversation_id=payload.conversation_id
        )

        result = await run_wegest_booking(booking_request)

    if result.get("success"):
        await clear_call_state(payload.conversation_id)
        return {
            "success": True,
            "conversation_id": payload.conversation_id,
            "message": "Appointment booked successfully",
            "booking_result": result,
            "next_action": "booking_complete"
        }

    return {
        "success": False,
        "conversation_id": payload.conversation_id,
        "message": result.get("message", "Booking failed"),
        "booking_result": result,
        "next_action": "retry_or_apologize"
    }


@app.post("/prepare-live-session")
async def prepare_live_session_endpoint(request: Request, payload: PrepareLiveSessionRequest):
    auth = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if auth != f"Bearer {API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    if not payload.conversation_id:
        raise HTTPException(status_code=400, detail="conversation_id required")

    try:
        try:
            session = await assign_idle_pool_session_to_conversation(payload.conversation_id)
        except Exception:
            logger.info("🔥 No warm session in pool, warming on-demand...")
            from session_manager import create_and_warm_pool_session, wegest_pool, pool_lock, POOL_SIZE, reset_pool_session
            async with pool_lock:
                for i in range(1, POOL_SIZE + 1):
                    pool_id = f"pool_{i}"
                    existing = wegest_pool.get(pool_id)
                    if existing and (not existing.page or existing.page.is_closed()):
                        await reset_pool_session(pool_id)
                        await create_and_warm_pool_session(pool_id)
                        break
                    elif not existing:
                        await create_and_warm_pool_session(pool_id)
                        break
                    elif existing.assigned_conversation_id != payload.conversation_id:
                        await reset_pool_session(pool_id)
                        await create_and_warm_pool_session(pool_id)
                        break
                else:
                    pool_id = f"pool_1"
                    await reset_pool_session(pool_id)
                    await create_and_warm_pool_session(pool_id)
            session = await assign_idle_pool_session_to_conversation(payload.conversation_id)

        async with session.lock:
            # Verify still alive
            try:
                if not session.page or session.page.is_closed():
                    raise Exception("Pool session page is closed")

                state = await session.page.evaluate("""() => {
                    const loginPanel = document.getElementById('pannello_login');
                    const agendaBtn = document.querySelector("[pannello='pannello_agenda']");
                    const menu = document.getElementById('menu');

                    return {
                        loginVisible: loginPanel ? getComputedStyle(loginPanel).display !== 'none' : false,
                        hasAgendaButton: !!agendaBtn,
                        hasMenu: !!menu
                    };
                }""")

                if state.get("loginVisible", False) or not (state.get("hasAgendaButton", False) or state.get("hasMenu", False)):
                    raise Exception("Pool session is no longer ready")

                session.last_used_at = datetime.utcnow()

                from utils import update_call_state
                await update_call_state(payload.conversation_id, {
                    "session_prepared": True
                })

                return {
                    "success": True,
                    "conversation_id": payload.conversation_id,
                    "session_ready": True,
                    "message": "Live Wegest session is ready"
                }

            except Exception as session_err:
                logger.warning(f"Assigned pool session failed health check: {session_err}")
                raise session_err

    except Exception as e:
        logger.error(f"❌ Session warm-up failed for {payload.conversation_id}: {e}")
        return {
            "success": False,
            "conversation_id": payload.conversation_id,
            "session_ready": False,
            "message": f"No live session available right now: {e}"
        }


@app.on_event("startup")
async def startup_event():
    load_cache_from_disk()
    from utils import load_operator_catalog, load_service_catalog
    load_operator_catalog()
    load_service_catalog()
    asyncio.create_task(cleanup_call_states_forever())
    asyncio.create_task(cleanup_wegest_sessions_forever())
    asyncio.create_task(cleanup_pool_sessions_forever())
    asyncio.create_task(warm_pool_on_startup())
    logger.info("🚀 App started (background refresh disabled, warm pool starting)")


async def cleanup_call_states_forever():
    while True:
        try:
            from utils import cleanup_expired_call_states
            await cleanup_expired_call_states()
        except Exception as e:
            logger.warning(f"Call state cleanup failed: {e}")
        await asyncio.sleep(300)  # every 5 minutes


async def cleanup_wegest_sessions_forever():
    while True:
        try:
            await cleanup_idle_wegest_sessions()
        except Exception as e:
            logger.warning(f"Wegest session cleanup failed: {e}")
        await asyncio.sleep(300)


async def cleanup_pool_sessions_forever():
    while True:
        try:
            await cleanup_idle_pool_sessions()
        except Exception as e:
            logger.warning(f"Pool session cleanup failed: {e}")
        await asyncio.sleep(300)
