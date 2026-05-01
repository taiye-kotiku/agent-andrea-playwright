"""
Pydantic models for Agent Andrea API
"""

from pydantic import BaseModel
from typing import Optional, Any


class BookingRequest(BaseModel):
    customer_name: str
    caller_phone: str
    service: str | None = None
    services: list[str] = []
    operator_preference: str = "prima disponibile"
    preferred_date: str
    preferred_time: str
    conversation_id: str | None = None


class UpdateBookingContextRequest(BaseModel):
    conversation_id: str
    services: list[str] = []
    service: str | None = None
    operator_preference: str | None = None
    preferred_date: str | None = None
    preferred_time: str | None = None
    customer_name: str | None = None
    caller_phone: str | None = None


class FinalizeBookingRequest(BaseModel):
    conversation_id: str


class GetBookingContextRequest(BaseModel):
    conversation_id: str


class AvailabilityRequest(BaseModel):
    preferred_date: str
    operator_preference: str = "prima disponibile"
    service: str | None = None
    services: list[str] = []
    conversation_id: str | None = None


class CheckBookingOptionsRequest(BaseModel):
    conversation_id: str


class PrepareLiveSessionRequest(BaseModel):
    conversation_id: str | None = None


class ServiceDurationRequest(BaseModel):
    service: str | None = None
    services: list[str] = []
