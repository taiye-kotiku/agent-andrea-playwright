"""
Call state management for conversation tracking.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import asyncio
import logging

logger = logging.getLogger(__name__)

call_states: Dict[str, Dict[str, Any]] = {}
call_states_lock = asyncio.Lock()
CALL_STATE_TTL_SECONDS = 60 * 60  # 1 hour


async def get_call_state(conversation_id: str) -> Dict[str, Any]:
    """Get or create call state for a conversation."""
    async with call_states_lock:
        state = call_states.get(conversation_id)
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
            call_states[conversation_id] = state
        return state.copy()


async def update_call_state(conversation_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
    """Update call state with new values."""
    async with call_states_lock:
        state = call_states.get(conversation_id)
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
            call_states[conversation_id] = state

        state.update(updates)
        state["updated_at"] = datetime.utcnow().isoformat()
        return state.copy()


async def clear_call_state(conversation_id: str):
    """Clear call state for a conversation."""
    async with call_states_lock:
        if conversation_id in call_states:
            del call_states[conversation_id]
            logger.info(f"🧠 Cleared call state for {conversation_id}")


async def cleanup_expired_states():
    """Remove call states older than TTL."""
    async with call_states_lock:
        now = datetime.utcnow()
        expired = []
        for cid, state in call_states.items():
            try:
                updated_at = datetime.fromisoformat(state.get("updated_at", ""))
                if (now - updated_at).total_seconds() > CALL_STATE_TTL_SECONDS:
                    expired.append(cid)
            except Exception:
                continue

        for cid in expired:
            del call_states[cid]
            logger.info(f"🧠 Cleaned up expired state for {cid}")

        return len(expired)
