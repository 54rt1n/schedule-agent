import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from schedule_a2a.dto import Task, TaskStatus, TaskState, Message, Part, TaskGetParams
from schedule_a2a.exceptions import TaskNotFoundError
from schedule_a2a.task_manager import SchedulingTaskManager
from ..conftest import build_rpc_request

def test_rpc_tasks_get_not_found(client: TestClient, auth_headers_valid: dict):
    """Test that calling tasks/get with a non-existent ID returns TaskNotFoundError."""
    non_existent_task_id = str(uuid.uuid4())

    # Prepare the RPC request for a non-existent ID
    rpc_payload = build_rpc_request("tasks/get", {"id": non_existent_task_id})

    # Make the API call
    response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    # Assertions
    assert response.status_code == 200 # JSON-RPC errors use 200 OK
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    # Check the specific error mapping for TaskNotFoundError
    error_payload = response_data["error"]
    assert error_payload["code"] == TaskNotFoundError.DEFAULT_CODE # -32001
    assert non_existent_task_id in error_payload["message"] # Message should contain the ID

    # Assert that the request ID is correctly echoed back
    assert response_data["id"] == rpc_payload["id"]

def test_rpc_tasks_get_success(client: TestClient, auth_headers_valid: dict):
    """Test successful retrieval of a task via RPC."""
    task_id = str(uuid.uuid4())
    expected_task = Task(
        id=task_id,
        sessionId="session-123",
        status=TaskStatus(
            state=TaskState.WORKING,
            message=Message(role="agent", parts=[Part(type="text", text="Processing...")])
        ),
        artifacts=[],
        history=[]
    )

    with patch("schedule_api.routes.rpc.handle_tasks_get", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = expected_task
        rpc_payload = build_rpc_request("tasks/get", {"id": task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "result" in response_data
    assert response_data.get("error") is None

    result_payload = response_data["result"]
    assert result_payload["id"] == expected_task.id
    assert result_payload["sessionId"] == expected_task.sessionId
    assert result_payload["status"]["state"] == expected_task.status.state.value
    assert result_payload["status"]["message"]["role"] == expected_task.status.message.role
    assert result_payload["status"]["message"]["parts"][0]["text"] == expected_task.status.message.parts[0].text
    assert result_payload["artifacts"] == expected_task.artifacts
    assert result_payload["history"] == expected_task.history
    mock_handler.assert_awaited_once()
    call_args, call_kwargs = mock_handler.await_args
    assert call_kwargs['params'].id == task_id
    assert isinstance(call_kwargs['task_manager'], SchedulingTaskManager)

def test_rpc_tasks_get_history_length(client: TestClient, auth_headers_valid: dict):
    """Test that the historyLength parameter is passed correctly."""
    task_id = str(uuid.uuid4())
    history_len = 5

    mock_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED)
    )

    with patch("schedule_api.routes.rpc.handle_tasks_get", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = mock_task
        rpc_params = {"id": task_id, "historyLength": history_len}
        rpc_payload = build_rpc_request("tasks/get", rpc_params)
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "result" in response_data
    assert response_data["result"]["id"] == task_id

    mock_handler.assert_awaited_once()
    call_args, call_kwargs = mock_handler.await_args
    received_params: TaskGetParams = call_kwargs['params']
    assert received_params.id == task_id
    assert received_params.historyLength == history_len
    assert isinstance(call_kwargs['task_manager'], SchedulingTaskManager) 