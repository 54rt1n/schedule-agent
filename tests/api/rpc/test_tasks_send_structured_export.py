import pytest
import uuid
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import base64

from sa.a2a.dto import TaskSendParams, Message, Part, TaskState, FileContent
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from ..conftest import build_rpc_request, create_db_event


@pytest.mark.asyncio
async def test_rpc_tasks_send_structured_export_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver
):
    """Test tasks/send with structured data for export_calendar intent."""
    # 1. Create an event to export using the new DB helper
    days_offset = 6
    event_title = "Event To Export"
    start_time = (datetime.now(timezone.utc) + timedelta(days=days_offset)).replace(hour=14, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(minutes=45)
    event_to_create = EventCreate(
        title=event_title,
        start_time=start_time,
        end_time=end_time,
        time_zone="UTC"
    )
    await create_db_event(
        event_create=event_to_create,
        app_db_service=app_db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver
    )
    target_date = start_time.strftime("%Y-%m-%d")

    # 2. Prepare Export Request
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    export_data = {
        "format": "ics",
        "start_date": target_date,
        "end_date": target_date
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "export_calendar",
        "payload": export_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_export_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Export Request
    response_export = client.post("/api/rpc", json=rpc_export_payload, headers=auth_headers_valid)

    # 4. Assertions
    assert response_export.status_code == 200
    response_export_data = response_export.json()
    assert "result" in response_export_data
    assert response_export_data.get("error") is None

    result_payload = response_export_data["result"]
    assert result_payload["id"] == task_id
    # Expect COMPLETED state if export is successful
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    # Check artifact for exported calendar data
    assert len(result_payload["artifacts"]) > 0
    # Artifact name is dynamically generated based on format
    expected_artifact_name = "calendar.ics"
    export_artifact = next((a for a in result_payload["artifacts"] if a["name"] == expected_artifact_name), None)
    assert export_artifact is not None, f"Artifact named '{expected_artifact_name}' not found"
    assert export_artifact["parts"][0]["type"] == "file"
    file_part = export_artifact["parts"][0]["file"]
    assert file_part["mimeType"] == "text/calendar"
    assert file_part["name"].endswith(".ics")
    # Decode base64 content and check for event title
    try:
        ics_content_bytes = base64.b64decode(file_part["bytes"])
        ics_content = ics_content_bytes.decode('utf-8')
        assert f"SUMMARY:{event_title}" in ics_content
    except Exception as e:
        pytest.fail(f"Failed to decode or validate ICS content: {e}") 