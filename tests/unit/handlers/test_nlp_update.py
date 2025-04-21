import pytest
import uuid
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, ANY

from pydantic import ValidationError

from scheduling_agent.handlers.nlp_update import handle_nlp_update
from scheduling_agent.db.models import Event, ModifiedInstance
from scheduling_agent.db.schemas import EventUpdate, EventQuery
from scheduling_agent.a2a.types import (
    Task,
    TaskState,
    Message,
    Part,
    Artifact,
    TaskStatus,
    InternalError,
)
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMFilters, LLMDateInfo
from scheduling_agent.core.exceptions import ConflictError, DatabaseError


# --- Fixtures --- #

@pytest.fixture
def mock_nlp_result_update() -> LLMSchedulingParseResult:
    """Provides a mock NLP result for updating an event."""
    return LLMSchedulingParseResult(
        intent="update_event",
        filters=LLMFilters(title="Existing Meeting"),
        date_info=LLMDateInfo(start_date="2024-02-01", specific_time="15:00:00", timeZone="America/New_York")
    )

@pytest.fixture
def mock_nlp_result_update_no_changes() -> LLMSchedulingParseResult:
    """Provides a mock NLP result with only a query title, no update data."""
    return LLMSchedulingParseResult(
        intent="update_event",
        filters=LLMFilters(title="Existing Meeting")
        # No date_info or other filters to extract update fields from
    )

@pytest.fixture
def mock_nlp_result_update_bad_date() -> LLMSchedulingParseResult:
    """Provides a mock NLP result with an unparseable date."""
    return LLMSchedulingParseResult(
        intent="update_event",
        filters=LLMFilters(title="Existing Meeting"),
        date_info=LLMDateInfo(start_date="tomorrow morning maybe?")
    )

@pytest.fixture
def found_event() -> Event:
    """Provides a sample unique event returned by find_events."""
    return Event(
        id=uuid.uuid4(),
        title="Existing Meeting",
        start_time=datetime(2024, 1, 15, 10, 0, tzinfo=None),
        end_time=datetime(2024, 1, 15, 11, 0, tzinfo=None),
        time_zone="UTC",
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )

@pytest.fixture
def multiple_found_events(found_event) -> list[Event]:
    """Provides multiple events returned by find_events."""
    event2 = Event(
        id=uuid.uuid4(),
        title="Existing Meeting LATER",
        start_time=datetime(2024, 1, 20, 10, 0, tzinfo=None),
        end_time=datetime(2024, 1, 20, 11, 0, tzinfo=None),
        time_zone="UTC",
    )
    return [found_event, event2]

@pytest.fixture
def updated_event_instance(found_event) -> Event:
    """Provides a sample updated Event object after update."""
    # Simulate the state *after* update based on mock_nlp_result_update
    return Event(
        id=found_event.id,
        title="Existing Meeting", # Title not updated in this example
        start_time=datetime(2024, 2, 1, 20, 0, tzinfo=None), # 15:00 America/New_York is 20:00 UTC
        end_time=datetime(2024, 2, 1, 21, 0, tzinfo=None), # Assumes 1hr duration
        time_zone="America/New_York",
        updated_at=datetime.now(UTC).replace(tzinfo=None), # Updated timestamp
        # Other fields inherited or unchanged
        created_at=found_event.created_at,
    )

# --- Test Cases --- #

@pytest.mark.asyncio
async def test_handle_nlp_update_success(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update: LLMSchedulingParseResult,
    found_event: Event,
    updated_event_instance: Event,
):
    """Tests successful update of a unique event found via NLP query."""
    task_id = "test_update_task_success"
    parsed_datetime = datetime(2024, 2, 1, 15, 0, 0) # Example parsed time

    # Configure mocks
    mock_db_service.find_events.return_value = [found_event]
    mock_date_utils.parse_datetime.return_value = parsed_datetime
    mock_db_service.update_event.return_value = updated_event_instance
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=None)
    mock_task_manager.complete_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock() # Provide the mock, even if unused
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    find_args, find_kwargs = mock_db_service.find_events.await_args
    assert isinstance(find_kwargs['query'], EventQuery)
    assert find_kwargs['query'].title_contains == mock_nlp_result_update.filters.title

    mock_date_utils.parse_datetime.assert_called()

    mock_db_service.update_event.assert_awaited_once()
    update_args, update_kwargs = mock_db_service.update_event.await_args
    assert update_kwargs['event_id'] == found_event.id
    assert isinstance(update_kwargs['update_data'], EventUpdate)
    assert update_kwargs['update_data'].start_time == parsed_datetime
    assert update_kwargs['update_data'].time_zone == "America/New_York"
    # End time assertion depends on duration calculation, harder to mock precisely here
    # assert update_kwargs['update_data'].end_time is not None

    mock_task_manager.complete_task.assert_awaited_once()
    complete_args, _ = mock_task_manager.complete_task.await_args
    assert complete_args[0] == task_id
    assert isinstance(complete_args[1], Message)
    assert f"updated the event '{found_event.title}'" in complete_args[1].parts[0].text
    assert isinstance(complete_args[2], Artifact)
    assert complete_args[2].name == "updated_event_details"
    assert complete_args[2].parts[0].data["id"] == str(updated_event_instance.id)

    assert result_task.status.state == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_handle_nlp_update_no_event_found(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update: LLMSchedulingParseResult,
):
    """Tests handler when no event matches the NLP query title."""
    task_id = "test_update_task_not_found"

    # Configure mocks
    mock_db_service.find_events.return_value = [] # No events found
    mock_status = TaskStatus(state=TaskState.FAILED, message=None)
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock()
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    mock_task_manager.fail_task.assert_awaited_once()
    fail_args, _ = mock_task_manager.fail_task.await_args
    assert fail_args[0] == task_id
    assert isinstance(fail_args[1], Message)
    assert f"couldn't find an event titled '{mock_nlp_result_update.filters.title}'" in fail_args[1].parts[0].text

    mock_db_service.update_event.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_nlp_update_multiple_events_found(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update: LLMSchedulingParseResult,
    multiple_found_events: list[Event],
):
    """Tests handler when multiple events match the NLP query title."""
    task_id = "test_update_task_multiple_found"

    # Configure mocks
    mock_db_service.find_events.return_value = multiple_found_events
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=None)
    mock_task_manager.require_input_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock()
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    mock_task_manager.require_input_task.assert_awaited_once()
    req_args, _ = mock_task_manager.require_input_task.await_args
    assert req_args[0] == task_id
    assert isinstance(req_args[1], Message)
    assert "found multiple events titled" in req_args[1].parts[0].text
    assert isinstance(req_args[2], Artifact)
    assert req_args[2].name == "ambiguous_events"
    assert len(req_args[2].parts[0].data) == len(multiple_found_events)

    mock_db_service.update_event.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task.status.state == TaskState.INPUT_REQUIRED

@pytest.mark.asyncio
async def test_handle_nlp_update_conflict_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update: LLMSchedulingParseResult,
    found_event: Event,
):
    """Tests handler when db_service.update_event raises ConflictError."""
    task_id = "test_update_task_conflict"
    conflict_details = [{"type": "instance", "event": {"id": str(uuid.uuid4()), "title": "Conflict Event"}}]

    # Configure mocks
    mock_db_service.find_events.return_value = [found_event]
    mock_date_utils.parse_datetime.return_value = datetime(2024, 2, 1, 15, 0, 0)
    mock_db_service.update_event.side_effect = ConflictError(conflicts=conflict_details)
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=None)
    mock_task_manager.require_input_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock()
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    mock_db_service.update_event.assert_awaited_once()
    mock_task_manager.require_input_task.assert_awaited_once()
    req_args, _ = mock_task_manager.require_input_task.await_args
    assert req_args[0] == task_id
    assert isinstance(req_args[1], Message)
    assert "failed due to scheduling conflicts" in req_args[1].parts[0].text
    assert isinstance(req_args[2], Artifact)
    assert req_args[2].name == "scheduling_conflicts"
    assert req_args[2].parts[0].data == conflict_details

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task.status.state == TaskState.INPUT_REQUIRED


@pytest.mark.asyncio
async def test_handle_nlp_update_not_implemented_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update: LLMSchedulingParseResult,
    found_event: Event,
):
    """Tests handler when db_service.update_event raises NotImplementedError."""
    task_id = "test_update_task_not_implemented"
    error_message = "Series update not supported"

    # Configure mocks
    mock_db_service.find_events.return_value = [found_event]
    mock_date_utils.parse_datetime.return_value = datetime(2024, 2, 1, 15, 0, 0)
    mock_db_service.update_event.side_effect = NotImplementedError(error_message)
    mock_status = TaskStatus(state=TaskState.FAILED, message=None)
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock()
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    mock_db_service.update_event.assert_awaited_once()
    mock_task_manager.fail_task.assert_awaited_once()
    fail_args, _ = mock_task_manager.fail_task.await_args
    assert fail_args[0] == task_id
    assert isinstance(fail_args[1], Message)
    assert error_message in fail_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_nlp_update_value_error_no_update_data(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update_no_changes: LLMSchedulingParseResult, # NLP result yields no updates
    found_event: Event,
):
    """Tests handler when NLP result provides no actionable update data."""
    task_id = "test_update_task_no_update_data"

    # Configure mocks
    mock_db_service.find_events.return_value = [found_event]
    # date_utils should not be called if no date_info in NLP result
    mock_status = TaskStatus(state=TaskState.FAILED, message=None)
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update_no_changes,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock()
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    mock_date_utils.parse_datetime.assert_not_called()
    mock_db_service.update_event.assert_not_called()
    mock_task_manager.fail_task.assert_awaited_once()
    fail_args, _ = mock_task_manager.fail_task.await_args
    assert fail_args[0] == task_id
    assert isinstance(fail_args[1], Message)
    assert "wasn't clear what changes" in fail_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_nlp_update_value_error_bad_date_parse(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_date_utils: MagicMock,
    mock_nlp_result_update_bad_date: LLMSchedulingParseResult, # NLP result has bad date
    found_event: Event,
):
    """Tests handler when date parsing from NLP result fails."""
    task_id = "test_update_task_bad_date"
    parse_error = ValueError("Cannot parse date string")

    # Configure mocks
    mock_db_service.find_events.return_value = [found_event]
    mock_date_utils.parse_datetime.side_effect = parse_error
    mock_status = TaskStatus(state=TaskState.FAILED, message=None)
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_update(
        task_id=task_id,
        nlp_result=mock_nlp_result_update_bad_date,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=mock_date_utils,
        conflict_resolver=MagicMock()
    )

    # Assertions
    mock_db_service.find_events.assert_awaited_once()
    mock_date_utils.parse_datetime.assert_called()
    mock_db_service.update_event.assert_not_called()
    mock_task_manager.fail_task.assert_awaited_once()
    fail_args, _ = mock_task_manager.fail_task.await_args
    assert fail_args[0] == task_id
    assert isinstance(fail_args[1], Message)
    assert "couldn't understand the new date/time" in fail_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task.status.state == TaskState.FAILED
