import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock
import uuid
import json
from datetime import datetime, UTC, timedelta

from sa.core.logging_config import get_logger
from sa.db.models.a2a import A2ATask
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.service import DatabaseService
from sa.core.exceptions import DatabaseError

logger = get_logger(__name__)


@pytest.fixture
def mock_a2a_task_data() -> dict:
    """Provides sample data for an A2ATask, matching model fields."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    task_id = str(uuid.uuid4())
    return {
        "id": task_id,
        "session_id": str(uuid.uuid4()),
        "state": "PENDING",
        "status_message_json": None,
        "params_json": json.dumps({"param1": "value1"}),
        "artifacts_json": None,
        "history_json": json.dumps([{"state": "PENDING", "timestamp": now_naive.isoformat()}]),
    }


@pytest.mark.asyncio
async def test_save_a2a_task_create(
    mock_db_service: DatabaseService,
    mock_a2a_task_data: dict,
):
    """Test saving a new A2A task."""
    # --- Arrange ---
    task_to_save = A2ATask(**mock_a2a_task_data)

    # Mock session.merge to return the object synchronously
    # The strategy awaits merge, so mock it as an AsyncMock returning the object
    mock_db_service.mock_session.merge = AsyncMock(return_value=task_to_save)

    # Mock refresh - strategy awaits this too
    async def refresh_side_effect(obj):
        obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
        return obj
    mock_db_service.mock_session.refresh = AsyncMock(side_effect=refresh_side_effect)

    # Mock flush - strategy awaits this
    mock_db_service.mock_session.flush = AsyncMock()
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act ---
    saved_task = await strategy.execute(data=task_to_save)

    # --- Assert ---
    assert saved_task is not None
    assert saved_task.id == mock_a2a_task_data["id"]
    assert saved_task.state == "PENDING"
    assert saved_task.params_json == mock_a2a_task_data["params_json"]
    mock_db_service.mock_session.merge.assert_awaited_once_with(task_to_save)
    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.refresh.assert_awaited_once_with(task_to_save)
    mock_db_service.mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_a2a_task_update(
    mock_db_service: DatabaseService,
    mock_a2a_task_data: dict,
):
    """Test saving an existing A2A task (simulates update)."""
    # --- Arrange ---
    task_to_save = A2ATask(**mock_a2a_task_data)
    task_to_save.state = "PROCESSING"
    task_to_save.artifacts_json = json.dumps({"result": "done"})

    # Mock session.merge, refresh, flush as in create, using mock_session
    # Strategy awaits merge
    mock_db_service.mock_session.merge = AsyncMock(return_value=task_to_save)
    # Strategy awaits refresh
    async def refresh_side_effect(obj):
        obj.updated_at = datetime.now(UTC).replace(tzinfo=None)
        return obj
    mock_db_service.mock_session.refresh = AsyncMock(side_effect=refresh_side_effect)
    # Strategy awaits flush
    mock_db_service.mock_session.flush = AsyncMock()
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act ---
    saved_task = await strategy.execute(data=task_to_save)

    # --- Assert ---
    assert saved_task is not None
    assert saved_task.id == mock_a2a_task_data["id"]
    assert saved_task.state == "PROCESSING"
    assert saved_task.artifacts_json is not None
    mock_db_service.mock_session.merge.assert_awaited_once_with(task_to_save)
    mock_db_service.mock_session.flush.assert_awaited_once()
    mock_db_service.mock_session.refresh.assert_awaited_once_with(task_to_save)
    mock_db_service.mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_a2a_task_none_input(
    mock_db_service: DatabaseService,
):
    """Test passing None as data to the strategy."""
    # --- Arrange ---
    # Ensure commit/rollback are mocked for the base execute path
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    # Mock merge/flush/refresh just in case, though they shouldn't be called
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
    # Commit should still be called by the base class if no exception occurred
    mock_db_service.mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_a2a_task_db_error(
    mock_db_service: DatabaseService,
    mock_a2a_task_data: dict,
):
    """Test that DatabaseError is raised on flush/merge failure."""
    # --- Arrange ---
    task_to_save = A2ATask(**mock_a2a_task_data)

    # Mock merge to raise an error - strategy awaits merge
    mock_db_service.mock_session.merge = AsyncMock(side_effect=Exception("Simulated DB merge error"))
    mock_db_service.mock_session.refresh = AsyncMock()
    mock_db_service.mock_session.flush = AsyncMock()
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = SaveA2ATaskStrategy(db_service=mock_db_service)

    # --- Act & Assert ---
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=task_to_save)

    assert "Strategy SaveA2ATaskStrategy failed" in str(exc_info.value)
    assert "Simulated DB merge error" in str(exc_info.value.__cause__)
    mock_db_service.mock_session.merge.assert_awaited_once()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_not_awaited()
    mock_db_service.mock_session.rollback.assert_awaited_once() 