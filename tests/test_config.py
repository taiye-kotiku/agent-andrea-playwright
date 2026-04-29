"""
Tests for configuration.
"""

import pytest
from app.core.config import Settings


class TestSettings:
    """Tests for Settings class."""

    def test_default_values(self):
        """Test default configuration values."""
        settings = Settings(
            api_secret="test_secret",
            pool_size=5,
            max_concurrent_sessions=10
        )
        assert settings.api_secret == "test_secret"
        assert settings.pool_size == 5
        assert settings.max_concurrent_sessions == 10

    def test_is_production(self):
        """Test is_production property."""
        # Default secret should not be production
        settings = Settings(api_secret="changeme")
        assert not settings.is_production

        # Custom secret should be production
        settings = Settings(api_secret="my_secret_key")
        assert settings.is_production

    def test_debug_screenshots_default(self):
        """Test debug_screenshots default value."""
        settings = Settings()
        assert settings.debug_screenshots is False
