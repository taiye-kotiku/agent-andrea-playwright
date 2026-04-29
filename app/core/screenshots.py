"""
Screenshot management for debug purposes.
"""

import base64
from typing import Dict, Optional

# Global state for screenshots
_screenshots: Dict[str, str] = {}


def get_screenshots() -> Dict[str, str]:
    """Get all screenshots."""
    return _screenshots


def clear_screenshots():
    """Clear all screenshots."""
    _screenshots.clear()


def add_screenshot(name: str, data: str):
    """Add a screenshot."""
    _screenshots[name] = data


def remove_screenshot(name: str):
    """Remove a screenshot by name."""
    _screenshots.pop(name, None)
