import pytest
from unittest.mock import AsyncMock, ANY

from scheduling_agent.handlers.nlp_create import handle_nlp_create
from scheduling_agent.a2a.types import (
    Task,
    TaskState,
    Message,
    TaskStatus,
)
from scheduling_agent.core.schemas import LLMSchedulingParseResult

# TODO: Implement comprehensive tests when handle_nlp_create logic is developed.

@pytest.mark.asyncio
async def test_handle_nlp_create_placeholder(
    mock_task_manager: AsyncMock,
):
    """Tests the placeholder implementation of handle_nlp_create."""
    task_id = "test_nlp_create_placeholder"
    # Create a dummy NLP result
    nlp_result = LLMSchedulingParseResult(intent="create_event")

    # Configure mock for require_input_task
    mock_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=None)
    mock_task_manager.require_input_task.return_value = Task(id=task_id, status=mock_status, artifacts=[], history=[])

    # Call the handler
    result_task = await handle_nlp_create(
        task_id=task_id,
        nlp_result=nlp_result,
        task_manager=mock_task_manager,
        # No other dependencies needed for placeholder
    )

    # Assertions
    mock_task_manager.require_input_task.assert_awaited_once()
    call_args, _ = mock_task_manager.require_input_task.await_args
    assert call_args[0] == task_id
    assert isinstance(call_args[1], Message)
    assert "not fully supported yet" in call_args[1].parts[0].text.lower()

    assert result_task is not None
    assert result_task.status.state == TaskState.INPUT_REQUIRED
