import pytest
import uuid
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock

from sa.a2a.dto import Task, TaskStatus, TaskState, Message, Part
from sa.a2a.exceptions import TaskNotFoundError, TaskNotCancelableError
from sa.a2a.task_manager import SchedulingTaskManager
from ..conftest import build_rpc_request


def test_rpc_tasks_cancel_success(client: TestClient, auth_headers_valid: dict):
    """Test successful cancellation of a task via RPC."""
    task_id = str(uuid.uuid4())
    expected_canceled_task = Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.CANCELED,
            message=Message(
                role="agent",
                parts=[Part(type="text", text="Task canceled by user.")]
            )
        )
    )

    # Patch the handler function directly within the routes module
    with patch("sa.api.routes.rpc.handle_tasks_cancel", new_callable=AsyncMock) as mock_handler:
        mock_handler.return_value = expected_canceled_task

        rpc_payload = build_rpc_request("tasks/cancel", {"id": task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "result" in response_data
    assert response_data.get("error") is None

    result_payload = response_data["result"]
    assert result_payload["id"] == task_id
    assert result_payload["status"]["state"] == TaskState.CANCELED.value
    assert result_payload["status"]["message"]["role"] == "agent"
    assert result_payload["status"]["message"]["parts"][0]["type"] == "text"
    assert result_payload["status"]["message"]["parts"][0]["text"] == "Task canceled by user."

    # Verify the mock handler was called correctly
    mock_handler.assert_awaited_once()
    call_args, call_kwargs = mock_handler.await_args
    assert call_kwargs['params'].id == task_id
    # Check if task_manager dependency was passed (it should be by FastAPI)
    assert 'task_manager' in call_kwargs
    assert isinstance(call_kwargs['task_manager'], SchedulingTaskManager)


def test_rpc_tasks_cancel_not_found(client: TestClient, auth_headers_valid: dict):
    """Test canceling a non-existent task ID."""
    non_existent_task_id = str(uuid.uuid4())

    with patch("sa.api.routes.rpc.handle_tasks_cancel", new_callable=AsyncMock) as mock_handler:
        # Configure the mock to raise the specific exception the RPC handler expects
        mock_handler.side_effect = TaskNotFoundError(f"Task {non_existent_task_id} not found.")

        rpc_payload = build_rpc_request("tasks/cancel", {"id": non_existent_task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == TaskNotFoundError.DEFAULT_CODE # -32001
    assert non_existent_task_id in error_payload["message"]
    assert "not found" in error_payload["message"].lower()

    mock_handler.assert_awaited_once()
    call_args, call_kwargs = mock_handler.await_args
    assert call_kwargs['params'].id == non_existent_task_id


def test_rpc_tasks_cancel_not_cancelable_completed(client: TestClient, auth_headers_valid: dict):
    """Test canceling a task that is already completed."""
    task_id = str(uuid.uuid4())

    with patch("sa.api.routes.rpc.handle_tasks_cancel", new_callable=AsyncMock) as mock_handler:
        mock_handler.side_effect = TaskNotCancelableError(f"Task {task_id} is already completed.")

        rpc_payload = build_rpc_request("tasks/cancel", {"id": task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == TaskNotCancelableError.DEFAULT_CODE # -32002
    assert task_id in error_payload["message"]
    assert "already completed" in error_payload["message"].lower()

    mock_handler.assert_awaited_once()
    call_args, call_kwargs = mock_handler.await_args
    assert call_kwargs['params'].id == task_id


def test_rpc_tasks_cancel_not_cancelable_failed(client: TestClient, auth_headers_valid: dict):
    """Test canceling a task that has already failed."""
    task_id = str(uuid.uuid4())

    with patch("sa.api.routes.rpc.handle_tasks_cancel", new_callable=AsyncMock) as mock_handler:
        mock_handler.side_effect = TaskNotCancelableError(f"Task {task_id} has already failed.")

        rpc_payload = build_rpc_request("tasks/cancel", {"id": task_id})
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    assert response.status_code == 200
    response_data = response.json()
    assert "error" in response_data
    assert response_data.get("result") is None

    error_payload = response_data["error"]
    assert error_payload["code"] == TaskNotCancelableError.DEFAULT_CODE # -32002
    assert task_id in error_payload["message"]
    assert "already failed" in error_payload["message"].lower() # Check for 'failed' specifically

    mock_handler.assert_awaited_once()
    call_args, call_kwargs = mock_handler.await_args
    assert call_kwargs['params'].id == task_id
