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

@pytest.mark.asyncio # Mark test as async
async def test_rpc_tasks_send_structured_delete_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService, # Add needed fixture
    date_utils: DateUtils, # Add needed fixture
    conflict_resolver: ConflictResolver # Add missing fixture
):
    """Test tasks/send with structured data for delete_event intent."""
    # 1. Create an event to delete using the new DB helper
    start_time = datetime.now(timezone.utc) + timedelta(days=4)
    end_time = start_time + timedelta(minutes=15)
    event_to_create = EventCreate(
        title="Event To Delete",
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

    # 2. Prepare Delete Request
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    delete_data = {
        "id": str(event_id) # Correct field name for EventDelete DAO
    }
    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        intent="delete_event", # Explicit intent
        message=Message(role="user", parts=[
            Part(type="data", data=delete_data) # Structured delete data
        ])
    )
    rpc_delete_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Delete Request
    response_delete = client.post("/api/rpc", json=rpc_delete_payload, headers=auth_headers_valid)

    # 4. Assertions
    assert response_delete.status_code == 200
    response_delete_data = response_delete.json()
    assert "result" in response_delete_data
    assert response_delete_data.get("error") is None

    result_payload = response_delete_data["result"]
    assert result_payload["id"] == task_id
    # Expect COMPLETED state if delete is successful
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value

    # 5. Verify Deletion (Optional but recommended)
    # Try to query the deleted event ID - expect failure or empty result
    query_task_id = str(uuid.uuid4())
    target_date = start_time.strftime("%Y-%m-%d")
    query_data = { "start_date": target_date, "end_date": target_date }
    query_params = TaskSendParams(
        id=query_task_id, sessionId=session_id, intent="query_events",
        message=Message(role="user", parts=[Part(type="data", data=query_data)])
    )
    rpc_verify_payload = build_rpc_request("tasks/send", query_params.model_dump())
    response_verify = client.post("/api/rpc", json=rpc_verify_payload, headers=auth_headers_valid)

    # Check response status and error absence before accessing result
    assert response_verify.status_code == 200
    response_verify_json = response_verify.json()
    assert "result" in response_verify_json, f"Verification query failed: {response_verify_json.get('error')}"
    assert response_verify_json.get("error") is None
    
    # Get results from the task status message (which contains a JSON string)
    verify_result_task = response_verify_json["result"]
    assert verify_result_task["status"]["state"] == TaskState.COMPLETED.value
    verify_message_part = verify_result_task["status"]["message"]["parts"][0]
    assert verify_message_part["type"] == "text"
    
    query_results = json.loads(verify_message_part["text"])
    assert isinstance(query_results, list)
    
    # Check that the deleted event is not in the parsed results
    found_deleted_event = any(e["id"] == str(event_id) for e in query_results)
    assert not found_deleted_event, f"Deleted event {event_id} was found in query results"

def test_rpc_tasks_send_structured_delete_not_found(
    client: TestClient,
    auth_headers_valid: dict
):
    """Test tasks/send structured delete for a non-existent event ID."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    non_existent_event_id = str(uuid.uuid4())

    # Prepare Delete Request for non-existent event
    delete_data = {
        "id": non_existent_event_id
    }
    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        intent="delete_event",
        message=Message(role="user", parts=[
            Part(type="data", data=delete_data)
        ])
    )
    rpc_delete_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # Send Delete Request
    response_delete = client.post("/api/rpc", json=rpc_delete_payload, headers=auth_headers_valid)

    # Assertions
    assert response_delete.status_code == 200
    response_delete_data = response_delete.json()
    assert "result" in response_delete_data
    assert response_delete_data.get("error") is None

    result_payload = response_delete_data["result"]
    assert result_payload["id"] == task_id
    # Expect FAILED state because the event wasn't found by the handler
    assert result_payload["status"]["state"] == TaskState.FAILED.value
    # Check the status message for indication of failure
    assert "not found" in result_payload["status"]["message"]["parts"][0]["text"].lower()
    assert non_existent_event_id in result_payload["status"]["message"]["parts"][0]["text"]

def test_rpc_tasks_send_structured_delete_validation_error(
    client: TestClient,
    auth_headers_valid: dict
):
    """Test tasks/send structured delete with an invalid event ID format."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    invalid_event_id = "not-a-valid-uuid"

    # Prepare Delete Request with invalid ID format
    delete_data = {
        "id": invalid_event_id
    }
    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        intent="delete_event",
        message=Message(role="user", parts=[
            Part(type="data", data=delete_data)
        ])
    )
    rpc_delete_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # Send Delete Request
    response_delete = client.post("/api/rpc", json=rpc_delete_payload, headers=auth_headers_valid)

    # Assertions
    assert response_delete.status_code == 200 # Expect JSON-RPC error
    response_delete_data = response_delete.json()
    # Expect InvalidParamsError because the handler should raise it
    assert "error" in response_delete_data
    assert response_delete_data.get("result") is None

    error_payload = response_delete_data["error"]
    assert error_payload["code"] == -32602 # INVALID_PARAMS
    # Check that the core message is present, details may vary
    assert "Invalid delete parameters" in error_payload["message"], f"Error message: {error_payload['message']}"
    # Check that the data field contains details about the validation failure
    error_data = error_payload.get("data")
    assert error_data is not None
    assert isinstance(error_data, list)
    assert len(error_data) > 0
    # Check the specifics of the first error detail
    first_error = error_data[0]
    assert first_error.get("type") == "uuid_parsing"
    assert first_error.get("loc") == ["id"] # Pydantic errors() uses list for loc

    # Verify request ID is echoed
    assert response_delete_data["id"] == rpc_delete_payload["id"] 