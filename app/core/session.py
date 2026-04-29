"""
Session management for Wegest automation.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Any, Dict
import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class WegestSession:
    """Individual Wegest browser session."""
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    logged_in: bool = False
    agenda_open: bool = False
    last_used_at: Optional[datetime] = None


@dataclass
class WegestPoolSession:
    """Pooled Wegest browser session for conversation handling."""
    id: str = ""
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    logged_in: bool = False
    agenda_open: bool = False
    in_use: bool = False
    assigned_conversation_id: Optional[str] = None
    last_used_at: Optional[datetime] = None


# Session storage
wegest_sessions: Dict[str, WegestSession] = {}
wegest_sessions_lock = asyncio.Lock()
wegest_pool: Dict[str, WegestPoolSession] = {}
conversation_to_pool_session: Dict[str, str] = {}

MAX_CONCURRENT_SESSIONS = 3
SESSION_IDLE_TTL_SECONDS = 60 * 15  # 15 minutes
POOL_SIZE = 2

pool_lock = asyncio.Lock()


async def get_session_stats() -> dict:
    """Get statistics about current sessions."""
    async with wegest_sessions_lock:
        return {
            "active_sessions": len(wegest_sessions),
            "pool_sessions": len(wegest_pool),
            "pool_in_use": sum(1 for s in wegest_pool.values() if s.in_use),
            "conversation_mappings": len(conversation_to_pool_session)
        }
