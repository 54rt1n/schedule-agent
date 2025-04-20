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
# Import AgentCard and get_agent_card for validation
from scheduling_agent.agent_card import AgentCard, get_agent_card

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
    """Test that the /.well-known/agent.json endpoint is accessible without authentication and returns the correct AgentCard."""
    well_known_url = "/.well-known/agent.json"

    response = await async_client.get(well_known_url)

    # Expect success (200) and validate content
    assert response.status_code == 200

    # Validate the response against the AgentCard model and content
    response_data = response.json()
    expected_card_data = get_agent_card().model_dump()

    # Check if the response data is a valid AgentCard (basic structure check)
    try:
        AgentCard(**response_data)
    except Exception as e:
        pytest.fail(f"Response data is not a valid AgentCard: {e}")

    # Compare key fields (adjust keys based on your actual AgentCard definition)
    assert response_data["name"] == expected_card_data["name"]
    assert response_data["description"] == expected_card_data["description"]
    # Compare URL as strings to handle potential HttpUrl type from Pydantic
    assert response_data["url"] == str(expected_card_data["url"])
    assert response_data["version"] == expected_card_data["version"]
    assert response_data["capabilities"] == expected_card_data["capabilities"]
    assert response_data["skills"] == expected_card_data["skills"]
    # Add more specific checks if needed, e.g., for nested structures
    # The line below was removed as AgentCapabilities has no nested 'jsonrpc' key
    # assert response_data["capabilities"]["jsonrpc"]["methods"] == expected_card_data["capabilities"]["jsonrpc"]["methods"] 