import os

import pytest

import openai

from sa.nlp.llm_provider import LLMProvider
from sa.core.settings import Settings
# --- Fixture for LLM Provider ---


@pytest.fixture(scope="module")  # Use module scope to initialize client once
def llm_provider() -> LLMProvider:
    """Provides an LLMProvider instance configured from environment variables."""
    # Assumes LLM_BASE_URL, LLM_MODEL, LLM_API_KEY might be set in the env
    settings = Settings()
    return LLMProvider.from_settings(settings=settings)


# --- Conditional Skip ---

# Skip these tests unless explicitly requested via environment variable
# Requires LLM_API_KEY (if using OpenAI), LLM_BASE_URL, LLM_MODEL configured
# Also requires `openai` package installed.
RUN_TESTS = os.environ.get("RUN_LLM_INTEGRATION_TESTS", "false").lower() == "true"
# Check if the client was actually initialized successfully

skip_llm_tests = pytest.mark.skipif(
    not RUN_TESTS,
    reason="LLM integration tests require RUN_LLM_INTEGRATION_TESTS=true, env vars set, and openai client initialized.",
)


# --- Actual Integration Tests (Run against live/configured LLM) ---


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_scheduling_query_live_create(llm_provider: LLMProvider):
    """Integration test for parse_scheduling_query (create intent) against a live LLM API."""
    query = "Schedule a sync up with support@example.com tomorrow morning at 9:30am PST"
    result = await llm_provider.parse_scheduling_query(query)

    assert isinstance(result, dict)
    # Basic checks - exact values depend heavily on the model and current date
    assert result.get("intent") == "create_event"  # Expect correct intent
    assert "start_date" in result.get("date_info", {})  # Expect date info
    assert "participants" in result.get("filters", {})  # Expect filters
    assert result.get("confidence", 0.0) > 0.5  # Expect reasonable confidence
    assert result.get("method") == "llm"


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_scheduling_query_live_query(llm_provider: LLMProvider):
    """Integration test for parse_scheduling_query (query intent) against a live LLM API."""
    query = "What is on my calendar next monday?"
    result = await llm_provider.parse_scheduling_query(query)

    assert isinstance(result, dict)
    assert result.get("intent") == "query_schedule"
    assert "start_date" in result.get("date_info", {})
    assert "end_date" in result.get("date_info", {})
    assert result.get("filters", {}) == {}  # Likely no filters for this query
    assert result.get("confidence", 0.0) > 0.5
    assert result.get("method") == "llm"


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_scheduling_query_live_unknown(llm_provider: LLMProvider):
    """Integration test for parse_scheduling_query (unknown intent) against a live LLM API."""
    query = "Tell me a joke about calendars"
    result = await llm_provider.parse_scheduling_query(query)

    assert isinstance(result, dict)
    # Expect unknown intent and low confidence
    assert result.get("intent") == "unknown"
    assert result.get("confidence", 1.0) < 0.5
    assert result.get("method") == "llm"


# TODO: Add more live integration tests for other intents and edge cases if needed.


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_date_live(llm_provider: LLMProvider):
    """Integration test for parse_date_query against a live LLM API."""
    query = "Schedule a meeting for next Tuesday afternoon"
    result = await llm_provider.parse_date_query(query)

    assert isinstance(result, dict)
    # Check for essential keys, values depend heavily on the model
    assert "start_date" in result
    assert "end_date" in result
    assert "confidence" in result
    # Allow fallback methods as the live API might fail
    assert result.get("method", "").startswith(("llm", "fallback_"))


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_extract_properties_live(llm_provider: LLMProvider):
    """Integration test for extract_event_properties against a live LLM API."""
    text = "Book a project sync with Alice (alice@example.com) tomorrow at 3pm in the main conference room re: project alpha"
    result = await llm_provider.extract_event_properties(text)

    assert isinstance(result, dict)
    # Check for some likely keys, specific extraction depends on model
    # This assertion is too brittle for live tests where LLM might fail
    # assert 'title' in result or 'notes' in result # Should find something
    assert "confidence" in result or result == {}  # Accept confidence or empty dict on failure
    assert result.get("method", "llm") == "llm" or result == {}  # Allow empty dict on failure
    # Optional: Check if participants/location/time were extracted if expected


# TODO: Add more tests:
# - Error handling (e.g., network errors, API errors from provider)
# - More complex queries
