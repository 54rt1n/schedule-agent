import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock
import uuid

from sa.core.logging_config import get_logger
from sa.db.models.a2a import A2ATask
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.db.service import DatabaseService
from sa.db.dao import GetA2ATaskById
from sa.a2a.dto import TaskState

logger = get_logger(__name__)


@pytest.mark.asyncio
async def test_get_a2a_task_by_id_found(
    mock_db_service: DatabaseService,
):
    """Test retrieving an existing A2A task by ID."""
    # --- Arrange ---
    task_id_to_find = str(uuid.uuid4())
    mock_task = MagicMock(spec=A2ATask)
    mock_task.id = task_id_to_find
    mock_task.state = TaskState.SUBMITTED.value
    mock_task.status_timestamp = None
    mock_task.metadata_json = None

    # Mock the session execute chain
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_task)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = GetA2ATaskByIdStrategy(db_service=mock_db_service)
    strategy_input = GetA2ATaskById(id=task_id_to_find)

    # --- Act ---
    retrieved_task = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert retrieved_task is not None
    assert retrieved_task.id == task_id_to_find
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_a2a_task_by_id_not_found(
    mock_db_service: DatabaseService,
):
    """Test retrieving a non-existent A2A task by ID returns None."""
    # --- Arrange ---
    non_existent_id = str(uuid.uuid4())

    # Mock the session execute chain to return None
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = GetA2ATaskByIdStrategy(db_service=mock_db_service)
    strategy_input = GetA2ATaskById(id=non_existent_id)

    # --- Act ---
    retrieved_task = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert retrieved_task is None
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_execute_result.scalar_one_or_none.assert_called_once()
    mock_db_service.mock_session.rollback.assert_not_awaited()
    mock_db_service.mock_session.commit.assert_awaited_once() 