import pytest
import uuid
import base64
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, ANY

from scheduling_agent.handlers.nlp_export import handle_nlp_export
from scheduling_agent.db.models import Event
from scheduling_agent.a2a.types import (
    Task,
    TaskState,
    Message,
    Part,
    Artifact,
    FileContent,
    TaskStatus,
    InvalidParamsError,
)
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMFilters, LLMDateInfo


@pytest.fixture
def mock_nlp_result_export(request) -> LLMSchedulingParseResult:
    """Provides a mock NLP result for export, customizable via request.param."""
    export_format = getattr(request, "param", "ics") # Default to ics
    return LLMSchedulingParseResult(
        intent="export_calendar",
        filters=LLMFilters(format=export_format),
        date_info=LLMDateInfo(start_date="2024-01-01", end_date="2024-01-07")
    )

@pytest.fixture
def events_for_export() -> list[Event]:
    """Provides a list of sample Event objects returned by db_service."""
    return [
        Event(
            id=uuid.uuid4(),
            title="Event 1",
            start_time=datetime(2024, 1, 1, 10, 0),
            end_time=datetime(2024, 1, 1, 11, 0),
            time_zone="UTC"
        ),
        Event(
            id=uuid.uuid4(),
            title="Event 2",
            start_time=datetime(2024, 1, 3, 14, 0),
            end_time=datetime(2024, 1, 3, 15, 30),
            time_zone="America/New_York"
        ),
    ]

@pytest.fixture
def mock_parsed_date_range() -> tuple[datetime, datetime]:
    """Provides a mock return value for date_utils.parse_nlp_date_range."""
    start = datetime(2024, 1, 1, 0, 0, 0)
    end = datetime(2024, 1, 7, 23, 59, 59, 999999)
    return start, end

# --- Test Cases --- #

@pytest.mark.parametrize("mock_nlp_result_export", ["ics", "org"], indirect=True)
@pytest.mark.asyncio
async def test_handle_nlp_export_success(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_exporters: dict[str, MagicMock], # Fixture providing {'ics': mock_ical_exporter, 'org': mock_org_exporter}
    mock_date_utils: MagicMock,
    mock_nlp_result_export: LLMSchedulingParseResult,
    events_for_export: list[Event],
    mock_parsed_date_range: tuple[datetime, datetime],
):
    """Tests successful calendar export for supported formats (ics, org)."""
    task_id = f"test_export_task_{mock_nlp_result_export.filters.format}"
    export_format = mock_nlp_result_export.filters.format
    mock_exporter = mock_exporters[export_format]
    expected_file_name = f"calendar.{export_format}"
    expected_mime_type = f"text/{'calendar' if export_format == 'ics' else 'plain'}" # Example mime types
    raw_export_data = f"EXPORTED DATA FOR {export_format}"
    encoded_export_data = base64.b64encode(raw_export_data.encode("utf-8")).decode("utf-8")

    # Configure mocks
    mock_date_utils.parse_nlp_date_range.return_value = mock_parsed_date_range
    mock_db_service.get_events_in_range.return_value = events_for_export
    mock_exporter.export.return_value = raw_export_data
    mock_exporter.get_mime_type = MagicMock(return_value=expected_mime_type)

    mock_status = TaskStatus(state=TaskState.COMPLETED, message=None)
    mock_task_manager.complete_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_export(
        task_id=task_id,
        nlp_result=mock_nlp_result_export,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        exporters=mock_exporters,
        date_utils=mock_date_utils,
    )

    # Assertions
    mock_date_utils.parse_nlp_date_range.assert_called_once_with(
        start_date_str=mock_nlp_result_export.date_info.start_date,
        end_date_str=mock_nlp_result_export.date_info.end_date
    )
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=mock_parsed_date_range[0],
        end=mock_parsed_date_range[1]
    )
    mock_exporter.export.assert_called_once_with(events_for_export)
    mock_exporter.get_mime_type.assert_called_once()

    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert f"exported successfully as {expected_file_name}" in complete_call_args[1].parts[0].text
    assert isinstance(complete_call_args[2], Artifact)
    artifact: Artifact = complete_call_args[2]
    assert artifact.name == expected_file_name
    assert len(artifact.parts) == 1
    part: Part = artifact.parts[0]
    assert part.type == "file"
    assert isinstance(part.file, FileContent)
    assert part.file.name == expected_file_name
    assert part.file.mimeType == expected_mime_type
    assert part.file.bytes == encoded_export_data

    assert result_task is not None
    assert result_task.status.state == TaskState.COMPLETED


@pytest.mark.parametrize("mock_nlp_result_export", ["unsupported_format"], indirect=True)
@pytest.mark.asyncio
async def test_handle_nlp_export_unsupported_format(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_exporters: dict[str, MagicMock],
    mock_date_utils: MagicMock,
    mock_nlp_result_export: LLMSchedulingParseResult,
    mock_parsed_date_range: tuple[datetime, datetime],
):
    """Tests the handler when an unsupported export format is requested."""
    task_id = "test_export_task_unsupported"
    unsupported_format = mock_nlp_result_export.filters.format

    # Configure mocks
    mock_date_utils.parse_nlp_date_range.return_value = mock_parsed_date_range
    # DB service might still be called depending on implementation, but exporter won't be found

    # Call the handler and expect InvalidParamsError
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_nlp_export(
            task_id=task_id,
            nlp_result=mock_nlp_result_export,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            exporters=mock_exporters,
            date_utils=mock_date_utils,
        )

    # Assertions
    assert f"Unsupported export format requested: {unsupported_format}" in str(exc_info.value)
    mock_date_utils.parse_nlp_date_range.assert_called_once()
    mock_db_service.get_events_in_range.assert_not_called() # Should fail before DB call
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called() # Handler raises, doesn't fail task directly
    mock_task_manager.require_input_task.assert_not_called()
