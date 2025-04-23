from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scheduling_agent.db.schemas import EventCreate
from scheduling_agent.db.service import DatabaseService

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
    await db_service.create_event(EventCreate(
        title=event_title,
        start_time=base_start_utc.replace(tzinfo=None),
        end_time=base_end.replace(tzinfo=None),
        time_zone="UTC",
        rrule=rrule,
    ))

    # --- Act ---
    retrieved_events = await db_service.get_events_in_range(query_start_utc, query_end_utc)

    # --- Assert ---
    expected_starts_naive = sorted([dt.replace(tzinfo=None) for dt in expected_starts_utc])
    retrieved_starts_naive = sorted([e.start_time for e in retrieved_events])

    assert len(retrieved_events) == len(expected_starts_naive), f"Test ID '{test_id}': Count mismatch"
    assert retrieved_starts_naive == expected_starts_naive, f"Test ID '{test_id}': Start time mismatch" 