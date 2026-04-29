"""
Availability cache management.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Any, Dict
import asyncio

logger = logging.getLogger(__name__)

CACHE_FILE = Path("availability_cache.json")

availability_cache = {
    "updated_at": None,
    "days": {}
}

cache_lock = asyncio.Lock()


def load_cache_from_disk():
    """Load availability cache from disk."""
    global availability_cache
    try:
        if CACHE_FILE.exists():
            availability_cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            logger.info("📦 Availability cache loaded from disk")
    except Exception as e:
        logger.warning(f"Failed to load cache from disk: {e}")


def save_cache_to_disk():
    """Save availability cache to disk."""
    try:
        CACHE_FILE.write_text(
            json.dumps(availability_cache, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        logger.info("💾 Availability cache saved to disk")
    except Exception as e:
        logger.warning(f"Failed to save cache to disk: {e}")


async def get_cached_availability(date: str) -> Optional[Dict]:
    """Get cached availability for a specific date."""
    async with cache_lock:
        load_cache_from_disk()
        return availability_cache.get("days", {}).get(date)


async def set_cached_availability(date: str, data: Dict):
    """Cache availability for a specific date."""
    async with cache_lock:
        availability_cache["days"][date] = data
        availability_cache["updated_at"] = datetime.utcnow().isoformat()
        save_cache_to_disk()


async def invalidate_cache(date: Optional[str] = None):
    """Invalidate cache for a date or all dates."""
    async with cache_lock:
        if date:
            if date in availability_cache["days"]:
                del availability_cache["days"][date]
                logger.info(f"📦 Cache invalidated for {date}")
        else:
            availability_cache["days"] = {}
            logger.info("📦 All availability cache invalidated")
        availability_cache["updated_at"] = datetime.utcnow().isoformat()
        save_cache_to_disk()
