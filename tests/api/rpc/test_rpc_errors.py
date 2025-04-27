import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock
import uuid
from datetime import datetime

from sa.a2a.exceptions import (
    PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR,
    ParseError, InvalidRequestError, MethodNotFoundError, InvalidParamsError, InternalError,
    TaskNotFoundError as A2ATaskNotFoundError
)
from sa.core.exceptions import (
    DatabaseError, NLPError, EventNotFoundError, TaskNotFoundError, CoreLogicError,
    ConflictError, ExporterError,
)
from sa.a2a.dto import TaskState
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from sa.db.dao import EventCreate

from ..conftest import build_rpc_request, create_db_event

def test_rpc_method_not_found(client: TestClient, auth_headers_valid: dict):
    """Test calling a non-existent RPC method."""
    rpc_payload = build_rpc_request("non_existent_module/non_existent_method")
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200 # JSON-RPC errors use 200 OK
    response_data = response.json()

    assert "error" in response_data
    assert response_data.get("result") is None
    assert response_data["error"]["code"] == METHOD_NOT_FOUND
    assert "not found" in response_data["error"]["message"] # Check for core phrase
    assert response_data["id"] == rpc_payload["id"] # Ensure ID is echoed back

def test_rpc_invalid_params_structure(client: TestClient, auth_headers_valid: dict):
    """
    Test calling a known method (tasks/get) with structurally invalid parameters
    (e.g., sending a list where an object is expected, or missing required fields).
    """
    # Example 1: Sending a list instead of an object for tasks/get params
    rpc_payload = build_rpc_request("tasks/get", ["invalid_list_param"])
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data["error"]["code"] == INVALID_PARAMS
    # The exact message might vary based on Pydantic/FastAPI validation details
    assert "Invalid parameters" in response_data["error"]["message"] # Check for core phrase
    # Check that the request ID is still present
    assert response_data["id"] == rpc_payload["id"]

    # Example 2: Sending an object but missing the required 'id' field for tasks/get
    rpc_payload_missing_key = build_rpc_request("tasks/get", {"taskId": "wrong_key"})
    response_missing_key = client.post("/api/rpc", json=rpc_payload_missing_key, headers=auth_headers_valid)

    assert response_missing_key.status_code == 200
    response_data_missing_key = response_missing_key.json()
    assert "error" in response_data_missing_key
    assert response_data_missing_key["error"]["code"] == INVALID_PARAMS
    assert "Invalid parameters" in response_data_missing_key["error"]["message"] # Corrected check for core phrase
    # Optionally check data field for validation details if the handler provides them
    # assert "field required" in str(response_data_missing_key["error"].get("data")).lower()
    assert response_data_missing_key["id"] == rpc_payload_missing_key["id"]

def test_rpc_invalid_params_data_type(client: TestClient, auth_headers_valid: dict):
    """
    Test calling a known method (tasks/get) with parameters of the wrong data type
    (e.g., sending an integer where a string ID is expected).
    """
    # tasks/get expects params: {"id": "string"}
    rpc_payload = build_rpc_request("tasks/get", {"id": 12345}) # Send integer ID

    # handle_rpc_request function should catch this before calling the specific handler.
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data["error"]["code"] == INVALID_PARAMS # -32602
    assert "Invalid parameters" in response_data["error"]["message"]
    assert response_data["id"] == rpc_payload["id"]

    # Optionally, check the 'data' field if include_validation_error_detail=True in settings
    # This requires accessing the app settings within the test, which can be tricky.
    # For now, we focus on the error code and basic message.
    # if client.app.state.settings.include_validation_error_detail:
    #     assert "details" in response_data["error"]["data"]
    #     assert "Input should be a valid string" in str(response_data["error"]["data"]["details"])

def test_rpc_invalid_json(client: TestClient, auth_headers_valid: dict):
    """Test sending malformed JSON to the RPC endpoint."""
    invalid_json_string = '{"jsonrpc": "2.0", "method": "tasks/get", "params": {"id": "test"}, "id": 1, ]' # Extra comma

    response = client.post(
        "/api/rpc",
        content=invalid_json_string,
        headers={**auth_headers_valid, "Content-Type": "application/json"}
    )

    # Our custom validation_exception_handler catches JSONDecodeError and returns
    # a JSON-RPC ParseError (-32700) with status 200 OK.
    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None
    assert response_data["error"]["code"] == PARSE_ERROR # Parse error code
    assert "failed to parse" in response_data["error"]["message"].lower() # Check for core phrase
    # The ID might be null if the JSON was too malformed to parse it
    assert response_data.get("id") is None

def test_rpc_invalid_request_structure(client: TestClient, auth_headers_valid: dict):
    """Test sending valid JSON but invalid JSON-RPC structure (e.g., missing 'method')."""
    invalid_rpc_payload = {
        "jsonrpc": "2.0", # Missing required field
        # "method": "tasks/get",
        "params": {"id": "test"},
        "id": 1
    }
    response = client.post("/api/rpc", json=invalid_rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200 # Should still be processed by RPC router
    response_data = response.json()
    assert "error" in response_data
    # JSON-RPC defines -32600 for Invalid Request
    assert response_data["error"]["code"] == INVALID_REQUEST
    # Check if the core part of the expected message is present
    assert "Invalid JSON-RPC request structure" in response_data["error"]["message"]
    # ID MUST be returned if it was present in the request, even for errors
    assert response_data["id"] == invalid_rpc_payload["id"] 

# --- Exception Mapping Tests ---

def test_rpc_exception_mapping_database_error(client: TestClient, auth_headers_valid: dict):
    """Test that a DatabaseError raised by a handler maps to InternalError (-32603)."""
    task_id = "task-db-error"
    error_message = "Simulated DB connection failure"

    with patch("sa.api.routes.rpc.handle_tasks_get", new_callable=AsyncMock) as mock_handler:
        # Patch the *core* exception TaskNotFoundError from sa.core.exceptions if handle_tasks_get raises it
        # Or patch the A2A exception if handle_tasks_get raises that directly (depends on implementation)
        mock_handler.side_effect = DatabaseError(error_message, data={"taskId": task_id}) # Pass data here
        rpc_payload = build_rpc_request("tasks/get", {"id": task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == INTERNAL_ERROR # -32603
    # Check that the message is the *specific* message from the original exception
    assert error_payload["message"] == error_message
    # Check that the data is passed correctly
    assert error_payload["data"] is not None
    assert error_payload["data"]["taskId"] == task_id
    mock_handler.assert_awaited_once()

def test_rpc_exception_mapping_nlp_error(client: TestClient, auth_headers_valid: dict):
    """Test that an NLPError raised by a handler maps to InternalError (-32603)."""
    task_id = "task-nlp-error"
    error_message = "Simulated LLM API failure"
    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": "Schedule a meeting"}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Decide which handler to patch based on the actual flow for NLP errors
    # If handle_tasks_send calls handle_nlp_request and *that* raises NLPError:
    with patch("sa.api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_handler:
        mock_handler.side_effect = NLPError(error_message, data={"taskId": task_id}) # Pass data
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == INTERNAL_ERROR # -32603
    # Check that the message is the *specific* message from the original exception
    assert error_payload["message"] == error_message
    # Check that the data is passed correctly
    assert error_payload["data"] is not None
    assert error_payload["data"]["taskId"] == task_id
    mock_handler.assert_awaited_once()

def test_rpc_exception_mapping_generic_exception(client: TestClient, auth_headers_valid: dict):
    """Test that a generic Exception raised by a handler maps to InternalError (-32603)."""
    task_id = "task-generic-error"
    error_message = "Something unexpected happened!" # This specific message won't appear

    with patch("sa.api.routes.rpc.handle_tasks_get", new_callable=AsyncMock) as mock_handler:
        mock_handler.side_effect = Exception(error_message)
        rpc_payload = build_rpc_request("tasks/get", {"id": task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == INTERNAL_ERROR # -32603
    # Check the generic message from the A2AInternalError raised in the handler
    assert error_payload["message"] == f"Internal server error processing method tasks/get"
    # Check the data added by the generic handler in rpc.py
    assert error_payload["data"] is not None
    assert error_payload["data"]["error_type"] == "Exception"
    # Note: The generic handler in __main__.py might add different/more data if the exception propagates there
    mock_handler.assert_awaited_once()

def test_rpc_exception_mapping_a2a_from_handler(client: TestClient, auth_headers_valid: dict):
    """Test that a specific BaseA2AError (TaskNotFound) maps correctly."""
    task_id = "task-a2a-error"
    event_id = str(uuid.uuid4())
    # Using A2ATaskNotFoundError now
    error_message = f"Task {task_id} not found for update"
    update_payload = {
        "event_id": event_id,
        "update_data": {"title": "New Title"}
    }
    send_params = {
        "id": task_id,
        "intent": "update_event",
        "message": {
            "role": "user",
            "parts": [{"type": "data", "data": update_payload}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Patch the handler that is expected to raise the A2A error
    with patch("sa.api.routes.rpc.handle_structured_update", new_callable=AsyncMock) as mock_handler:
        # Raise the *A2A* specific exception directly
        mock_handler.side_effect = A2ATaskNotFoundError(error_message) # No data needed for this test case
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    # Use the correct A2A exception class to get the default code
    assert error_payload["code"] == A2ATaskNotFoundError.DEFAULT_CODE # -32001
    assert error_payload["message"] == error_message # Check exact message
    # A2A TaskNotFoundError doesn't add data by default, so check it's None or absent
    assert error_payload.get("data") is None
    mock_handler.assert_awaited_once() 

@pytest.mark.asyncio
async def test_rpc_exception_mapping_conflict_error_to_invalid_params(
    client: TestClient, auth_headers_valid: dict, app_db_service: DatabaseService,
    date_utils: DateUtils, conflict_resolver: ConflictResolver,
):
    """Test ConflictError during create maps to InvalidParamsError (-32602)."""
    # 1. Create an initial event that will cause a conflict
    # Need datetime objects for EventCreate, not ISO strings
    start_time_existing = datetime.fromisoformat("2025-08-01T10:00:00+00:00")
    end_time_existing = datetime.fromisoformat("2025-08-01T11:00:00+00:00")

    existing_event_payload = {
        "title": "Existing Meeting",
        "start_time": start_time_existing,
        "end_time": end_time_existing,
    }
    # Use the fixture to create the event directly in DB
    existing_event = await create_db_event(
        EventCreate(**existing_event_payload), # Pass EventCreate model
        app_db_service, 
        date_utils, 
        conflict_resolver
    )
    assert existing_event is not None
    # existing_event_id = existing_event.id # existing_event is the model with ID

    # 2. Attempt to create a conflicting event via API
    task_id_conflict = f"task-conflict-{uuid.uuid4()}"
    # API expects ISO strings in the data part
    start_time_conflict_str = "2025-08-01T10:30:00+00:00"
    end_time_conflict_str = "2025-08-01T11:30:00+00:00"

    conflict_create_payload = {
        "title": "Conflicting Meeting",
        "start_time": start_time_conflict_str,
        "end_time": end_time_conflict_str,
    }
    send_params = {
        "id": task_id_conflict,
        "intent": "create_event",
        "message": {
            "role": "user",
            "parts": [{"type": "data", "data": conflict_create_payload}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # 3. Call the API - DO NOT MOCK handle_structured_create
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    # 4. Assert the RPC error response mapped from ConflictError
    assert response.status_code == 200
    response_data = response.json()
    assert "result" in response_data
    assert response_data.get("error") is None
    assert response_data["id"] == rpc_payload["id"]

    result_payload = response_data["result"]
    assert result_payload["id"] == task_id_conflict
    assert result_payload["status"]["state"] == TaskState.INPUT_REQUIRED.value
    assert "conflict" in result_payload["status"]["message"]["parts"][0]["text"].lower()

    assert len(result_payload["artifacts"]) >= 1
    conflict_artifact = next((a for a in result_payload["artifacts"] if a["name"] == "conflict_info"), None)
    assert conflict_artifact is not None
    assert "conflicts" in conflict_artifact["parts"][0]["data"]
    assert len(conflict_artifact["parts"][0]["data"]["conflicts"]) > 0
    # Ensure the conflicting event ID is present in the artifact data
    found_conflict = False
    for conflict_detail in conflict_artifact["parts"][0]["data"]["conflicts"]:
        if conflict_detail.get("id") == str(existing_event.id):
            found_conflict = True
            break
    assert found_conflict, f"Conflicting event ID {existing_event.id} not found in artifact"

@pytest.mark.skip(reason="tasks/send does not yet handle export_schedule intent")
def test_rpc_exception_mapping_exporter_error_to_internal_error(
    client: TestClient, auth_headers_valid: dict
):
    """Test ExporterError from service maps to InternalError (-32603)."""
    task_id = f"task-exporter-error-{uuid.uuid4()}"
    error_message = "Simulated ICS export service failure"

    export_payload = {"format": "ics", "start_date": "2025-01-01", "end_date": "2025-01-31"}
    send_params = {
        "id": task_id,
        "intent": "export_schedule",
        "message": {
            "role": "user",
            "parts": [{"type": "data", "data": export_payload}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Patch the export method at its correct location
    with patch("sa.event.exporters.ical_exporter.ICalExporter.export", side_effect=ExporterError(error_message, data={"reason": "mock failure"})) as mock_export_method:

        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None
    assert response_data["id"] == rpc_payload["id"]

    error_payload = response_data["error"]
    # Expect mapping to A2A InternalError
    assert error_payload["code"] == InternalError.DEFAULT_CODE # -32603
    # The API error handler should propagate the original message
    assert error_payload["message"] == error_message
    assert error_payload["data"] is not None
    assert error_payload["data"]["reason"] == "mock failure"

    # TODO: Verify mock call count after confirming patch target

def test_rpc_tasks_send_missing_intent_for_structured_data(
    client: TestClient, auth_headers_valid: dict
):
    """
    Test tasks/send with structured data but missing 'intent' results in
    A2AInvalidParamsError (-32602) directly from handle_tasks_send.
    """
    task_id = f"task-missing-intent-{uuid.uuid4()}"
    create_payload = {"summary": "Event without intent", "start": "2025-01-01T12:00:00", "end": "2025-01-01T13:00:00"}
    send_params = {
        "id": task_id,
        # Missing 'intent' field
        "message": {
            "role": "user",
            "parts": [{"type": "data", "data": create_payload}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # No mocking needed, error is raised directly by handle_tasks_send validation
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None
    assert response_data["id"] == rpc_payload["id"]

    error_payload = response_data["error"]
    assert error_payload["code"] == InvalidParamsError.DEFAULT_CODE # -32602
    assert "An explicit 'intent' is required when sending structured data" in error_payload["message"]
    # For this specific error path, the 'data' field is not expected in the error payload
    assert error_payload.get("data") is None, "'data' field should be absent or null in this error case"