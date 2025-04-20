
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Security
    api_keys: list[str] = []  # Default to empty list if not set

    # Database Configuration
    database_url: str = "sqlite+aiosqlite:///./schedule_agent.db"

    # LLM Provider Configuration
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = "gpt-4o-mini"  # Sensible default

    # Server Configuration
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # Agent Configuration
    default_timezone: str = "UTC"
    agent_version: str = "0.1.0"

    # Logging Configuration
    log_level: str = "INFO"

    # Configure loading from .env file
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


# Create a single instance of the settings to be imported elsewhere
settings = Settings()
