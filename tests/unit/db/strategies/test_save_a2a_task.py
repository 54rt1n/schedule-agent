import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock, ANY
import uuid
import json
from datetime import datetime, UTC, timedelta

from sa.core.logging_config import get_logger
from sa.db.models.a2a import A2ATask
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.service import DatabaseService
from sa.core.exceptions import DatabaseError
from sa.db.dao import A2ATaskCreate
from sa.a2a.dto import TaskState

logger = get_logger(__name__)


@pytest.fixture
def mock_a2a_task_create_dao() -> A2ATaskCreate:
    """Provides sample data as an A2ATaskCreate DAO."""
    task_id = str(uuid.uuid4())
    initial_state = TaskState.SUBMITTED
    # Use json.dumps for reliable JSON string creation
    initial_history_json = json.dumps([{"role": "user", "parts": [{"type": "text", "text": "Book meeting"}]}])
    metadata_json = json.dumps({"client": "test"})

    return A2ATaskCreate(
        id=task_id,
        session_id=str(uuid.uuid4()),
        state=initial_state.value,
        status_message_json=None,
        status_timestamp=datetime.now(UTC),
        artifacts_json=None,
        history_json=initial_history_json,
        metadata_json=metadata_json
    )


@pytest.mark.asyncio
async def test_save_a2a_task_create(
    mock_db_service: DatabaseService,
    mock_a2a_task_create_dao: A2ATaskCreate,
):
    """Test saving a new A2A task using the DAO."""
    # --- Arrange ---
    dao_input = mock_a2a_task_create_dao

    # This is the object we expect the strategy to create and merge
    # Note: created_at/updated_at are set by DB/refresh mock
    expected_merged_object = A2ATask(
        id=dao_input.id,
        session_id=dao_input.session_id,
        state=dao_input.state,
        status_message_json=dao_input.status_message_json,
        status_timestamp=dao_input.status_timestamp,
        artifacts_json=dao_input.artifacts_json,
        history_json=dao_input.history_json,
        metadata_json=dao_input.metadata_json,
    )

    mock_db_service.mock_session.merge = AsyncMock(return_value=expected_merged_object)

    async def refresh_side_effect(obj):
        obj.updated_at = datetime.now(UTC)
        obj.created_at = datetime.now(UTC) - timedelta(seconds=1)
        return obj
    mock_db_service.mock_session.refresh = AsyncMock(side_effect=refresh_side_effect)
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act ---
    saved_task = await strategy.execute(data=dao_input)

    # --- Assert ---
    assert saved_task is not None
    assert saved_task.id == dao_input.id
    assert saved_task.state == dao_input.state
    assert saved_task.status_timestamp == dao_input.status_timestamp
    assert saved_task.history_json == dao_input.history_json
    assert saved_task.metadata_json == dao_input.metadata_json
    assert saved_task.artifacts_json == dao_input.artifacts_json

    mock_db_service.mock_session.merge.assert_awaited_once_with(ANY)
    merged_arg = mock_db_service.mock_session.merge.await_args[0][0]
    assert isinstance(merged_arg, A2ATask)
    assert merged_arg.id == dao_input.id
    assert merged_arg.state == dao_input.state
    assert merged_arg.history_json == dao_input.history_json
    assert merged_arg.metadata_json == dao_input.metadata_json
    assert merged_arg.status_timestamp == dao_input.status_timestamp

    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.refresh.assert_awaited_once_with(expected_merged_object)
    mock_db_service.mock_session.commit.assert_awaited_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_a2a_task_update(
    mock_db_service: DatabaseService,
    mock_a2a_task_create_dao: A2ATaskCreate,
):
    """Test saving an existing A2A task (simulates update via merge)."""
    # --- Arrange ---
    dao_input = mock_a2a_task_create_dao
    dao_input.state = TaskState.WORKING.value
    dao_input.artifacts_json = json.dumps([{"name": "result"}])
    dao_input.status_timestamp = datetime.now(UTC)

    expected_merged_object = A2ATask(
        id=dao_input.id,
        session_id=dao_input.session_id,
        state=dao_input.state,
        status_message_json=dao_input.status_message_json,
        status_timestamp=dao_input.status_timestamp,
        artifacts_json=dao_input.artifacts_json,
        history_json=dao_input.history_json,
        metadata_json=dao_input.metadata_json,
    )

    mock_db_service.mock_session.merge = AsyncMock(return_value=expected_merged_object)
    async def refresh_side_effect(obj):
        obj.updated_at = datetime.now(UTC)
        obj.created_at = datetime.now(UTC) - timedelta(seconds=10)
        return obj
    mock_db_service.mock_session.refresh = AsyncMock(side_effect=refresh_side_effect)
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act ---
    saved_task = await strategy.execute(data=dao_input)

    # --- Assert ---
    assert saved_task is not None
    assert saved_task.id == dao_input.id
    assert saved_task.state == TaskState.WORKING.value
    assert saved_task.artifacts_json is not None

    mock_db_service.mock_session.merge.assert_awaited_once_with(ANY)
    merged_arg = mock_db_service.mock_session.merge.await_args[0][0]
    assert isinstance(merged_arg, A2ATask)
    assert merged_arg.id == dao_input.id
    assert merged_arg.state == dao_input.state
    assert merged_arg.artifacts_json == dao_input.artifacts_json

    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.refresh.assert_awaited_once_with(expected_merged_object)
    mock_db_service.mock_session.commit.assert_awaited_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_a2a_task_none_input(
    mock_db_service: DatabaseService,
):
    """Test passing None as data to the strategy."""
    # --- Arrange ---
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    mock_db_service.mock_session.merge = AsyncMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act ---
    result = await strategy.execute(data=None)

    # --- Assert ---
    assert result is None
    mock_db_service.mock_session.merge.assert_not_awaited()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_awaited_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()

@pytest.mark.asyncio
async def test_save_a2a_task_db_error(
    mock_db_service: DatabaseService,
    mock_a2a_task_create_dao: A2ATaskCreate,
):
    """Test that DatabaseError is raised on merge/flush failure."""
    # --- Arrange ---
    dao_input = mock_a2a_task_create_dao

    # Correctly mock the side_effect with the base Exception class
    mock_db_service.mock_session.merge = AsyncMock(side_effect=Exception("Simulated DB merge error"))
    mock_db_service.mock_session.refresh = AsyncMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=dao_input)

    assert "Strategy SaveA2ATaskStrategy failed" in str(exc_info.value)
    assert "Simulated DB merge error" in str(exc_info.value.__cause__)
    mock_db_service.mock_session.merge.assert_awaited_once()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_not_awaited()
    mock_db_service.mock_session.rollback.assert_awaited_once()