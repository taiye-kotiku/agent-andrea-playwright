"""
Booking context management endpoints.
"""

from fastapi import APIRouter, Depends, Request
from typing import Dict, Any

from app.core.auth import verify_api_secret
from app.models import (
    UpdateBookingContextRequest,
    GetBookingContextRequest,
    CheckBookingOptionsRequest,
    AvailabilityRequest
)
from app.services.call_state import get_call_state, update_call_state
from app.services.wegest import run_availability_check, build_operator_time_suggestions
from app.utils.helpers import get_missing_booking_fields, normalize_requested_services
from app.utils.time_utils import normalize_date_to_iso

router = APIRouter(prefix="/api", tags=["context"])


@router.post("/update-booking-context")
async def update_booking_context(
    request: Request,
    payload: UpdateBookingContextRequest,
    _: str = Depends(verify_api_secret)
):
    """Update booking context for a conversation."""
    normalized_services = normalize_requested_services(payload.service, payload.services)

    updates = {}
    if normalized_services:
        updates["services"] = normalized_services
    if payload.operator_preference is not None:
        updates["operator_preference"] = payload.operator_preference
    if payload.preferred_date is not None:
        updates["preferred_date"] = normalize_date_to_iso(payload.preferred_date)
    if payload.preferred_time is not None:
        updates["preferred_time"] = payload.preferred_time
    if payload.customer_name is not None:
        updates["customer_name"] = payload.customer_name
    if payload.caller_phone is not None:
        updates["caller_phone"] = payload.caller_phone

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


@router.post("/get-booking-context")
async def get_booking_context(
    request: Request,
    payload: GetBookingContextRequest,
    _: str = Depends(verify_api_secret)
):
    """Get current booking context for a conversation."""
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


@router.post("/check-booking-options")
async def check_booking_options(
    request: Request,
    payload: CheckBookingOptionsRequest,
    _: str = Depends(verify_api_secret)
):
    """Check booking options based on current context."""
    state = await get_call_state(payload.conversation_id)

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

    avail_request = AvailabilityRequest(
        preferred_date=preferred_date,
        operator_preference=operator_preference,
        services=services,
        service=None,
        conversation_id=payload.conversation_id
    )

    availability_result = await run_availability_check(avail_request)

    await update_call_state(payload.conversation_id, {
        "last_availability_result": availability_result
    })

    exact_operator_matches = []
    closest_operator_options = []

    if preferred_time and operator_preference.lower() == "prima disponibile":
        exact_operator_matches, closest_operator_options = build_operator_time_suggestions(
            availability_result.get("operators", []),
            preferred_time
        )

    # Determine next action
    if not availability_result.get("is_open", False):
        next_action = "choose_day"
    elif availability_result.get("requested_services"):
        valid_times = availability_result.get("all_valid_start_times", [])
        next_action = "choose_time" if valid_times else "choose_operator_or_day"
    else:
        available_times = availability_result.get("all_available_times", [])
        next_action = "choose_time" if available_times else "choose_operator_or_day"

    return {
        "success": True,
        "conversation_id": payload.conversation_id,
        "booking_context": state,
        "availability": availability_result,
        "operators_available_at_requested_time": exact_operator_matches,
        "closest_operator_options": closest_operator_options,
        "next_action": next_action
    }
