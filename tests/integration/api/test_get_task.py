import pytest
import pytest_asyncio
import httpx
import uuid
from unittest.mock import AsyncMock, MagicMock

# Import fixtures from conftest
from .conftest import RPC_URL

# Import necessary types
from scheduling_agent.common.types import (
    JSONRPCRequest,
    TaskGetParams,
    TaskState,
    Task,
    TaskStatus,
    Message,
    Part,
    TaskNotFoundError,
)

@pytest.mark.asyncio
async def test_get_task_success(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
): 
    """Test tasks/get successfully retrieves a task."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock the TaskManager response
    mock_retrieved_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.WORKING, message=Message(role="agent", parts=[Part(type="text", text="Processing...")]))
    )
    mock_task_manager.get_task.return_value = mock_retrieved_task

    # Prepare the RPC request
    params = TaskGetParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get",
        params=params.model_dump(),
        id=request_id
    )

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    result_task_data = response_data["result"]
    assert result_task_data.get("id") == task_id
    assert result_task_data.get("status", {}).get("state") == TaskState.WORKING.value

    # Assert mock calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id)

@pytest.mark.asyncio
async def test_get_task_not_found(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
): 
    """Test tasks/get returns correct error when task is not found."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock TaskManager to return None
    mock_task_manager.get_task.return_value = None 

    # Prepare the RPC request
    params = TaskGetParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get",
        params=params.model_dump(),
        id=request_id
    )

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    assert response_data["error"] is not None

    error_data = response_data["error"]
    assert error_data.get("code") == TaskNotFoundError.DEFAULT_CODE
    assert f"Task with ID '{task_id}' not found." in error_data.get("message", "")

    # Assert mock calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id) 