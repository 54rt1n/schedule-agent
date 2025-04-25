import pytest
from sqlalchemy.ext.asyncio import AsyncSession
import uuid
from unittest.mock import patch, MagicMock, AsyncMock

from sa.core.logging_config import get_logger
from sa.db.models import Event
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy
from sa.db.service import DatabaseService

logger = get_logger(__name__)


@pytest.mark.asyncio
async def test_get_event_by_id_found(
    mock_db_service: DatabaseService,
    sample_event_data: dict
):
    """Test retrieving an existing event by ID using the strategy."""
    # --- Arrange ---
    # Create the expected event object directly (no DB interaction needed)
    event_id_to_find = sample_event_data["id"]
    expected_event = Event(**sample_event_data)
    # Ensure relationships expected by eager loading exist as empty lists on the mock
    expected_event.participants = []
    expected_event.excluded_dates = []
    expected_event.modified_instances = []

    # Mock the return value of session.execute(...).scalar_one_or_none()
    # Create a mock for the intermediate result object
    mock_execute_result = MagicMock()
    # Make scalar_one_or_none synchronous
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=expected_event)

    # Configure execute on the mock session
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    # Instantiate the strategy
    strategy = GetEventByIdStrategy(db_service=mock_db_service)

    # --- Act ---
    retrieved_event = await strategy.execute(data=event_id_to_find)

    # --- Assert ---
    assert retrieved_event is not None
    assert retrieved_event.id == event_id_to_find
    assert retrieved_event.title == sample_event_data["title"]
    # Check if relationships were loaded (they should be the empty lists we added)
    assert retrieved_event.participants == []
    assert retrieved_event.excluded_dates == []
    assert retrieved_event.modified_instances == []

    # Verify that session.execute was called once by the strategy
    mock_db_service.mock_session.execute.assert_awaited_once()

@pytest.mark.asyncio
async def test_get_event_by_id_not_found(
    mock_db_service: DatabaseService,
):
    """Test retrieving a non-existent event by ID using the strategy returns None."""
    # --- Arrange ---
    non_existent_id = uuid.uuid4()

    # Mock the return value of session.execute(...).scalar_one_or_none()
    mock_execute_result = MagicMock()
    # Make scalar_one_or_none synchronous
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
    # Configure execute on the mock session
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    # Instantiate the strategy
    strategy = GetEventByIdStrategy(db_service=mock_db_service)

    # --- Act ---
    retrieved_event = await strategy.execute(data=non_existent_id)

    # --- Assert ---
    assert retrieved_event is None
    mock_db_service.mock_session.execute.assert_awaited_once() 