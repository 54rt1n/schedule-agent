import pytest
import uuid
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, ANY

from pydantic import ValidationError

from scheduling_agent.handlers.structured_update import handle_structured_update
from scheduling_agent.db.schemas import EventUpdate
from scheduling_agent.db.models import Event, ModifiedInstance
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.a2a.types import (
    Task,
    TaskState,
    Message,
    Part,
    Artifact,
    StructuredUpdatePayload,
    TaskStatus,
    InvalidParamsError,
    InternalError,
)


@pytest.fixture
def event_id() -> uuid.UUID:
    return uuid.uuid4()

@pytest.fixture
def valid_update_data() -> dict:
    """Provides valid partial update data for an event."""
    return {
        "title": "Updated Test Event",
        "location": "Updated Location",
    }

@pytest.fixture
def valid_update_payload(event_id: uuid.UUID, valid_update_data: dict) -> dict:
    """Provides a valid structured update payload dictionary."""
    return {
        "event_id": str(event_id), # Ensure UUID is stringified for payload validation
        "update_data": valid_update_data,
    }

@pytest.fixture
def updated_db_event(event_id: uuid.UUID) -> Event:
    """Provides a sample updated Event object as returned by db_service.update_event."""
    return Event(
        id=event_id,
        title="Updated Test Event",
        start_time=datetime(2024, 1, 1, 10, 0, tzinfo=None),
        end_time=datetime(2024, 1, 1, 11, 0, tzinfo=None),
        time_zone="UTC",
        location="Updated Location",
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
    )

# --- Test Cases --- #

@pytest.mark.asyncio
async def test_handle_structured_update_success(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock, # Even if unused by func, fixture is needed
    event_id: uuid.UUID,
    valid_update_payload: dict,
    updated_db_event: Event,
):
    """Tests successful event update via structured input."""
    task_id = "test_update_task_123"

    # Configure mock return values
    mock_db_service.update_event.return_value = updated_db_event
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Mock Success")]))
    mock_artifact = Artifact(name="mock_artifact", parts=[Part(type="text", text="Mock Data")])
    mock_task_manager.complete_task.return_value = Task(id=task_id, status=mock_status, artifacts=[mock_artifact], history=[])

    # Call the handler
    result_task = await handle_structured_update(
        task_id=task_id,
        input_data=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.update_event.assert_awaited_once_with(
        event_id=event_id, update_data=ANY
    )
    # Check the type and content of update_data passed
    call_args, call_kwargs = mock_db_service.update_event.await_args
    assert isinstance(call_kwargs['update_data'], EventUpdate)
    assert call_kwargs['update_data'].title == valid_update_payload['update_data']['title']
    assert call_kwargs['update_data'].location == valid_update_payload['update_data']['location']

    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert f"Event '{event_id}' updated successfully." in complete_call_args[1].parts[0].text
    assert isinstance(complete_call_args[2], Artifact)
    assert complete_call_args[2].name == "updated_event_details"
    assert complete_call_args[2].parts[0].data["id"] == str(updated_db_event.id)

    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.COMPLETED

@pytest.mark.asyncio
async def test_handle_structured_update_event_not_found(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    event_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Tests the handler when the event to update is not found."""
    task_id = "test_update_not_found"

    # Configure mocks
    mock_db_service.update_event.return_value = None # Simulate event not found
    mock_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[Part(type="text", text="Mock Fail")]))
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_structured_update(
        task_id=task_id,
        input_data=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.update_event.assert_awaited_once_with(event_id=event_id, update_data=ANY)
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id
    assert isinstance(fail_call_args[1], Message)
    assert f"Could not find event with ID '{event_id}'" in fail_call_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_structured_update_conflict(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    event_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Tests the handler when a conflict occurs during update."""
    task_id = "test_update_conflict"
    conflict_details = [{"type": "instance", "event": {"id": str(uuid.uuid4()), "title": "Conflict Event"}}]

    # Configure mocks
    mock_db_service.update_event.side_effect = ConflictError(conflicts=conflict_details)
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=Message(role="agent", parts=[Part(type="text", text="Mock Input Req")]))
    mock_artifact = Artifact(name="mock_conflict", parts=[Part(type="data", data={'conflict': True})])
    mock_task_manager.require_input_task.return_value = Task(id=task_id, status=mock_status, artifacts=[mock_artifact], history=[])

    # Call the handler
    result_task = await handle_structured_update(
        task_id=task_id,
        input_data=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.update_event.assert_awaited_once_with(event_id=event_id, update_data=ANY)
    mock_task_manager.require_input_task.assert_awaited_once()
    req_call_args, _ = mock_task_manager.require_input_task.await_args
    assert req_call_args[0] == task_id
    assert isinstance(req_call_args[1], Message)
    assert "failed due to scheduling conflicts" in req_call_args[1].parts[0].text
    assert isinstance(req_call_args[2], Artifact)
    assert req_call_args[2].name == "scheduling_conflicts"
    assert req_call_args[2].parts[0].data == conflict_details

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.INPUT_REQUIRED

@pytest.mark.asyncio
async def test_handle_structured_update_not_implemented(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    event_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Tests the handler when the update operation is not implemented."""
    task_id = "test_update_not_impl"
    error_message = "Updating recurring series time is not supported."

    # Configure mocks
    mock_db_service.update_event.side_effect = NotImplementedError(error_message)
    mock_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[Part(type="text", text="Mock Fail")]))
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_structured_update(
        task_id=task_id,
        input_data=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.update_event.assert_awaited_once_with(event_id=event_id, update_data=ANY)
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id
    assert isinstance(fail_call_args[1], Message)
    assert error_message in fail_call_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_structured_update_value_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    event_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Tests the handler when a ValueError occurs during update (e.g., invalid instance time)."""
    task_id = "test_update_value_err"
    error_message = "Original start time missing for instance update."

    # Configure mocks
    mock_db_service.update_event.side_effect = ValueError(error_message)
    mock_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[Part(type="text", text="Mock Fail")]))
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_structured_update(
        task_id=task_id,
        input_data=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
    )

    # Assertions
    mock_db_service.update_event.assert_awaited_once_with(event_id=event_id, update_data=ANY)
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id
    assert isinstance(fail_call_args[1], Message)
    assert error_message in fail_call_args[1].parts[0].text

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

    assert result_task is not None
    assert result_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_structured_update_validation_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    event_id: uuid.UUID,
):
    """Tests the handler with invalid structured update payload."""
    task_id = "test_update_validation_err"
    invalid_payload = {"event_id": str(event_id), "update_data": {"start_time": "invalid-date"}}

    # Call the handler and expect InvalidParamsError
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_update(
            task_id=task_id,
            input_data=invalid_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions
    assert "Invalid data provided for event update." in str(exc_info.value)
    assert isinstance(exc_info.value.data, list) # Pydantic error list
    assert isinstance(exc_info.value.__cause__, ValidationError)

    mock_db_service.update_event.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_structured_update_internal_error(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    event_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Tests the handler when an unexpected error occurs."""
    task_id = "test_update_internal_err"
    original_exception = Exception("Unexpected DB failure")

    # Configure mocks
    mock_db_service.update_event.side_effect = original_exception

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_update(
            task_id=task_id,
            input_data=valid_update_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions
    assert "An unexpected error occurred while updating event." in str(exc_info.value)
    assert exc_info.value.__cause__ is original_exception

    mock_db_service.update_event.assert_awaited_once()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()
