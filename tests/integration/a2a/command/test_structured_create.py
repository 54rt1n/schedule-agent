import pytest
from uuid import uuid4
import uuid

from sa.a2a.command.structured_create import handle_structured_create
from sa.a2a.dto import Message, Part
from sa.a2a.task_manager import SchedulingTaskManager
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.a2a.dto import TaskSendParams, TaskState
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy
from sa.db.dao import EventGetById

@pytest.mark.asyncio
async def test_handle_structured_create_success(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test successful event creation via handle_structured_create."""
    task_id = str(uuid4())
    input_data = {
        "title": "Integration Test Meeting",
        "start_time": "2024-08-15T10:00:00Z",
        "end_time": "2024-08-15T11:00:00Z",
        "description": "Discuss integration tests.",
        "location": "Virtual",
        "attendees": ["test@example.com"],
    }

    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=input_data)]),
        intent="create_event",
    )

    # Mock the task manager initially to track state changes
    # In a real integration test, we'd check the DB or task manager's state
    await task_manager.create_task(params)

    # Call the handler
    final_task = await handle_structured_create(
        task_id=task_id,
        payload=input_data,
        task_manager=task_manager,
        db_service=db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # Assertions
    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    assert final_task.status.message.role == "agent"
    assert "created successfully" in final_task.status.message.parts[0].text

    assert final_task.artifacts is not None
    assert len(final_task.artifacts) == 1
    artifact = final_task.artifacts[0]
    assert artifact.name == "created_event"
    assert artifact.parts[0].type == "data"
    created_event_data = artifact.parts[0].data
    assert created_event_data["title"] == input_data["title"]
    assert created_event_data["location"] == input_data["location"]

    # Verify in DB (optional but good for integration tests)
    created_event_id = EventGetById(id=uuid.UUID(created_event_data["id"]))
    db_event = await get_event_by_id_strategy.execute(data=created_event_id)
    assert db_event is not None
    assert db_event.title == input_data["title"]
    assert db_event.id == created_event_id.id

# TODO: Add tests for conflict errors
# TODO: Add tests for validation errors
# TODO: Add tests for other potential errors 