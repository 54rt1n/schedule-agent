import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from schedule_a2a.command.structured_delete import handle_structured_delete
from schedule_a2a.dto import Task, TaskState, TaskStatus, Message, Part
from schedule_core.db.dao import EventDelete
from schedule_core.event.date_utils import DateUtils
from schedule_a2a.exceptions import InvalidParamsError, InternalError
from pydantic import ValidationError # For checking cause

# Mock return tasks from TaskManager
mock_completed_task = Task(
    id="mock_task_id_completed",
    status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
)
mock_failed_task = Task(
    id="mock_task_id_failed",
    status=TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[])),
)


@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_delete.DeleteEventStrategy")
async def test_handle_structured_delete_success(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete successfully deleting an event with unwrapped payload."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()

    # Prepare the raw delete payload dictionary
    delete_payload = EventDelete(id=event_id_to_delete).model_dump()
    # No longer need message

    # Mock strategy (remains the same)
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = True
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response (remains the same)
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Call the handler, passing the payload dict directly
    final_task = await handle_structured_delete(
        task_id=task_id,
        payload=delete_payload, # Pass dict directly
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions (remain the same)
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=EventDelete(id=event_id_to_delete))
    assert final_task is not None
    assert final_task.status.state == TaskState.COMPLETED
    expected_message = f"Successfully deleted event {event_id_to_delete}."
    mock_task_manager.complete_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.fail_task.assert_not_awaited()


@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_delete.DeleteEventStrategy")
async def test_handle_structured_delete_not_found(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
    # date_utils: DateUtils, # Removed unused fixture
):
    """Test handle_structured_delete when the event is not found with unwrapped payload."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()

    # Prepare the raw delete payload dictionary
    delete_payload = EventDelete(id=event_id_to_delete).model_dump()
    # No longer need message

    # Mock strategy (remains the same)
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = False
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response (remains the same)
    mock_task_manager.fail_task.return_value = mock_failed_task

    # Call the handler, passing the payload dict directly
    final_task = await handle_structured_delete(
        task_id=task_id,
        payload=delete_payload, # Pass dict directly
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions (remain the same)
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=EventDelete(id=event_id_to_delete))
    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED
    expected_message = f"Event with ID {event_id_to_delete} not found for deletion."
    mock_task_manager.fail_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.complete_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_structured_delete_invalid_payload_validation(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete raises InvalidParamsError for invalid EventDelete payload."""
    task_id = str(uuid.uuid4())
    # Invalid payload (e.g., missing 'id')
    invalid_delete_payload = {"wrong_key": "some_value"}
    # No longer need message

    # Expect InvalidParamsError from the handler's internal validation
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_delete(
            task_id=task_id,
            payload=invalid_delete_payload, # Pass invalid dict
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assert on the caught exception (remains the same)
    assert "Invalid delete parameters" in str(exc_info.value)
    assert isinstance(exc_info.value.data, list)
    assert exc_info.value.data[0]["type"] == "missing"
    assert exc_info.value.data[0]["loc"] == ("id",)

    # Ensure TaskManager was not called (remains the same)
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_delete.DeleteEventStrategy")
async def test_handle_structured_delete_db_exception(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete raises InternalError when DB operation fails with unwrapped payload."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()
    # Prepare the raw delete payload dictionary
    delete_payload = EventDelete(id=event_id_to_delete).model_dump()
    # No longer need message

    # Mock strategy to raise exception (remains the same)
    mock_delete_strategy_instance = AsyncMock()
    db_error = Exception("Database connection error")
    mock_delete_strategy_instance.execute.side_effect = db_error
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Expect InternalError from the handler
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_delete(
            task_id=task_id,
            payload=delete_payload, # Pass dict directly
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions (remain the same)
    assert "An unexpected error occurred" in str(exc_info.value)
    assert "Database connection error" in str(exc_info.value)
    assert exc_info.value.__cause__ is db_error
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=EventDelete(id=event_id_to_delete))
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.complete_task.assert_not_called() 