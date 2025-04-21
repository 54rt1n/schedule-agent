# import uuid # Unused
from datetime import UTC, datetime, timedelta
# from unittest.mock import AsyncMock # Unused

import pytest # Keep for parametrize
# import pytest_asyncio # Implicitly used
from sqlalchemy.ext.asyncio import AsyncSession
# from sqlmodel import select # Unused

# Remove comment about conftest
from scheduling_agent.db.schemas import EventCreate
from scheduling_agent.db.service import DatabaseService

# Tests for querying events (get_events_in_range)

@pytest.mark.asyncio
async def test_get_events_in_range_simple(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving simple, non-recurring events within a date range."""
    # --- Arrange ---
    evt1_start = datetime(2024, 8, 15, 10, 0, 0)
    evt1_end = datetime(2024, 8, 15, 11, 0, 0)
    evt2_start = datetime(2024, 8, 20, 14, 0, 0)
    evt2_end = datetime(2024, 8, 20, 15, 0, 0)
    evt3_start = datetime(2024, 8, 10, 12, 0, 0)
    evt3_end = datetime(2024, 8, 10, 13, 0, 0)
    evt4_start = datetime(2024, 8, 25, 16, 0, 0)
    evt4_end = datetime(2024, 8, 25, 17, 0, 0)
    evt5_start = datetime(2024, 8, 14, 23, 0, 0)
    evt5_end = datetime(2024, 8, 15, 1, 0, 0)
    evt6_start = datetime(2024, 8, 20, 23, 0, 0)
    evt6_end = datetime(2024, 8, 21, 1, 0, 0)
    evt7_start = datetime(2024, 8, 12, 10, 0, 0)
    evt7_end = datetime(2024, 8, 22, 10, 0, 0)

    events_data = [
        EventCreate(title="Evt1", start_time=evt1_start, end_time=evt1_end, time_zone="UTC"),
        EventCreate(title="Evt2", start_time=evt2_start, end_time=evt2_end, time_zone="UTC"),
        EventCreate(title="Evt3", start_time=evt3_start, end_time=evt3_end, time_zone="UTC"),
        EventCreate(title="Evt4", start_time=evt4_start, end_time=evt4_end, time_zone="UTC"),
        EventCreate(title="Evt5", start_time=evt5_start, end_time=evt5_end, time_zone="UTC"),
        EventCreate(title="Evt6", start_time=evt6_start, end_time=evt6_end, time_zone="UTC"),
        EventCreate(title="Evt7", start_time=evt7_start, end_time=evt7_end, time_zone="UTC"),
    ]
    for data in events_data:
        await db_service.create_event(event_data=data)

    range_start = datetime(2024, 8, 15, 0, 0, 0)
    range_end = datetime(2024, 8, 21, 0, 0, 0)

    # --- Act ---
    range_start_aware = range_start.replace(tzinfo=UTC)
    range_end_aware = range_end.replace(tzinfo=UTC)
    retrieved_events = await db_service.get_events_in_range(
        start=range_start_aware, end=range_end_aware
    )

    # --- Assert ---
    assert len(retrieved_events) == 5
    retrieved_titles = sorted([evt.title for evt in retrieved_events])
    assert retrieved_titles == sorted(["Evt1", "Evt2", "Evt5", "Evt6", "Evt7"])


@pytest.mark.asyncio
async def test_get_events_in_range_recurring(
    db_service: DatabaseService, test_session: AsyncSession
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
    base_event = await db_service.create_event(event_data=base_event_data)
    assert base_event.id is not None

    range_start = datetime(2024, 9, 10, 0, 0, 0)
    range_end = datetime(2024, 9, 20, 0, 0, 0)

    # --- Act ---
    range_start_aware = range_start.replace(tzinfo=UTC)
    range_end_aware = range_end.replace(tzinfo=UTC)
    retrieved_events = await db_service.get_events_in_range(
        start=range_start_aware, end=range_end_aware
    )

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
    test_session: AsyncSession, # Keep test_session if used for verification inside test
    tz_name: str,
    event_local_start: datetime,
    rrule_str: str,
    query_range_start_utc: datetime,
    query_range_end_utc: datetime,
    expected_naive_utc_starts: list[datetime],
):
    """Test recurrence expansion across DST transitions within DatabaseService."""
    # Arrange
    event_duration = timedelta(hours=1)
    event_local_end = event_local_start + event_duration
    base_event_data = EventCreate(
        title=f"DST Integration {tz_name}",
        start_time=event_local_start,
        end_time=event_local_end,
        time_zone=tz_name,
        rrule=rrule_str,
    )
    created_base_event = await db_service.create_event(event_data=base_event_data)
    assert created_base_event is not None

    # Act
    retrieved_event_instances = await db_service.get_events_in_range(
        start=query_range_start_utc, end=query_range_end_utc
    )

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


@pytest.mark.asyncio
async def test_get_events_in_range_empty(db_service: DatabaseService):
    """Test get_events_in_range returns empty list when no events are in range."""
    # --- Arrange ---
    range_start_aware = datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC)
    range_end_aware = datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    retrieved_events = await db_service.get_events_in_range(
        start=range_start_aware, end=range_end_aware
    )

    # --- Assert ---
    assert retrieved_events == []


@pytest.mark.asyncio
async def test_get_events_in_range_recurring_outside_range(db_service: DatabaseService):
    """Test that a recurring event entirely outside the range is not returned."""
    # --- Arrange ---
    event_create_data = EventCreate(
        title="Far Future Daily Meeting",
        start_time=datetime(2025, 1, 1, 10, 0, 0),
        end_time=datetime(2025, 1, 1, 11, 0, 0),
        time_zone="UTC",
        rrule="FREQ=DAILY;COUNT=5",
    )
    await db_service.create_event(event_data=event_create_data)

    query_start = datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2024, 12, 31, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    events = await db_service.get_events_in_range(query_start, query_end)

    # --- Assert ---
    assert not events 