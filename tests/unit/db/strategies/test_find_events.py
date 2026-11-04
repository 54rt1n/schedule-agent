import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock
import uuid
from datetime import datetime, UTC

from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import Event # Import model
from schedule_core.db.strategies.find_events import FindEventsStrategy
from schedule_core.db.service import DatabaseService # For type hint
from schedule_core.db.dao import EventQuery, EventResponse # Import DAO

logger = get_logger(__name__)

@pytest.mark.asyncio
async def test_find_events_no_criteria(
    mock_db_service: DatabaseService,
):
    """Test finding events with no specific criteria (should return all mocked)."""
    # --- Arrange ---
    query_criteria = EventQuery() # Empty query

    # Define mock events to be returned
    mock_event1 = MagicMock(spec=Event, id=uuid.uuid4(), title="Event A", participants=[], excluded_dates=[], modified_instances=[])
    mock_event2 = MagicMock(spec=Event, id=uuid.uuid4(), title="Event B", participants=[], excluded_dates=[], modified_instances=[])
    mock_results_list = [mock_event1, mock_event2]

    # --- Mock session.execute chain --- #
    mock_unique_scalars = MagicMock()
    mock_unique_scalars.all = MagicMock(return_value=mock_results_list)
    mock_scalars = MagicMock()
    mock_scalars.unique = MagicMock(return_value=mock_unique_scalars)
    mock_execute_result = MagicMock()
    mock_execute_result.scalars = MagicMock(return_value=mock_scalars)

    # Configure execute on the mock session
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = FindEventsStrategy(db_service=mock_db_service)

    # --- Act ---
    results = await strategy.execute(data=query_criteria)

    # --- Assert ---
    mock_db_service.mock_session.execute.assert_awaited_once()
    assert len(results) == 2
    assert results == mock_results_list

@pytest.mark.asyncio
async def test_find_events_with_title_criteria(
    mock_db_service: DatabaseService,
):
    """Test finding events filtering by title."""
    # --- Arrange ---
    query_criteria = EventQuery(title_contains="Specific")

    # Define mock events - only one matches
    mock_event1 = MagicMock(spec=Event, id=uuid.uuid4(), title="Specific Event", participants=[], excluded_dates=[], modified_instances=[])
    # Assume the query filtering happens *before* the mock return in a real scenario,
    # so the mock should only return the expected matched events.
    mock_results_list = [mock_event1]

    # --- Mock session.execute chain --- #
    mock_unique_scalars = MagicMock()
    mock_unique_scalars.all = MagicMock(return_value=mock_results_list)
    mock_scalars = MagicMock()
    mock_scalars.unique = MagicMock(return_value=mock_unique_scalars)
    mock_execute_result = MagicMock()
    mock_execute_result.scalars = MagicMock(return_value=mock_scalars)

    # Configure execute on the mock session
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = FindEventsStrategy(db_service=mock_db_service)

    # --- Act ---
    results = await strategy.execute(data=query_criteria)

    # --- Assert ---
    mock_db_service.mock_session.execute.assert_awaited_once()
    # We can inspect the executed statement if needed:
    # executed_stmt = test_session.execute.await_args[0][0]
    # assert 'lower(event.title) like lower(:title_1)' in str(executed_stmt.compile(compile_kwargs={"literal_binds": True}))
    assert len(results) == 1
    assert results[0].title == "Specific Event"

@pytest.mark.asyncio
async def test_find_events_with_time_criteria(
    mock_db_service: DatabaseService,
):
    """Test finding events filtering by time range."""
    # --- Arrange ---
    start_time = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
    end_time = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
    query_criteria = EventQuery(start_time=start_time, end_time=end_time)

    # Define mock events - only one matches the overlap
    mock_event1 = MagicMock(spec=Event, id=uuid.uuid4(), title="Overlapping Event",
                           start_time=datetime(2024, 1, 1, 9, 0, 0), # Naive UTC in DB
                           end_time=datetime(2024, 1, 1, 11, 0, 0), # Naive UTC in DB
                           participants=[], excluded_dates=[], modified_instances=[])
    mock_results_list = [mock_event1]

    # --- Mock session.execute chain --- #
    mock_unique_scalars = MagicMock()
    mock_unique_scalars.all = MagicMock(return_value=mock_results_list)
    mock_scalars = MagicMock()
    mock_scalars.unique = MagicMock(return_value=mock_unique_scalars)
    mock_execute_result = MagicMock()
    mock_execute_result.scalars = MagicMock(return_value=mock_scalars)

    # Configure execute on the mock session
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = FindEventsStrategy(db_service=mock_db_service)

    # --- Act ---
    results = await strategy.execute(data=query_criteria)

    # --- Assert ---
    mock_db_service.mock_session.execute.assert_awaited_once()
    # We could inspect the statement's where clause here too if needed
    assert len(results) == 1
    assert results[0].title == "Overlapping Event" 