from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import ExcludedDate, ModifiedInstance
from schedule_core.db.dao import EventCreate, TimeRange
from schedule_core.db.service import DatabaseService
from schedule_core.event.date_utils import DateUtils
from schedule_core.db.strategies.create_event import CreateEventStrategy
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_core.event.conflict_resolver import ConflictResolver


logger = get_logger(__name__)

# Tests for recurring event rules (COUNT, UNTIL, EXDATE, ModifiedInstance, etc.)

# Refactored COUNT tests (Case 22 & 23)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_id", "event_title", "base_start_utc", "rrule", "query_start_utc", "query_end_utc", "expected_starts_utc"),
    [
        # Case 22: COUNT - Exact number of instances
        (
            "count_exact",
            "Event With Count 5",
            datetime(2024, 12, 1, 10, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=5", # Occurs Dec 1, 2, 3, 4, 5
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC), # Query covers all
            datetime(2024, 12, 6, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 12, 1, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 2, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 3, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 4, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 5, 10, 0, 0, tzinfo=UTC),
            ],
        ),
        # Case 23: COUNT - Range covers subset
        (
            "count_subset",
            "Event With Count 10 Subset",
            datetime(2024, 12, 1, 10, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;COUNT=10", # Occurs Dec 1 to Dec 10
            datetime(2024, 12, 3, 0, 0, 0, tzinfo=UTC), # Query covers instances 3-7
            datetime(2024, 12, 8, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 12, 3, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 4, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 5, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 6, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 7, 10, 0, 0, tzinfo=UTC),
            ],
        ),
    ],
    ids=["case22_count_exact", "case23_count_subset"]
)
async def test_get_events_in_range_recurring_count(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    test_id: str,
    event_title: str,
    base_start_utc: datetime,
    rrule: str,
    query_start_utc: datetime,
    query_end_utc: datetime,
    expected_starts_utc: list[datetime],
):
    """Test that recurrence respects the COUNT parameter."""
    # --- Arrange ---
    base_end = base_start_utc + timedelta(hours=1)
    event_data = EventCreate(
        title=event_title,
        start_time=base_start_utc.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(db_service=db_service, date_utils=date_utils,
                                                get_events_in_range_strategy=get_events_in_range_strategy,
                                                conflict_resolver=conflict_resolver)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start_utc, end_time=query_end_utc)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_starts_utc])
    retrieved_starts_naive = sorted([e.start_time for e in retrieved_events])

    assert len(retrieved_events) == len(expected_starts_naive), f"Test ID '{test_id}': Count mismatch"
    assert retrieved_starts_naive == expected_starts_naive, f"Test ID '{test_id}': Start time mismatch"

# Refactored UNTIL tests (Case 24 & 25)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_id", "event_title", "base_start_utc", "rrule", "query_start_utc", "query_end_utc", "expected_starts_utc"),
    [
        # Case 24: UNTIL - Date boundary
        (
            "until_date",
            "Event With Until Date",
            datetime(2024, 12, 1, 10, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;UNTIL=20241203", # Inclusive, last instance on Dec 3
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC), # Query covers all possible
            datetime(2024, 12, 5, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 12, 1, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 2, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 3, 10, 0, 0, tzinfo=UTC),
            ],
        ),
        # Case 25: UNTIL - DateTime boundary (UTC)
        (
            "until_datetime_utc",
            "Event With Until DateTime UTC",
            datetime(2024, 12, 10, 9, 0, 0, tzinfo=UTC),
            f"FREQ=HOURLY;UNTIL={datetime(2024, 12, 10, 11, 0, 0, tzinfo=UTC).strftime('%Y%m%dT%H%M%SZ')}", # Last instance starts at 11:00 UTC
            datetime(2024, 12, 10, 0, 0, 0, tzinfo=UTC), # Query covers all possible
            datetime(2024, 12, 11, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 12, 10, 9, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 10, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 10, 11, 0, 0, tzinfo=UTC),
            ],
        ),
        # Case 25b: UNTIL - DateTime boundary (UTC), range ends before UNTIL
        (
            "until_datetime_utc_subset",
            "Event With Until DateTime UTC Subset",
            datetime(2024, 12, 10, 9, 0, 0, tzinfo=UTC),
            f"FREQ=HOURLY;UNTIL={datetime(2024, 12, 10, 11, 0, 0, tzinfo=UTC).strftime('%Y%m%dT%H%M%SZ')}",
            datetime(2024, 12, 10, 9, 30, 0, tzinfo=UTC), # Query starts after first instance
            datetime(2024, 12, 10, 10, 30, 0, tzinfo=UTC), # Query ends after second instance
            [
                # Only the 10:00 instance should be returned
                datetime(2024, 12, 10, 9, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 10, 10, 0, 0, tzinfo=UTC),
            ],
        ),
    ],
    ids=["case24_until_date", "case25_until_datetime_utc", "case25b_until_datetime_subset"]
)
async def test_get_events_in_range_recurring_until(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    test_id: str,
    event_title: str,
    base_start_utc: datetime,
    rrule: str,
    query_start_utc: datetime,
    query_end_utc: datetime,
    expected_starts_utc: list[datetime],
):
    """Test that recurrence respects the UNTIL parameter (Date and DateTime)."""
    # --- Arrange ---
    base_end = base_start_utc + timedelta(hours=1) # Assume 1hr duration for simplicity
    event_data = EventCreate(
        title=event_title,
        start_time=base_start_utc.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(db_service=db_service, date_utils=date_utils,
                                                get_events_in_range_strategy=get_events_in_range_strategy,
                                                conflict_resolver=conflict_resolver)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start_utc, end_time=query_end_utc)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_starts_utc])
    retrieved_starts_naive = sorted([e.start_time for e in retrieved_events])

    assert len(retrieved_events) == len(expected_starts_naive), f"Test ID '{test_id}': Count mismatch"
    assert retrieved_starts_naive == expected_starts_naive, f"Test ID '{test_id}': Start time mismatch"

# Test for Case 46: EXDATE - Exclude instance within range
@pytest.mark.asyncio
async def test_get_events_in_range_recurring_exdate_inside(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    initialize_db
):
    """Test that EXDATE correctly excludes an instance within the query range."""
    # --- Arrange ---
    event_title = "Event With Excluded Date Inside"
    base_start = datetime(2024, 12, 10, 10, 0, 0, tzinfo=UTC)
    base_end = datetime(2024, 12, 10, 11, 0, 0, tzinfo=UTC)
    rrule = "FREQ=DAILY;COUNT=5" # Occurs Dec 10, 11, 12, 13, 14

    event_data = EventCreate(
        title=event_title,
        start_time=base_start.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(db_service=db_service, date_utils=date_utils,
                                                get_events_in_range_strategy=get_events_in_range_strategy,
                                                conflict_resolver=conflict_resolver)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    assert created_event.id is not None

    # Exclude Dec 12 (which is inside the query range)
    excluded_dt_naive = datetime(2024, 12, 12, 10, 0, 0)
    async with db_service.get_session() as session:
        async with session.begin():
            exdate = ExcludedDate(event_id=created_event.id, excluded_date=excluded_dt_naive)
            session.add(exdate)
            await session.flush()

    # Query range covering all potential original instances
    query_start = datetime(2024, 12, 10, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2024, 12, 15, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expecting 4 events (Dec 10, 11, 13, 14), excluding Dec 12
    assert len(retrieved_events) == 4
    retrieved_starts = sorted([e.start_time for e in retrieved_events])
    expected_starts = sorted([
        datetime(2024, 12, 10, 10, 0, 0),
        datetime(2024, 12, 11, 10, 0, 0),
        datetime(2024, 12, 13, 10, 0, 0),
        datetime(2024, 12, 14, 10, 0, 0),
    ])
    assert retrieved_starts == expected_starts

# Test for Case 47: EXDATE - Exclude instance at range start
@pytest.mark.asyncio
async def test_get_events_in_range_recurring_exdate_at_start(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test that EXDATE correctly excludes an instance starting exactly at query start."""
    # --- Arrange ---
    event_title = "Event With Excluded Date At Start"
    base_start = datetime(2024, 12, 10, 10, 0, 0, tzinfo=UTC)
    base_end = datetime(2024, 12, 10, 11, 0, 0, tzinfo=UTC)
    rrule = "FREQ=DAILY;COUNT=3" # Occurs Dec 10, 11, 12

    event_data = EventCreate(
        title=event_title,
        start_time=base_start.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(db_service=db_service, date_utils=date_utils,
                                                get_events_in_range_strategy=get_events_in_range_strategy,
                                                conflict_resolver=conflict_resolver)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    assert created_event.id is not None

    # Exclude Dec 11
    excluded_dt_naive = datetime(2024, 12, 11, 10, 0, 0)
    async with db_service.get_session() as session:
        async with session.begin():
            exdate = ExcludedDate(event_id=created_event.id, excluded_date=excluded_dt_naive)
            session.add(exdate)
            await session.flush()

    # Query range starting exactly at the excluded instance time
    query_start = datetime(2024, 12, 11, 10, 0, 0, tzinfo=UTC)
    query_end = datetime(2024, 12, 13, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expecting only Dec 12 instance (Dec 11 is excluded)
    assert len(retrieved_events) == 1
    assert retrieved_events[0].start_time == datetime(2024, 12, 12, 10, 0, 0)

# Test for Case 52: Modified: Instance moved *into* range
@pytest.mark.asyncio
async def test_get_events_in_range_recurring_mod_into_range(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    initialize_db
):
    """Test modification moving an instance into the query range."""
    # --- Arrange ---
    event_title = "Event Instance Moved Into Range"
    base_start = datetime(2025, 3, 3, 10, 0, 0, tzinfo=UTC) # Monday
    base_end = datetime(2025, 3, 3, 11, 0, 0, tzinfo=UTC)
    rrule = "FREQ=WEEKLY;BYDAY=MO;COUNT=4" # Mar 3, 10, 17, 24

    event_data = EventCreate(
        title=event_title,
        start_time=base_start.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(db_service=db_service, date_utils=date_utils,
                                                get_events_in_range_strategy=get_events_in_range_strategy,
                                                conflict_resolver=conflict_resolver)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    assert created_event.id is not None

    # Modify the Mar 10 instance to Mar 11 (Tuesday), which is inside the query range
    original_date_naive = datetime(2025, 3, 10, 10, 0, 0)
    modified_start_naive = datetime(2025, 3, 11, 14, 0, 0)
    modified_end_naive = datetime(2025, 3, 11, 15, 0, 0)

    async with db_service.get_session() as session:
        async with session.begin():
            mod_instance = ModifiedInstance(
                event_id=created_event.id,
                original_date=original_date_naive,
                start_time=modified_start_naive,
                end_time=modified_end_naive,
                time_zone="UTC",
                title="Modified Into Range Title",
            )
            session.add(mod_instance)
            await session.flush()

    # Query range [Mar 11, Mar 16) - only covers the modified instance
    query_start = datetime(2025, 3, 11, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2025, 3, 16, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    assert len(retrieved_events) == 1
    assert retrieved_events[0].start_time == modified_start_naive
    assert retrieved_events[0].title == "Modified Into Range Title"

# Test for Case 53: Modified: Instance moved *out of* range
@pytest.mark.asyncio
async def test_get_events_in_range_recurring_mod_out_of_range(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test modification moving an instance out of the query range."""
    # --- Arrange ---
    event_title = "Event Instance Moved Out Of Range"
    base_start = datetime(2025, 3, 3, 10, 0, 0, tzinfo=UTC) # Monday
    base_end = datetime(2025, 3, 3, 11, 0, 0, tzinfo=UTC)
    rrule = "FREQ=WEEKLY;BYDAY=MO;COUNT=4" # Mar 3, 10, 17, 24

    event_data = EventCreate(
        title=event_title,
        start_time=base_start.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service, date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    assert created_event.id is not None

    # Modify the Mar 10 instance (originally inside query range) to Mar 18 (outside)
    original_date_naive = datetime(2025, 3, 10, 10, 0, 0)
    modified_start_naive = datetime(2025, 3, 18, 14, 0, 0)
    modified_end_naive = datetime(2025, 3, 18, 15, 0, 0)

    async with db_service.get_session() as session:
        async with session.begin():
            mod_instance = ModifiedInstance(
                event_id=created_event.id,
                original_date=original_date_naive,
                start_time=modified_start_naive,
                end_time=modified_end_naive,
                time_zone="UTC",
            )
            session.add(mod_instance)
            await session.flush()

    # Query range [Mar 5, Mar 15) - covers original Mar 10, but not modified Mar 18
    query_start = datetime(2025, 3, 5, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2025, 3, 15, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expect 0 events, as Mar 10 was replaced and moved outside
    assert len(retrieved_events) == 0

# Test for Case 54: Modified: Instance time changed within range
@pytest.mark.asyncio
async def test_get_events_in_range_recurring_mod_time_inside(db_service: DatabaseService, date_utils: DateUtils, conflict_resolver: ConflictResolver):
    """Test modification changing time of an instance still within the query range."""
    # --- Arrange ---
    event_title = "Event Instance Time Modified Inside"
    base_start = datetime(2025, 3, 10, 10, 0, 0, tzinfo=UTC) # Monday 10:00
    base_end = datetime(2025, 3, 10, 11, 0, 0, tzinfo=UTC)
    rrule = "FREQ=DAILY;COUNT=3" # Mar 10, 11, 12 (all 10:00)

    event_data = EventCreate(
        title=event_title,
        start_time=base_start.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)

    create_event_strategy = CreateEventStrategy(
        db_service=db_service, date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy)
    created_event = await create_event_strategy.execute(data=event_data)
    assert created_event is not None
    assert created_event.id is not None

    # Modify the Mar 11 instance to start at 14:00 (still inside query range)
    original_date_naive = datetime(2025, 3, 11, 10, 0, 0)
    modified_start_naive = datetime(2025, 3, 11, 14, 0, 0)
    modified_end_naive = datetime(2025, 3, 11, 15, 0, 0)

    async with db_service.get_session() as session:
        async with session.begin():
            mod_instance = ModifiedInstance(
                event_id=created_event.id,
                original_date=original_date_naive,
                start_time=modified_start_naive,
                end_time=modified_end_naive,
                time_zone="UTC",
                title="Modified Time Title",
            )
            session.add(mod_instance)
            await session.flush()

    # Query range covering all original/modified instances
    query_start = datetime(2025, 3, 10, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2025, 3, 13, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    strategy_input = TimeRange(start_time=query_start, end_time=query_end)
    retrieved_events = await get_events_in_range_strategy.execute(data=strategy_input)

    # --- Assert ---
    # Expecting 3 events: Mar 10 (original), Mar 11 (modified), Mar 12 (original)
    assert len(retrieved_events) == 3
    retrieved_details = sorted([(e.start_time, e.title) for e in retrieved_events])
    expected_details = sorted([
        (datetime(2025, 3, 10, 10, 0, 0), event_title),
        (datetime(2025, 3, 11, 14, 0, 0), "Modified Time Title"), # Modified time & title
        (datetime(2025, 3, 12, 10, 0, 0), event_title),
    ])
    assert retrieved_details == expected_details 