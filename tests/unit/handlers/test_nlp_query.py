import pytest
import uuid
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, ANY

from pydantic import ValidationError

from scheduling_agent.handlers.nlp_query import handle_nlp_query
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventQuery
from scheduling_agent.a2a.types import (
    Task,
    TaskState,
    Message,
    Part,
    Artifact,
    TaskStatus,
    InternalError,
)
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMFilters
from scheduling_agent.core.exceptions import DatabaseError


@pytest.fixture
def mock_nlp_result_with_filter() -> LLMSchedulingParseResult:
    """Provides a mock NLP result with a title filter."""
    return LLMSchedulingParseResult(
        intent="query_events",
        filters=LLMFilters(title="Meeting")
    )

@pytest.fixture
def mock_nlp_result_invalid_filter() -> LLMSchedulingParseResult:
    """Provides a mock NLP result with data that fails EventQuery validation."""
    # LLMFilters is valid, but EventQuery validation inside handler will fail
    # because EventQuery doesn't have a 'format' field.
    # We need to simulate data extraction logic inside the handler correctly.
    # The handler currently only extracts start_time, end_time, title_contains.
    # Let's pass an invalid type for title_contains via the mock's attributes directly.
    mock_filters = MagicMock(spec=LLMFilters)
    mock_filters.title = 123  # Invalid type for EventQuery
    mock_filters.start_time = None
    mock_filters.end_time = None
    # Ensure hasattr checks pass
    mock_filters.configure_mock(**{"start_time": None, "end_time": None, "title": 123})

    return LLMSchedulingParseResult(
        intent="query_events",
        filters=mock_filters
    )

@pytest.fixture
def queried_db_events() -> list[Event]:
    """Provides a list of sample Event objects returned by db_service.query_events."""
    return [
        Event(
            id=uuid.uuid4(),
            title="Meeting about Project X",
            start_time=datetime(2024, 1, 1, 10, 0, tzinfo=None),
            end_time=datetime(2024, 1, 1, 11, 0, tzinfo=None),
            time_zone="UTC",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        ),
        Event(
            id=uuid.uuid4(),
            title="Follow-up Meeting",
            start_time=datetime(2024, 1, 2, 14, 0, tzinfo=None),
            end_time=datetime(2024, 1, 2, 14, 30, tzinfo=None),
            time_zone="America/New_York",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        ),
    ]

# --- Test Cases --- #

@pytest.mark.asyncio
async def test_handle_nlp_query_success_with_results(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_nlp_result_with_filter: LLMSchedulingParseResult,
    queried_db_events: list[Event],
):
    """Tests successful event query with results found."""
    task_id = "test_query_task_success"

    # Configure mocks
    mock_db_service.find_events.return_value = queried_db_events
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=None)
    mock_task_manager.complete_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_query(
        task_id=task_id,
        nlp_result=mock_nlp_result_with_filter,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    call_args, call_kwargs = mock_db_service.find_events.await_args
    assert isinstance(call_kwargs['query'], EventQuery)
    assert call_kwargs['query'].title_contains == "Meeting"

    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert f"Found {len(queried_db_events)} event(s)" in complete_call_args[1].parts[0].text
    assert isinstance(complete_call_args[2], Artifact)
    assert complete_call_args[2].name == "queried_events"
    assert len(complete_call_args[2].parts[0].data) == len(queried_db_events)
    assert complete_call_args[2].parts[0].data[0]["title"] == queried_db_events[0].title

    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_handle_nlp_query_success_no_results(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_nlp_result_with_filter: LLMSchedulingParseResult,
):
    """Tests successful event query with no results found."""
    task_id = "test_query_task_no_results"

    # Configure mocks
    mock_db_service.find_events.return_value = [] # Use find_events, No events found
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=None)
    mock_task_manager.complete_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_query(
        task_id=task_id,
        nlp_result=mock_nlp_result_with_filter,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once() # Use find_events
    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert "No events found" in complete_call_args[1].parts[0].text
    assert complete_call_args[2] is None # Artifact should be None

    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_handle_nlp_query_database_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_nlp_result_with_filter: LLMSchedulingParseResult,
):
    """Tests the handler when a DatabaseError occurs during query."""
    task_id = "test_query_task_db_error"
    db_error = DatabaseError("Connection failed")

    # Configure mocks
    mock_db_service.find_events.side_effect = db_error

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_nlp_query(
            task_id=task_id,
            nlp_result=mock_nlp_result_with_filter,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An internal error occurred while querying the database." in str(exc_info.value)
    assert exc_info.value.__cause__ is db_error

    mock_db_service.find_events.assert_awaited_once()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_nlp_query_validation_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_nlp_result_invalid_filter: LLMSchedulingParseResult,
):
    """Tests the handler when extracted filters cause EventQuery validation error."""
    task_id = "test_query_task_validation_error"

    # Configure mocks
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=None)
    mock_task_manager.require_input_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_query(
        task_id=task_id,
        nlp_result=mock_nlp_result_invalid_filter,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    mock_db_service.find_events.assert_not_called() # Should fail before calling DB

    mock_task_manager.require_input_task.assert_awaited_once()
    req_call_args, _ = mock_task_manager.require_input_task.await_args
    assert req_call_args[0] == task_id
    assert isinstance(req_call_args[1], Message)
    assert "couldn't understand the details" in req_call_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.INPUT_REQUIRED
