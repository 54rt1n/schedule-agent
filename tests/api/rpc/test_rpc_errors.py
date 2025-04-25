import pytest
from fastapi.testclient import TestClient
from ..conftest import build_rpc_request

# JSON-RPC Error Codes (as defined by JSON-RPC 2.0 spec)
# https://www.jsonrpc.org/specification#error_object
PARSE_ERROR = -32700      # Invalid JSON was received by the server. An error occurred on the server while parsing the JSON text.
INVALID_REQUEST = -32600  # The JSON sent is not a valid Request object.
METHOD_NOT_FOUND = -32601 # The method does not exist / is not available.
INVALID_PARAMS = -32602   # Invalid method parameter(s).
INTERNAL_ERROR = -32603   # Internal JSON-RPC error.
# -32000 to -32099 are reserved for implementation-defined server-errors.
TASK_NOT_FOUND = -32001
TASK_NOT_CANCELABLE = -32002
INVALID_TASK_STATE = -32003
AGENT_CAPABILITY_ERROR = -32004

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