import pytest
import uuid
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta
import json

from sa.a2a.dto import TaskSendParams, Message, Part, TaskState
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from ..conftest import build_rpc_request, create_db_event

@pytest.mark.asyncio
async def test_rpc_tasks_send_structured_query_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver
):
    """Test tasks/send with structured data for query_events intent."""
    # 1. Create an event to query using the new DB helper
    days_offset = 3
    event_title = "Query Target Event"
    start_time = (datetime.now(timezone.utc) + timedelta(days=days_offset)).replace(hour=10, minute=0, second=0, microsecond=0)
    end_time = start_time + timedelta(hours=1)
    event_to_create = EventCreate(
        title=event_title,
        start_time=start_time,
        end_time=end_time,
        time_zone="UTC"
    )
    created_event = await create_db_event(
        event_create=event_to_create,
        app_db_service=app_db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver
    )
    target_date = start_time.strftime("%Y-%m-%d")

    # 2. Prepare Query Request
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    query_data = {
        "start_date": target_date,
        "end_date": target_date
        # Add other query params like title if needed by handler
    }
    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        intent="query_events", # Explicit intent
        message=Message(role="user", parts=[
            Part(type="data", data=query_data) # Structured query data
        ])
    )
    rpc_query_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Query Request
    response_query = client.post("/api/rpc", json=rpc_query_payload, headers=auth_headers_valid)

    # 4. Assertions
    assert response_query.status_code == 200
    response_query_data = response_query.json()
    assert "result" in response_query_data
    assert response_query_data.get("error") is None

    result_payload = response_query_data["result"]
    assert result_payload["id"] == task_id
    # Expect COMPLETED state if query is successful
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    
    # Check the status message for query results (JSON string)
    assert result_payload["status"]["message"]["role"] == "agent"
    query_message_part = result_payload["status"]["message"]["parts"][0]
    assert query_message_part["type"] == "text"
    
    query_results = json.loads(query_message_part["text"])
    assert isinstance(query_results, list)
    
    # Check that our created event is in the parsed results
    found_event = False
    for event_data in query_results:
        if event_data["id"] == str(created_event.id) and event_data["title"] == event_title: # Compare ID as string
            found_event = True
            break
    assert found_event, f"Created event {created_event.id} not found in query results" 