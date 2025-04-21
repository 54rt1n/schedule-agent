import pytest
from unittest.mock import AsyncMock, MagicMock
import uuid
import json
from datetime import datetime, UTC

# Import necessary types and handlers
from scheduling_agent.handlers.structured_query import handle_structured_query
from scheduling_agent.a2a.types import (
    Task,
    TaskState,
    TaskStatus,
    Message,
    Part,
)
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventQuery

# Mock return task from TaskManager
mock_completed_task = Task(
    id="mock_task_id",
    status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
)

@pytest.mark.asyncio
async def test_handle_structured_query(
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test the handle_structured_query handler with valid input."""
    task_id = str(uuid.uuid4())

    # Example structured query payload
    query = EventQuery(title_contains="Meeting")
    query_data = query.model_dump(exclude_unset=True) # Use exclude_unset like handler
    message = Message(role="user", parts=[Part(type="data", data=query_data)])

    # Mock DB response
    mock_event = Event(
        id=uuid.uuid4(),
        title="Team Meeting",
        start_time=datetime(2024, 1, 1, 10, 0, 0),
        end_time=datetime(2024, 1, 1, 11, 0, 0),
        location="Office",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC)
    )
    mock_db_service.find_events.return_value = [mock_event]

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Call the handler
    final_task = await handle_structured_query(
        task_id=task_id,
        message=message,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is not None
    assert final_task.status.state == TaskState.COMPLETED

    # Check DB call
    mock_db_service.find_events.assert_awaited_once_with(query=query)

    # Check TaskManager call
    expected_result_list = [
        {
            "id": str(mock_event.id),
            "title": mock_event.title,
            "start_time": mock_event.start_time.isoformat(),
            "end_time": mock_event.end_time.isoformat(),
            "location": mock_event.location,
        }
    ]
    expected_result_json = json.dumps(expected_result_list)
    mock_task_manager.complete_task.assert_awaited_once_with(task_id, expected_result_json) 