import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock
import uuid
import json
from datetime import UTC, datetime

from sa.core.logging_config import get_logger
from sa.db.models.a2a import A2ATask
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy, UpdateStatusInput
from sa.db.service import DatabaseService
from sa.core.exceptions import DatabaseError, TaskNotFoundError
from sa.a2a.dto import TaskStatus, TaskState, Message, Part

logger = get_logger(__name__)


@pytest.fixture
def mock_a2a_task_initial() -> A2ATask:
    """Provides a base A2ATask object matching the DB model."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    task_id = str(uuid.uuid4())
    # Initial history using the DTO TaskStatus structure for consistency
    initial_history_dto = TaskStatus(state=TaskState.SUBMITTED, message=None)
    return A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()), # Add session_id if applicable
        state=TaskState.SUBMITTED.value, # Use the correct initial state from TaskState Enum
        status_message_json=None, # Initially no message
        params_json=json.dumps({"param1": "value1"}),
        artifacts_json=None,
        history_json=json.dumps([initial_history_dto.model_dump()]), # Store DTO history
        created_at=now_naive,
        updated_at=now_naive,
    )

@pytest.mark.asyncio
async def test_update_a2a_task_status_success(
    mock_db_service: DatabaseService,
    mock_a2a_task_initial: A2ATask,
):
    """Test successfully updating the status of an A2A task."""
    # --- Arrange ---
    task_id = mock_a2a_task_initial.id

    # Create the new status using DTO models
    new_message_text = "Task picked up for processing."
    new_message = Message(role="agent", parts=[Part(type="text", text=new_message_text)])
    new_status = TaskStatus(
        state=TaskState.WORKING, # Use Enum member
        message=new_message
    )
    strategy_input: UpdateStatusInput = (task_id, new_status)

    # Mock finding the task
    mock_execute_result = MagicMock()
    # Make scalar_one_or_none synchronous
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_a2a_task_initial)
    # Configure session.execute on mock_session
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act ---
    await strategy.execute(data=strategy_input)

    # --- Assert ---
    mock_db_service.mock_session.execute.assert_awaited_once()
    # Check that scalar_one_or_none was called on the result
    mock_execute_result.scalar_one_or_none.assert_called_once()

    # Verify the DB model object was modified correctly before add/flush
    assert mock_a2a_task_initial.state == TaskState.WORKING.value # Check against Enum value
    # Compare the stored JSON string with the JSON dump of the DTO message
    assert mock_a2a_task_initial.status_message_json == new_message.model_dump_json()
    # Ensure updated_at timestamp was advanced (or at least changed)
    assert mock_a2a_task_initial.updated_at > mock_a2a_task_initial.created_at

    # Verify history update in the DB model field
    history = json.loads(mock_a2a_task_initial.history_json)
    assert len(history) == 2 # Initial + New
    assert history[1]["state"] == TaskState.WORKING.value
    assert history[1]["message"] is not None
    assert history[1]["message"]["role"] == "agent"
    assert len(history[1]["message"]["parts"]) == 1
    assert history[1]["message"]["parts"][0]["type"] == "text"
    assert history[1]["message"]["parts"][0]["text"] == new_message_text

    # Verify session calls
    mock_db_service.mock_session.add.assert_called_once_with(mock_a2a_task_initial)
    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.commit.assert_awaited_once() # Base class commit


@pytest.mark.asyncio
async def test_update_a2a_task_status_not_found(
    mock_db_service: DatabaseService,
):
    """Test updating status fails if the task is not found."""
    # --- Arrange ---
    non_existent_id = str(uuid.uuid4())
    # Use DTO TaskStatus for input
    new_status = TaskStatus(state=TaskState.COMPLETED, message=None)
    strategy_input: UpdateStatusInput = (non_existent_id, new_status)

    # Mock finding the task returns None
    mock_execute_result = MagicMock()
    # Make scalar_one_or_none synchronous
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock() # For base class error handling
    # Mock add/flush just in case
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(TaskNotFoundError) as exc_info:
        await strategy.execute(data=strategy_input)

    # Check the exception details
    assert non_existent_id in str(exc_info.value)

    # Verify mock interactions
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()
    mock_db_service.mock_session.add.assert_not_called()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_not_awaited()
    mock_db_service.mock_session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_a2a_task_status_db_error(
    mock_db_service: DatabaseService,
    mock_a2a_task_initial: A2ATask,
):
    """Test DatabaseError is raised on commit/flush failure."""
    # --- Arrange ---
    task_id = mock_a2a_task_initial.id
    # Use DTO TaskStatus for input
    new_status = TaskStatus(state=TaskState.FAILED, message=None)
    strategy_input: UpdateStatusInput = (task_id, new_status)

    # Mock finding the task
    mock_execute_result = MagicMock()
    # Make scalar_one_or_none synchronous
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_a2a_task_initial)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    mock_db_service.mock_session.add = MagicMock()
    # Mock flush to raise an error - strategy awaits flush
    mock_db_service.mock_session.flush = AsyncMock(side_effect=Exception("Simulated DB flush error"))
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=strategy_input)

    assert "Strategy UpdateA2ATaskStatusStrategy failed" in str(exc_info.value)
    # The error now originates from flush, not scalar_one_or_none
    assert "Simulated DB flush error" in str(exc_info.value.__cause__)
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()
    mock_db_service.mock_session.add.assert_called_once() # Add was called
    mock_db_service.mock_session.flush.assert_awaited_once() # Flush was attempted
    mock_db_service.mock_session.commit.assert_not_awaited()
    mock_db_service.mock_session.rollback.assert_awaited_once()

@pytest.mark.asyncio
async def test_update_a2a_task_status_json_error(
    mock_db_service: DatabaseService,
    mock_a2a_task_initial: A2ATask,
):
    """Test error handling when existing history JSON is invalid."""
    # --- Arrange ---
    task_id = mock_a2a_task_initial.id
    mock_a2a_task_initial.history_json = "[invalid json" # Corrupt history
    # Use DTO TaskStatus for input
    new_status = TaskStatus(state=TaskState.COMPLETED, message=None)
    strategy_input: UpdateStatusInput = (task_id, new_status)

    # Mock finding the task
    mock_execute_result = MagicMock()
    # Make scalar_one_or_none synchronous
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_a2a_task_initial)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    # Mock add/flush just in case
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=strategy_input)

    # Check the wrapped exception is json.JSONDecodeError
    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()
    mock_db_service.mock_session.add.assert_not_called() # Fails before add
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_not_awaited()
    mock_db_service.mock_session.rollback.assert_awaited_once() 