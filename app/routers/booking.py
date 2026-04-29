"""
Booking and availability check endpoints.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from typing import Dict, Any

from app.core.auth import verify_api_secret
from app.models import BookingRequest, AvailabilityRequest
from app.services.wegest import run_wegest_booking, run_availability_check
from app.services.call_state import update_call_state, clear_call_state, get_call_state
from app.utils.helpers import normalize_requested_services

router = APIRouter(prefix="/api", tags=["booking"])


@router.post("/book")
async def book_appointment(
    request: Request,
    booking: BookingRequest,
    _: str = Depends(verify_api_secret)
):
    """Book an appointment in Wegest."""
    from app.services.wegest import screenshots

    logger = logging.getLogger(__name__)
    logger.info(f"📅 Booking: {booking.customer_name} | {booking.service or booking.services} | {booking.preferred_date} {booking.preferred_time}")

    if booking.conversation_id:
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
        await clear_call_state(booking.conversation_id)

    return result


@router.post("/check-availability")
async def check_availability(
    request: Request,
    avail: AvailabilityRequest,
    _: str = Depends(verify_api_secret)
):
    """Check availability for a given date."""
    from app.services.wegest import screenshots

    logger = logging.getLogger(__name__)
    logger.info(f"🔍 Availability check: {avail.preferred_date}")

    result = await run_availability_check(avail)

    if avail.conversation_id:
        await update_call_state(avail.conversation_id, {
            "preferred_date": avail.preferred_date,
            "operator_preference": avail.operator_preference,
            "services": normalize_requested_services(avail.service, avail.services),
            "last_availability_result": result
        })
        logger.info(f"🧠 Updated call state from availability for {avail.conversation_id}")

    return result
