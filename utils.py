"""
Utility functions for Agent Andrea
"""

import config
from datetime import datetime, timedelta
from typing import Optional, Any
import json


def js_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"').replace("\n", "\\n")


def parse_optional_time_to_minutes(t: str | None) -> int | None:
    if not t:
        return None
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def ceil_to_quarter(minutes: int) -> int:
    if minutes <= 0:
        return 0
    return ((minutes + 14) // 15) * 15


def quarter_time_to_minutes(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def minutes_to_quarter_time(total: int) -> str:
    h = str(total // 60).zfill(2)
    m = str(total % 60).zfill(2)
    return f"{h}:{m}"


def normalize_date_to_iso(date_str: str | None) -> str | None:
    if not date_str:
        return date_str

    date_str = date_str.strip()

    # already ISO
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    # dd-MM-yyyy
    try:
        dt = datetime.strptime(date_str, "%d-%m-%Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return date_str


def normalize_requested_services(service: str | None, services: list[str]) -> list[str]:
    out = [s.strip() for s in (services or []) if s and s.strip()]
    if not out and service:
        out = [service.strip()]
    return out


def compute_valid_start_times(available_slots: list[str], required_operator_minutes: int) -> list[str]:
    if required_operator_minutes <= 0:
        return sorted(set(available_slots))

    required_block = ceil_to_quarter(required_operator_minutes)
    required_steps = required_block // 15

    available_set = set(available_slots)
    valid = []

    for slot in sorted(available_slots):
        start = quarter_time_to_minutes(slot)
        ok = True
        for i in range(required_steps):
            t = minutes_to_quarter_time(start + i * 15)
            if t not in available_set:
                ok = False
                break
        if ok:
            valid.append(slot)

    return valid


def build_operator_time_suggestions(
    operators: list[dict[str, Any]],
    requested_time: str | None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Returns:
      - exact operators available at requested time
      - nearest operator/time alternatives if no exact match
    """
    requested_minutes = parse_optional_time_to_minutes(requested_time)
    if requested_minutes is None:
        return [], []

    exact_matches = []
    nearest = []

    for op in operators:
        valid_times = op.get("valid_start_times") or op.get("available_slots") or []
        for t in valid_times:
            mins = quarter_time_to_minutes(t)
            delta = abs(mins - requested_minutes)

            if mins == requested_minutes:
                exact_matches.append({
                    "name": op["name"],
                    "id": op["id"],
                    "time": t,
                    "delta_minutes": 0
                })
            else:
                nearest.append({
                    "name": op["name"],
                    "id": op["id"],
                    "time": t,
                    "delta_minutes": delta
                })

    nearest.sort(key=lambda x: (x["delta_minutes"], x["time"], x["name"]))

    # Keep only closest 5 alternatives
    if exact_matches:
        return exact_matches, []

    return [], nearest[:5]


def get_missing_booking_fields(state: dict[str, Any]) -> list[str]:
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


def load_operator_catalog():
    try:
        if config.OPERATOR_CATALOG_FILE.exists():
            config.operator_catalog = json.loads(config.OPERATOR_CATALOG_FILE.read_text(encoding="utf-8"))
            config.logger.info("👥 Operator catalog loaded from disk")
    except Exception as e:
        config.logger.warning(f"Failed to load operator catalog: {e}")


def save_operator_catalog():
    try:
        config.OPERATOR_CATALOG_FILE.write_text(
            json.dumps(config.operator_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        config.logger.info("💾 Operator catalog saved")
    except Exception as e:
        config.logger.warning(f"Failed to save operator catalog: {e}")


def load_service_catalog():
    try:
        if config.SERVICE_CATALOG_FILE.exists():
            config.service_catalog = json.loads(config.SERVICE_CATALOG_FILE.read_text(encoding="utf-8"))
            config.logger.info("🧴 Service catalog loaded from disk")
    except Exception as e:
        config.logger.warning(f"Failed to load service catalog: {e}")


def save_service_catalog():
    try:
        config.SERVICE_CATALOG_FILE.write_text(
            json.dumps(config.service_catalog, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        config.logger.info("💾 Service catalog saved")
    except Exception as e:
        config.logger.warning(f"Failed to save service catalog: {e}")


def load_cache_from_disk():
    try:
        if config.CACHE_FILE.exists():
            config.availability_cache = json.loads(config.CACHE_FILE.read_text(encoding="utf-8"))
            config.logger.info("📦 Availability cache loaded from disk")
    except Exception as e:
        config.logger.warning(f"Failed to load cache from disk: {e}")


def save_cache_to_disk():
    try:
        config.CACHE_FILE.write_text(
            json.dumps(config.availability_cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        config.logger.info("💾 Availability cache saved to disk")
    except Exception as e:
        config.logger.warning(f"Failed to save cache to disk: {e}")


async def get_call_state(conversation_id: str) -> dict[str, Any]:
    async with config.call_states_lock:
        state = config.call_states.get(conversation_id)
        if not state:
            state = {
                "conversation_id": conversation_id,
                "services": [],
                "operator_preference": None,
                "preferred_date": None,
                "preferred_time": None,
                "customer_name": None,
                "caller_phone": None,
                "last_availability_result": None,
                "booking_confirmed": False,
                "updated_at": datetime.utcnow().isoformat()
            }
            config.call_states[conversation_id] = state
        return state.copy()


async def update_call_state(conversation_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    async with config.call_states_lock:
        state = config.call_states.get(conversation_id)
        if not state:
            state = {
                "conversation_id": conversation_id,
                "services": [],
                "operator_preference": None,
                "preferred_date": None,
                "preferred_time": None,
                "customer_name": None,
                "caller_phone": None,
                "last_availability_result": None,
                "booking_confirmed": False,
                "updated_at": datetime.utcnow().isoformat()
            }

        state.update(updates)
        state["updated_at"] = datetime.utcnow().isoformat()
        config.call_states[conversation_id] = state
        return state.copy()


async def clear_call_state(conversation_id: str):
    async with config.call_states_lock:
        if conversation_id in config.call_states:
            del config.call_states[conversation_id]
            config.logger.info(f"🧹 Cleared call state for {conversation_id}")


async def cleanup_expired_call_states():
    async with config.call_states_lock:
        now = datetime.utcnow()
        expired = []

        for conversation_id, state in config.call_states.items():
            try:
                updated_at = datetime.fromisoformat(state["updated_at"])
                age = (now - updated_at).total_seconds()
                if age > config.CALL_STATE_TTL_SECONDS:
                    expired.append(conversation_id)
            except Exception:
                expired.append(conversation_id)

        for conversation_id in expired:
            del config.call_states[conversation_id]

        if expired:
            config.logger.info(f"🧹 Cleaned {len(expired)} expired call states")


async def set_cached_day(date_str: str, payload: dict):
    async with config.cache_lock:
        config.availability_cache["days"][date_str] = payload
        config.availability_cache["updated_at"] = datetime.utcnow().isoformat()
        save_cache_to_disk()


async def get_cached_day(date_str: str):
    async with config.cache_lock:
        return config.availability_cache["days"].get(date_str)


async def invalidate_cached_day(date_str: str):
    async with config.cache_lock:
        if date_str in config.availability_cache["days"]:
            del config.availability_cache["days"][date_str]
            config.availability_cache["updated_at"] = datetime.utcnow().isoformat()
            save_cache_to_disk()
            config.logger.info(f"🗑️ Invalidated cache for {date_str}")
