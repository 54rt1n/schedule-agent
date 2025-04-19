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
    CancelTaskParams,
    TaskState,
    Task,
    TaskStatus,
    Message,
    Part,
    TaskNotFoundError,
    TaskNotCancelableError,
)

@pytest.mark.asyncio
async def test_cancel_task_success(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
):
    """Test tasks/cancel successfully cancels a task."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock the TaskManager response for successful cancellation
    mock_canceled_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.CANCELED, message=Message(role="agent", parts=[Part(type="text", text="Task canceled by request.")]))
    )
    mock_task_manager.cancel_task.return_value = mock_canceled_task

    # Prepare the RPC request
    params = CancelTaskParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/cancel",
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
    assert result_task_data.get("status", {}).get("state") == TaskState.CANCELED.value

    # Assert mock calls
    mock_task_manager.cancel_task.assert_awaited_once_with(task_id=task_id) # Ensure keyword arg matches TaskManager if defined that way

@pytest.mark.asyncio
async def test_cancel_task_not_found(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
):
    """Test tasks/cancel returns correct error when task is not found."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock TaskManager to raise TaskNotFoundError
    mock_task_manager.cancel_task.side_effect = TaskNotFoundError(f"Task with ID '{task_id}' not found for cancellation")

    # Prepare the RPC request
    params = CancelTaskParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/cancel",
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
    assert f"Task with ID '{task_id}' not found for cancellation" in error_data.get("message", "")

    # Assert mock calls
    mock_task_manager.cancel_task.assert_awaited_once_with(task_id=task_id)

@pytest.mark.asyncio
async def test_cancel_task_not_cancelable(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
):
    """Test tasks/cancel returns correct error when task is not in a cancelable state."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock TaskManager to raise TaskNotCancelableError
    mock_task_manager.cancel_task.side_effect = TaskNotCancelableError(f"Task {task_id} is in state 'completed' and cannot be canceled.")

    # Prepare the RPC request
    params = CancelTaskParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/cancel",
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
    assert error_data.get("code") == TaskNotCancelableError.DEFAULT_CODE
    assert f"Task {task_id} is in state 'completed' and cannot be canceled." in error_data.get("message", "")

    # Assert mock calls
    mock_task_manager.cancel_task.assert_awaited_once_with(task_id=task_id) 