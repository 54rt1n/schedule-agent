import pytest
from fastapi.testclient import TestClient
from .conftest import build_rpc_request

def test_rpc_no_auth(client: TestClient):
    """Test accessing RPC endpoint without authentication."""
    rpc_payload = build_rpc_request("tasks/get", {"id": "some-id"})
    response = client.post("/api/rpc", json=rpc_payload)
    # Expect 403 Forbidden because auth middleware should reject
    assert response.status_code == 403
    assert "Not authenticated" in response.text

def test_rpc_invalid_auth(client: TestClient, auth_headers_invalid: dict):
    """Test accessing RPC endpoint with an invalid Bearer token."""
    rpc_payload = build_rpc_request("tasks/get", {"id": "some-id"})
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_invalid)
    assert response.status_code == 403
    assert "Not authenticated" in response.text

def test_rpc_valid_auth(client: TestClient, auth_headers_valid: dict):
    """Test accessing RPC endpoint with valid authentication."""
    rpc_payload = build_rpc_request("tasks/get", {"id": "non-existent-id"})
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)
    # Expect 200 OK because JSON-RPC errors are returned in the body
    assert response.status_code == 200
    response_data = response.json()
    # Expect a TaskNotFoundError because the ID doesn't exist
    assert "error" in response_data
    # Assuming TaskNotFoundError maps to -32001 based on app's exception handling
    assert response_data["error"]["code"] == -32001
    assert "Task with ID" in response_data["error"]["message"] 