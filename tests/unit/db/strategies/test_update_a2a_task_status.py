import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock
import uuid
import json
from datetime import UTC, datetime

from sa.core.logging_config import get_logger
from sa.db.models.a2a import A2ATask
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy
from sa.db.dao import UpdateA2ATaskStatus
from sa.db.service import DatabaseService
from sa.core.exceptions import DatabaseError, TaskNotFoundError as CoreTaskNotFoundError
from sa.a2a.dto import TaskState

logger = get_logger(__name__)


@pytest.fixture
def mock_a2a_task_initial() -> A2ATask:
    """Provides a base A2ATask object matching the DB model (with new fields)."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    task_id = str(uuid.uuid4())
    initial_state = TaskState.SUBMITTED
    initial_history_json = json.dumps([{"role": "user", "parts": [{"type": "text", "text": "Initial request"}]}])

    return A2ATask(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=initial_state.value,
        status_message_json=None,
        status_timestamp=now_naive, # Example timestamp
        artifacts_json=None,
        history_json=initial_history_json,
        metadata_json=None,
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
    initial_history_list = json.loads(mock_a2a_task_initial.history_json)

    # Prepare data for the DAO
    new_state = TaskState.WORKING
    new_status_ts = datetime.now(UTC)
    new_message_json = json.dumps({"role": "agent", "parts": [{"type": "text", "text": "Working on it"}]})
    # Simulate manager providing updated history JSON
    new_history_list = initial_history_list + [json.loads(new_message_json)]
    new_history_json = json.dumps(new_history_list)

    strategy_input = UpdateA2ATaskStatus(
        id=task_id,
        new_state=new_state.value,
        new_status_message_json=new_message_json,
        new_status_timestamp=new_status_ts,
        new_history_json=new_history_json # Provide updated history
    )

    # Mock finding the task
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_a2a_task_initial)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act ---
    await strategy.execute(data=strategy_input)

    # --- Assert ---
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()

    # Verify the DB model object was modified correctly before add/flush
    assert mock_a2a_task_initial.state == new_state.value
    assert mock_a2a_task_initial.status_message_json == new_message_json
    assert mock_a2a_task_initial.status_timestamp == new_status_ts
    assert mock_a2a_task_initial.history_json == new_history_json
    # updated_at is handled by DB onupdate, not asserted here

    # Verify session calls
    mock_db_service.mock_session.add.assert_called_once_with(mock_a2a_task_initial)
    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.commit.assert_awaited_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_update_a2a_task_status_no_history_update(
    mock_db_service: DatabaseService,
    mock_a2a_task_initial: A2ATask,
):
    """Test updating status without providing new history JSON."""
    # --- Arrange ---
    task_id = mock_a2a_task_initial.id
    original_history_json = mock_a2a_task_initial.history_json

    new_state = TaskState.WORKING
    new_status_ts = datetime.now(UTC)
    # Example: A status update message not meant for history
    new_message_json = json.dumps({"role": "agent", "parts": [{"type": "text", "text": "Internal check complete"}]})

    strategy_input = UpdateA2ATaskStatus(
        id=task_id,
        new_state=new_state.value,
        new_status_message_json=new_message_json,
        new_status_timestamp=new_status_ts,
        new_history_json=None # Explicitly None
    )

    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_a2a_task_initial)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act ---
    await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_a2a_task_initial.state == new_state.value
    assert mock_a2a_task_initial.status_message_json == new_message_json
    assert mock_a2a_task_initial.status_timestamp == new_status_ts
    # Crucially, history should NOT have changed
    assert mock_a2a_task_initial.history_json == original_history_json

    mock_db_service.mock_session.add.assert_called_once_with(mock_a2a_task_initial)
    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.commit.assert_awaited_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_update_a2a_task_status_not_found(
    mock_db_service: DatabaseService,
):
    """Test updating status fails if the task is not found."""
    # --- Arrange ---
    non_existent_id = str(uuid.uuid4())
    strategy_input = UpdateA2ATaskStatus(
        id=non_existent_id,
        new_state=TaskState.COMPLETED.value,
        new_status_message_json=None,
        new_status_timestamp=datetime.now(UTC),
        new_history_json=None
    )

    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(CoreTaskNotFoundError) as exc_info:
        await strategy.execute(data=strategy_input)

    assert non_existent_id in str(exc_info.value)

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
    """Test DatabaseError is raised on flush failure."""
    # --- Arrange ---
    task_id = mock_a2a_task_initial.id
    strategy_input = UpdateA2ATaskStatus(
        id=task_id,
        new_state=TaskState.FAILED.value,
        new_status_message_json=None,
        new_status_timestamp=datetime.now(UTC),
        new_history_json=None
    )

    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_a2a_task_initial)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock(side_effect=Exception("Simulated DB flush error"))
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = UpdateA2ATaskStatusStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=strategy_input)

    assert "Strategy UpdateA2ATaskStatusStrategy failed" in str(exc_info.value)
    assert "Simulated DB flush error" in str(exc_info.value.__cause__)
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()
    mock_db_service.mock_session.add.assert_called_once() # Add was called
    mock_db_service.mock_session.flush.assert_awaited_once() # Flush was attempted
    mock_db_service.mock_session.commit.assert_not_awaited()
    mock_db_service.mock_session.rollback.assert_awaited_once() 