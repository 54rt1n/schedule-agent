import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock, AsyncMock # Added mocking imports
import uuid

from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import Event
from schedule_core.db.strategies.delete_event import DeleteEventStrategy
from schedule_core.db.service import DatabaseService # Needed for type hint
from schedule_core.core.exceptions import DatabaseError
from schedule_core.db.dao import EventDelete # Import DAO for input type clarity

logger = get_logger(__name__)


@pytest.mark.asyncio
async def test_delete_event_success(
    mock_db_service: DatabaseService,
    sample_event_data: dict
):
    """Test successfully deleting an existing event using the strategy."""
    # --- Arrange ---
    event_id_to_delete = sample_event_data["id"]
    mock_event_to_delete = MagicMock(spec=Event)
    # Add attributes needed for logging or other operations if any
    mock_event_to_delete.id = event_id_to_delete
    mock_event_to_delete.title = sample_event_data.get("title", "Sample")

    # Mock the result of session.execute for the delete operation
    mock_execute_result = MagicMock()
    mock_execute_result.rowcount = 1 # Simulate one row affected (deleted)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Mock session.get is no longer needed with execute mock, but keep delete for base class
    # mock_db_service.mock_session.get = AsyncMock(return_value=mock_event_to_delete)
    mock_db_service.mock_session.delete = AsyncMock() # Still needed if called by _run (it's not in this case)
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = DeleteEventStrategy(db_service=mock_db_service)
    strategy_input = EventDelete(id=event_id_to_delete) # Use DAO

    # --- Act ---
    deleted = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert deleted is True
    mock_db_service.mock_session.execute.assert_awaited_once() # Check execute was called
    # Assertions for get/delete are removed as they are not directly called in the strategy anymore
    # mock_db_service.mock_session.get.assert_awaited_once_with(Event, event_id_to_delete)
    # mock_db_service.mock_session.delete.assert_awaited_once_with(mock_event_to_delete)
    # Base execute should call commit
    mock_db_service.mock_session.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_delete_event_not_found(
    mock_db_service: DatabaseService,
):
    """Test attempting to delete a non-existent event returns False."""
    # --- Arrange ---
    non_existent_id = uuid.uuid4()

    # Mock the result of session.execute for the delete operation
    mock_execute_result = MagicMock()
    mock_execute_result.rowcount = 0 # Simulate zero rows affected
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Mock session.get is no longer needed
    # mock_db_service.mock_session.get = AsyncMock(return_value=None)
    mock_db_service.mock_session.delete = AsyncMock() # Should not be called
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = DeleteEventStrategy(db_service=mock_db_service)
    strategy_input = EventDelete(id=non_existent_id) # Use DAO

    # --- Act ---
    deleted = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert deleted is False
    mock_db_service.mock_session.execute.assert_awaited_once() # Check execute was called
    # Assertions for get/delete are removed
    # mock_db_service.mock_session.get.assert_awaited_once_with(Event, non_existent_id)
    mock_db_service.mock_session.delete.assert_not_awaited()
    # Base execute should call commit even if _run returns False
    mock_db_service.mock_session.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_delete_event_db_error(
    mock_db_service: DatabaseService,
    sample_event_data: dict
):
    """Test that the strategy raises DatabaseError on commit failure."""
    # --- Arrange ---
    event_id_to_delete = sample_event_data["id"]
    mock_event_to_delete = MagicMock(spec=Event)
    mock_event_to_delete.id = event_id_to_delete
    mock_event_to_delete.title = sample_event_data.get("title", "Sample Error")

    # Mock the result of session.execute - commit fails *after* execute
    mock_execute_result = MagicMock()
    mock_execute_result.rowcount = 1 # Assume execute itself succeeded
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    # Mock session.get/delete are not needed
    # mock_db_service.mock_session.get = AsyncMock(return_value=mock_event_to_delete)
    # mock_db_service.mock_session.delete = AsyncMock()

    # Simulate a commit error
    mock_db_service.mock_session.commit = AsyncMock(side_effect=Exception("Simulated DB commit error"))
    mock_db_service.mock_session.rollback = AsyncMock() # Ensure rollback mock exists

    strategy = DeleteEventStrategy(db_service=mock_db_service)
    strategy_input = EventDelete(id=event_id_to_delete) # Use DAO

    with pytest.raises(DatabaseError) as exc_info:
        # --- Act ---
        await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert "Strategy DeleteEventStrategy failed" in str(exc_info.value)
    assert "Simulated DB commit error" in str(exc_info.value.__cause__) # Check the cause
    mock_db_service.mock_session.execute.assert_awaited_once() # Execute was called
    # Assertions for get/delete removed
    # mock_db_service.mock_session.get.assert_awaited_once_with(Event, event_id_to_delete)
    # mock_db_service.mock_session.delete.assert_awaited_once_with(mock_event_to_delete)
    mock_db_service.mock_session.commit.assert_awaited_once() # Commit was attempted
    mock_db_service.mock_session.rollback.assert_awaited_once() # Rollback should be called by base class _failure 