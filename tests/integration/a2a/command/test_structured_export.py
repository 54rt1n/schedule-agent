import pytest
import base64
from uuid import uuid4
from datetime import datetime, timezone, timedelta

from sa.a2a.command.structured_export import handle_structured_export
from sa.a2a.dto import (
    Message,
    Part,
    TaskState,
    InvalidParamsError,
    TaskSendParams,
    FileContent,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.db.strategies.create_event import CreateEventStrategy
from sa.nlp.dao import LLMDateInfo as DateInfo # For constructing input


@pytest.mark.asyncio
async def test_handle_structured_export_success_ics(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    create_event_strategy: CreateEventStrategy,
):
    """Test successful calendar export in ICS format."""
    # 1. Create some events
    start1 = datetime(2024, 10, 1, 9, 0, 0, tzinfo=timezone.utc)
    await create_event_strategy.execute(EventCreate(title="Export Event 1", start_time=start1, end_time=start1 + timedelta(hours=1)))
    start2 = datetime(2024, 10, 5, 15, 0, 0, tzinfo=timezone.utc)
    await create_event_strategy.execute(EventCreate(title="Export Event 2", start_time=start2, end_time=start2 + timedelta(minutes=30)))

    # 2. Prepare export payload
    task_id = str(uuid4())
    # Export events between Oct 1 and Oct 6
    date_info = DateInfo(start_date="2024-10-01", end_date="2024-10-06")
    export_data = {"format": "ics", "date_info": date_info.model_dump()}
    export_message = Message(role="user", parts=[Part(type="data", data=export_data)])

    params = TaskSendParams(id=task_id, message=export_message, intent="export_calendar")
    await task_manager.create_task(params)

    # 3. Call the handler
    final_task = await handle_structured_export(
        task_id=task_id,
        message=export_message,
        task_manager=task_manager,
        db_service=db_service,
        exporters=exporters,
        date_utils=date_utils,
    )

    # 4. Assertions
    assert final_task is not None
    assert final_task.id == task_id
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.status.message is not None
    assert "Calendar exported successfully as calendar.ics" in final_task.status.message.parts[0].text

    assert final_task.artifacts is not None
    assert len(final_task.artifacts) == 1
    artifact = final_task.artifacts[0]
    assert artifact.name == "calendar.ics"
    assert artifact.parts[0].type == "file"
    file_part = artifact.parts[0].file
    assert isinstance(file_part, FileContent)
    assert file_part.name == "calendar.ics"
    assert file_part.mimeType == "text/calendar"

    # Decode and check content basics
    decoded_content = base64.b64decode(file_part.bytes).decode("utf-8")
    assert "BEGIN:VCALENDAR" in decoded_content
    assert "SUMMARY:Export Event 1" in decoded_content
    assert "SUMMARY:Export Event 2" in decoded_content
    assert "END:VCALENDAR" in decoded_content

@pytest.mark.asyncio
async def test_handle_structured_export_no_events(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
):
    """Test export when no events are in the specified range."""
    task_id = str(uuid4())
    # Export events in November (none exist)
    date_info = DateInfo(start_date="2024-11-01", end_date="2024-11-30")
    export_data = {"format": "ics", "date_info": date_info.model_dump()}
    export_message = Message(role="user", parts=[Part(type="data", data=export_data)])

    params = TaskSendParams(id=task_id, message=export_message, intent="export_calendar")
    await task_manager.create_task(params)

    final_task = await handle_structured_export(
        task_id=task_id,
        message=export_message,
        task_manager=task_manager,
        db_service=db_service,
        exporters=exporters,
        date_utils=date_utils,
    )

    assert final_task is not None
    assert final_task.status.state == TaskState.COMPLETED
    assert final_task.artifacts is not None
    artifact = final_task.artifacts[0]
    file_part = artifact.parts[0].file
    decoded_content = base64.b64decode(file_part.bytes).decode("utf-8")
    assert "BEGIN:VCALENDAR" in decoded_content
    assert "BEGIN:VEVENT" not in decoded_content # Should be empty calendar
    assert "END:VCALENDAR" in decoded_content

@pytest.mark.asyncio
async def test_handle_structured_export_unsupported_format(
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
):
    """Test requesting an unsupported export format."""
    task_id = str(uuid4())
    date_info = DateInfo(start_date="2024-10-01", end_date="2024-10-06")
    export_data = {"format": "unsupported_format", "date_info": date_info.model_dump()}
    export_message = Message(role="user", parts=[Part(type="data", data=export_data)])

    params = TaskSendParams(id=task_id, message=export_message, intent="export_calendar")
    await task_manager.create_task(params)

    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_export(
            task_id=task_id,
            message=export_message,
            task_manager=task_manager,
            db_service=db_service,
            exporters=exporters,
            date_utils=date_utils,
        )
    assert "Unsupported export format requested: unsupported_format" in str(exc_info.value)

# TODO: Add test for missing date_info or format in payload if strict validation needed 