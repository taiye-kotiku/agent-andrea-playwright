"""
Tests for time utilities.
"""

import pytest
from app.utils.time_utils import (
    parse_optional_time_to_minutes,
    quarter_time_to_minutes,
    minutes_to_quarter_time,
    ceil_to_quarter,
    normalize_date_to_iso
)


class TestParseOptionalTimeToMinutes:
    """Tests for parse_optional_time_to_minutes."""

    def test_valid_time(self):
        """Test parsing valid time string."""
        result = parse_optional_time_to_minutes("14:30")
        assert result == 14 * 60 + 30  # 870

    def test_none_input(self):
        """Test with None input."""
        result = parse_optional_time_to_minutes(None)
        assert result is None

    def test_empty_string(self):
        """Test with empty string."""
        result = parse_optional_time_to_minutes("")
        assert result is None

    def test_invalid_format(self):
        """Test with invalid format."""
        result = parse_optional_time_to_minutes("invalid")
        assert result is None


class TestQuarterTimeToMinutes:
    """Tests for quarter_time_to_minutes."""

    def test_valid_time(self):
        """Test converting quarter time to minutes."""
        result = quarter_time_to_minutes("14:30")
        assert result == 870

    def test_midnight(self):
        """Test midnight time."""
        result = quarter_time_to_minutes("00:00")
        assert result == 0


class TestMinutesToQuarterTime:
    """Tests for minutes_to_quarter_time."""

    def test_valid_minutes(self):
        """Test converting minutes to time string."""
        result = minutes_to_quarter_time(870)
        assert result == "14:30"

    def test_zero_minutes(self):
        """Test zero minutes."""
        result = minutes_to_quarter_time(0)
        assert result == "00:00"


class TestCeilToQuarter:
    """Tests for ceil_to_quarter."""

    def test_exact_quarter(self):
        """Test when minutes is exact quarter."""
        result = ceil_to_quarter(30)
        assert result == 30

    def test_needs_rounding(self):
        """Test when minutes needs rounding."""
        result = ceil_to_quarter(32)
        assert result == 45

    def test_zero(self):
        """Test zero input."""
        result = ceil_to_quarter(0)
        assert result == 0

    def test_negative(self):
        """Test negative input."""
        result = ceil_to_quarter(-5)
        assert result == 0


class TestNormalizeDateToIso:
    """Tests for normalize_date_to_iso."""

    def test_iso_format(self):
        """Test ISO format input."""
        result = normalize_date_to_iso("2024-12-25")
        assert result == "2024-12-25"

    def test_dd_mm_yyyy_format(self):
        """Test dd-mm-yyyy format."""
        result = normalize_date_to_iso("25-12-2024")
        assert result == "2024-12-25"

    def test_none_input(self):
        """Test None input."""
        result = normalize_date_to_iso(None)
        assert result is None

    def test_invalid_format(self):
        """Test invalid format (returns as-is)."""
        result = normalize_date_to_iso("invalid-date")
        assert result == "invalid-date"
