import pytest
from uuid import uuid4
from unittest.mock import AsyncMock

from schedule_a2a.command.nlp_request import handle_nlp_request
from schedule_a2a.dto import (
    Message,
    Part,
    TaskState,
    TaskSendParams,
)
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.db.service import DatabaseService
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.exporters.base import CalendarExporter
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_nlp.nlp_processor import NLPProcessor
from schedule_nlp.dao import LLMSchedulingParseResult as NLPResult


@pytest.mark.asyncio
async def test_handle_nlp_request_routes_to_export(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
):
    """Test that NLP request with export intent routes correctly."""
    task_id = str(uuid4())
    query = "Export my calendar for next week as ics"
    nlp_message = Message(role="user", parts=[Part(type="text", text=query)])

    # Mock the NLP result
    mock_nlp_result = NLPResult(
        intent="export_calendar",
        payload={"date_info": {"start_date": "2024-10-07", "end_date": "2024-10-14"}, "format": "ics"},
    )
    nlp_processor.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)

    params = TaskSendParams(id=task_id, message=nlp_message, intent=None) # Intent determined by NLP
    await task_manager.create_task(params)

    final_task = await handle_nlp_request(
        task_id=task_id,
        message=nlp_message,
        task_manager=task_manager,
        db_service=db_service,
        nlp_processor=nlp_processor,
        exporters=exporters,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
    )

    assert final_task.intent == "export_calendar"
    assert final_task.payload == mock_nlp_result.payload

    # Assert nlp_processor was called
    nlp_processor.parse_natural_language_query.assert_awaited_once_with(query)

@pytest.mark.asyncio
async def test_handle_nlp_request_unhandled_intent(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
):
    """Test fallback for an unhandled NLP intent."""
    task_id = str(uuid4())
    query = "Tell me a joke"
    nlp_message = Message(role="user", parts=[Part(type="text", text=query)])

    # Mock NLP result with unhandled intent
    mock_nlp_result = NLPResult(
        intent="unknown",
    )
    nlp_processor.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)

    params = TaskSendParams(id=task_id, message=nlp_message, intent=None)
    await task_manager.create_task(params)

    final_task = await handle_nlp_request(
        task_id=task_id,
        message=nlp_message,
        task_manager=task_manager,
        db_service=db_service,
        nlp_processor=nlp_processor,
        exporters=exporters,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED # Fallback fails the task
    assert final_task.status.message is not None
    assert "Sorry, I didn't understand that request." in final_task.status.message.parts[0].text
    assert not final_task.artifacts


@pytest.mark.asyncio
async def test_handle_nlp_request_nlp_failure(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
):
    """Test handling failure during NLP processing."""
    task_id = str(uuid4())
    query = "Some query that causes error"
    nlp_message = Message(role="user", parts=[Part(type="text", text=query)])

    # Mock NLP processor to raise an exception
    nlp_processor.parse_natural_language_query = AsyncMock(side_effect=Exception("NLP exploded!"))

    params = TaskSendParams(id=task_id, message=nlp_message, intent=None)
    await task_manager.create_task(params)

    final_task = await handle_nlp_request(
        task_id=task_id,
        message=nlp_message,
        task_manager=task_manager,
        db_service=db_service,
        nlp_processor=nlp_processor,
        exporters=exporters,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    assert "Error processing query: NLP exploded!" in final_task.status.message.parts[0].text
    assert not final_task.artifacts


@pytest.mark.asyncio
async def test_handle_nlp_request_invalid_message_format(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
):
    """Test handling message with incorrect format (e.g., no text part)."""
    task_id = str(uuid4())
    # Message with data part instead of text part
    invalid_message = Message(role="user", parts=[Part(type="data", data={"some": "data"})])

    params = TaskSendParams(id=task_id, message=invalid_message, intent=None)
    await task_manager.create_task(params)

    final_task = await handle_nlp_request(
        task_id=task_id,
        message=invalid_message,
        task_manager=task_manager,
        db_service=db_service,
        nlp_processor=nlp_processor,
        exporters=exporters,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
    )

    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.FAILED
    assert final_task.status.message is not None
    # The role in the error message is 'agent' after previous fix
    assert final_task.status.message.role == "agent"
    assert "Invalid input: Expected a text query." in final_task.status.message.parts[0].text
    assert not final_task.artifacts 