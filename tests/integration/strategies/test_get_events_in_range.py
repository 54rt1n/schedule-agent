import pytest
from datetime import UTC, datetime, timedelta

from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_core.db.strategies.create_event import CreateEventStrategy # Needed for setup
from schedule_core.event.date_utils import DateUtils # Dependency of GetEventsInRangeStrategy
from schedule_core.db.dao import TimeRange, EventCreate
from schedule_core.db.models import Event


@pytest.mark.asyncio
async def test_get_events_in_range_simple(
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
    date_utils: DateUtils,
):
    """Test retrieving a simple non-recurring event within a time range."""
    # --- Arrange ---
    # Create an event that falls within our query range
    event_start = datetime(2025, 6, 15, 14, 0, 0) # 2 PM UTC
    event_end = datetime(2025, 6, 15, 15, 0, 0)   # 3 PM UTC
    event_to_create = EventCreate(
        title="Event Within Range Test",
        start_time=event_start,
        end_time=event_end,
        time_zone="UTC",
    )
    created_event = await create_event_strategy.execute(data=event_to_create)
    assert created_event is not None
    created_event_id = created_event.id

    # Define query range (aware datetimes)
    query_start_aware = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC) # 12 PM UTC
    query_end_aware = datetime(2025, 6, 15, 16, 0, 0, tzinfo=UTC)   # 4 PM UTC

    strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    # strategy_input: TimeRange = (query_start_aware, query_end_aware) # Old tuple
    # Instantiate the TimeRange DAO
    strategy_input = TimeRange(start_time=query_start_aware, end_time=query_end_aware)

    # --- Act ---
    results = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert isinstance(results, list)
    assert len(results) >= 1 # Should find at least the event we created

    # Find our specific event in the results
    found_event = None
    for event in results:
        if event.id == created_event_id:
            found_event = event
            break

    assert found_event is not None
    assert found_event.title == "Event Within Range Test"
    # Note: The strategy returns naive UTC datetimes based on current implementation
    assert found_event.start_time == event_start
    assert found_event.end_time == event_end

# TODO: Add tests for:
# - Recurring events with ExcludedDate
# - Events exactly matching boundary times
# - Overlap checks with mixed non-recurring and recurring/modified instances
# - Timezone handling complexity
# - Performance with many recurring events?