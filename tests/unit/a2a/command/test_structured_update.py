from datetime import datetime, UTC
import json
from pydantic import ValidationError
import pytest
from unittest.mock import AsyncMock, ANY, patch, MagicMock
import uuid

from schedule_a2a.dto import (
    Task,
    TaskState,
    Message,
    Part,
    Artifact,
    TaskStatus,
)
from schedule_a2a.exceptions import InternalError, InvalidParamsError
from schedule_a2a.command.structured_update import handle_structured_update
from schedule_core.core.exceptions import ConflictError
from schedule_core.db.dao import EventUpdate
from schedule_core.db.models import Event
from schedule_core.db.strategies.update_event import UpdateEventStrategy
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils


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
def valid_update_payload_dict(event_id: uuid.UUID, valid_update_data: dict) -> dict:
    """Provides a valid raw dictionary payload for update."""
    return {"id": event_id, **valid_update_data}

@pytest.fixture
def updated_db_event(event_id: uuid.UUID) -> Event:
    """Provides a sample updated Event object as returned by UpdateEventStrategy.execute."""
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
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_success(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
    valid_update_payload_dict: dict,
    valid_update_data: dict,
    updated_db_event: Event,
):
    """Tests successful event update via structured input with unwrapped payload."""
    task_id = "test_update_task_123"

    # Configure mock strategy
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.return_value = updated_db_event
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Configure mock TaskManager
    mock_status = TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Mock Success")]))
    mock_artifact = Artifact(name="mock_artifact", parts=[Part(type="text", text="Mock Data")])
    mock_task_manager.complete_task.return_value = Task(id=task_id, status=mock_status, artifacts=[mock_artifact], history=[])

    # Call the handler, passing the payload dict directly
    result_task = await handle_structured_update(
        task_id=task_id,
        payload=valid_update_payload_dict,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # Assertions for strategy
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service, conflict_resolver=conflict_resolver, date_utils=date_utils)
    mock_update_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_update_strategy_instance.execute.await_args
    passed_data = call_kwargs['data']
    assert isinstance(passed_data, EventUpdate)
    assert passed_data.id == event_id
    assert passed_data.title == valid_update_data['title']
    assert passed_data.location == valid_update_data['location']

    # Assertions for task manager
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
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_event_not_found(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
    valid_update_payload_dict: dict,
):
    """Tests the handler when the event to update is not found with unwrapped payload."""
    task_id = "test_update_not_found"

    # Configure mock strategy
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.return_value = None
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Configure mock TaskManager
    mock_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[Part(type="text", text="Mock Fail")]))
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler, passing the payload dict directly
    result_task = await handle_structured_update(
        task_id=task_id,
        payload=valid_update_payload_dict,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # Assertions for strategy
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service, conflict_resolver=conflict_resolver, date_utils=date_utils)
    mock_update_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_update_strategy_instance.execute.await_args
    passed_data = call_kwargs['data']
    assert isinstance(passed_data, EventUpdate)
    assert passed_data.id == event_id

    # Assertions for task manager
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
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_conflict(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
    valid_update_payload_dict: dict,
):
    """Tests the handler when a conflict occurs during update with unwrapped payload."""
    task_id = "test_update_conflict"
    conflict_details = [{"type": "instance", "event": {"id": str(uuid.uuid4()), "title": "Conflict Event"}}]

    # Configure mock strategy
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = ConflictError(conflicts=conflict_details)
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Configure mock TaskManager
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=Message(role="agent", parts=[Part(type="text", text="Mock Input Req")]))
    mock_artifact = Artifact(name="mock_conflict", parts=[Part(type="data", data={'conflict': True})])
    mock_task_manager.require_input_task.return_value = Task(id=task_id, status=mock_status, artifacts=[mock_artifact], history=[])

    # Call the handler, passing the payload dict directly
    result_task = await handle_structured_update(
        task_id=task_id,
        payload=valid_update_payload_dict,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # Assertions for strategy
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service, conflict_resolver=conflict_resolver, date_utils=date_utils)
    mock_update_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_update_strategy_instance.execute.await_args
    passed_data = call_kwargs['data']
    assert isinstance(passed_data, EventUpdate)
    assert passed_data.id == event_id

    # Assertions for task manager
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
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_not_implemented(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
    valid_update_payload_dict: dict,
):
    """Tests the handler when the update operation is not implemented with unwrapped payload."""
    task_id = "test_update_not_impl"
    error_message = "Updating recurring series time is not supported."

    # Configure mock strategy
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = NotImplementedError(error_message)
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Configure mock TaskManager
    mock_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[Part(type="text", text="Mock Fail")]))
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler, passing the payload dict directly
    result_task = await handle_structured_update(
        task_id=task_id,
        payload=valid_update_payload_dict,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # Assertions for strategy
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service, conflict_resolver=conflict_resolver, date_utils=date_utils)
    mock_update_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_update_strategy_instance.execute.await_args
    passed_data = call_kwargs['data']
    assert isinstance(passed_data, EventUpdate)
    assert passed_data.id == event_id

    # Assertions for task manager
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
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_value_error(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
    valid_update_payload_dict: dict,
):
    """Tests the handler when a ValueError occurs during update with unwrapped payload."""
    task_id = "test_update_value_err"
    error_message = "Original start time missing for instance update."

    # Configure mock strategy
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = ValueError(error_message)
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Configure mock TaskManager
    mock_status = TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[Part(type="text", text="Mock Fail")]))
    mock_task_manager.fail_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler, passing the payload dict directly
    result_task = await handle_structured_update(
        task_id=task_id,
        payload=valid_update_payload_dict,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # Assertions for strategy
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service, conflict_resolver=conflict_resolver, date_utils=date_utils)
    mock_update_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_update_strategy_instance.execute.await_args
    passed_data = call_kwargs['data']
    assert isinstance(passed_data, EventUpdate)
    assert passed_data.id == event_id

    # Assertions for task manager
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
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_validation_error(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
):
    """Tests the handler with invalid structured update payload (dict)."""
    task_id = "test_update_validation_err"
    invalid_payload = {"id": str(event_id), "start_time": "invalid-date"}

    # Call the handler and expect InvalidParamsError
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_update(
            task_id=task_id,
            payload=invalid_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    # Assertions
    assert "Invalid data provided for event update." in str(exc_info.value)
    assert isinstance(exc_info.value.data, list)
    assert isinstance(exc_info.value.__cause__, ValidationError)
    mock_update_strategy_class.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_update.UpdateEventStrategy")
async def test_handle_structured_update_internal_error(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    event_id: uuid.UUID,
    valid_update_payload_dict: dict,
):
    """Tests the handler when an unexpected error occurs with unwrapped payload."""
    task_id = "test_update_internal_err"
    original_exception = Exception("Unexpected DB failure")

    # Configure mock strategy
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = original_exception
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Call the handler, passing the payload dict directly
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_update(
            task_id=task_id,
            payload=valid_update_payload_dict,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    # Assertions
    assert "An unexpected error occurred while updating event." in str(exc_info.value)
    assert exc_info.value.__cause__ is original_exception

    # Assert strategy was called, but task manager was not
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service, conflict_resolver=conflict_resolver, date_utils=date_utils)
    mock_update_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_update_strategy_instance.execute.await_args
    passed_data = call_kwargs['data']
    assert isinstance(passed_data, EventUpdate)
    assert passed_data.id == event_id

    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()
