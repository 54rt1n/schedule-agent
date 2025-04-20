import base64
import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Import necessary types
from scheduling_agent.a2a.types import (
    Artifact,
    InvalidParamsError,
    JSONRPCRequest,
    Message,
    Part,
    Task,
    TaskSendParams,
    TaskState,
    TaskStatus,
)

# Import exporters for mocking (optional, but helps with spec)
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.schemas import LLMDateInfo, LLMFilters, LLMSchedulingParseResult
from scheduling_agent.db.models import Event

# Import fixtures from conftest
from .conftest import RPC_URL


@pytest.mark.asyncio
async def test_export_calendar_success(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
    mock_nlp_processor: AsyncMock,
    # Add mock for the exporter that will be injected
    # Assuming dependency injection for exporters is set up in conftest or app
):
    """Test tasks/send triggers export_calendar intent and returns file artifact."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "Export next week's calendar as ics"

    # 1. Mock NLP Processor to return export intent
    mock_nlp_result = LLMSchedulingParseResult(
        intent="export_calendar",
        date_info=LLMDateInfo(start_date="next week start", end_date="next week end"),
        filters={"format": "ics"},
    )
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # 2. Mock Database Service to return events
    mock_events = [
        Event(id=uuid.uuid4(), title="Meeting A", start_time=..., end_time=...),
        Event(id=uuid.uuid4(), title="Meeting B", start_time=..., end_time=...),
    ]
    mock_db_service.get_events_in_range.return_value = mock_events

    # 3. Mock the ICalExporter behavior (assuming it's injected)
    # We need to mock the *instance* that will be used by the handler.
    # This requires knowing how exporters are injected/accessed.
    # For now, let's assume a mock exporter instance can be configured.
    # A better approach might be to mock the get_exporters dependency if that exists.
    mock_exporter_instance = MagicMock(spec=ICalExporter)
    exported_string = "BEGIN:VCALENDAR...VCARD...VEVENT...END:VCALENDAR"
    mock_exporter_instance.export = MagicMock(return_value=exported_string)
    mock_exporter_instance.get_mime_type = MagicMock(return_value="text/calendar")

    # *** How to ensure this mock instance is used? ***
    # This depends on how dependencies.py and conftest.py inject exporters.
    # Assuming for now `app.dependency_overrides` handles this for a hypothetical `get_exporters` dependency.
    # If exporters are directly part of another dependency (like TaskManager init), mock that part.

    # 4. Mock TaskManager complete_task to return the final task
    expected_base64_bytes = base64.b64encode(exported_string.encode("utf-8")).decode("utf-8")
    expected_artifact = Artifact(
        name="calendar.ics",
        parts=[
            Part(
                type="file",
                file={
                    "name": "calendar.ics",
                    "mimeType": "text/calendar",
                    "bytes": expected_base64_bytes,
                },
            )
        ],
    )
    final_task = Task(
        id=task_id, status=TaskStatus(state=TaskState.COMPLETED), artifacts=[expected_artifact]
    )
    mock_task_manager.complete_task.return_value = final_task

    # Prepare RPC request
    params = TaskSendParams(
        id=task_id, message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0", method="tasks/send", params=params.model_dump(), id=request_id
    )

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    result_task_data = response_data["result"]
    assert result_task_data.get("id") == task_id
    assert result_task_data.get("status", {}).get("state") == TaskState.COMPLETED.value
    assert len(result_task_data.get("artifacts", [])) == 1
    actual_artifact = result_task_data["artifacts"][0]
    assert actual_artifact.get("name") == "calendar.ics"
    assert len(actual_artifact.get("parts", [])) == 1
    file_part = actual_artifact["parts"][0]
    assert file_part.get("type") == "file"
    file_content = file_part.get("file")
    assert file_content is not None
    assert file_content.get("name") == "calendar.ics"
    assert file_content.get("mimeType") == "text/calendar"
    assert file_content.get("bytes") == expected_base64_bytes

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once_with(query)
    mock_db_service.get_events_in_range.assert_awaited_once()
    mock_task_manager.complete_task.assert_awaited_once()
    # TODO: Assert artifact details passed to complete_task


@pytest.mark.asyncio
async def test_export_calendar_unsupported_format(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
    mock_nlp_processor: AsyncMock,
    # Other mocks like db_service, conflict_resolver might be needed by handler signature
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
):
    """Test tasks/send with export_calendar intent but unsupported format."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "Export calendar as foo"

    # 1. Mock NLP Processor to return export intent with bad format
    # Mock NLP result for export intent with unsupported format
    # Create an LLMFilters instance with the format attribute
    mock_filters = LLMFilters(format="foo")
    mock_nlp_result = LLMSchedulingParseResult(
        intent="export_calendar",
        filters=mock_filters,  # Use the LLMFilters instance
    )
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # Prepare RPC request
    params = TaskSendParams(
        id=task_id, message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0", method="tasks/send", params=params.model_dump(), id=request_id
    )

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions: Expect InvalidParamsError
    assert response.status_code == 200  # JSON-RPC errors use 200 OK
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data

    error_data = response_data["error"]
    assert error_data.get("code") == InvalidParamsError.DEFAULT_CODE
    assert "Unsupported export format requested: foo" in error_data.get("message", "")

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once_with(query)
    mock_db_service.get_events_in_range.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()  # Should raise before failing task
