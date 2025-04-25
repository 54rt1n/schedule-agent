import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from sa.a2a.command.structured_delete import handle_structured_delete
from sa.a2a.dto import Task, TaskState, TaskStatus, Message, Part
from sa.db.dao import EventDelete
from sa.event.date_utils import DateUtils

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
@patch("sa.a2a.command.structured_delete.DeleteEventStrategy")
async def test_handle_structured_delete_success(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete successfully deleting an event."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()  # Use UUID object

    # Example structured delete payload
    delete_data = EventDelete(id=event_id_to_delete).model_dump()
    message = Message(role="user", parts=[Part(type="data", data=delete_data)])

    # Mock the DeleteEventStrategy instance and its execute method
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = True
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Call the handler
    final_task = await handle_structured_delete(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=event_id_to_delete)
    assert final_task is not None
    assert final_task.status.state == TaskState.COMPLETED

    # Check TaskManager call
    expected_message = f"Successfully deleted event {event_id_to_delete}."
    mock_task_manager.complete_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.fail_task.assert_not_awaited()


@pytest.mark.asyncio
@patch("sa.a2a.command.structured_delete.DeleteEventStrategy")
async def test_handle_structured_delete_not_found(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
    date_utils: DateUtils,  # Keep date_utils if needed elsewhere, otherwise remove
):
    """Test handle_structured_delete when the event is not found."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()

    # Example structured delete payload
    delete_data = EventDelete(id=event_id_to_delete).model_dump()
    message = Message(role="user", parts=[Part(type="data", data=delete_data)])

    # Mock the DeleteEventStrategy instance and its execute method
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = False
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response
    mock_task_manager.fail_task.return_value = mock_failed_task

    # Call the handler
    final_task = await handle_structured_delete(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=event_id_to_delete)
    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED

    # Check TaskManager call
    expected_message = f"Event with ID {event_id_to_delete} not found for deletion."
    mock_task_manager.fail_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.complete_task.assert_not_awaited()

# Add tests for invalid payload, missing data part, non-dict payload, etc.
# For example:

@pytest.mark.asyncio
async def test_handle_structured_delete_invalid_payload_validation(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete with invalid EventDelete payload."""
    task_id = str(uuid.uuid4())
    # Invalid payload (e.g., missing 'id')
    invalid_delete_data = {"wrong_key": "some_value"}
    message = Message(role="user", parts=[Part(type="data", data=invalid_delete_data)])

    mock_task_manager.fail_task.return_value = mock_failed_task

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED
    # Check that fail_task was called with a message indicating validation error
    mock_task_manager.fail_task.assert_awaited_once()
    args, _ = mock_task_manager.fail_task.call_args
    assert task_id == args[0]
    # Assert the text content of the message part
    assert isinstance(args[1], Message)
    assert "Invalid delete parameters" in args[1].parts[0].text
    mock_task_manager.complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_structured_delete_missing_data_part(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete with missing data part in the message."""
    task_id = str(uuid.uuid4())
    message = Message(role="user", parts=[]) # No parts

    mock_task_manager.fail_task.return_value = mock_failed_task

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED
    expected_message = "Error processing delete: Missing or invalid data part in message for structured delete."
    mock_task_manager.fail_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_structured_delete_non_dict_payload(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete with non-dictionary data payload."""
    task_id = str(uuid.uuid4())
    message = Message(role="user", parts=[Part(type="data", data="not_a_dictionary")])

    mock_task_manager.fail_task.return_value = mock_failed_task

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED
    expected_message = "Error processing delete: Data part payload is not a dictionary."
    mock_task_manager.fail_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.complete_task.assert_not_awaited()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_delete.DeleteEventStrategy")
async def test_handle_structured_delete_db_exception(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete when the DB operation raises an exception."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()
    delete_data = EventDelete(id=event_id_to_delete).model_dump()
    message = Message(role="user", parts=[Part(type="data", data=delete_data)])

    # Mock the DeleteEventStrategy to raise an exception
    mock_delete_strategy_instance = AsyncMock()
    db_error = Exception("Database connection error")
    mock_delete_strategy_instance.execute.side_effect = db_error
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    mock_task_manager.fail_task.return_value = mock_failed_task

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=event_id_to_delete)
    expected_message = f"An unexpected error occurred: {db_error}"
    mock_task_manager.fail_task.assert_awaited_once_with(
        task_id, Message(role="agent", parts=[Part(type="text", text=expected_message)])
    )
    mock_task_manager.complete_task.assert_not_awaited() 