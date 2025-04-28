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

# Tests for FREQ and INTERVAL rules

# Refactored FREQ and INTERVAL tests (Cases 18-21)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_id", "event_title", "base_start_utc", "rrule", "query_start_utc", "query_end_utc", "expected_starts_utc"),
    [
        # Case 18: INTERVAL: FREQ=DAILY;INTERVAL=2
        (
            "interval_daily",
            "Every Other Day Meeting",
            datetime(2024, 11, 1, 9, 0, 0, tzinfo=UTC), # Friday
            "FREQ=DAILY;INTERVAL=2;COUNT=5", # Nov 1, 3, 5, 7, 9
            datetime(2024, 11, 2, 0, 0, 0, tzinfo=UTC), # Query covers Nov 2 to Nov 8 (exclusive)
            datetime(2024, 11, 8, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 3, 9, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 5, 9, 0, 0, tzinfo=UTC),
                datetime(2024, 11, 7, 9, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 19: INTERVAL: FREQ=WEEKLY;INTERVAL=2
        (
            "interval_weekly",
            "Every Other Week Meeting",
            datetime(2024, 11, 4, 10, 0, 0, tzinfo=UTC), # First Monday of Nov 2024
            "FREQ=WEEKLY;INTERVAL=2;COUNT=3", # Nov 4, Nov 18, Dec 2
            datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC), # Query covers mid-Nov to mid-Dec
            datetime(2024, 12, 10, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 18, 10, 0, 0, tzinfo=UTC),
                datetime(2024, 12, 2, 10, 0, 0, tzinfo=UTC),
            ]
        ),
        # Case 20: FREQ: FREQ=MONTHLY
        (
            "freq_monthly",
            "Monthly Report Due",
            datetime(2024, 10, 15, 14, 0, 0, tzinfo=UTC),
            "FREQ=MONTHLY;COUNT=4", # Oct 15, Nov 15, Dec 15, Jan 15
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC), # Query covers Nov 1 to Nov 30
            datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 11, 15, 14, 0, 0, tzinfo=UTC)]
        ),
        # Case 21: FREQ: FREQ=YEARLY
        (
            "freq_yearly",
            "Annual Review",
            datetime(2023, 6, 10, 11, 0, 0, tzinfo=UTC),
            "FREQ=YEARLY;COUNT=3", # Jun 10 2023, 2024, 2025
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC), # Query covers all of 2024
            datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC),
            [datetime(2024, 6, 10, 11, 0, 0, tzinfo=UTC)]
        ),
    ],
    ids=["case18_interval_daily", "case19_interval_weekly", "case20_freq_monthly", "case21_freq_yearly"]
)
async def test_get_events_in_range_freq_interval(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver, # Added dependency
    initialize_db: None, # Add DB initialization
    test_id: str,
    event_title: str,
    base_start_utc: datetime,
    rrule: str,
    query_start_utc: datetime,
    query_end_utc: datetime,
    expected_starts_utc: list[datetime],
):
    """Test recurrence rules involving FREQ and INTERVAL."""
    # --- Arrange ---
    base_end = base_start_utc + timedelta(hours=1) # Assume 1hr duration
    # Need the GetEventsInRangeStrategy instance for CreateEventStrategy
    get_events_strategy = GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils)
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver, # Pass dependency
        get_events_in_range_strategy=get_events_strategy # Pass strategy instance
    )
    await create_event_strategy.execute(data=EventCreate( # Use 'data=' parameter name
        title=event_title,
        start_time=base_start_utc.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    ))

    # --- Act ---
    # Use the same GetEventsInRangeStrategy instance
    strategy = get_events_strategy
    strategy_input = TimeRange(start_time=query_start_utc, end_time=query_end_utc)
    retrieved_events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_starts_utc])
    retrieved_starts_naive = sorted([e.start_time for e in retrieved_events])

    assert len(retrieved_events) == len(expected_starts_naive), f"Test ID '{test_id}': Count mismatch"
    assert retrieved_starts_naive == expected_starts_naive, f"Test ID '{test_id}': Start time mismatch" 