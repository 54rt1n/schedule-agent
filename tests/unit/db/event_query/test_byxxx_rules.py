from datetime import UTC, datetime, timedelta

import pytest
# from sqlalchemy.ext.asyncio import AsyncSession # No longer needed directly

from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate, TimeRange
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
# Need ConflictResolver for CreateEventStrategy
from sa.event.conflict_resolver import ConflictResolver

logger = get_logger(__name__)

# Helper function to create events and assert results
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
    event_create = EventCreate(
        title=event_title,
        start_time=base_start_utc.replace(tzinfo=None),
        end_time=base_end_utc.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    )

    # Need the GetEventsInRangeStrategy instance for CreateEventStrategy
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)

    create_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_strategy # Pass the instance
    )
    await create_strategy.execute(data=event_create)

    # Act
    # Use the same instance for getting events
    strategy = get_events_strategy
    strategy_input = TimeRange(start_time=query_start_utc, end_time=query_end_utc)
    retrieved_events = await strategy.execute(data=strategy_input)

    # Assert
    assert len(retrieved_events) == len(expected_start_times_utc)
    retrieved_starts_naive = sorted([e.start_time for e in retrieved_events])
    expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_start_times_utc])
    assert retrieved_starts_naive == expected_starts_naive

# Tests for simple BYxxx rules (Cases 27-37) - Refactored
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_id", "event_title", "base_start_utc", "rrule", "query_start_utc", "query_end_utc", "expected_start_times_utc"),
    [
        # Case 27: BYMONTH: FREQ=YEARLY;BYMONTH=3,6
        (
            "bymonth",
            "Yearly March/June",
            datetime(2024, 3, 5, 10, 0, 0, tzinfo=UTC),
            "FREQ=YEARLY;BYMONTH=3,6;COUNT=4", # Mar/Jun 2024, Mar/Jun 2025
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2025, 4, 1, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 3, 5, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 6, 5, 10, 0, 0, tzinfo=UTC),
                datetime(2025, 3, 5, 10, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 28: BYMONTHDAY: FREQ=MONTHLY;BYMONTHDAY=15
        (
            "bymonthday_pos",
            "Monthly 15th",
            datetime(2024, 10, 15, 12, 0, 0, tzinfo=UTC),
            "FREQ=MONTHLY;BYMONTHDAY=15;COUNT=3", # Oct 15, Nov 15, Dec 15
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 11, 15, 12, 0, 0, tzinfo=UTC)]
        ),
        # Case 29: BYMONTHDAY: FREQ=MONTHLY;BYMONTHDAY=-1
        (
            "bymonthday_neg",
            "Monthly Last Day",
            datetime(2024, 1, 31, 9, 0, 0, tzinfo=UTC),
            "FREQ=MONTHLY;BYMONTHDAY=-1;COUNT=4", # Jan 31, Feb 29, Mar 31, Apr 30
            datetime(2024, 2, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 4, 1, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 2, 29, 9, 0, 0, tzinfo=UTC), # Leap year
                datetime(2024, 3, 31, 9, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 30: BYDAY (Weekly): FREQ=WEEKLY;BYDAY=MO,FR
        (
            "byday_weekly",
            "Weekly Mon/Fri",
            datetime(2024, 11, 4, 10, 0, 0, tzinfo=UTC), # Monday
            "FREQ=WEEKLY;BYDAY=MO,FR;COUNT=6", # Nov 4(M), 8(F), 11(M), 15(F), 18(M), 22(F)
            datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC), # Sun
            datetime(2024, 11, 17, 0, 0, 0, tzinfo=UTC), # Sun
            [
                datetime(2024, 11, 11, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 15, 10, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 31: BYDAY (Monthly): FREQ=MONTHLY;BYDAY=1FR
        (
            "byday_monthly_first",
            "Monthly 1st Friday",
            datetime(2024, 11, 1, 11, 0, 0, tzinfo=UTC), # 1st Fri Nov 2024
            "FREQ=MONTHLY;BYDAY=1FR;COUNT=3", # Nov 1, Dec 6, Jan 3
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 12, 31, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 12, 6, 11, 0, 0, tzinfo=UTC)]
        ),
        # Case 32: BYDAY (Monthly): FREQ=MONTHLY;BYDAY=-1SU
        (
            "byday_monthly_last",
            "Monthly Last Sunday",
            datetime(2024, 10, 27, 16, 0, 0, tzinfo=UTC), # Last Sun Oct 2024
            "FREQ=MONTHLY;BYDAY=-1SU;COUNT=3", # Oct 27, Nov 24, Dec 29
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 30, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 11, 24, 16, 0, 0, tzinfo=UTC)]
        ),
        # Case 33: BYHOUR: FREQ=DAILY;BYHOUR=9,17
        (
            "byhour",
            "Daily 9am/5pm",
            datetime(2024, 11, 1, 9, 0, 0, tzinfo=UTC),
            "FREQ=DAILY;BYHOUR=9,17;COUNT=4", # Nov 1 9am, Nov 1 5pm, Nov 2 9am, Nov 2 5pm
            datetime(2024, 11, 1, 12, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 2, 12, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 1, 17, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 2, 9, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 34: BYMINUTE: FREQ=HOURLY;BYMINUTE=0,30
        (
            "byminute",
            "Hourly :00/:30",
            datetime(2024, 11, 1, 10, 0, 0, tzinfo=UTC),
            "FREQ=HOURLY;BYMINUTE=0,30;COUNT=5", # 10:00, 10:30, 11:00, 11:30, 12:00
            datetime(2024, 11, 1, 10, 15, 0, tzinfo=UTC),
            datetime(2024, 11, 1, 11, 15, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 1, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 1, 10, 30, 0, tzinfo=UTC),
                datetime(2024, 11, 1, 11, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 35: BYSECOND: FREQ=MINUTELY;BYSECOND=0
        (
            "bysecond",
            "Minutely :00 sec",
            datetime(2024, 11, 1, 10, 0, 0, tzinfo=UTC),
            "FREQ=MINUTELY;BYSECOND=0;COUNT=3", # 10:00:00, 10:01:00, 10:02:00
            datetime(2024, 11, 1, 10, 0, 30, tzinfo=UTC),
            datetime(2024, 11, 1, 10, 2, 30, tzinfo=UTC),
            [
                datetime(2024, 11, 1, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 1, 10, 1, 0, tzinfo=UTC),
                datetime(2024, 11, 1, 10, 2, 0, tzinfo=UTC),
            ]
        ),
        # Case 36: BYYEARDAY: FREQ=YEARLY;BYYEARDAY=1,-1
        (
            "byyearday",
            "Yearly Jan 1/Dec 31",
            datetime(2024, 1, 1, 8, 0, 0, tzinfo=UTC),
            "FREQ=YEARLY;BYYEARDAY=1,-1;COUNT=4", # Jan 1/Dec 31 2024, Jan 1/Dec 31 2025
            datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 12, 31, 8, 0, 0, tzinfo=UTC),
                datetime(2025, 1, 1, 8, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 37: BYWEEKNO: FREQ=YEARLY;BYWEEKNO=1,52
        (
            "byweekno",
            "Yearly Week 1/52",
            datetime(2024, 1, 1, 13, 0, 0, tzinfo=UTC), # Monday, Week 1 2024
            "FREQ=YEARLY;BYWEEKNO=1,52;BYDAY=MO;COUNT=4", # Mon Wk1/52 2024, Mon Wk1/52 2025
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2025, 2, 1, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 12, 23, 13, 0, 0, tzinfo=UTC), # Mon, Week 52 2024
                datetime(2024, 12, 30, 13, 0, 0, tzinfo=UTC), # Mon, Week 1 2025
            ]
        ),
    ],
    ids=[
        "case27_bymonth",
        "case28_bymonthday_pos",
        "case29_bymonthday_neg",
        "case30_byday_weekly",
        "case31_byday_monthly_first",
        "case32_byday_monthly_last",
        "case33_byhour",
        "case34_byminute",
        "case35_bysecond",
        "case36_byyearday",
        "case37_byweekno",
    ]
)
async def test_get_events_in_range_simple_byxxx_rules(
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
    """Test simple BYxxx recurrence rules using the helper."""
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
