import pytest
from uuid import uuid4, UUID
from datetime import datetime, timedelta, timezone

from sa.a2a.command.structured_update import handle_structured_update
from sa.a2a.dto import (
    Message,
    Part,
    TaskStatus,
    TaskState,
    StructuredUpdatePayload,
    InvalidParamsError,
    InternalError,
    TaskSendParams,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.db.dao import EventCreate, EventUpdate
from sa.db.models import Event
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy
from sa.db.strategies.create_event import CreateEventStrategy


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

    # 2. Prepare update payload
    task_id = str(uuid4())
    update_details = EventUpdate(title="Updated Meeting Title", location="Office B")
    payload = StructuredUpdatePayload(event_id=event_id, update_data=update_details)
    update_message = Message(role="user", parts=[Part(type="data", data=payload.model_dump())])

    params = TaskSendParams(id=task_id, message=update_message, intent="update_event")
    await task_manager.create_task(params)

    # 3. Call the handler
    final_task = await handle_structured_update(
        task_id=task_id,
        message=update_message,
        task_manager=task_manager,
        db_service=db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    # 4. Assertions
    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    assert f"Event '{event_id}' updated successfully." in final_task.status.message.parts[0].text

    assert final_task.artifacts is not None
    assert len(final_task.artifacts) == 1
    artifact = final_task.artifacts[0]
    assert artifact.name == "updated_event_details"
    updated_event_data = artifact.parts[0].data
    assert updated_event_data["id"] == str(event_id)
    assert updated_event_data["title"] == "Updated Meeting Title"
    assert updated_event_data["location"] == "Office B"

    # 5. Verify in DB
    db_event = await get_event_by_id_strategy.execute(data=event_id)
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
    update_details = EventUpdate(title="Doesn't Matter")
    payload = StructuredUpdatePayload(event_id=non_existent_id, update_data=update_details)
    update_message = Message(role="user", parts=[Part(type="data", data=payload.model_dump())])

    params = TaskSendParams(id=task_id, message=update_message, intent="update_event")
    await task_manager.create_task(params)

    final_task = await handle_structured_update(
        task_id=task_id,
        message=update_message,
        task_manager=task_manager,
        db_service=db_service,
        conflict_resolver=conflict_resolver,
        date_utils=date_utils,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    assert f"Could not find event with ID '{non_existent_id}' to update." in final_task.status.message.parts[0].text
    assert not final_task.artifacts # No artifact on failure


@pytest.mark.asyncio
async def test_handle_structured_update_invalid_payload(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
):
    """Test update with invalid data structure."""
    task_id = str(uuid4())
    # Malformed payload (missing event_id)
    invalid_data = {"update_data": {"title": "Bad Data"}}
    update_message = Message(role="user", parts=[Part(type="data", data=invalid_data)])

    params = TaskSendParams(id=task_id, message=update_message, intent="update_event")
    await task_manager.create_task(params)

    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_update(
            task_id=task_id,
            message=update_message,
            task_manager=task_manager,
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
        )

    assert "Invalid data provided for event update." in str(exc_info.value)
    # Check that task status reflects failure (assuming task_manager catches this)
    # final_task = await task_manager.get_task(task_id)
    # assert final_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_handle_structured_update_missing_data_part(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
):
    """Test update with missing or empty data part in the message."""
    task_id = str(uuid4())
    # Message with no parts or wrong type/empty data
    messages_to_test = [
        Message(role="user", parts=[]), # No parts
        Message(role="user", parts=[Part(type="text", text="hello")]), # Wrong type
    ]

    for update_message in messages_to_test:
        params = TaskSendParams(id=task_id, message=update_message, intent="update_event")
        # We might need to recreate the task for each loop iteration if task state matters
        await task_manager.create_task(params)

        # Call the handler - it should catch ValueError and return a FAILED task
        final_task = await handle_structured_update(
            task_id=task_id,
            message=update_message,
            task_manager=task_manager,
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            # Add date_utils since the handler now requires it
            date_utils=date_utils, 
        )

        # Assert the task failed as expected
        assert final_task is not None
        assert final_task.id == task_id
        assert final_task.status.state == TaskState.FAILED
        assert final_task.status.message is not None
        # Check for the specific error message from the ValueError handler
        assert "Missing data payload" in final_task.status.message.parts[0].text

# TODO: Add test for ConflictError if update_event implements conflict checks
# TODO: Add test for NotImplementedError if specific updates are unsupported

