from datetime import UTC, datetime
import pytest
from pydantic import ValidationError
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from sa.a2a.dto import (
    Artifact,
    InternalError,
    InvalidParamsError,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from sa.a2a.command.structured_create import handle_structured_create
from sa.core.exceptions import ConflictError
from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate
from sa.db.models import Event
from sa.db.strategies.create_event import CreateEventStrategy
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils

logger = get_logger(__name__)

@pytest.fixture
def valid_create_input() -> dict:
    """Provides valid input data for creating an event."""
    return {
        "title": "Test Event",
        "start_time": "2024-01-01T10:00:00Z",
        "end_time": "2024-01-01T11:00:00Z",
        "time_zone": "UTC",
        "location": "Test Location",
        "notes": "Test notes",
        "participant_identifiers": ["alice@example.com"]
    }

@pytest.fixture
def created_db_event(valid_create_input: dict) -> Event:
    """Provides a sample Event object as returned by CreateEventStrategy.execute."""
    # Create a dictionary excluding participant_identifiers which is not a direct field of Event
    event_data = {k: v for k, v in valid_create_input.items() if k != "participant_identifiers"}
    # Parse datetime strings to naive datetime objects
    start_dt = datetime.fromisoformat(event_data["start_time"].replace("Z", "+00:00"))
    end_dt = datetime.fromisoformat(event_data["end_time"].replace("Z", "+00:00"))
    # Ensure naive UTC for DB model
    event_data["start_time"] = start_dt.replace(tzinfo=None)
    event_data["end_time"] = end_dt.replace(tzinfo=None)
    return Event(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
        **event_data
    )

@pytest.fixture
def invalid_format_create_input() -> dict:
    """Provides input data with an invalid time format."""
    return {
        "title": "Invalid Time Event",
        "start_time": "Jan 1st 10am", # Invalid format
        "end_time": "Jan 1st 11am",   # Invalid format
        "time_zone": "UTC",
    }


@pytest.mark.asyncio
async def test_handle_structured_create_success(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    valid_create_input: dict,
    created_db_event: Event
):
    """Tests successful event creation via structured input.

    Mocks the CreateEventStrategy.execute method.
    """
    task_id = "test_task_123"

    # Construct valid TaskStatus, Message, Artifact for mock task manager return
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Mock Success")]))
    mock_artifact = Artifact(name="mock_artifact", parts=[Part(type="text", text="Mock Data")])
    mock_task_manager.complete_task.return_value = Task(
        id=task_id, status=mock_status, artifacts=[mock_artifact], history=[]
    )

    # Mock the strategy execution
    with patch("sa.a2a.command.structured_create.CreateEventStrategy.execute", return_value=created_db_event) as mock_execute:

        # Call the handler, passing real date_utils and conflict_resolver
        result_task = await handle_structured_create(
            task_id=task_id,
            input_data=valid_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    # Assertions
    mock_execute.assert_awaited_once()
    # Verify EventCreate object was passed correctly to the strategy
    call_args, _ = mock_execute.await_args
    assert isinstance(call_args[0], EventCreate)
    assert call_args[0].title == valid_create_input["title"]
    # Pydantic automatically converts valid ISO strings to datetime
    assert call_args[0].start_time == datetime.fromisoformat(valid_create_input["start_time"].replace("Z", "+00:00"))

    mock_task_manager.complete_task.assert_awaited_once()
    # Verify correct arguments passed to complete_task
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert complete_call_args[1].parts[0].text == f"Event '{created_db_event.title}' created successfully."
    assert isinstance(complete_call_args[2], Artifact)
    assert complete_call_args[2].name == "created_event"
    assert complete_call_args[2].parts[0].type == "data"
    assert complete_call_args[2].parts[0].data["id"] == str(created_db_event.id) # Check ID in artifact

    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_handle_structured_create_conflict(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    valid_create_input: dict,
):
    """Tests the conflict path during event creation.

    Mocks CreateEventStrategy.execute to raise ConflictError.
    Does NOT mock conflict_resolver methods.
    """
    task_id = "test_task_conflict"

    # Simple dict for event data to be returned by mock dump
    conflict_event_data = {
        "id": str(uuid.uuid4()), "title": "Existing Event", "start_time": "2024-01-01T10:30:00Z",
        "end_time": "2024-01-01T11:30:00Z", "time_zone": "UTC"
    }
    # Mock an Event instance that returns the dict above when dumped
    mock_event_instance = MagicMock(spec=Event)
    mock_event_instance.model_dump.return_value = conflict_event_data

    # Construct ConflictError using the mock Event instance
    conflict_error = ConflictError(conflicts=[{"type": "instance", "event": mock_event_instance}])

    # Mock task manager return for require_input
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=Message(role="agent", parts=[Part(type="text", text="Mock Input Req")]))
    mock_artifact = Artifact(name="mock_conflict", parts=[Part(type="data", data={"conflict": True})])
    mock_task_manager.require_input_task.return_value = Task(
        id=task_id, status=mock_status, artifacts=[mock_artifact], history=[]
    )

    # Patch strategy execute ONLY
    with patch("sa.a2a.command.structured_create.CreateEventStrategy.execute", side_effect=conflict_error) as mock_execute:

        # Call the handler
        result_task = await handle_structured_create(
            task_id=task_id,
            input_data=valid_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    # Assertions
    mock_execute.assert_awaited_once()
    # Check EventCreate passed to strategy
    strat_call_args, _ = mock_execute.await_args
    assert isinstance(strat_call_args[0], EventCreate)
    assert strat_call_args[0].title == valid_create_input["title"]

    mock_task_manager.require_input_task.assert_awaited_once()
    # Verify correct arguments passed to require_input_task
    req_call_args, _ = mock_task_manager.require_input_task.await_args
    assert req_call_args[0] == task_id
    assert isinstance(req_call_args[1], Message)
    assert "conflict" in req_call_args[1].parts[0].text.lower()
    assert "modify your request" in req_call_args[1].parts[0].text.lower() # Check updated message
    assert isinstance(req_call_args[2], Artifact)
    assert req_call_args[2].name == "conflict_info"
    assert req_call_args[2].parts[0].type == "data"
    assert "conflicts" in req_call_args[2].parts[0].data
    assert "alternatives" not in req_call_args[2].parts[0].data # Verify alternatives are removed
    # Check conflict data in artifact matches the dumped data from the mock instance
    assert req_call_args[2].parts[0].data["conflicts"] == [conflict_event_data] # Compare with the dict

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.INPUT_REQUIRED

@pytest.mark.asyncio
async def test_handle_structured_create_conflict_alternative_generation_fails(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    valid_create_input: dict,
):
    """Tests the path where conflict occurs and reporting the conflict fails (simulates internal error)."""
    task_id = "test_task_alt_fail"
    # Mock an Event instance for the conflict error
    mock_event_instance = MagicMock(spec=Event)
    mock_event_instance.model_dump.return_value = {"id": str(uuid.uuid4()), "title": "Existing Event"}
    conflict_error = ConflictError(conflicts=[{"type": "instance", "event": mock_event_instance}])

    reporting_error = ValueError("Failed to create artifact") # Simulate error during conflict handling

    # Patch strategy execute to raise conflict, and task manager to raise error during handling
    with patch("sa.a2a.command.structured_create.CreateEventStrategy.execute", side_effect=conflict_error) as mock_execute, \
         patch.object(mock_task_manager, 'require_input_task', side_effect=reporting_error) as mock_require_input:

        # Call the handler and expect InternalError because require_input_task failed
        with pytest.raises(InternalError) as exc_info:
            await handle_structured_create(
                task_id=task_id,
                input_data=valid_create_input,
                task_manager=mock_task_manager,
                db_service=mock_db_service,
                conflict_resolver=conflict_resolver,
                date_utils=date_utils,
            )

    # Assertions
    assert "error occurred while reporting them" in str(exc_info.value)
    assert exc_info.value.__cause__ is reporting_error # Check the original error is chained

    mock_execute.assert_awaited_once()
    mock_require_input.assert_awaited_once() # Verify require_input was called
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()


@pytest.mark.asyncio
async def test_handle_structured_create_invalid_data(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    invalid_format_create_input: dict # Use the fixture with invalid time format
):
    """Tests the handler with invalid input data (invalid format)."""
    task_id = "test_task_invalid"

    # No need to mock strategy or resolver as validation happens first

    # Call the handler and expect InvalidParamsError due to Pydantic validation
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_create(
            task_id=task_id,
            input_data=invalid_format_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    # Assertions - Expecting the error raised from the Pydantic validation block
    assert "Invalid data provided for event creation." in str(exc_info.value)
    assert isinstance(exc_info.value.data, list) # Pydantic error list
    assert isinstance(exc_info.value.__cause__, ValidationError)

    # Verify nothing else was called
    # Strategy instantiation happens inside, so no direct mock available unless we patch CreateEventStrategy.__init__
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_structured_create_not_matching_criteria(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
):
    """Tests input data that doesn't match the expected create structure (missing time)."""
    task_id = "test_task_no_match"
    non_create_input = {"title": "Event with no time"} # Missing time field

    # No need to mock strategy or resolver as the initial check fails

    # Call the handler and expect InvalidParamsError from the initial check
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_create(
            task_id=task_id,
            input_data=non_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    # Assertions - Expecting the error raised from the 'else' block
    assert "Could not determine create action from structured data provided." in str(exc_info.value)

    # Verify nothing else was called
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
