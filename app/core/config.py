"""
Application configuration using pydantic BaseSettings.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False
    )

    # API Security
    api_secret: str = "changeme"

    # Session Management
    pool_size: int = 2
    max_concurrent_sessions: int = 3
    session_idle_ttl_seconds: int = 900  # 15 minutes
    call_state_ttl_seconds: int = 3600  # 1 hour

    # Application
    debug_screenshots: bool = False
    port: int = 8000
    host: str = "0.0.0.0"

    # Playwright
    playwright_headless: bool = True
    playwright_timeout: int = 30000

    @property
    def is_production(self) -> bool:
        """Check if running in production (API_SECRET not default)."""
        return self.api_secret != "changeme"

    def __str__(self):
        """String representation without exposing secret."""
        return f"Settings(pool_size={self.pool_size}, debug_screenshots={self.debug_screenshots})"


settings = Settings()
