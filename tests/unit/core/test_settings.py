# modules/sa_core/tests/unit/test_settings.py
import os
from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict

# Direct import, assuming sa_core is discoverable via path configuration
from schedule_core.core.settings import Settings

# Fixture to clear relevant environment variables before each test
@pytest.fixture(autouse=True)
def clear_env_vars(monkeypatch):
    vars_to_clear = [
        "API_KEYS",
        "DATABASE_URL",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "SERVER_HOST",
        "SERVER_PORT",
        "DEFAULT_TIMEZONE",
        "AGENT_VERSION",
        "LOG_LEVEL",
        "DOTENV_PATH",
    ]
    for var in vars_to_clear:
        monkeypatch.delenv(var, raising=False)


def test_default_settings():
    """Test that settings load with correct default values."""
    # Ensure no .env file is accidentally loaded by pydantic-settings default behavior
    class TestSettings(Settings):
        model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    settings = TestSettings()
    assert settings.api_keys == []
    assert settings.database_url == "sqlite+aiosqlite:///./local/schedule_agent.db"
    assert settings.llm_api_key is None
    assert settings.llm_base_url is None
    assert settings.llm_model == "gpt-4o-mini"
    assert settings.server_host == "0.0.0.0"
    assert settings.server_port == 8081
    assert settings.default_timezone == "UTC"
    assert settings.agent_version == "0.1.0"
    assert settings.log_level == "INFO"


def test_environment_variable_override(monkeypatch):
    """Test that environment variables override default settings."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@host:port/db")
    monkeypatch.setenv("LLM_MODEL", "gpt-4-turbo")
    monkeypatch.setenv("SERVER_PORT", "9999") # Note: env var is string
    monkeypatch.setenv("API_KEYS", '["key1", "key2"]') # JSON string format for list

    # Ensure no .env file is accidentally loaded
    class TestSettings(Settings):
         model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    settings = TestSettings()

    assert settings.database_url == "postgresql+asyncpg://user:pass@host:port/db"
    assert settings.llm_model == "gpt-4-turbo"
    assert settings.server_port == 9999 # Should be coerced to int
    assert settings.api_keys == ["key1", "key2"] # Should be coerced to list
    assert settings.log_level == "INFO" # Unchanged default


def test_dotenv_file_loading(tmp_path: Path):
    """Test loading settings from a .env file."""
    dotenv_content = """
    DATABASE_URL=sqlite+aiosqlite:///./test_db_from_env.db # Use SQLite format
    LOG_LEVEL=DEBUG
    SERVER_PORT=8080
    API_KEYS='["env_key_1"]'
    EXTRA_VAR=should_be_ignored
    """
    dotenv_path = tmp_path / ".testenv" # Use a unique name
    dotenv_path.write_text(dotenv_content)

    # Create settings instance telling it where to find the .env file
    class TestSettings(Settings):
        model_config = SettingsConfigDict(env_file=str(dotenv_path), env_file_encoding="utf-8", extra="ignore")

    settings = TestSettings()

    assert settings.database_url == "sqlite+aiosqlite:///./test_db_from_env.db" # Check SQLite format
    assert settings.log_level == "DEBUG"
    assert settings.server_port == 8080
    assert settings.api_keys == ["env_key_1"]
    assert settings.llm_model == "gpt-4o-mini" # Default value, not in .env
    # Check that extra var is ignored ('extra=ignore')
    assert not hasattr(settings, "EXTRA_VAR")


def test_env_vars_override_dotenv(monkeypatch, tmp_path: Path):
    """Test that environment variables take precedence over .env file values."""
    dotenv_content = """
    LOG_LEVEL=WARNING
    SERVER_PORT=7000
    """
    dotenv_path = tmp_path / ".testenv_override" # Use a unique name
    dotenv_path.write_text(dotenv_content)

    monkeypatch.setenv("LOG_LEVEL", "ERROR") # Env var should win
    monkeypatch.setenv("DATABASE_URL", "env_db_url") # Only in env var

    # Create settings instance telling it where to find the .env file
    class TestSettings(Settings):
        model_config = SettingsConfigDict(env_file=str(dotenv_path), env_file_encoding="utf-8", extra="ignore")


    settings = TestSettings()

    assert settings.log_level == "ERROR" # Env var wins over .env 'WARNING'
    assert settings.server_port == 7000 # From .env (not overridden by env var)
    assert settings.database_url == "env_db_url" # From env var (not in .env)
    assert settings.llm_model == "gpt-4o-mini" # Default


def test_type_coercion(monkeypatch):
    """Test specific type coercions."""
    monkeypatch.setenv("SERVER_PORT", "1234")
    # Test different ways pydantic-settings might parse lists from env vars
    monkeypatch.setenv("API_KEYS", '["key_a", "key_b", "key_c"]')

    # Ensure no .env file is accidentally loaded
    class TestSettings(Settings):
         model_config = SettingsConfigDict(env_file=None, env_file_encoding="utf-8", extra="ignore")

    settings = TestSettings()
    assert settings.server_port == 1234
    assert isinstance(settings.server_port, int)
    assert settings.api_keys == ["key_a", "key_b", "key_c"]
    assert isinstance(settings.api_keys, list)
    assert all(isinstance(k, str) for k in settings.api_keys)

    # Test comma-separated string (if pydantic-settings supports it, might depend on version)
    # monkeypatch.setenv("API_KEYS", "key_d,key_e")
    # settings_comma = Settings()
    # assert settings_comma.api_keys == ["key_d", "key_e"] # This might fail depending on pydantic-settings version/config

    # Test comma-separated string (if pydantic-settings supports it, might depend on version)
    # monkeypatch.setenv("API_KEYS", "key_d,key_e")
    # settings_comma = Settings()
    # assert settings_comma.api_keys == ["key_d", "key_e"] # This might fail depending on pydantic-settings version/config 


@pytest.mark.parametrize("value", ["first, second", '["first", "second"]'])
def test_api_keys_accept_supported_environment_formats(monkeypatch, value):
    monkeypatch.setenv("API_KEYS", value)
    assert Settings(_env_file=None).api_keys == ["first", "second"]


def test_api_keys_validate_constructor_values():
    from pydantic import ValidationError

    assert Settings(_env_file=None, api_keys=["first"]).api_keys == ["first"]
    with pytest.raises(ValidationError):
        Settings(_env_file=None, api_keys='[123]')
