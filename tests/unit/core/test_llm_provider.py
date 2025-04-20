import pytest
import json
from unittest.mock import patch, AsyncMock, ANY, MagicMock
from datetime import datetime, timezone, timedelta

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
async def test_unit_parse_scheduling_query_rate_limit_error(
    mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test fallback logic when LLM API call raises a RateLimitError."""
    query = "Another meeting"

    # Check if openai module and specific error are available
    api_error = None
    if openai and hasattr(openai, 'RateLimitError'):
        # RateLimitError requires a response and body
        mock_response = MagicMock()
        mock_response.status_code = 429
        api_error = openai.RateLimitError(
            message="Rate limit exceeded",
            response=mock_response,
            body={"error": {"code": "rate_limit_exceeded"}}
        )

    # Simulate LLM failure by raising the error
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(side_effect=api_error)
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions: Expect fallback behavior
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once() # LLM call attempted
    assert result.method == "fallback_default"
    assert result.intent == "unknown"
    assert result.confidence == 0.2


@pytest.mark.asyncio
@patch('openai.AsyncClient', new_callable=AsyncMock)
async def test_unit_parse_scheduling_query_authentication_error(
    mock_async_client_class: AsyncMock, llm_provider: LLMProvider
):
    """Unit test fallback logic when LLM API call raises an AuthenticationError."""
    query = "Secret meeting"

    # Check if openai module and specific error are available
    api_error = None
    if openai and hasattr(openai, 'AuthenticationError'):
        # AuthenticationError requires a response and body
        mock_response = MagicMock()
        mock_response.status_code = 401
        api_error = openai.AuthenticationError(
            message="Incorrect API key provided",
            response=mock_response,
            body={"error": {"code": "invalid_api_key"}}
        )

    # Simulate LLM failure by raising the error
    mock_client_instance = AsyncMock()
    mock_client_instance.chat.completions.create = AsyncMock(side_effect=api_error)
    llm_provider.client = mock_client_instance

    # Call the method under test
    result = await llm_provider.parse_scheduling_query(query)

    # Assertions: Expect fallback behavior
    assert result is not None
    mock_client_instance.chat.completions.create.assert_awaited_once() # LLM call attempted
    assert result.method == "fallback_default"
    assert result.intent == "unknown"
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

# --- Tests for _get_fallback_date_result --- #

@patch('scheduling_agent.core.llm_provider.dateparser.parse')
def test_unit_fallback_date_result_success(mock_dateparser_parse, llm_provider: LLMProvider):
    """Unit test _get_fallback_date_result successful parsing."""
    query = "Meeting next Tuesday at 5pm"
    # Mock dateparser.parse to return a specific datetime
    mock_dt = datetime(2024, 6, 11, 17, 0, 0, tzinfo=timezone.utc)
    mock_dateparser_parse.return_value = mock_dt

    result_dict = llm_provider._get_fallback_date_result(query)

    mock_dateparser_parse.assert_called_once()
    assert result_dict == {
        "start_date": "2024-06-11",
        "end_date": "2024-06-11",
        "confidence": 0.6,
        "method": "fallback_dateparser"
    }

@patch('scheduling_agent.core.llm_provider.dateparser.parse')
def test_unit_fallback_date_result_parse_fail(mock_dateparser_parse, llm_provider: LLMProvider):
    """Unit test _get_fallback_date_result when dateparser fails to parse."""
    query = "Some random text without dates"
    # Mock dateparser.parse to return None (failure)
    mock_dateparser_parse.return_value = None

    # Use freezegun or similar if exact date matters, otherwise check structure
    today_iso = datetime.now(timezone.utc).date().isoformat()

    result_dict = llm_provider._get_fallback_date_result(query)

    mock_dateparser_parse.assert_called_once()
    assert result_dict == {
        "start_date": today_iso,
        "end_date": today_iso,
        "confidence": 0.3,
        "method": "fallback_default"
    }

@patch('scheduling_agent.core.llm_provider.dateparser.parse')
def test_unit_fallback_date_result_exception(mock_dateparser_parse, llm_provider: LLMProvider):
    """Unit test _get_fallback_date_result when dateparser raises an exception."""
    query = "Data that causes issues"
    # Mock dateparser.parse to raise an exception
    mock_dateparser_parse.side_effect = ValueError("Parsing error")

    # Use freezegun or similar if exact date matters, otherwise check structure
    today_iso = datetime.now(timezone.utc).date().isoformat()

    result_dict = llm_provider._get_fallback_date_result(query)

    mock_dateparser_parse.assert_called_once()
    assert result_dict == {
        "start_date": today_iso,
        "end_date": today_iso,
        "confidence": 0.1,
        "method": "fallback_error"
    }

# --- Tests for parse_scheduling_query using fallback --- #

@pytest.mark.asyncio
@patch('scheduling_agent.core.llm_provider.LLMProvider._generate_json')
@patch('scheduling_agent.core.llm_provider.LLMProvider._get_fallback_date_result')
async def test_unit_parse_scheduling_query_uses_fallback_on_llm_failure(
    mock_fallback, mock_generate_json, llm_provider: LLMProvider
):
    """Verify fallback is called when _generate_json returns None."""
    query = "Schedule lunch"
    mock_generate_json.return_value = None # Simulate LLM failure

    # Mock the fallback return value
    fallback_return = {
        "start_date": "2024-07-01",
        "end_date": "2024-07-01",
        "method": "fallback_dateparser"
    }
    mock_fallback.return_value = fallback_return

    result = await llm_provider.parse_scheduling_query(query)

    mock_generate_json.assert_awaited_once_with(ANY, query)
    mock_fallback.assert_called_once_with(query)

    assert result.method == "fallback_dateparser"
    assert result.intent == "unknown"
    assert result.confidence == 0.2
    assert result.date_info.start_date == "2024-07-01"
    assert result.date_info.end_date == "2024-07-01"

@pytest.mark.asyncio
@patch('scheduling_agent.core.llm_provider.LLMProvider._generate_json')
@patch('scheduling_agent.core.llm_provider.LLMProvider._get_fallback_date_result')
async def test_unit_parse_scheduling_query_uses_fallback_on_validation_error(
    mock_fallback, mock_generate_json, llm_provider: LLMProvider
):
    """Verify fallback is called when LLM response fails Pydantic validation."""
    query = "Book meeting for tomorrow"
    # Simulate LLM returning invalid structure (invalid type for confidence)
    llm_output = {
        "intent": "create_event", # Provide intent but make confidence invalid
        "date_info": {"start_date": "2024-07-02"},
        "confidence": "not-a-float" # Invalid type
    }
    mock_generate_json.return_value = llm_output

    # Mock the fallback return value
    fallback_return = {
        "start_date": "2024-07-02",
        "end_date": "2024-07-02",
        "method": "fallback_dateparser",
        "confidence": 0.6 # Not used directly by parse_scheduling_query
    }
    mock_fallback.return_value = fallback_return

    result = await llm_provider.parse_scheduling_query(query)

    mock_generate_json.assert_awaited_once_with(ANY, query)
    mock_fallback.assert_called_once_with(query)

    assert result.method == "fallback_dateparser"
    assert result.intent == "unknown"
    assert result.confidence == 0.2 # Fallback confidence overrides LLM/fallback confidence
    assert result.date_info.start_date == "2024-07-02"
    assert result.date_info.end_date == "2024-07-02"
