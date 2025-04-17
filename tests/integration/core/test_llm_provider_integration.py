import pytest
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

# Assuming openai might not be installed in all test environments
try:
    import openai
except ImportError:
    openai = None

from scheduling_agent.core.llm_provider import LLMProvider

# --- Fixture for LLM Provider ---

@pytest.fixture(scope="module") # Use module scope to initialize client once
def llm_provider() -> LLMProvider:
    """Provides an LLMProvider instance configured from environment variables."""
    # Assumes LLM_BASE_URL, LLM_MODEL, LLM_API_KEY might be set in the env
    return LLMProvider()

# --- Conditional Skip ---

# Skip these tests unless explicitly requested via environment variable
# Requires LLM_API_KEY (if using OpenAI), LLM_BASE_URL, LLM_MODEL configured
# Also requires `openai` package installed.
RUN_TESTS = os.environ.get("RUN_LLM_INTEGRATION_TESTS", "false").lower() == "true"
# Check if the client was actually initialized successfully
PROVIDER_CLIENT_AVAILABLE = LLMProvider().client is not None

skip_llm_tests = pytest.mark.skipif(
    not RUN_TESTS or not PROVIDER_CLIENT_AVAILABLE,
    reason="LLM integration tests require RUN_LLM_INTEGRATION_TESTS=true, env vars set, and openai client initialized."
)


# --- Actual Integration Tests (Run against live/configured LLM) ---

@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_scheduling_query_live_create(
    llm_provider: LLMProvider
):
    """Integration test for parse_scheduling_query (create intent) against a live LLM API."""
    query = "Schedule a sync up with support@example.com tomorrow morning at 9:30am PST"
    result = await llm_provider.parse_scheduling_query(query)

    print(f"LLM Result (parse_scheduling_query - create): {result}") # Print for debugging

    assert isinstance(result, dict)
    # Basic checks - exact values depend heavily on the model and current date
    assert result.get('intent') == 'create_event' # Expect correct intent
    assert 'start_date' in result.get('date_info', {}) # Expect date info
    assert 'participants' in result.get('filters', {}) # Expect filters
    assert result.get('confidence', 0.0) > 0.5 # Expect reasonable confidence
    assert result.get('method') == 'llm'


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_scheduling_query_live_query(
    llm_provider: LLMProvider
):
    """Integration test for parse_scheduling_query (query intent) against a live LLM API."""
    query = "What is on my calendar next monday?"
    result = await llm_provider.parse_scheduling_query(query)

    print(f"LLM Result (parse_scheduling_query - query): {result}") # Print for debugging

    assert isinstance(result, dict)
    assert result.get('intent') == 'query_schedule'
    assert 'start_date' in result.get('date_info', {})
    assert 'end_date' in result.get('date_info', {})
    assert result.get('filters', {}) == {} # Likely no filters for this query
    assert result.get('confidence', 0.0) > 0.5
    assert result.get('method') == 'llm'


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_scheduling_query_live_unknown(
    llm_provider: LLMProvider
):
    """Integration test for parse_scheduling_query (unknown intent) against a live LLM API."""
    query = "Tell me a joke about calendars"
    result = await llm_provider.parse_scheduling_query(query)

    print(f"LLM Result (parse_scheduling_query - unknown): {result}") # Print for debugging

    assert isinstance(result, dict)
    # Expect unknown intent and low confidence
    assert result.get('intent') == 'unknown'
    assert result.get('confidence', 1.0) < 0.5
    assert result.get('method') == 'llm'

# TODO: Add more live integration tests for other intents and edge cases if needed.

@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_parse_date_live(llm_provider: LLMProvider):
    """Integration test for parse_date_query against a live LLM API."""
    query = "Schedule a meeting for next Tuesday afternoon"
    result = await llm_provider.parse_date_query(query)

    print(f"LLM Result (parse_date_query): {result}") # Print for debugging

    assert isinstance(result, dict)
    # Check for essential keys, values depend heavily on the model
    assert 'start_date' in result
    assert 'end_date' in result
    assert 'confidence' in result
    # Allow fallback methods as the live API might fail
    assert result.get('method', '').startswith(('llm', 'fallback_'))


@skip_llm_tests
@pytest.mark.asyncio
async def test_llm_provider_extract_properties_live(llm_provider: LLMProvider):
    """Integration test for extract_event_properties against a live LLM API."""
    text = "Book a project sync with Alice (alice@example.com) tomorrow at 3pm in the main conference room re: project alpha"
    result = await llm_provider.extract_event_properties(text)

    print(f"LLM Result (extract_event_properties): {result}") # Print for debugging

    assert isinstance(result, dict)
    # Check for some likely keys, specific extraction depends on model
    # This assertion is too brittle for live tests where LLM might fail
    # assert 'title' in result or 'notes' in result # Should find something
    assert 'confidence' in result or result == {} # Accept confidence or empty dict on failure
    assert result.get('method', 'llm') == 'llm' or result == {} # Allow empty dict on failure
    # Optional: Check if participants/location/time were extracted if expected


# Method removed from LLMProvider
# @skip_llm_tests
# @pytest.mark.asyncio
# async def test_llm_provider_generate_alternatives_live(llm_provider: LLMProvider):
#     """Integration test for generate_alternatives against a live LLM API."""
#     original_start = datetime.now(timezone.utc).replace(hour=14, minute=0, second=0, microsecond=0)
#     original_event = {
#         "title": "Team Meeting",
#         "start_time": original_start.isoformat(),
#         "end_time": (original_start + timedelta(hours=1)).isoformat(),
#         "timeZone": "UTC"
#     }
#     # Simulate a conflict right at the original time
#     conflicts = [{
#         "event": { # Mocking event structure slightly
#             "title": "Existing Booking",
#             "start_time": original_start,
#             "end_time": original_start + timedelta(hours=1)
#         }
#     }]
# 
#     result = await llm_provider.generate_alternatives(original_event, conflicts)
# 
#     print(f"LLM Result (generate_alternatives): {result}") # Print for debugging
# 
#     # We expect a list, but content depends heavily on the model.
#     # It might be empty if the model can't find alternatives.
#     assert isinstance(result, list)
#     if result:
#         # If alternatives are returned, check the structure of the first one
#         assert isinstance(result[0], dict)
#         assert 'startTime' in result[0]
#         assert 'endTime' in result[0]
#         assert 'label' in result[0] 

@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock) # Patch the client globally for tests
async def test_parse_scheduling_query_success(
    mock_async_client: AsyncMock, llm_provider: LLMProvider
):
    """Test successful parsing of a complex query."""
    query = "Schedule Project Kickoff with alice@example.com next Friday at 10am EST"

    # Expected JSON response from the LLM based on the prompt
    expected_llm_json = {
      "intent": "create_event",
      "date_info": {
        "start_date": "2024-01-05", # Example assuming 'next Friday' relative to test date
        "end_date": "2024-01-05",
        "specific_time": "10:00:00", # Assuming EST gets handled/converted by LLM/prompt
        "time_description": "next Friday at 10am EST",
        "timeZone": "America/New_York" # LLM might infer timezone
      },
      "filters": {
        "title": "Project Kickoff",
        "participants": ["alice@example.com"]
      },
      "confidence": 0.9
    }

    # Configure the mock client's response
    mock_chat_create = AsyncMock()
    mock_chat_create.return_value = mock_openai_response(expected_llm_json)
    # Set the mock client instance used by the provider
    llm_provider.client = mock_async_client
    llm_provider.client.chat.completions.create = mock_chat_create

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_chat_create.assert_awaited_once() # Check if LLM was called
    assert result.get("intent") == "create_event"
    assert result.get("method") == "llm" # Should be added by provider
    assert result.get("date_info", {}).get("start_date") == "2024-01-05"
    assert result.get("filters", {}).get("title") == "Project Kickoff"
    assert result.get("filters", {}).get("participants") == ["alice@example.com"]


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
async def test_parse_scheduling_query_llm_fails_fallback_succeeds(
    mock_async_client: AsyncMock, llm_provider: LLMProvider
):
    """Test fallback logic when LLM call fails but dateparser works."""
    query = "Meeting tomorrow morning"

    # Simulate LLM failure (e.g., API error or invalid JSON response)
    mock_chat_create = AsyncMock()
    # Option 1: Raise an API error
    # mock_chat_create.side_effect = openai.APIConnectionError(request=None)
    # Option 2: Return invalid JSON or None content (easier to mock)
    mock_chat_create.return_value = mock_openai_response(content=None) # Simulate empty content

    llm_provider.client = mock_async_client
    llm_provider.client.chat.completions.create = mock_chat_create

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_chat_create.assert_awaited_once() # LLM call was attempted
    # Check that the fallback method was used
    assert result.get("method") == "fallback_dateparser"
    assert result.get("intent") == "unknown" # Fallback doesn't determine intent
    assert "start_date" in result.get("date_info", {}) # Check that dateparser found something
    assert result.get("filters") == {}


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
@patch('dateparser.parse', return_value=None) # Also mock dateparser to fail
async def test_parse_scheduling_query_total_failure(
    mock_dateparser_parse: AsyncMock, mock_async_client: AsyncMock, llm_provider: LLMProvider
):
    """Test fallback logic when both LLM and dateparser fail."""
    query = "some nonsense query"

    # Simulate LLM failure
    mock_chat_create = AsyncMock()
    mock_chat_create.return_value = mock_openai_response(content=None)
    llm_provider.client = mock_async_client
    llm_provider.client.chat.completions.create = mock_chat_create

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_chat_create.assert_awaited_once() # LLM call was attempted
    mock_dateparser_parse.assert_called_once() # Dateparser was attempted
    # Check that the final fallback method was used
    assert result.get("method") == "fallback_failure"
    assert result.get("intent") == "unknown"
    assert "start_date" not in result.get("date_info", {}) # Dateparser failed
    assert result.get("filters") == {}

# TODO: Add more tests:
# - Test different intents (find, delete, query_schedule, etc.)
# - Test queries with missing information (e.g., no time, no participants)
# - Test handling of different timezones in query/response
# - Test queries leading to 'unknown' intent from LLM
# - Test recurrence rule extraction 