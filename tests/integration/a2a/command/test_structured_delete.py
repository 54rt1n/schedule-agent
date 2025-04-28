import pytest
from uuid import uuid4, UUID
from datetime import datetime, timezone, timedelta

# Fixture imports needed
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy

from sa.a2a.command.structured_delete import handle_structured_delete
from sa.a2a.dto import (
    Message,
    Part,
    TaskStatus,
    TaskState,
    TaskSendParams,
    Task,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.db.dao import EventCreate, Event, EventDelete, EventGetById
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.a2a.exceptions import InvalidParamsError, InternalError, TaskNotFoundError
from pydantic import ValidationError
from unittest.mock import ANY


@pytest.mark.asyncio
async def test_handle_structured_delete_success(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    get_events_in_range_strategy: GetEventsInRangeStrategy,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test successful deletion of an event via structured command."""
    task_id = str(uuid4())
    # 1. Create an event to delete
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy,
    )
    event_data = EventCreate(
        title="Event To Delete",
        start_time="2024-09-10T10:00:00Z",
        end_time="2024-09-10T11:00:00Z",
    )
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    event_id = created_event.id

    # 2. Create the corresponding A2A task
    # Use a minimal message, the handler only uses the payload
    initial_message = Message(role="user", parts=[Part(type="text", text="delete")])
    create_params = TaskSendParams(id=task_id, message=initial_message, intent="delete_event")
    await task_manager.create_task(create_params)

    # 3. Prepare the delete payload
    delete_payload = EventDelete(id=event_id).model_dump()

    # 4. Call the handler
    result_task = await handle_structured_delete(
        task_id=task_id,
        payload=delete_payload,
        task_manager=task_manager,
        db_service=db_service,
    )

    # 5. Verify task is completed
    assert isinstance(result_task, Task)
    assert result_task.id == task_id
    assert result_task.status.state == TaskState.COMPLETED
    assert result_task.status.message is not None
    assert result_task.status.message.role == "agent"
    assert f"Successfully deleted event {event_id}" in result_task.status.message.parts[0].text

    # 6. Verify event is actually deleted
    deleted_event = await get_event_by_id_strategy.execute(data=EventGetById(id=event_id))
    assert deleted_event is None

@pytest.mark.asyncio
async def test_handle_structured_delete_not_found(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test handling deletion when the event ID is not found."""
    task_id = str(uuid4())
    non_existent_event_id = uuid4()

    # 1. Create the corresponding A2A task
    initial_message = Message(role="user", parts=[Part(type="text", text="delete")])
    create_params = TaskSendParams(id=task_id, message=initial_message, intent="delete_event")
    await task_manager.create_task(create_params)

    # 2. Prepare the delete payload
    delete_payload = EventDelete(id=non_existent_event_id).model_dump()

    # 3. Call the handler
    result_task = await handle_structured_delete(
        task_id=task_id,
        payload=delete_payload,
        task_manager=task_manager,
        db_service=db_service,
    )

    # 4. Verify task is failed
    assert isinstance(result_task, Task)
    assert result_task.id == task_id
    assert result_task.status.state == TaskState.FAILED
    assert result_task.status.message is not None
    assert result_task.status.message.role == "agent"
    assert f"Event with ID {non_existent_event_id} not found for deletion." in result_task.status.message.parts[0].text

    # 5. Verify event wasn't deleted (it never existed)
    still_not_found = await get_event_by_id_strategy.execute(data=EventGetById(id=non_existent_event_id))
    assert still_not_found is None

@pytest.mark.asyncio
async def test_handle_structured_delete_invalid_payload(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
):
    """Test delete raises InvalidParamsError with invalid payload structure."""
    task_id = str(uuid4())
    invalid_payload = {"wrong_key": "some_value"} # Missing 'id'

    # 1. Create the corresponding A2A task (needed for context, though handler fails early)
    initial_message = Message(role="user", parts=[Part(type="text", text="delete")])
    create_params = TaskSendParams(id=task_id, message=initial_message, intent="delete_event")
    await task_manager.create_task(create_params)

    # 2. Expect InvalidParamsError when calling the handler
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_delete(
            task_id=task_id,
            payload=invalid_payload,
            task_manager=task_manager,
            db_service=db_service,
        )

    assert "Invalid delete parameters in payload" in str(exc_info.value)
    assert isinstance(exc_info.value.data, list) # Should contain Pydantic errors