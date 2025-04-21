import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid

# Import necessary types and handlers
from scheduling_agent.handlers.structured_delete import handle_structured_delete
from scheduling_agent.a2a.types import Task, TaskState, TaskStatus, Message, Part
from scheduling_agent.db.schemas import EventDelete

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
async def test_handle_structured_delete_success(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete successfully deleting an event."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4() # Use UUID object

    # Example structured delete payload
    delete_data = EventDelete(id=event_id_to_delete).model_dump()
    message = Message(role="user", parts=[Part(type="data", data=delete_data)])

    # Mock DB response (success)
    mock_db_service.delete_event.return_value = True

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
    assert final_task is not None
    assert final_task.status.state == TaskState.COMPLETED

    # Check DB call
    mock_db_service.delete_event.assert_awaited_once_with(event_id=event_id_to_delete)

    # Check TaskManager call
    expected_message = f"Successfully deleted event {event_id_to_delete}."
    mock_task_manager.complete_task.assert_awaited_once_with(task_id, expected_message)
    mock_task_manager.fail_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_structured_delete_not_found(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test handle_structured_delete when the event is not found."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4() # Use UUID object

    # Example structured delete payload
    delete_data = EventDelete(id=event_id_to_delete).model_dump()
    message = Message(role="user", parts=[Part(type="data", data=delete_data)])

    # Mock DB response (not found)
    mock_db_service.delete_event.return_value = False

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
    assert final_task is not None
    assert final_task.status.state == TaskState.FAILED

    # Check DB call
    mock_db_service.delete_event.assert_awaited_once_with(event_id=event_id_to_delete)

    # Check TaskManager call
    expected_message = f"Event with ID {event_id_to_delete} not found for deletion."
    mock_task_manager.fail_task.assert_awaited_once_with(task_id, expected_message)
    mock_task_manager.complete_task.assert_not_awaited() 