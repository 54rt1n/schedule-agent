import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from scheduling_agent.a2a.types import (
    Artifact,
    InternalError,
    InvalidParamsError,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate
from scheduling_agent.handlers.structured_create import handle_structured_create


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
    """Provides a sample Event object as returned by db_service.create_event."""
    # Create a dictionary excluding participant_identifiers which is not a direct field of Event
    event_data = {k: v for k, v in valid_create_input.items() if k != "participant_identifiers"}
    # Parse datetime strings to naive datetime objects
    event_data["start_time"] = datetime.fromisoformat(event_data["start_time"].replace("Z", "+00:00")).replace(tzinfo=None)
    event_data["end_time"] = datetime.fromisoformat(event_data["end_time"].replace("Z", "+00:00")).replace(tzinfo=None)
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
    mock_conflict_resolver: AsyncMock,
    valid_create_input: dict,
    created_db_event: Event
):
    """Tests successful event creation via structured input."""
    task_id = "test_task_123"

    # Configure mock return values
    mock_db_service.create_event.return_value = created_db_event

    # Construct valid TaskStatus, Message, Artifact for mock return
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Mock Success")]))
    mock_artifact = Artifact(name="mock_artifact", parts=[Part(type="text", text="Mock Data")])
    mock_task_manager.complete_task.return_value = Task(
        id=task_id, status=mock_status, artifacts=[mock_artifact], history=[]
    )

    # Call the handler
    result_task = await handle_structured_create(
        task_id=task_id,
        input_data=valid_create_input,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.create_event.assert_awaited_once()
    # Verify EventCreate object was passed correctly (flexible matching)
    call_args, call_kwargs = mock_db_service.create_event.await_args
    assert isinstance(call_kwargs["event_data"], EventCreate)
    assert call_kwargs["event_data"].title == valid_create_input["title"]
    assert call_kwargs["event_data"].start_time == datetime.fromisoformat(valid_create_input["start_time"].replace("Z", "+00:00"))

    mock_conflict_resolver.detect_conflicts.assert_not_called() # Should not be called directly by handler
    mock_conflict_resolver.generate_alternative_times.assert_not_called()

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
    mock_conflict_resolver: AsyncMock,
    valid_create_input: dict,
):
    """Tests the conflict path during event creation."""
    task_id = "test_task_conflict"
    # Use a simple dict for event in conflict details to avoid Pydantic issues in test setup
    conflict_event_data = {
        "id": str(uuid.uuid4()),
        "title": "Existing Event",
        "start_time": "2024-01-01T10:30:00Z",
        "end_time": "2024-01-01T11:30:00Z",
        "time_zone": "UTC",
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
        "location": None,
        "notes": None,
        "rrule": None
    }
    conflict_details = [{"type": "instance", "event": conflict_event_data }]
    # Mock the Event model's dump for the conflict artifact creation
    mock_event_instance = MagicMock(spec=Event)
    mock_event_instance.model_dump.return_value = conflict_event_data

    alternative_times = [{"startTime": "2024-01-01T11:30:00Z", "endTime": "2024-01-01T12:30:00Z", "label": "Later"}]

    # Configure mocks for conflict path
    # Use a ConflictError instance that holds the serializable dict, not the Event model
    mock_db_service.create_event.side_effect = ConflictError(conflicts=[{"type": "instance", "event": mock_event_instance}])
    mock_conflict_resolver.generate_alternative_times.return_value = alternative_times

    # Construct valid TaskStatus, Message, Artifact for mock return
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=Message(role="agent", parts=[Part(type="text", text="Mock Input Req")]))
    mock_artifact = Artifact(name="mock_conflict", parts=[Part(type="data", data={"conflict": True})])
    mock_task_manager.require_input_task.return_value = Task(
        id=task_id, status=mock_status, artifacts=[mock_artifact], history=[]
    )

    # Call the handler
    result_task = await handle_structured_create(
        task_id=task_id,
        input_data=valid_create_input,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.create_event.assert_awaited_once()
    mock_conflict_resolver.generate_alternative_times.assert_awaited_once()
    # Check args passed to generate_alternative_times
    alt_call_args, _ = mock_conflict_resolver.generate_alternative_times.await_args
    assert isinstance(alt_call_args[0], EventCreate)
    assert alt_call_args[0].title == valid_create_input["title"]
    # Check that the conflict detail passed contains the mock event
    assert len(alt_call_args[1]) == 1
    assert alt_call_args[1][0]["event"] is mock_event_instance

    mock_task_manager.require_input_task.assert_awaited_once()
    # Verify correct arguments passed to require_input_task
    req_call_args, _ = mock_task_manager.require_input_task.await_args
    assert req_call_args[0] == task_id
    assert isinstance(req_call_args[1], Message)
    assert "conflict" in req_call_args[1].parts[0].text.lower()
    assert isinstance(req_call_args[2], Artifact)
    assert req_call_args[2].name == "conflict_info"
    assert req_call_args[2].parts[0].type == "data"
    assert "conflicts" in req_call_args[2].parts[0].data
    assert "alternatives" in req_call_args[2].parts[0].data
    # Check serializable data was used in artifact
    assert req_call_args[2].parts[0].data["conflicts"] == [conflict_event_data]
    assert req_call_args[2].parts[0].data["alternatives"] == alternative_times

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.INPUT_REQUIRED

@pytest.mark.asyncio
async def test_handle_structured_create_conflict_alternative_generation_fails(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    valid_create_input: dict,
):
    """Tests the path where conflict occurs and alternative generation fails."""
    task_id = "test_task_alt_fail"
    # Use a mock object for the event in conflict details
    mock_event_instance = MagicMock(spec=Event)
    mock_event_instance.model_dump.return_value = {"id": str(uuid.uuid4()), "title": "Existing Event"}
    conflict_details = [{"type": "instance", "event": mock_event_instance}]
    alternative_error = ValueError("Could not generate alternatives")

    # Configure mocks for conflict path with alternative generation failure
    mock_db_service.create_event.side_effect = ConflictError(conflicts=conflict_details)
    mock_conflict_resolver.generate_alternative_times.side_effect = alternative_error

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_create(
            task_id=task_id,
            input_data=valid_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions
    assert "failed due to conflicts" in str(exc_info.value)
    assert "generating alternatives also failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is alternative_error

    mock_db_service.create_event.assert_awaited_once()
    mock_conflict_resolver.generate_alternative_times.assert_awaited_once()
    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()


@pytest.mark.asyncio
async def test_handle_structured_create_invalid_data(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    invalid_format_create_input: dict # Use the fixture with invalid time format
):
    """Tests the handler with invalid input data (invalid format)."""
    task_id = "test_task_invalid"

    # Call the handler and expect InvalidParamsError due to Pydantic validation
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_create(
            task_id=task_id,
            input_data=invalid_format_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions - Expecting the error raised from the Pydantic validation block
    assert "Invalid data provided for event creation." in str(exc_info.value)
    assert isinstance(exc_info.value.data, list) # Pydantic error list
    assert isinstance(exc_info.value.__cause__, ValidationError)

    mock_db_service.create_event.assert_not_called()
    mock_conflict_resolver.generate_alternative_times.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_structured_create_not_matching_criteria(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
):
    """Tests input data that doesn't match the expected create structure (missing time)."""
    task_id = "test_task_no_match"
    non_create_input = {"title": "Event with no time"} # Missing time field

    # Call the handler and expect InvalidParamsError from the initial check
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_create(
            task_id=task_id,
            input_data=non_create_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions - Expecting the error raised from the 'else' block
    assert "Could not determine create action from structured data provided." in str(exc_info.value)

    mock_db_service.create_event.assert_not_called()
    mock_conflict_resolver.generate_alternative_times.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
