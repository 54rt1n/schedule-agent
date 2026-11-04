import pytest
import uuid
from fastapi.testclient import TestClient
from datetime import datetime, timezone, timedelta

from schedule_a2a.dto import TaskSendParams, Message, Part, TaskState
from schedule_core.db.dao import EventCreate
from schedule_core.db.service import DatabaseService
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.conflict_resolver import ConflictResolver
from ..conftest import build_rpc_request, create_db_event

def test_rpc_tasks_send_structured_create_success(client: TestClient, auth_headers_valid: dict):
    """Test tasks/send with structured data for create_event intent."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(hours=1)

    # Structured data payload matching EventCreate schema
    structured_data = {
        "title": "Structured Test Event",
        "start_time": start_time.isoformat(), # Use ISO format string
        "end_time": end_time.isoformat(),   # Use ISO format string
        "time_zone": "UTC" # Add timezone
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "create_event",
        "payload": structured_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper) # Use wrapped data
        ])
    )
    rpc_send_payload = build_rpc_request("tasks/send", send_params.model_dump())

    response_send = client.post("/api/rpc", json=rpc_send_payload, headers=auth_headers_valid)

    # Assertions
    assert response_send.status_code == 200
    response_data = response_send.json()
    assert "result" in response_data
    assert response_data.get("error") is None

    # Validate the returned task state
    result_payload = response_data["result"]
    assert result_payload["id"] == task_id
    # Check state: Depends on the implementation of handle_structured_create
    # It might complete immediately if no conflicts. Let's assume COMPLETED for now.
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    # Optionally check artifacts for created event details
    assert len(result_payload["artifacts"]) > 0
    assert result_payload["artifacts"][0]["name"] == "created_event"
    assert result_payload["artifacts"][0]["parts"][0]["data"]["title"] == structured_data["title"] 

@pytest.mark.asyncio
async def test_rpc_tasks_send_structured_create_conflict(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService, # Fixture for DB service
    date_utils: DateUtils, # Fixture for DateUtils
    conflict_resolver: ConflictResolver # Fixture for ConflictResolver
):
    """Test tasks/send structured create resulting in a conflict."""
    # 1. Create an initial event directly in the DB
    start_time1 = datetime.now(timezone.utc).replace(hour=10, minute=0, second=0, microsecond=0)
    end_time1 = start_time1 + timedelta(hours=1)
    event_to_create1 = EventCreate(
        title="Existing Event",
        start_time=start_time1,
        end_time=end_time1,
        time_zone="UTC"
    )
    existing_event = await create_db_event(
        event_create=event_to_create1,
        app_db_service=app_db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver
    )
    assert existing_event is not None

    # 2. Prepare API request for an overlapping event
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    # This event overlaps the first one (10:30 - 11:30 vs 10:00 - 11:00)
    start_time2 = start_time1 + timedelta(minutes=30)
    end_time2 = start_time2 + timedelta(hours=1)

    conflicting_structured_data = {
        "title": "Conflicting Event",
        "start_time": start_time2.isoformat(),
        "end_time": end_time2.isoformat(),
        "time_zone": "UTC"
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "create_event",
        "payload": conflicting_structured_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper) # Use wrapped data
        ])
    )
    rpc_send_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send the API request
    response_send = client.post("/api/rpc", json=rpc_send_payload, headers=auth_headers_valid)

    # 4. Assertions
    assert response_send.status_code == 200 # JSON-RPC errors use 200 OK
    response_data = response_send.json()

    # ConflictError should NOT result in an RPC error, but an input-required task state.
    assert "result" in response_data
    assert response_data.get("error") is None

    result_payload = response_data["result"]
    assert result_payload["id"] == task_id
    assert result_payload["status"]["state"] == TaskState.INPUT_REQUIRED.value
    assert "Could not create event due to scheduling conflicts" in result_payload["status"]["message"]["parts"][0]["text"]

    assert len(result_payload["artifacts"]) == 1
    conflict_artifact = result_payload["artifacts"][0]
    assert conflict_artifact["name"] == "conflict_info"
    assert "conflicts" in conflict_artifact["parts"][0]["data"]
    assert len(conflict_artifact["parts"][0]["data"]["conflicts"]) > 0
    assert conflict_artifact["parts"][0]["data"]["conflicts"][0]["id"] == str(existing_event.id)

def test_rpc_tasks_send_structured_create_validation_error(
    client: TestClient,
    auth_headers_valid: dict
):
    """Test tasks/send structured create with invalid data (missing title)."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    start_time = datetime.now(timezone.utc)
    end_time = start_time + timedelta(hours=1)

    # Invalid structured data payload (missing 'title')
    invalid_structured_data = {
        # "title": "Missing Title",
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "time_zone": "UTC"
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "create_event",
        "payload": invalid_structured_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper) # Use wrapped data
        ])
    )
    rpc_send_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # Send the API request
    response_send = client.post("/api/rpc", json=rpc_send_payload, headers=auth_headers_valid)

    # Assertions
    assert response_send.status_code == 200 # JSON-RPC errors use 200 OK
    response_data = response_send.json()
    # Expect InvalidParamsError because the handler should raise it
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == -32602 # INVALID_PARAMS (was expecting -32603 incorrectly)
    # Check for the specific message from the handler's A2AInvalidParamsError
    assert "Could not determine create action from structured data provided." in error_payload["message"]
    # Data field might contain details, let's not assert its absence/presence for now

    # Verify request ID is echoed
    assert response_data["id"] == rpc_send_payload["id"] 