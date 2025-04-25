from datetime import UTC, datetime, timedelta

import pytest
# from sqlalchemy.ext.asyncio import AsyncSession # No longer needed directly

from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
# Need ConflictResolver for CreateEventStrategy
from sa.event.conflict_resolver import ConflictResolver

logger = get_logger(__name__)

# Helper function (copied from test_byxxx_rules.py)
async def _run_by_rule_test(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver, # Added dependency
    event_title: str,
    base_start_utc: datetime,
    rrule: str,
    query_start_utc: datetime,
    query_end_utc: datetime,
    expected_start_times_utc: list[datetime],
):
    # Arrange
    base_end_utc = base_start_utc + timedelta(hours=1)
    # Need the GetEventsInRangeStrategy instance for CreateEventStrategy
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)

    create_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy # Pass the instance
    )
    event_create = EventCreate(
        title=event_title,
        start_time=base_start_utc.replace(tzinfo=None),
        end_time=base_end_utc.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )
    await create_strategy.execute(data=event_create)

    # Act
    # Use the same instance for getting events
    strategy = get_events_strategy
    strategy_input = (query_start_utc, query_end_utc)
    retrieved_events = await strategy.execute(data=strategy_input)

    # Assert
    assert len(retrieved_events) == len(expected_start_times_utc)
    retrieved_starts_naive = sorted([e.start_time for e in retrieved_events])
    expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_start_times_utc])
    assert retrieved_starts_naive == expected_starts_naive

# Tests for complex BYxxx rule interactions (Cases 38-41, 43-44) - Refactored
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_id", "event_title", "base_start_utc", "rrule", "query_start_utc", "query_end_utc", "expected_start_times_utc"),
    [
        # Case 38: BYDAY + BYHOUR: FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=9
        (
            "byday_byhour",
            "Weekly Mon/Fri 9am",
            datetime(2024, 11, 1, 9, 0, 0, tzinfo=UTC), # Friday 9am
            "FREQ=WEEKLY;BYDAY=MO,FR;BYHOUR=9;COUNT=4", # Nov 1(F), 4(M), 8(F), 11(M)
            datetime(2024, 11, 3, 0, 0, 0, tzinfo=UTC), # Sunday
            datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC), # Sunday
            [
                datetime(2024, 11, 4, 9, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 8, 9, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 39: BYDAY + BYSETPOS: FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1 (Last weekday of month)
        (
            "last_weekday_of_month",
            "Monthly Last Weekday",
            datetime(2024, 10, 31, 16, 0, 0, tzinfo=UTC), # Last weekday Oct (Thurs)
            "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1;COUNT=4", # Oct 31, Nov 29(F), Dec 31(Tu), Jan 31(F)
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 29, 16, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 40: INTERVAL + BYDAY: FREQ=WEEKLY;INTERVAL=2;BYDAY=SU
        (
            "interval_byday",
            "Every Other Sunday",
            datetime(2024, 11, 3, 11, 0, 0, tzinfo=UTC), # First Sunday Nov 2024
            "FREQ=WEEKLY;INTERVAL=2;BYDAY=SU;COUNT=4", # Nov 3, Nov 17, Dec 1, Dec 15
            datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 10, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 17, 11, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 1, 11, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 41: BYMONTH + BYMONTHDAY: FREQ=YEARLY;BYMONTH=1,7;BYMONTHDAY=15
        (
            "bymonth_bymonthday",
            "Jan/Jul 15th",
            datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC),
            "FREQ=YEARLY;BYMONTH=1,7;BYMONTHDAY=15;COUNT=4", # Jan 15/Jul 15 2024, Jan 15/Jul 15 2025
            datetime(2024, 5, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2025, 5, 1, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 7, 15, 10, 0, 0, tzinfo=UTC),
                datetime(2025, 1, 15, 10, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 43: BYSETPOS: FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2 (2nd Tuesday)
        (
            "bysetpos_second_tuesday",
            "Monthly 2nd Tuesday",
            datetime(2024, 10, 8, 14, 0, 0, tzinfo=UTC), # 2nd Tue Oct 2024
            "FREQ=MONTHLY;BYDAY=TU;BYSETPOS=2;COUNT=4", # Oct 8, Nov 12, Dec 10, Jan 14
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 15, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 12, 14, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 10, 14, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 44: WKST: FREQ=WEEKLY;BYDAY=SA,SU;WKST=SU (Weekend meeting, week starts Sunday)
        (
            "wkst_sunday",
            "Weekend Meeting WKST=SU",
            datetime(2024, 11, 9, 13, 0, 0, tzinfo=UTC), # Saturday
            "FREQ=WEEKLY;BYDAY=SA,SU;WKST=SU;COUNT=6", # Nov 9(Sa), 10(Su), 16(Sa), 17(Su), 23(Sa), 24(Su)
            datetime(2024, 11, 11, 0, 0, 0, tzinfo=UTC), # Monday
            datetime(2024, 11, 18, 0, 0, 0, tzinfo=UTC), # Monday
            [
                datetime(2024, 11, 16, 13, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 17, 13, 0, 0, tzinfo=UTC),
            ]
        ),
    ],
    ids=[
        "case38_byday_byhour",
        "case39_last_weekday",
        "case40_interval_byday",
        "case41_bymonth_bymonthday",
        "case43_bysetpos_2nd_tue",
        "case44_wkst_sun",
    ]
)
async def test_get_events_in_range_complex_byxxx_rules(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    initialize_db: None, # Add DB initialization
    test_id: str,
    event_title: str,
    base_start_utc: datetime,
    rrule: str,
    query_start_utc: datetime,
    query_end_utc: datetime,
    expected_start_times_utc: list[datetime],
):
    """Test complex BYxxx rule interactions using the helper."""
    # Note: Case 42 (BYYEARDAY + BYWEEKNO) is skipped as it's an invalid combo
    await _run_by_rule_test(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        event_title=event_title,
        base_start_utc=base_start_utc,
        rrule=rrule,
        query_start_utc=query_start_utc,
        query_end_utc=query_end_utc,
        expected_start_times_utc=expected_start_times_utc,
    )
