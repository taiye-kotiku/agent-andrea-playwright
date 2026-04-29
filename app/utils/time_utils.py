"""
Time parsing and formatting utilities.
"""

from datetime import datetime, timedelta


def parse_optional_time_to_minutes(t: str | None) -> int | None:
    """Convert time string (HH:MM) to minutes. Returns None if invalid."""
    if not t:
        return None
    try:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return None


def quarter_time_to_minutes(t: str) -> int:
    """Convert quarter-hour time string (HH:MM) to minutes."""
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def minutes_to_quarter_time(total: int) -> str:
    """Convert minutes to HH:MM format."""
    h = str(total // 60).zfill(2)
    m = str(total % 60).zfill(2)
    return f"{h}:{m}"


def ceil_to_quarter(minutes: int) -> int:
    """Round up to nearest quarter hour (15 min)."""
    if minutes <= 0:
        return 0
    return ((minutes + 14) // 15) * 15


def normalize_date_to_iso(date_str: str | None) -> str | None:
    """Normalize various date formats to ISO format (YYYY-MM-DD)."""
    if not date_str:
        return date_str

    date_str = date_str.strip()

    # Try ISO format first
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    # Try dd-MM-yyyy format
    try:
        dt = datetime.strptime(date_str, "%d-%m-%Y")
        return dt.strftime("%Y-%m-%d")
    except Exception:
        pass

    return date_str
