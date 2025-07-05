# sa/core/settings.py
# Copyright (C) 2025 Martin Bukowski

"""Settings for the scheduling agent."""

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field, Field


class Settings(BaseSettings):
    # Security - store as string to avoid JSON parsing issues
    api_keys_raw: str = Field(default="", alias="api_keys")
    
    @computed_field
    @property
    def api_keys(self) -> list[str]:
        """Parse API keys from comma-separated string."""
        if not self.api_keys_raw.strip():
            return []
        return [key.strip() for key in self.api_keys_raw.split(',') if key.strip()]

    # Database Configuration
    database_url: str = "sqlite+aiosqlite:////app/local/schedule_agent.db"

    # LLM Provider Configuration
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"

    # Server Configuration
    server_host: str = "0.0.0.0"
    server_port: int = 8081

    # Agent Configuration
    default_timezone: str = "UTC"
    agent_version: str = "0.1.0"

    # Logging Configuration
    log_level: str = "INFO"

    # Debugging/Development Configuration
    include_validation_error_detail: bool = False

    reload: bool = False

    # Configure loading from .env file
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @computed_field
    @property
    def basic_database_url(self) -> str:
        """Return the database URL suitable for synchronous drivers like Alembic."""
        # Simple replacement for sqlite, adjust logic if other async drivers are used
        return self.database_url.replace("+aiosqlite", "")

