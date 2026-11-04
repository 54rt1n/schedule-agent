import pytest
from uuid import uuid4, UUID
from datetime import datetime, timedelta, timezone
from unittest.mock import ANY

from schedule_a2a.command.structured_update import handle_structured_update
from schedule_a2a.dto import (
    Message,
    Part,
    Task,
    TaskState,
    TaskSendParams,
)
from schedule_a2a.exceptions import InvalidParamsError
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.db.dao import EventCreate, EventUpdate, EventGetById
from schedule_core.db.service import DatabaseService
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils
from schedule_core.db.strategies.get_event_by_id import GetEventByIdStrategy
from schedule_core.db.strategies.create_event import CreateEventStrategy


@pytest.mark.asyncio
async def test_handle_structured_update_success(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    create_event_strategy: CreateEventStrategy,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test successful event update via handle_structured_update."""
    # 1. Create an event to update
    initial_data = EventCreate(
        title="Initial Meeting",
        start_time=datetime(2024, 9, 1, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 9, 1, 11, 0, 0, tzinfo=timezone.utc),
        time_zone="America/New_York",
        location="Office A",
    )
    created_event = await create_event_strategy.execute(data=initial_data)
    event_id = created_event.id

    # 2. Prepare update payload dictionary
    task_id = str(uuid4())
    # Construct the dictionary payload directly
    update_payload_dict = {
        "id": str(event_id),
        "title": "Updated Meeting Title",
        "location": "Office B"
    }
    # No need to wrap in Message or StructuredUpdatePayload
    # Create a placeholder task record so TaskManager can find it later
    await task_manager.create_task(TaskSendParams(id=task_id, message=Message(role="user", parts=[])))

    # 3. Call the handler with the dictionary payload
    final_task = await handle_structured_update(
        task_id=task_id,
        payload=update_payload_dict, # Pass dict directly
        task_manager=task_manager,
        db_service=db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # 4. Assertions (Task completion)
    assert final_task is not None
    assert isinstance(final_task, Task)
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    # Handler constructs message internally, check content
    assert f"Event '{event_id}' updated successfully." in final_task.status.message.parts[0].text

    # Assertions (Artifact)
    assert final_task.artifacts is not None
    assert len(final_task.artifacts) == 1
    artifact = final_task.artifacts[0]
    assert artifact.name == "updated_event_details"
    updated_event_data = artifact.parts[0].data
    assert updated_event_data["id"] == str(event_id)
    assert updated_event_data["title"] == "Updated Meeting Title"
    assert updated_event_data["location"] == "Office B"

    # 5. Verify in DB
    db_event = await get_event_by_id_strategy.execute(data=EventGetById(id=event_id))
    assert db_event is not None
    assert db_event.title == "Updated Meeting Title"
    assert db_event.location == "Office B"


@pytest.mark.asyncio
async def test_handle_structured_update_not_found(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
):
    """Test updating a non-existent event."""
    task_id = str(uuid4())
    non_existent_id = uuid4()
    # Construct the dictionary payload directly
    update_payload_dict = {
        "id": str(non_existent_id),
        "title": "Doesn't Matter"
    }
    # No need to wrap in Message or StructuredUpdatePayload
    # Create a placeholder task record so TaskManager can find it later
    await task_manager.create_task(TaskSendParams(id=task_id, message=Message(role="user", parts=[])))

    # 3. Call the handler with the dictionary payload
    final_task = await handle_structured_update(
        task_id=task_id,
        payload=update_payload_dict, # Pass dict directly
        task_manager=task_manager,
        db_service=db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    # Handler constructs message internally, check content
    assert f"Could not find event with ID '{non_existent_id}' to update." in final_task.status.message.parts[0].text
    assert not final_task.artifacts # No artifact on failure


@pytest.mark.asyncio
async def test_handle_structured_update_invalid_payload(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
):
    """Test update with invalid data structure (missing id)."""
    task_id = str(uuid4())
    # Malformed payload dictionary (missing event_id/id)
    invalid_payload_dict = {"title": "Bad Data - No ID"}
    # No need to call task_manager.create_task here, as the handler raises error before TaskManager is called

    # Handler should raise InvalidParamsError during internal validation
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_update(
            task_id=task_id,
            payload=invalid_payload_dict, # Pass invalid dict directly
            task_manager=task_manager,
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    assert "Invalid data provided for event update." in str(exc_info.value)
    # Removed assertions about task_manager calls as it's not mocked
    # The pytest.raises block confirms error before task_manager would be called


# TODO: Add test for ConflictError if update_event implements conflict checks
# TODO: Add test for NotImplementedError if specific updates are unsupported

