import pytest
import uuid
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from sa.a2a.dto import TaskSendParams, Message, Part, TaskState
from sa.db.dao import EventCreate # Import DAO
from sa.db.service import DatabaseService # Import for type hint
from sa.event.date_utils import DateUtils # Import for type hint
from sa.event.conflict_resolver import ConflictResolver # Import for type hint
from ..conftest import build_rpc_request, create_db_event # Import new helper

@pytest.mark.asyncio # Mark test as async
async def test_rpc_tasks_send_structured_update_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService, # Add needed fixture
    date_utils: DateUtils, # Add needed fixture
    conflict_resolver: ConflictResolver # Add missing fixture
):
    """Test tasks/send with structured data for update_event intent."""
    # 1. Create an event to update first using the DB helper
    start_time = datetime.now(timezone.utc) + timedelta(days=1)
    end_time = start_time + timedelta(minutes=30)
    event_to_create = EventCreate(
        title="Event To Update",
        start_time=start_time,
        end_time=end_time,
        time_zone="UTC"
    )
    created_event = await create_db_event(
        event_create=event_to_create,
        app_db_service=app_db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver # Pass the fixture
    )
    event_id = created_event.id
    
    # 2. Prepare Update Request
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    new_title = "Updated Event Title"
    # Prepare the nested structure expected by StructuredUpdatePayload
    event_update_details = {
        "title": new_title
    }
    # This is the payload for the message part's data field
    structured_payload = {
        "event_id": str(event_id),
        "update_data": event_update_details
    }
    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        intent="update_event", # Explicit intent
        message=Message(role="user", parts=[
            Part(type="data", data=structured_payload) # Pass the corrected payload
        ])
    )
    rpc_update_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Update Request
    response_update = client.post("/api/rpc", json=rpc_update_payload, headers=auth_headers_valid)

    # 4. Assertions
    assert response_update.status_code == 200
    response_update_data = response_update.json()
    assert "result" in response_update_data
    assert response_update_data.get("error") is None

    result_payload = response_update_data["result"]
    assert result_payload["id"] == task_id
    # Expect COMPLETED state if update is successful
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    # Check artifact for updated event details
    assert len(result_payload["artifacts"]) > 0
    updated_event_artifact = next((a for a in result_payload["artifacts"] if a["name"] == "updated_event_details"), None)
    assert updated_event_artifact is not None
    assert updated_event_artifact["parts"][0]["data"]["id"] == str(event_id) # Compare as string
    assert updated_event_artifact["parts"][0]["data"]["title"] == new_title
    