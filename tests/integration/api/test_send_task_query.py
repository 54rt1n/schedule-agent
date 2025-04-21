import uuid
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

# Import necessary types
from scheduling_agent.a2a.types import (
    JSONRPCRequest,
    Message,
    Part,
    Task,
    TaskSendParams,
    TaskState,
    TaskStatus,
)
from scheduling_agent.core.schemas import LLMDateInfo, LLMSchedulingParseResult

# Import fixtures from conftest
from .conftest import RPC_URL


@pytest.mark.asyncio
async def test_send_task_query_success(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_nlp_processor: AsyncMock,
    mock_conflict_resolver: AsyncMock,  # Required by handler
):
    """Test tasks/send for querying the schedule successfully (natural language input)."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "What do I have tomorrow?"

    # Mock NLP result for query intent
    mock_nlp_result = LLMSchedulingParseResult(
        intent="query_events", date_info=LLMDateInfo(start_date="tomorrow", end_date="tomorrow")
    )
    mock_nlp_processor.process.return_value = mock_nlp_result

    # Mock DB query results (e.g., empty list)
    mock_db_service.query_events.return_value = []

    # Mock the final task state returned by task_manager.complete_task
    final_task = Task(
        id=task_id,
        sessionId=None,
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(
                role="agent", parts=[Part(type="text", text="No events found matching your query.")]
            ),  # Adjusted message
        ),
        params=None,
        artifacts=[],  # No artifact expected if no events found
        history=[],
    )
    mock_task_manager.complete_task.return_value = final_task

    # Prepare the natural language input for the query
    params = TaskSendParams(
        id=task_id, message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id,
    )

    # Action: Send the request
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
    assert len(result_task_data.get("artifacts", [])) == 0  # Check for no artifacts

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_nlp_processor.process.assert_awaited_once_with(query)
    mock_db_service.query_events.assert_awaited_once()
    mock_task_manager.complete_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_rpc_tasks_send_unclear_intent(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock,
    mock_nlp_processor: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
):
    """Test calling tasks/send with generic text, expecting INPUT_REQUIRED."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    params = TaskSendParams(
        id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0", method="tasks/send", params=params.model_dump(), id=request_id
    )

    # Mock NLP to return 'unknown' intent
    mock_nlp_result = LLMSchedulingParseResult(intent="unknown")
    mock_nlp_processor.process.return_value = mock_nlp_result

    # Mock the final task state returned by task_manager.require_input_task
    final_task = Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.INPUT_REQUIRED,
            message=Message(
                role="agent",
                parts=[
                    Part(
                        type="text",
                        text="I wasn't sure what you wanted to do. Could you please rephrase your request?",
                    )
                ],
            ),
        ),
        artifacts=[],
    )
    mock_task_manager.require_input_task.return_value = final_task

    # Action: Send the request
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
    assert result_task_data.get("status", {}).get("state") == TaskState.INPUT_REQUIRED.value
    status_message = result_task_data.get("status", {}).get("message", {})
    assert status_message.get("role") == "agent"
    assert len(status_message.get("parts", [])) == 1
    assert status_message["parts"][0].get("type") == "text"
    assert status_message["parts"][0].get("text", "") == "I wasn't sure what you wanted to do. Could you please rephrase your request?"

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_nlp_processor.process.assert_awaited_once_with("test")
    mock_task_manager.require_input_task.assert_awaited_once()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
