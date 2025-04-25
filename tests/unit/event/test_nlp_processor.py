from unittest.mock import AsyncMock, MagicMock

import pytest

# Import pytest-asyncio decorator
import pytest_asyncio

from sa.core.logging_config import get_logger
from sa.event.date_utils import DateUtils
from sa.nlp.llm_provider import LLMProvider
from sa.nlp.nlp_processor import NLPProcessor
from sa.nlp.dao import LLMDateInfo, LLMFilters, LLMSchedulingParseResult

logger = get_logger(__name__)

# --- Fixtures ---


@pytest_asyncio.fixture  # Use pytest_asyncio.fixture for async fixtures
async def mock_llm_provider() -> AsyncMock:
    """Provides an async mock LLMProvider with the primary method mocked."""
    mock = AsyncMock(spec=LLMProvider)
    # Mock the primary method used by NLPProcessor
    mock.parse_scheduling_query = AsyncMock(return_value=LLMSchedulingParseResult())
    # Mock the fallback date parser method (used internally by provider's fallback logic)
    # Although NLPProcessor doesn't call this directly, LLMProvider might,
    # so having a mock available can be useful for provider tests or complex integration.
    mock._get_fallback_date_result = AsyncMock(return_value=None)
    return mock


@pytest.fixture
def mock_date_utils() -> MagicMock:
    """Provides a mock DateUtils (currently unused by NLPProcessor)."""
    # NLPProcessor currently passes DateUtils to LLMProvider but doesn't call methods on it directly.
    return MagicMock(spec=DateUtils)


@pytest_asyncio.fixture  # Use pytest_asyncio.fixture for async fixtures
async def nlp_processor(mock_llm_provider: AsyncMock, mock_date_utils: MagicMock) -> NLPProcessor:
    """Provides an NLPProcessor instance with mocked dependencies."""
    return NLPProcessor(llm_provider=mock_llm_provider, date_utils=mock_date_utils)


# --- Tests for parse_natural_language_query ---


@pytest.mark.asyncio
async def test_parse_successful_llm_response(
    nlp_processor: NLPProcessor, mock_llm_provider: AsyncMock
):
    """Test NLPProcessor handles a successful LLM response model."""
    query = "Schedule meeting with Alice tomorrow 3pm"
    # Construct the expected Pydantic model response from the mock provider
    mock_model_response = LLMSchedulingParseResult(
        intent="create_event",
        date_info=LLMDateInfo(start_date="2024-01-01", specific_time="15:00:00"),
        filters=LLMFilters(participants=["Alice"]),
        confidence=0.9,
        method="llm",
    )
    mock_llm_provider.parse_scheduling_query.return_value = mock_model_response

    # NLPProcessor receives the model and returns it directly
    result_model = await nlp_processor.parse_natural_language_query(query)

    # Assert on the mock method directly
    mock_llm_provider.parse_scheduling_query.assert_awaited_once_with(query)

    # Verify the returned model attributes
    assert isinstance(result_model, LLMSchedulingParseResult)
    assert result_model.intent == "create_event"
    assert result_model.confidence == 0.9
    assert result_model.method == "llm"

    # Verify nested date_info attributes
    assert result_model.date_info.start_date == "2024-01-01"
    assert result_model.date_info.end_date is None
    assert result_model.date_info.specific_time == "15:00:00"
    assert result_model.date_info.end_time is None
    assert result_model.date_info.is_recurring is False
    assert result_model.date_info.method is None  # Default value

    # Verify nested filters attributes
    assert result_model.filters.participants == ["Alice"]
    assert result_model.filters.title is None
    assert result_model.filters.location is None


@pytest.mark.asyncio
async def test_parse_minimal_llm_response(
    nlp_processor: NLPProcessor, mock_llm_provider: AsyncMock
):
    """Test handling a minimal Pydantic model response from the provider."""
    query = "cancel lunch"
    mock_model_response = LLMSchedulingParseResult(
        intent="delete_event",
        confidence=0.8,
        method="llm",
        # date_info and filters will use their default_factory
    )
    mock_llm_provider.parse_scheduling_query.return_value = mock_model_response

    result_model = await nlp_processor.parse_natural_language_query(query)

    # Assert on the mock method directly
    mock_llm_provider.parse_scheduling_query.assert_awaited_once_with(query)

    # Verify the processor returned the model correctly, including defaults
    assert isinstance(result_model, LLMSchedulingParseResult)
    assert result_model.intent == "delete_event"
    assert result_model.confidence == 0.8
    assert result_model.method == "llm"

    # Check default date_info values
    assert result_model.date_info.start_date is None
    assert result_model.date_info.end_date is None
    assert result_model.date_info.specific_time is None
    assert result_model.date_info.end_time is None
    assert result_model.date_info.is_recurring is False
    assert result_model.date_info.method is None

    # Check default filters values
    assert result_model.filters.participants == []
    assert result_model.filters.title is None
    assert result_model.filters.location is None


@pytest.mark.asyncio
async def test_parse_llm_failure_with_fallback(
    nlp_processor: NLPProcessor, mock_llm_provider: AsyncMock
):
    """Test handling the fallback Pydantic model from the provider."""
    query = "meeting next tuesday"
    # This is the Pydantic model LLMProvider returns after its internal fallback
    provider_fallback_model = LLMSchedulingParseResult(
        intent="unknown",
        date_info=LLMDateInfo(
            start_date="2024-01-02",  # Example date
            end_date="2024-01-02",
            method="fallback_dateparser",
            confidence=0.6,  # Note: date_info confidence isn't used by NLPProcessor
        ),
        filters=LLMFilters(),  # Default empty filters
        confidence=0.2,  # Overall confidence is low
        method="fallback_dateparser",
    )
    mock_llm_provider.parse_scheduling_query.return_value = provider_fallback_model

    result_model = await nlp_processor.parse_natural_language_query(query)

    # Assert on the mock method directly
    mock_llm_provider.parse_scheduling_query.assert_awaited_once_with(query)

    # Verify the processor returns the fallback model directly
    assert isinstance(result_model, LLMSchedulingParseResult)
    assert result_model.intent == "unknown"
    assert result_model.confidence == 0.2
    assert result_model.method == "fallback_dateparser"

    # Check date_info from fallback
    assert result_model.date_info.start_date == "2024-01-02"
    assert result_model.date_info.end_date == "2024-01-02"
    assert result_model.date_info.specific_time is None
    assert result_model.date_info.end_time is None
    assert result_model.date_info.method == "fallback_dateparser"
    assert result_model.date_info.is_recurring is False

    # Check default filters
    assert result_model.filters.participants == []
    assert result_model.filters.title is None
    assert result_model.filters.location is None


@pytest.mark.asyncio
async def test_parse_total_failure(nlp_processor: NLPProcessor, mock_llm_provider: AsyncMock):
    """Test handling the total failure fallback Pydantic model."""
    query = "blargle flibble"
    # This is the Pydantic model LLMProvider returns on total failure
    # Assume LLMProvider sets date_info based on its internal fallback
    # Let's assume today is 2024-01-01 for consistency
    provider_failure_model = LLMSchedulingParseResult(
        intent="unknown",
        date_info=LLMDateInfo(
            start_date="2024-01-01", end_date="2024-01-01", method="fallback_default"
        ),
        filters=LLMFilters(),
        confidence=0.2,
        method="fallback_default",
    )
    mock_llm_provider.parse_scheduling_query.return_value = provider_failure_model

    result_model = await nlp_processor.parse_natural_language_query(query)

    # Assert on the mock method directly
    mock_llm_provider.parse_scheduling_query.assert_awaited_once_with(query)

    # Verify the processor returns the model directly
    assert isinstance(result_model, LLMSchedulingParseResult)
    assert result_model.intent == "unknown"
    assert result_model.confidence == 0.2
    assert result_model.method == "fallback_default"

    # Check date_info defaults from total failure fallback model
    assert result_model.date_info.start_date == "2024-01-01"
    assert result_model.date_info.end_date == "2024-01-01"
    assert result_model.date_info.specific_time is None
    assert result_model.date_info.end_time is None
    assert result_model.date_info.method == "fallback_default"
    assert result_model.date_info.is_recurring is False

    # Check filters defaults
    assert result_model.filters.participants == []
    assert result_model.filters.title is None
    assert result_model.filters.location is None


@pytest.mark.asyncio
async def test_parse_llm_provider_returns_none(
    nlp_processor: NLPProcessor, mock_llm_provider: AsyncMock
):
    """Test the safety net if provider returns None."""
    query = "some query"
    mock_llm_provider.parse_scheduling_query.return_value = None

    # Check that calling the processor returns the default failure Pydantic model
    result_model = await nlp_processor.parse_natural_language_query(query)

    # Assert on the mock method directly
    mock_llm_provider.parse_scheduling_query.assert_awaited_once_with(query)

    # Check the processor's default failure model attributes
    assert isinstance(result_model, LLMSchedulingParseResult)
    assert result_model.intent == "unknown"
    assert result_model.confidence == 0.0
    assert result_model.method == "failure"

    # Check default date_info
    assert result_model.date_info.start_date is None
    assert result_model.date_info.end_date is None
    assert result_model.date_info.specific_time is None
    assert result_model.date_info.end_time is None
    assert result_model.date_info.is_recurring is False
    assert result_model.date_info.method is None

    # Check default filters
    assert result_model.filters.participants == []
    assert result_model.filters.title is None
    assert result_model.filters.location is None


# Test for LLM provider exception handling (Optional but good practice)
@pytest.mark.asyncio
async def test_parse_llm_provider_raises_exception(
    nlp_processor: NLPProcessor, mock_llm_provider: AsyncMock
):
    """Test processor handles exceptions raised by the LLM provider."""
    query = "query that causes error"
    # Simulate the provider raising an arbitrary exception
    mock_llm_provider.parse_scheduling_query.side_effect = ValueError("LLM API Error")

    # Call the processor
    result_model = await nlp_processor.parse_natural_language_query(query)

    # Assert on the mock method directly
    mock_llm_provider.parse_scheduling_query.assert_awaited_once_with(query)

    # Check that the processor caught the exception and returned the failure model
    assert isinstance(result_model, LLMSchedulingParseResult)
    assert result_model.intent == "unknown"
    assert result_model.confidence == 0.0
    assert result_model.method == "failure"
    # Check default nested models are present
    assert isinstance(result_model.date_info, LLMDateInfo)
    assert isinstance(result_model.filters, LLMFilters)
