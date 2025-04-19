import pytest
import pytest_asyncio
import httpx
import uuid
from unittest.mock import MagicMock, patch

# Import fixtures from conftest
from .conftest import RPC_URL, BASE_URL # Import BASE_URL for well-known

# Import necessary types
from scheduling_agent.common.types import JSONRPCRequest
# Import middleware module to patch its settings
from scheduling_agent.api.middleware import auth as auth_middleware

# --- Fixture to Enable Auth for these Tests ---

@pytest.fixture(autouse=True)
def enable_auth_for_tests():
    """Patches VALID_API_KEYS specifically for tests in this module."""
    test_api_keys = {"test-key-1", "test-key-2"}
    with patch.object(auth_middleware, 'VALID_API_KEYS', test_api_keys) as _mocked_keys:
        # print(f"Auth enabled for test: {auth_middleware.VALID_API_KEYS}") # Debug
        yield
    # print(f"Auth restored after test: {auth_middleware.VALID_API_KEYS}") # Debug

# --- Tests for API Key Authentication Middleware ---

# TODO: Define a valid API key to use in tests (maybe via fixture/settings)
# For now, assume "test-key-1" is valid based on how settings might be mocked or loaded.
VALID_API_KEY = "test-key-1"
INVALID_API_KEY = "invalid-key"

@pytest.mark.asyncio
async def test_rpc_endpoint_no_auth_header(async_client: httpx.AsyncClient):
    """Test RPC endpoint returns 401/403 if Authorization header is missing."""
    request_id = str(uuid.uuid4())
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get", # Use a known method
        params={"id": "some-task-id"},
        id=request_id
    )

    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Expecting 401 Unauthorized or 403 Forbidden
    assert response.status_code in [401, 403]
    # Optionally check response body for error message if middleware provides one
    # assert "Not authenticated" in response.text or "Forbidden" in response.text

@pytest.mark.asyncio
async def test_rpc_endpoint_invalid_bearer_token(async_client: httpx.AsyncClient):
    """Test RPC endpoint returns 401/403 if Authorization header has invalid key."""
    request_id = str(uuid.uuid4())
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get",
        params={"id": "some-task-id"},
        id=request_id
    )
    headers = {"Authorization": f"Bearer {INVALID_API_KEY}"}

    response = await async_client.post(RPC_URL, json=rpc_request.model_dump(), headers=headers)

    assert response.status_code in [401, 403]

@pytest.mark.asyncio
async def test_rpc_endpoint_valid_bearer_token(async_client: httpx.AsyncClient, mock_task_manager: MagicMock):
    """Test RPC endpoint works (returns 200 OK) with a valid Authorization header.
       We expect an A2A error (e.g., TaskNotFound) because the underlying call will proceed.
    """
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get",
        params={"id": task_id},
        id=request_id
    )
    headers = {"Authorization": f"Bearer {VALID_API_KEY}"}

    # Mock TaskManager to return TaskNotFound error for this specific call
    # Ensure the mock is configured *before* the client call
    from scheduling_agent.common.types import TaskNotFoundError
    mock_task_manager.get_task.side_effect = TaskNotFoundError(f"Task with ID '{task_id}' not found.")

    response = await async_client.post(RPC_URL, json=rpc_request.model_dump(), headers=headers)

    # Expect 200 OK because the auth passed, but the response should contain the A2A error
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "error" in response_data
    assert response_data["error"].get("code") == TaskNotFoundError.DEFAULT_CODE

@pytest.mark.asyncio
async def test_well_known_endpoint_no_auth_needed(async_client: httpx.AsyncClient):
    """Test that the /.well-known/ endpoint is accessible without authentication."""
    # Assuming /.well-known/agent.json exists or is handled
    # We might need to mock the actual endpoint handler if it depends on things
    # or just test that we don't get a 401/403.
    well_known_url = "/.well-known/agent.json" # Adjust if path is different

    # Override the specific endpoint if necessary to prevent other errors
    # from fastapi import FastAPI
    # from scheduling_agent.api.main import app
    # async def mock_well_known(): return {"name": "Test Agent"}
    # original_well_known = app.routes # Requires more complex route finding/override
    # app.get(well_known_url)(mock_well_known) # Simplified override example

    response = await async_client.get(well_known_url)

    # Expect success (200) or potentially Not Found (404) if not implemented,
    # but crucially NOT 401 or 403.
    assert response.status_code != 401
    assert response.status_code != 403
    # For now, accept 200 or 404
    assert response.status_code in [200, 404] # Adjust expected code if endpoint is implemented

    # Restore original route if overridden (needs proper implementation)
    # app.routes = original_well_known 