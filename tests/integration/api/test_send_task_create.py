import pytest
import pytest_asyncio
import httpx
import uuid
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone

# Import fixtures from conftest
from .conftest import RPC_URL

# Import necessary types
from scheduling_agent.common.types import (
    JSONRPCRequest,
    TaskSendParams,
    TaskState,
    Task,
    TaskStatus,
    Message,
    Part,
    Artifact,
)
# Import db models/services for mocking/assertion
from scheduling_agent.db.models import Event
from scheduling_agent.core.exceptions import ConflictError

@pytest.mark.asyncio
async def test_send_task_create_success(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_conflict_resolver: AsyncMock, # Required by handler even if not used in this path
    mock_nlp_processor: AsyncMock, # Required by handler even if not used in this path
):
    """Test tasks/send for creating an event successfully (structured data input)."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    event_title = "Team Meeting"
    created_event_id = str(uuid.uuid4())

    # Mock the successful event creation in DB service
    mock_created_event = Event(
        id=uuid.UUID(created_event_id),
        title=event_title,
        start_time=datetime(2025, 1, 1, 10, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2025, 1, 1, 11, 0, 0, tzinfo=timezone.utc),
        description="Sync up",
        calendar_id="primary" # Example
    )
    mock_db_service.create_event.return_value = mock_created_event

    # Mock the final task state returned by task_manager.complete_task
    final_task = Task(
        id=task_id,
        sessionId=None,
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role="agent", parts=[Part(type="text", text=f"Event '{event_title}' created successfully.")])
        ),
        params=None, # Params might be set in actual implementation
        artifacts=[
            Artifact(name="created_event", parts=[Part(type="data", data=mock_created_event.model_dump())])
        ],
        history=[] # History might be populated
    )
    mock_task_manager.complete_task.return_value = final_task # Make complete_task return final state

    # Prepare the structured data input for the event
    event_data = {
        "title": event_title,
        "start_time": "2025-01-01T10:00:00Z",
        "end_time": "2025-01-01T11:00:00Z",
        "description": "Sync up",
        "calendar_id": "primary"
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(), # Send Pydantic model as dict
        id=request_id,
    )

    # Action: Send the request (dependencies automatically overridden by conftest fixture)
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    # Assert the result matches the final task state
    result_task_data = response_data["result"]
    expected_task_data = final_task.model_dump()

    assert result_task_data.get("id") == expected_task_data.get("id")
    assert result_task_data.get("status", {}).get("state") == TaskState.COMPLETED.value
    assert len(result_task_data.get("artifacts", [])) == 1
    assert result_task_data["artifacts"][0].get("name") == "created_event"
    assert result_task_data["artifacts"][0]["parts"][0].get("data", {}).get("id") == created_event_id
    assert result_task_data["artifacts"][0]["parts"][0]["data"]["title"] == event_title

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_db_service.create_event.assert_awaited_once()
    mock_task_manager.complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_task_create_conflict(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_conflict_resolver: AsyncMock, # Required by handler
    mock_nlp_processor: AsyncMock, # Required by handler
):
    """Test tasks/send for creating an event when DB raises ConflictError."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    event_title = "Conflicting Meeting"

    # Prepare the structured data input for the event
    event_data = {
        "title": event_title,
        "start_time": "2025-01-02T11:00:00Z",
        "end_time": "2025-01-02T12:00:00Z",
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id,
    )

    # Mock DB service to raise ConflictError
    conflicting_event_id = uuid.uuid4()
    conflicting_event = Event(
        id=conflicting_event_id,
        title="Existing Event",
        start_time=datetime(2025, 1, 2, 10, 30, 0, tzinfo=timezone.utc),
        end_time=datetime(2025, 1, 2, 11, 30, 0, tzinfo=timezone.utc)
    )
    conflict_error = ConflictError(message="Database conflict", conflicts=[{"event": conflicting_event}])
    mock_db_service.create_event.side_effect = conflict_error

    # Mock the conflict resolver alternatives
    mock_conflict_resolver.generate_alternative_times.return_value = ["2025-01-02T13:00:00Z"]

    # Mock the final task state returned by task_manager.require_input_task
    expected_artifact = Artifact(
        name="conflict_info",
        parts=[Part(type="data", data={
            "message": str(conflict_error),
            "conflicts": [conflicting_event.model_dump()],
            "alternatives": ["2025-01-02T13:00:00Z"]
        })]
    )
    final_task = Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.INPUT_REQUIRED,
            message=Message(role="agent", parts=[Part(type="text", text="Event creation failed due to conflicts. Please provide an alternative time or confirm override.")])
        ),
        artifacts=[expected_artifact]
    )
    mock_task_manager.require_input_task.return_value = final_task

    # Action: Send the request (dependencies automatically overridden by conftest fixture)
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    # Assert the result matches the final task state (INPUT_REQUIRED)
    result_task_data = response_data["result"]
    expected_task_data = final_task.model_dump()

    assert result_task_data.get("id") == expected_task_data.get("id")
    assert result_task_data.get("status", {}).get("state") == TaskState.INPUT_REQUIRED.value
    assert len(result_task_data.get("artifacts", [])) == 1
    assert result_task_data["artifacts"][0].get("name") == "conflict_info"
    artifact_data = result_task_data["artifacts"][0]["parts"][0].get("data", {})
    assert artifact_data.get("message") == str(conflict_error)
    assert len(artifact_data.get("conflicts", [])) == 1
    assert artifact_data["conflicts"][0]["id"] == str(conflicting_event.id)
    assert artifact_data.get("alternatives") == ["2025-01-02T13:00:00Z"]

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_db_service.create_event.assert_awaited_once()
    mock_conflict_resolver.generate_alternative_times.assert_awaited_once()
    mock_task_manager.require_input_task.assert_awaited_once()
    mock_task_manager.complete_task.assert_not_called() 