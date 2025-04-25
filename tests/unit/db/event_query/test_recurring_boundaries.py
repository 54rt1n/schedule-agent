from datetime import UTC, datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver

logger = get_logger(__name__)

# Tests for recurring event queries (get_events_in_range)

@pytest.mark.asyncio
async def test_get_events_in_range_recurring(
    db_service: DatabaseService, date_utils: DateUtils, test_session: AsyncSession, conflict_resolver: ConflictResolver
):
    """Test retrieving recurring event instances within a date range."""
    # --- Arrange ---
    base_start_naive = datetime(2024, 9, 4, 11, 0, 0)
    base_end_naive = datetime(2024, 9, 4, 12, 0, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=WE;COUNT=4"
    base_event_data = EventCreate(
        title="Recurring Weekly",
        start_time=base_start_naive,
        end_time=base_end_naive,
        time_zone="UTC",
        rrule=rrule_str,
    )
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy
    )
    base_event = await create_event_strategy.execute(data=base_event_data)
    assert base_event.id is not None

    range_start = datetime(2024, 9, 10, 0, 0, 0)
    range_end = datetime(2024, 9, 20, 0, 0, 0)

    # --- Act ---
    range_start_aware = range_start.replace(tzinfo=UTC)
    range_end_aware = range_end.replace(tzinfo=UTC)
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = (range_start_aware, range_end_aware)
    retrieved_events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert len(retrieved_events) == 2
    retrieved_starts_naive = sorted([evt.start_time for evt in retrieved_events])
    expected_starts_naive = [
        datetime(2024, 9, 11, 11, 0, 0),
        datetime(2024, 9, 18, 11, 0, 0),
    ]
    assert retrieved_starts_naive == expected_starts_naive
    for instance in retrieved_events:
        assert instance.title == "Recurring Weekly"
        assert instance.time_zone == "UTC"
        assert instance.rrule is None


@pytest.mark.asyncio
async def test_get_events_in_range_recurring_outside_range(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test that a recurring event entirely outside the range is not returned."""
    # --- Arrange ---
    event_create_data = EventCreate(
        title="Far Future Daily Meeting",
        start_time=datetime(2025, 1, 1, 10, 0, 0),
        end_time=datetime(2025, 1, 1, 11, 0, 0),
        time_zone="UTC",
        rrule="FREQ=DAILY;COUNT=5",
    )
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy
    )
    await create_event_strategy.execute(data=event_create_data)

    query_start = datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2024, 12, 31, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = (query_start, query_end)
    events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert not events


# Define a fixed query range for recurring boundary tests
RECUR_QUERY_START = datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC)
RECUR_QUERY_END = datetime(2024, 11, 17, 0, 0, 0, tzinfo=UTC) # 1 week range

@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "test_id",
        "event_start_utc",
        "event_end_utc",
        "rrule",
        "expected_count",
        "expected_start_dates_utc", # List of expected naive UTC start dates within range
    ),
    [
        # Case 12: All Instances Before Range
        (
            "recur_before",
            datetime(2024, 11, 1, 10, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 1, 11, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=5", # Ends Nov 5th
            0,
            [],
        ),
        # Case 13: Instances Straddle Start of Range
        (
            "recur_straddle_start",
            datetime(2024, 11, 8, 10, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 8, 11, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=5", # Occurs 8, 9, 10, 11, 12
            3,
            [
                datetime(2024, 11, 10, 10, 0, 0),
                datetime(2024, 11, 11, 10, 0, 0),
                datetime(2024, 11, 12, 10, 0, 0),
            ],
        ),
        # Case 14: Instances Fully Inside Range
        (
            "recur_fully_inside",
            datetime(2024, 11, 11, 10, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 11, 11, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=3", # Occurs 11, 12, 13
            3,
            [
                datetime(2024, 11, 11, 10, 0, 0),
                datetime(2024, 11, 12, 10, 0, 0),
                datetime(2024, 11, 13, 10, 0, 0),
            ],
        ),
        # Case 15: Instances Straddle End of Range
        (
            "recur_straddle_end",
            datetime(2024, 11, 15, 10, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 15, 11, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=5", # Occurs 15, 16, 17, 18, 19
            2,
            [
                datetime(2024, 11, 15, 10, 0, 0),
                datetime(2024, 11, 16, 10, 0, 0),
                # 17th starts *after* range end
            ],
        ),
        # Case 16: Instance Contains Range (single long event)
        (
            "recur_contains_range",
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC), # Long event
            "FREQ=MONTHLY;COUNT=1", # Only one instance
            1,
            [datetime(2024, 11, 1, 0, 0, 0)], # The single instance overlaps
        ),
        # Case 17: All Instances After Range
        (
            "recur_after",
            datetime(2024, 11, 18, 10, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 18, 11, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=5",
            0,
            [],
        ),
    ],
    ids=[
        "case12_recur_before",
        "case13_recur_straddle_start",
        "case14_recur_fully_inside",
        "case15_recur_straddle_end",
        "case16_recur_contains_range",
        "case17_recur_after",
    ]
)
async def test_get_events_in_range_recurring_boundaries(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    test_id: str,
    event_start_utc: datetime,
    event_end_utc: datetime,
    rrule: str,
    expected_count: int,
    expected_start_dates_utc: list[datetime],
):
    """Test get_events_in_range for recurring event boundaries and overlaps."""
    # --- Arrange ---
    event_title = f"Event {test_id}"
    # Ensure base times are naive UTC for DB storage
    base_start_naive = event_start_utc.replace(tzinfo=None)
    base_end_naive = event_end_utc.replace(tzinfo=None)

    event_data = EventCreate(
        title=event_title,
        start_time=base_start_naive,
        end_time=base_end_naive,
        time_zone="UTC", # Using UTC for simplicity
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy
    )
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    assert created_event.title == event_title
    assert created_event.rrule == rrule

    # --- Act ---
    strategy_input = (RECUR_QUERY_START, RECUR_QUERY_END)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    # These assertions are likely to fail due to the original bug
    assert len(retrieved_events) == expected_count, (
        f"Test ID '{test_id}': Expected {expected_count} instances, but got {len(retrieved_events)}. "
        f"Base Event: [{event_start_utc.isoformat()}, {event_end_utc.isoformat()}), RRULE: {rrule}, "
        f"Query: [{RECUR_QUERY_START.isoformat()}, {RECUR_QUERY_END.isoformat()})"
    )

    if expected_count > 0:
        retrieved_starts = sorted([e.start_time for e in retrieved_events])
        # Convert expected starts to naive for comparison with results
        expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_start_dates_utc])

        assert retrieved_starts == expected_starts_naive, (
            f"Test ID '{test_id}': Mismatch in expected instance start times. "
            f"Expected: {[dt.isoformat() for dt in expected_starts_naive]}, "
            f"Got: {[dt.isoformat() for dt in retrieved_starts]}"
        )
        # Check that returned events look like instances (no rrule)
        for evt in retrieved_events:
            assert evt.rrule is None
            assert evt.title == event_title
