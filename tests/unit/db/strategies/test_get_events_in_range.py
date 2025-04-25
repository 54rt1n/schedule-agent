from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock
import uuid

from sa.core.logging_config import get_logger
from sa.db.models import Event, ModifiedInstance, ExcludedDate, Participant
from sa.db.service import DatabaseService
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
from sa.db.dao import TimeRange

logger = get_logger(__name__)

# --- Test Cases --- #

@pytest.mark.asyncio
async def test_get_events_non_recurring_overlap(
    mock_db_service: DatabaseService,
    date_utils: DateUtils
):
    """Test fetching non-recurring events that overlap the query range."""
    # --- Arrange ---
    query_start_aware = datetime(2024, 7, 10, 10, 0, 0, tzinfo=UTC) # 10:00 UTC
    query_end_aware = datetime(2024, 7, 10, 14, 0, 0, tzinfo=UTC)   # 14:00 UTC

    # Define the mock events expected from the non-recurring query
    event1 = MagicMock(spec=Event, id=uuid.uuid4(), title="Event 1", rrule=None, start_time=datetime(2024,7,10,8,0), end_time=datetime(2024,7,10,10,30), participants=[])
    event2 = MagicMock(spec=Event, id=uuid.uuid4(), title="Event 2", rrule=None, start_time=datetime(2024,7,10,11,0), end_time=datetime(2024,7,10,12,0), participants=[])
    event3 = MagicMock(spec=Event, id=uuid.uuid4(), title="Event 3", rrule=None, start_time=datetime(2024,7,10,13,30), end_time=datetime(2024,7,10,15,0), participants=[])
    event4 = MagicMock(spec=Event, id=uuid.uuid4(), title="Event 4", rrule=None, start_time=datetime(2024,7,10,9,0), end_time=datetime(2024,7,10,14,0), participants=[])
    # Events 5 & 6 are outside the range and should not be returned by the mocked query

    mock_non_recurring_results = [event1, event2, event3, event4]
    mock_recurring_results = [] # No recurring events in this test scenario

    # --- Mock session.execute --- #
    # Mock the .scalars().all() chain for non-recurring
    mock_scalars_non_recurring = MagicMock()
    mock_scalars_non_recurring.all = MagicMock(return_value=mock_non_recurring_results)
    mock_result_non_recurring = MagicMock()
    mock_result_non_recurring.scalars = MagicMock(return_value=mock_scalars_non_recurring)

    # Mock the .scalars().unique().all() chain for recurring
    mock_unique_recurring = MagicMock()
    mock_unique_recurring.all = MagicMock(return_value=mock_recurring_results)
    mock_scalars_recurring = MagicMock()
    mock_scalars_recurring.unique = MagicMock(return_value=mock_unique_recurring)
    mock_result_recurring = MagicMock()
    mock_result_recurring.scalars = MagicMock(return_value=mock_scalars_recurring)

    # session.execute will be called twice, return corresponding mock results
    mock_db_service.mock_session.execute = AsyncMock(side_effect=[
        mock_result_non_recurring, # First call for non-recurring
        mock_result_recurring      # Second call for recurring bases
    ])
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = GetEventsInRangeStrategy(db_service=mock_db_service, date_utils=date_utils)
    strategy_input: TimeRange = (query_start_aware, query_end_aware)

    # --- Act ---
    results = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 2
    assert len(results) == 4 # Expect 4 overlapping non-recurring events
    result_titles = sorted([e.title for e in results])
    assert result_titles == ["Event 1", "Event 2", "Event 3", "Event 4"]
    assert any(e.id == event2.id for e in results)
    assert any(e.id == event4.id for e in results)

@pytest.mark.asyncio
async def test_get_events_recurring_basic_expansion(
    mock_db_service: DatabaseService,
    date_utils: DateUtils
):
    """Test fetching instances of a basic recurring event within the range."""
    # --- Arrange ---
    query_start_aware = datetime(2024, 7, 10, 12, 0, 0, tzinfo=UTC)
    query_end_aware = datetime(2024, 7, 11, 15, 0, 0, tzinfo=UTC)

    # Define the mock base recurring event expected from the second query
    base_event_id = uuid.uuid4()
    mock_base_event = MagicMock(
        spec=Event,
        id=base_event_id,
        title="Daily Standup",
        start_time=datetime(2024, 7, 8, 13, 0, 0), # Naive UTC representation of 9:00 ET
        end_time=datetime(2024, 7, 8, 13, 30, 0),   # Naive UTC representation of 9:30 ET
        time_zone="America/New_York",
        rrule="FREQ=DAILY;COUNT=5",
        location=None, notes=None,
        participants=[], excluded_dates=[], modified_instances=[]
    )

    mock_non_recurring_results = [] # No non-recurring events in this test
    mock_recurring_results = [mock_base_event] # The base recurring event

    # --- Mock session.execute (similar to previous test) --- #
    mock_scalars_non_recurring = MagicMock()
    mock_scalars_non_recurring.all = MagicMock(return_value=mock_non_recurring_results)
    mock_result_non_recurring = MagicMock()
    mock_result_non_recurring.scalars = MagicMock(return_value=mock_scalars_non_recurring)

    mock_unique_recurring = MagicMock()
    mock_unique_recurring.all = MagicMock(return_value=mock_recurring_results)
    mock_scalars_recurring = MagicMock()
    mock_scalars_recurring.unique = MagicMock(return_value=mock_unique_recurring)
    mock_result_recurring = MagicMock()
    mock_result_recurring.scalars = MagicMock(return_value=mock_scalars_recurring)

    mock_db_service.mock_session.execute = AsyncMock(side_effect=[
        mock_result_non_recurring,
        mock_result_recurring
    ])
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    # Expected naive UTC instances within range (calculated by date_utils):
    expected_starts_naive_utc = [
        datetime(2024, 7, 10, 13, 0, 0),
        datetime(2024, 7, 11, 13, 0, 0),
    ]

    strategy = GetEventsInRangeStrategy(db_service=mock_db_service, date_utils=date_utils)
    strategy_input: TimeRange = (query_start_aware, query_end_aware)

    # --- Act ---
    results = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 2
    assert len(results) == 2
    result_starts = sorted([e.start_time for e in results]) # Results should be naive UTC
    assert result_starts == expected_starts_naive_utc
    # Check that these are marked as instances and carry base event info
    for instance in results:
        assert instance.title == mock_base_event.title
        assert instance.time_zone == mock_base_event.time_zone
        assert instance.id == base_event_id # ID should be base event's


@pytest.mark.asyncio
async def test_get_events_recurring_with_modification(
    mock_db_service: DatabaseService,
    date_utils: DateUtils
):
    """Test range fetch includes modified instances correctly."""
    # --- Arrange ---
    query_start_aware = datetime(2024, 8, 12, 0, 0, 0, tzinfo=UTC)
    query_end_aware = datetime(2024, 8, 13, 0, 0, 0, tzinfo=UTC)

    # Define the modified instance details
    mod_instance_id = uuid.uuid4()
    original_start_aug12_naive_utc = datetime(2024, 8, 12, 9, 0, 0)
    new_start_aug12_naive_utc = datetime(2024, 8, 12, 10, 30, 0) # New time: 10:30 UTC
    new_end_aug12_naive_utc = datetime(2024, 8, 12, 11, 0, 0)   # New time: 11:00 UTC

    mock_mod_instance = MagicMock(
        spec=ModifiedInstance,
        id=mod_instance_id,
        original_date=original_start_aug12_naive_utc,
        start_time=new_start_aug12_naive_utc,
        end_time=new_end_aug12_naive_utc,
        title="Weekly Meeting (Delayed)",
        time_zone="UTC",
        location=None, notes=None
    )

    # Define the mock base recurring event expected from the second query
    base_event_id = uuid.uuid4()
    mock_base_event = MagicMock(
        spec=Event,
        id=base_event_id,
        title="Weekly Meeting",
        start_time=datetime(2024, 8, 5, 9, 0, 0), # Naive UTC for 10:00 BST
        end_time=datetime(2024, 8, 5, 10, 0, 0),  # Naive UTC for 11:00 BST
        time_zone="Europe/London",
        rrule="FREQ=WEEKLY;COUNT=3",
        location=None, notes=None,
        participants=[], excluded_dates=[],
        # Crucially, include the mock modified instance here
        modified_instances=[mock_mod_instance]
    )
    # Link the modified instance back to the base event ID
    mock_mod_instance.event_id = base_event_id


    mock_non_recurring_results = []
    mock_recurring_results = [mock_base_event]

    # --- Mock session.execute (similar to previous test) --- #
    mock_scalars_non_recurring = MagicMock()
    mock_scalars_non_recurring.all = MagicMock(return_value=mock_non_recurring_results)
    mock_result_non_recurring = MagicMock()
    mock_result_non_recurring.scalars = MagicMock(return_value=mock_scalars_non_recurring)

    mock_unique_recurring = MagicMock()
    mock_unique_recurring.all = MagicMock(return_value=mock_recurring_results)
    mock_scalars_recurring = MagicMock()
    mock_scalars_recurring.unique = MagicMock(return_value=mock_unique_recurring)
    mock_result_recurring = MagicMock()
    mock_result_recurring.scalars = MagicMock(return_value=mock_scalars_recurring)

    mock_db_service.mock_session.execute = AsyncMock(side_effect=[
        mock_result_non_recurring,
        mock_result_recurring
    ])
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    strategy = GetEventsInRangeStrategy(db_service=mock_db_service, date_utils=date_utils)
    strategy_input: TimeRange = (query_start_aware, query_end_aware)

    # --- Act ---
    results = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 2
    assert len(results) == 1 # Only the modified instance falls in the Aug 12 range
    instance = results[0]

    assert instance.id == mod_instance_id # ID should be the ModifiedInstance ID
    assert instance.title == "Weekly Meeting (Delayed)"
    assert instance.start_time == new_start_aug12_naive_utc
    assert instance.end_time == new_end_aug12_naive_utc
    assert instance.time_zone == "UTC"