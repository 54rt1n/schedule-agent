import pytest
import pytest_asyncio
import httpx
import uuid

# Import the app instance from your API module
from scheduling_agent.api.main import app
# Import necessary types
from scheduling_agent.common.types import JSONRPCRequest, JSONRPCResponse, MethodNotFoundError, InternalError, Message, Part, TaskSendParams

BASE_URL = "http://test"
RPC_URL = "/api/rpc/" # Added trailing slash

@pytest_asyncio.fixture
async def async_client():
    """Provides an httpx client configured for the FastAPI app."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
        yield client

@pytest.mark.asyncio
async def test_rpc_tasks_send_unclear_intent(async_client: httpx.AsyncClient):
    """Test calling tasks/send with generic text, expecting INPUT_REQUIRED."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    # Minimal valid params for tasks/send with simple text
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text="test")])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(), # Send as dict
        id=request_id
    )

    # Note: This test relies on the default dependency injection behavior.
    # The NLPProcessor should return an unclear intent for "test".

    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    assert response.status_code == 200 # JSON-RPC uses 200 OK
    response_data = response.json()

    # Validate JSON-RPC response structure for SUCCESS
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    # Expect NO error field
    assert "error" not in response_data or response_data["error"] is None
    # Expect a RESULT field
    assert "result" in response_data
    assert response_data["result"] is not None

    # Validate the Task object in the result
    result_task_data = response_data["result"]
    assert result_task_data.get("id") == task_id
    # Import TaskState if not already imported at the top
    from scheduling_agent.common.types import TaskState
    assert result_task_data.get("status", {}).get("state") == TaskState.INPUT_REQUIRED.value # Expect INPUT_REQUIRED
    # Check for the specific clarification message
    status_message = result_task_data.get("status", {}).get("message", {})
    assert status_message.get("role") == "agent"
    assert len(status_message.get("parts", [])) == 1
    assert status_message["parts"][0].get("type") == "text"
    # Check if the expected clarification text is present
    assert "I wasn't sure what you wanted to do" in status_message["parts"][0].get("text", "")

@pytest.mark.asyncio
async def test_rpc_unknown_method(async_client: httpx.AsyncClient):
    """Test calling an unknown RPC method."""
    request_id = str(uuid.uuid4())
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0", # USE 2.0
        method="foo/bar", # An unknown method
        params={"arg1": "value1"},
        id=request_id
    )

    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    assert response.status_code == 200 # JSON-RPC uses 200 OK even for errors
    response_data = response.json()

    # Validate JSON-RPC response structure
    assert response_data.get("jsonrpc") == "2.0" # Expect 2.0
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    assert response_data["error"] is not None

    # Validate the specific A2A error (MethodNotFoundError)
    error_data = response_data["error"]
    assert error_data.get("code") == MethodNotFoundError.DEFAULT_CODE
    assert error_data.get("message") == MethodNotFoundError.DEFAULT_MESSAGE

# TODO: Add tests for tasks/get routing once placeholder exists
# TODO: Add tests for invalid parameters (e.g., missing 'id' for tasks/get) once validation is added
# TODO: Add tests for requests without an ID (Notifications) - should they still route? 