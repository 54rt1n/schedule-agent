import pytest
import json
from unittest.mock import patch, AsyncMock

# Assuming openai might not be installed in all test environments
try:
    import openai
except ImportError:
    openai = None

from scheduling_agent.core.llm_provider import LLMProvider

# --- Fixtures ---

@pytest.fixture
def llm_provider() -> LLMProvider:
    """Provides an LLMProvider instance for unit tests."""
    # Initialize with dummy key/URL for testing internal logic
    # The actual client object will be mocked in tests
    provider = LLMProvider(api_key="dummy_key", base_url="http://dummy.url", model="dummy-model")
    # Ensure client attribute exists for mocking even if openai isn't installed
    if not hasattr(provider, 'client'):
        provider.client = None
    return provider


# --- Helper to Mock OpenAI Client Response ---

def mock_openai_response(content: dict = None, status_code: int = 200, error: Exception = None):
    """Creates a mock OpenAI API response object or simulates an error."""
    if error:
        mock_response = AsyncMock()
        mock_response.side_effect = error
        return mock_response

    mock_choice = AsyncMock()
    mock_choice.message = AsyncMock()
    mock_choice.message.content = json.dumps(content) if content else None

    mock_response = AsyncMock()
    mock_response.choices = [mock_choice]
    return mock_response

# --- Unit Tests (Testing internal logic with mocks) ---

@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock) # Patch the client
async def test_unit_parse_scheduling_query_success(
    mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test successful parsing logic, mocking the client call."""
    query = "Schedule Project Kickoff with alice@example.com next Friday at 10am EST"

    # Expected JSON response from the LLM based on the prompt
    expected_llm_json = {
      "intent": "create_event",
      "date_info": {
        "start_date": "2024-01-05",
        "end_date": "2024-01-05",
        "specific_time": "10:00:00",
        "time_description": "next Friday at 10am EST",
        "timeZone": "America/New_York"
      },
      "filters": {
        "title": "Project Kickoff",
        "participants": ["alice@example.com"]
      },
      "confidence": 0.9
    }

    # Configure the mock client instance and its create method
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_openai_response(expected_llm_json)
    )
    # Assign the configured instance to the provider
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once()
    # Check prompt content (optional, can be brittle)
    call_args, call_kwargs = mock_client_instance.chat.completions.create.call_args
    assert call_kwargs['model'] == llm_provider.model
    assert call_kwargs['messages'][1]['content'] == query
    assert 'Determine the user\'s primary **intent**' in call_kwargs['messages'][0]['content']

    # Check attributes directly on the Pydantic model
    assert result.intent == "create_event"
    assert result.method == "llm" # Should be added by provider
    assert result.date_info is not None
    assert result.date_info.start_date == "2024-01-05"
    assert result.filters is not None
    assert result.filters.title == "Project Kickoff"
    assert result.filters.participants == ["alice@example.com"]
    assert result.confidence == 0.9


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
async def test_unit_parse_scheduling_query_llm_fails_fallback_succeeds(
    mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test fallback logic when LLM call returns None/empty content."""
    query = "Meeting tomorrow morning"

    # Simulate LLM failure by returning None content
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_openai_response(content=None)
    )
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once() # LLM call was attempted

    # Check attributes directly on the Pydantic model
    assert result.method == "fallback_default"
    assert result.intent == "unknown"
    assert result.date_info is not None
    assert result.date_info.method == "fallback_default"
    assert result.date_info.start_date is not None # Check presence of date
    assert result.filters is not None
    assert result.filters.participants == [] # Check default value
    assert result.confidence == 0.2


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
@patch('dateparser.parse', return_value=None) # Also mock dateparser to fail
async def test_unit_parse_scheduling_query_total_failure(
    mock_dateparser_parse: AsyncMock, mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test fallback logic when both LLM and dateparser fail."""
    query = "some nonsense query"

    # Simulate LLM failure
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_openai_response(content=None)
    )
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once() # LLM call was attempted
    mock_dateparser_parse.assert_called_once() # Dateparser was attempted

    # Check attributes directly on the Pydantic model
    assert result.method == "fallback_default"
    assert result.intent == "unknown"
    assert result.date_info is not None
    assert result.date_info.method == "fallback_default"
    assert result.date_info.start_date is not None # Should have today's date
    assert result.filters is not None
    assert result.filters.participants == [] # Check default value
    assert result.confidence == 0.2


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
async def test_unit_parse_scheduling_query_api_error(
    mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test fallback logic when LLM API call raises an error."""
    query = "Meeting friday"

    # Check if openai module is available before trying to use its exceptions
    api_error = None
    if openai:
        api_error = openai.APIConnectionError(request=AsyncMock()) # Use a mock request

    # Simulate LLM failure by raising an API error
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(side_effect=api_error)
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once() # LLM call was attempted

    # Check attributes directly on the Pydantic model
    assert result.method == "fallback_default"
    assert result.intent == "unknown"
    assert result.date_info is not None
    assert result.date_info.method == "fallback_default"
    assert result.date_info.start_date is not None # Should have today's date
    assert result.filters is not None
    assert result.filters.participants == [] # Check default value
    assert result.confidence == 0.2


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
async def test_unit_parse_scheduling_query_invalid_json(
    mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test fallback logic when LLM returns syntactically invalid JSON."""
    query = "Meeting with Bob later"

    # Simulate LLM returning invalid JSON string
    mock_client_instance = AsyncMock()
    mock_invalid_response = AsyncMock()
    mock_choice = AsyncMock()
    mock_choice.message = AsyncMock()
    # Malformed JSON (e.g., missing closing brace for date_info)
    mock_choice.message.content = '{"intent": "create_event", "date_info": {"start_date": "today"'
    mock_invalid_response.choices = [mock_choice]

    mock_client_instance.chat.completions.create = AsyncMock(
        return_value=mock_invalid_response
    )
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once() # LLM call was attempted

    # Check that the fallback was used
    assert result.method == "fallback_default"
    assert result.intent == "unknown"
    assert result.date_info is not None
    assert result.date_info.method == "fallback_default"
    assert result.date_info.start_date is not None # Check presence of date
    assert result.filters is not None
    assert result.filters.participants == [] # Check default value
    assert result.confidence == 0.2

# TODO: Add unit tests for:
# - LLM returning invalid JSON
# - Different intents being parsed correctly from mock LLM response
# - Cases where only filters are found, or only date_info
