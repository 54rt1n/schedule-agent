import pytest
import json
from uuid import uuid4, UUID
from datetime import datetime, timezone, timedelta

from sa.a2a.command.structured_query import handle_structured_query
from sa.a2a.dto import (
    Message,
    Part,
    TaskStatus,
    TaskState,
    InvalidParamsError,
    TaskSendParams,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.db.dao import EventCreate, EventQuery
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy

@pytest.mark.asyncio
async def test_handle_structured_query_success_by_id(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
):
    """Test successful event query by ID via handle_structured_query."""
    # 1. Create an event to query
    event_time = datetime(2024, 9, 5, 14, 0, 0, tzinfo=timezone.utc)
    initial_data = EventCreate(
        title="Query Me Event",
        start_time=event_time,
        end_time=event_time + timedelta(hours=1),
    )
    created_event = await create_event_strategy.execute(data=initial_data)
    event_id = created_event.id

    # 2. Prepare query payload
    task_id = str(uuid4())
    query_params = EventQuery(id=event_id)
    query_message = Message(role="user", parts=[Part(type="data", data=query_params.model_dump())])

    params = TaskSendParams(id=task_id, message=query_message, intent="query_events")
    await task_manager.create_task(params)

    # 3. Call the handler
    final_task = await handle_structured_query(
        task_id=task_id,
        message=query_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    # 4. Assertions
    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    result_text = final_task.status.message.parts[0].text
    assert result_text is not None
    result_data = json.loads(result_text)
    assert isinstance(result_data, list)
    assert len(result_data) == 1
    assert result_data[0]["id"] == str(event_id)
    assert result_data[0]["title"] == "Query Me Event"
    assert not final_task.artifacts


@pytest.mark.asyncio
async def test_handle_structured_query_no_results(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
):
    """Test querying when no events match."""
    task_id = str(uuid4())
    query_params = EventQuery(title="NonExistentTitle")
    query_message = Message(role="user", parts=[Part(type="data", data=query_params.model_dump())])

    params = TaskSendParams(id=task_id, message=query_message, intent="query_events")
    await task_manager.create_task(params)

    final_task = await handle_structured_query(
        task_id=task_id,
        message=query_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    result_text = final_task.status.message.parts[0].text
    assert result_text == "[]" # Empty JSON list
    assert not final_task.artifacts

@pytest.mark.asyncio
async def test_handle_structured_query_invalid_payload(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
):
    """Test query with invalid data structure (e.g., bad date format)."""
    task_id = str(uuid4())
    invalid_data = {"start_time": "not-a-real-date"}
    query_message = Message(role="user", parts=[Part(type="data", data=invalid_data)])

    params = TaskSendParams(id=task_id, message=query_message, intent="query_events")
    await task_manager.create_task(params)

    final_task = await handle_structured_query(
        task_id=task_id,
        message=query_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    assert "Error processing query: Invalid query parameters" in final_task.status.message.parts[0].text


@pytest.mark.asyncio
async def test_handle_structured_query_missing_data_part(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
):
    """Test query with missing or empty data part."""
    task_id = str(uuid4())
    query_message = Message(role="user", parts=[]) # No parts

    params = TaskSendParams(id=task_id, message=query_message, intent="query_events")
    await task_manager.create_task(params)

    final_task = await handle_structured_query(
        task_id=task_id,
        message=query_message,
        task_manager=task_manager,
        db_service=db_service,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    assert "Error processing query: Missing or invalid data part" in final_task.status.message.parts[0].text 