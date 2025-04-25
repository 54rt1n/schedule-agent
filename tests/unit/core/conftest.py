# tests/unit/core/conftest.py
import pytest
import os

# Although settings are tested individually, this provides a baseline
# for other tests in sa/core that might implicitly rely on settings.
@pytest.fixture(scope="function", autouse=True)
def override_settings_for_tests(monkeypatch):
    """
    Fixture to automatically override potentially sensitive or environment-specific
    settings for the duration of each test function.
    Sets defaults for testing: in-memory DB, local LLM URL, test model, etc.
    """
    # Database
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    # LLM Provider Test Defaults
    monkeypatch.setenv("LLM_BASE_URL", "http://local.test/v1")
    monkeypatch.setenv("LLM_MODEL", "test_model")
    monkeypatch.delenv("LLM_API_KEY", raising=False)

    # Other settings
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
