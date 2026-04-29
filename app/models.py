"""
Pydantic models for the Wegest Booking Service.
"""

from pydantic import BaseModel
from typing import Optional, Any, List


class BookingRequest(BaseModel):
    """Request model for booking an appointment."""
    customer_name: str
    caller_phone: str
    service: Optional[str] = None
    services: List[str] = []
    operator_preference: str = "prima disponibile"
    preferred_date: str
    preferred_time: str
    conversation_id: Optional[str] = None


class UpdateBookingContextRequest(BaseModel):
    """Request model for updating booking context."""
    conversation_id: str
    services: List[str] = []
    service: Optional[str] = None
    operator_preference: Optional[str] = None
    preferred_date: Optional[str] = None
    preferred_time: Optional[str] = None
    customer_name: Optional[str] = None
    caller_phone: Optional[str] = None


class FinalizeBookingRequest(BaseModel):
    """Request model for finalizing a booking."""
    conversation_id: str


class GetBookingContextRequest(BaseModel):
    """Request model for getting booking context."""
    conversation_id: str


class AvailabilityRequest(BaseModel):
    """Request model for checking availability."""
    preferred_date: str
    operator_preference: str = "prima disponibile"
    service: Optional[str] = None
    services: List[str] = []
    conversation_id: Optional[str] = None


class CheckBookingOptionsRequest(BaseModel):
    """Request model for checking booking options."""
    conversation_id: str


class PrepareLiveSessionRequest(BaseModel):
    """Request model for preparing a live session."""
    conversation_id: Optional[str] = None


class ServiceDurationRequest(BaseModel):
    """Request model for getting service duration."""
    service: Optional[str] = None
    services: List[str] = []
