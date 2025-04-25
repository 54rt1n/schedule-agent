import pytest
from uuid import uuid4, UUID
from datetime import datetime, timezone, timedelta

from sa.a2a.command.structured_delete import handle_structured_delete
from sa.a2a.dto import (
    Message,
    Part,
    TaskStatus,
    TaskState,
    InvalidParamsError,
    TaskSendParams,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.db.dao import EventCreate, EventDelete
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy

@pytest.mark.asyncio
async def test_handle_structured_delete_success(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test successful event deletion via handle_structured_delete."""
    # 1. Create an event to delete
    initial_data = EventCreate(
        title="Event To Delete",
        start_time=datetime(2024, 9, 10, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 9, 10, 11, 0, 0, tzinfo=timezone.utc),
    )
    created_event = await create_event_strategy.execute(data=initial_data)
    event_id = created_event.id

    # Verify it exists
    assert await get_event_by_id_strategy.execute(data=event_id) is not None

    # 2. Prepare delete payload
    task_id = str(uuid4())
    delete_params = EventDelete(id=event_id)
    delete_message = Message(role="user", parts=[Part(type="data", data=delete_params.model_dump())])

    params = TaskSendParams(id=task_id, message=delete_message, intent="delete_event")
    await task_manager.create_task(params)

    # 3. Call the handler
    final_task = await handle_structured_delete(
        task_id=task_id,
        message=delete_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    # 4. Assertions
    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    assert f"Successfully deleted event {event_id}" in final_task.status.message.parts[0].text
    assert not final_task.artifacts

    # 5. Verify in DB (should be gone)
    db_event = await get_event_by_id_strategy.execute(data=event_id)
    assert db_event is None


@pytest.mark.asyncio
async def test_handle_structured_delete_not_found(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test deleting a non-existent event."""
    task_id = str(uuid4())
    non_existent_id = uuid4()

    # Verify it doesn't exist
    assert await get_event_by_id_strategy.execute(data=non_existent_id) is None

    delete_params = EventDelete(id=non_existent_id)
    delete_message = Message(role="user", parts=[Part(type="data", data=delete_params.model_dump())])

    params = TaskSendParams(id=task_id, message=delete_message, intent="delete_event")
    await task_manager.create_task(params)

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=delete_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED # Not found is treated as failure
    assert final_task.status.message is not None
    assert f"Event with ID {non_existent_id} not found for deletion." in final_task.status.message.parts[0].text
    assert not final_task.artifacts


@pytest.mark.asyncio
async def test_handle_structured_delete_invalid_payload(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
):
    """Test delete with invalid payload (e.g., not a UUID)."""
    task_id = str(uuid4())
    invalid_data = {"id": "not-a-uuid"}
    delete_message = Message(role="user", parts=[Part(type="data", data=invalid_data)])

    params = TaskSendParams(id=task_id, message=delete_message, intent="delete_event")
    await task_manager.create_task(params)

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=delete_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    assert "Error processing delete: Invalid delete parameters" in final_task.status.message.parts[0].text


@pytest.mark.asyncio
async def test_handle_structured_delete_missing_data_part(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
):
    """Test delete with missing or incorrect data part."""
    task_id = str(uuid4())
    delete_message = Message(role="user", parts=[Part(type="text", text="delete something")])

    params = TaskSendParams(id=task_id, message=delete_message, intent="delete_event")
    await task_manager.create_task(params)

    final_task = await handle_structured_delete(
        task_id=task_id,
        message=delete_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    assert "Error processing delete: Missing or invalid data part" in final_task.status.message.parts[0].text 