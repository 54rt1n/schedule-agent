from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate, TimeRange
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver

logger = get_logger(__name__)

@pytest.mark.asyncio
async def test_get_events_in_range_mix_simple_recurring_inside(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test retrieving a mix of simple and recurring instances within range."""
    # --- Arrange ---
    query_start = datetime(2025, 1, 15, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2025, 1, 22, 0, 0, 0, tzinfo=UTC)

    # Simple event fully inside
    simple_title = "Simple Event Inside"
    simple_start = datetime(2025, 1, 16, 10, 0, 0, tzinfo=UTC)
    simple_end = datetime(2025, 1, 16, 11, 0, 0, tzinfo=UTC)
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy
    )
    await create_event_strategy.execute(data=EventCreate(
        title=simple_title,
        start_time=simple_start.replace(tzinfo=None),
        end_time=simple_end.replace(tzinfo=None),
        time_zone="UTC",
    ))

    # Recurring event with one instance inside
    recur_title = "Recurring Event Instance Inside"
    recur_base_start = datetime(2025, 1, 18, 14, 0, 0, tzinfo=UTC)
    recur_base_end = datetime(2025, 1, 18, 15, 0, 0, tzinfo=UTC)
    rrule = "FREQ=DAILY;COUNT=3" # Jan 18, 19, 20
    await create_event_strategy.execute(data=EventCreate(
        title=recur_title,
        start_time=recur_base_start.replace(tzinfo=None),
        end_time=recur_base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    ))

    # --- Act ---
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expect 1 simple event + 3 recurring instances = 4 total
    assert len(retrieved_events) == 4
    retrieved_titles = sorted([e.title for e in retrieved_events])
    expected_titles = sorted([
        simple_title,
        recur_title, # Instance 1
        recur_title, # Instance 2
        recur_title, # Instance 3
    ])
    assert retrieved_titles == expected_titles

    # Verify start times
    retrieved_starts = sorted([e.start_time for e in retrieved_events])
    expected_starts = sorted([
        datetime(2025, 1, 16, 10, 0, 0), # Simple
        datetime(2025, 1, 18, 14, 0, 0), # Recur 1
        datetime(2025, 1, 19, 14, 0, 0), # Recur 2
        datetime(2025, 1, 20, 14, 0, 0), # Recur 3
    ])
    assert retrieved_starts == expected_starts


# Test for Case 23: Mix - Simple Outside, Recurring Inside
@pytest.mark.asyncio
async def test_get_events_in_range_mix_simple_outside_recurring_inside(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test retrieving only recurring instance when simple event is outside range."""
    # --- Arrange ---
    query_start = datetime(2025, 2, 10, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2025, 2, 15, 0, 0, 0, tzinfo=UTC)

    # Simple event fully outside (before)
    simple_title = "Simple Event Outside"
    simple_start = datetime(2025, 2, 1, 10, 0, 0, tzinfo=UTC)
    simple_end = datetime(2025, 2, 1, 11, 0, 0, tzinfo=UTC)
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy
    )
    await create_event_strategy.execute(data=EventCreate(
        title=simple_title,
        start_time=simple_start.replace(tzinfo=None),
        end_time=simple_end.replace(tzinfo=None),
        time_zone="UTC",
    ))

    # Recurring event with one instance inside
    recur_title = "Recurring Event Inside Range"
    recur_base_start = datetime(2025, 2, 11, 14, 0, 0, tzinfo=UTC)
    recur_base_end = datetime(2025, 2, 11, 15, 0, 0, tzinfo=UTC)
    rrule = "FREQ=DAILY;COUNT=3" # Feb 11, 12, 13
    await create_event_strategy.execute(data=EventCreate(
        title=recur_title,
        start_time=recur_base_start.replace(tzinfo=None),
        end_time=recur_base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    ))

    # --- Act ---
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expect only the 3 recurring instances
    assert len(retrieved_events) == 3
    retrieved_titles = sorted([e.title for e in retrieved_events])
    assert retrieved_titles == [recur_title] * 3

    # Verify start times
    retrieved_starts = sorted([e.start_time for e in retrieved_events])
    expected_starts = sorted([
        datetime(2025, 2, 11, 14, 0, 0),
        datetime(2025, 2, 12, 14, 0, 0),
        datetime(2025, 2, 13, 14, 0, 0),
    ])
    assert retrieved_starts == expected_starts


# Test for Case 24: Timezone - Event created in non-UTC TZ
@pytest.mark.asyncio
async def test_get_events_in_range_timezone_non_utc(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test retrieving an event created in a non-UTC timezone using a UTC query range."""
    # --- Arrange ---
    event_title = "New York Event"
    event_tz = "America/New_York"
    # Event time: 10:00 AM NY Time (which is 15:00 UTC during standard time)
    event_local_start = datetime(2025, 2, 5, 10, 0, 0) # Naive local time
    event_local_end = datetime(2025, 2, 5, 11, 0, 0)   # Naive local time

    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy
    )
    await create_event_strategy.execute(data=EventCreate(
        title=event_title,
        start_time=event_local_start, # Pass naive local time
        end_time=event_local_end,
        time_zone=event_tz,
    ))

    # Query range in UTC that overlaps with the event (15:00-16:00 UTC)
    query_start_utc = datetime(2025, 2, 5, 14, 30, 0, tzinfo=UTC)
    query_end_utc = datetime(2025, 2, 5, 15, 30, 0, tzinfo=UTC)

    # --- Act ---
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=query_start_utc, end_time=query_end_utc)
    retrieved_events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expect 1 event
    assert len(retrieved_events) == 1
    assert retrieved_events[0].title == event_title
    # Check that the returned start/end times are naive UTC representations
    # 10:00 America/New_York on 2025-02-05 is 15:00 UTC
    expected_start_naive_utc = datetime(2025, 2, 5, 15, 0, 0)
    expected_end_naive_utc = datetime(2025, 2, 5, 16, 0, 0)
    assert retrieved_events[0].start_time == expected_start_naive_utc
    assert retrieved_events[0].end_time == expected_end_naive_utc
    # The returned event instance should ideally keep the original timezone info
    # Note: Current implementation returns UTC for instances, let's assert that for now.
    # assert retrieved_events[0].time_zone == event_tz # This would be ideal
    assert retrieved_events[0].time_zone == event_tz # Update based on impl: it should preserve TZ

# Note: Case 25 (DST Recurring) should be in test_recurring.py

# Test for Case 58: DST Transition Query (Recurring)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tz_name", "event_local_start", "rrule_str", "query_range_start_utc", "query_range_end_utc", "expected_naive_utc_starts"),
    [
        # US/New York: Spring Forward
        (
            "America/New_York", datetime(2024, 3, 8, 9, 0, 0), "FREQ=DAILY;COUNT=5",
            datetime(2024, 3, 8, 0, 0, 0, tzinfo=UTC), datetime(2024, 3, 13, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 3, 8, 14, 0, 0), datetime(2024, 3, 9, 14, 0, 0), datetime(2024, 3, 10, 13, 0, 0),
             datetime(2024, 3, 11, 13, 0, 0), datetime(2024, 3, 12, 13, 0, 0)],
        ),
        # US/New York: Fall Back
        (
            "America/New_York", datetime(2024, 11, 1, 9, 0, 0), "FREQ=DAILY;COUNT=5",
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC), datetime(2024, 11, 6, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 11, 1, 13, 0, 0), datetime(2024, 11, 2, 13, 0, 0), datetime(2024, 11, 3, 14, 0, 0),
             datetime(2024, 11, 4, 14, 0, 0), datetime(2024, 11, 5, 14, 0, 0)],
        ),
        # Europe/London: Spring Forward
        (
            "Europe/London", datetime(2024, 3, 29, 9, 0, 0), "FREQ=DAILY;COUNT=5",
            datetime(2024, 3, 29, 0, 0, 0, tzinfo=UTC), datetime(2024, 4, 3, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 3, 29, 9, 0, 0), datetime(2024, 3, 30, 9, 0, 0), datetime(2024, 3, 31, 8, 0, 0),
             datetime(2024, 4, 1, 8, 0, 0), datetime(2024, 4, 2, 8, 0, 0)],
        ),
        # Europe/London: Fall Back
        (
            "Europe/London", datetime(2024, 10, 25, 9, 0, 0), "FREQ=DAILY;COUNT=5",
            datetime(2024, 10, 25, 0, 0, 0, tzinfo=UTC), datetime(2024, 10, 30, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 10, 25, 8, 0, 0), datetime(2024, 10, 26, 8, 0, 0), datetime(2024, 10, 27, 9, 0, 0),
             datetime(2024, 10, 28, 9, 0, 0), datetime(2024, 10, 29, 9, 0, 0)],
        ),
    ],
)
async def test_get_events_in_range_dst_transitions(
    db_service: DatabaseService,
    date_utils: DateUtils,
    test_session: AsyncSession, # Keep test_session if used for verification inside test
    conflict_resolver: ConflictResolver,
    tz_name: str,
    event_local_start: datetime,
    rrule_str: str,
    query_range_start_utc: datetime,
    query_range_end_utc: datetime,
    expected_naive_utc_starts: list[datetime],
):
    """Test recurrence expansion across DST transitions within DatabaseService."""
    # Arrange
    event_duration = timedelta(hours=1) # Need timedelta from datetime import
    event_local_end = event_local_start + event_duration
    base_event_data = EventCreate(
        title=f"DST Integration {tz_name}",
        start_time=event_local_start,
        end_time=event_local_end,
        time_zone=tz_name,
        rrule=rrule_str,
    )
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy
    )
    created_base_event = await create_event_strategy.execute(data=base_event_data)
    assert created_base_event is not None

    # Act
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=query_range_start_utc, end_time=query_range_end_utc)
    retrieved_event_instances = await strategy.execute(data=strategy_input)

    # Assert
    assert len(retrieved_event_instances) == len(expected_naive_utc_starts)
    retrieved_naive_utc_starts = sorted([evt.start_time for evt in retrieved_event_instances])
    assert retrieved_naive_utc_starts == expected_naive_utc_starts
    retrieved_naive_utc_ends = sorted([evt.end_time for evt in retrieved_event_instances])
    expected_naive_utc_ends = sorted([start + event_duration for start in expected_naive_utc_starts])
    assert retrieved_naive_utc_ends == expected_naive_utc_ends
    for instance in retrieved_event_instances:
        assert instance.title == f"DST Integration {tz_name}"
        assert instance.time_zone == tz_name
        assert instance.rrule is None 