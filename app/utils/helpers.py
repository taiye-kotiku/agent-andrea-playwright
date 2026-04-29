"""
General helper functions.
"""

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def js_escape(s: str) -> str:
    """Escape string for use in JavaScript string literals."""
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")


def normalize_requested_services(service: str | None, services: list[str]) -> list[str]:
    """Normalize service input to a clean list of service names."""
    out = [s.strip() for s in (services or []) if s and s.strip()]
    if not out and service:
        out = [service.strip()]
    return out


def get_missing_booking_fields(state: Dict[str, Any]) -> list[str]:
    """Return list of missing required fields for booking."""
    missing = []

    services = state.get("services") or []
    if not services:
        missing.append("services")

    if not state.get("operator_preference"):
        missing.append("operator_preference")

    if not state.get("preferred_date"):
        missing.append("preferred_date")

    if not state.get("preferred_time"):
        missing.append("preferred_time")

    if not state.get("customer_name"):
        missing.append("customer_name")

    if not state.get("caller_phone"):
        missing.append("caller_phone")

    return missing
