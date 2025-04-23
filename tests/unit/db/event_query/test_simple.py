from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from scheduling_agent.db.schemas import EventCreate
from scheduling_agent.db.service import DatabaseService

# Tests for simple event queries (get_events_in_range)

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


# Define a fixed query range for boundary tests for simplicity
# S = Start (inclusive), E = End (exclusive)
QUERY_RANGE_START = datetime(2024, 10, 15, 10, 0, 0, tzinfo=UTC)
QUERY_RANGE_END = datetime(2024, 10, 15, 11, 0, 0, tzinfo=UTC) # 1 hour duration

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("test_id", "event_start_offset", "event_end_offset", "expected_count", "expected_title_part"),
    [
        # Case 1: Fully Before (Ends before S)
        ("simple_before", timedelta(hours=-2), timedelta(hours=-1), 0, None),
        # Case 2: Overlap Start (Starts before S, Ends inside (S, E))
        ("simple_overlap_start", timedelta(hours=-1), timedelta(minutes=30), 1, "Overlap Start"),
        # Case 3: Fully Inside (Starts inside (S, E), Ends inside (S, E))
        ("simple_fully_inside", timedelta(minutes=10), timedelta(minutes=50), 1, "Fully Inside"),
        # Case 4: Overlap End (Starts inside (S, E), Ends after E)
        ("simple_overlap_end", timedelta(minutes=30), timedelta(hours=1, minutes=30), 1, "Overlap End"),
        # Case 5: Contains Range (Starts before S, Ends after E)
        ("simple_contains", timedelta(hours=-1), timedelta(hours=2), 1, "Contains Range"),
        # Case 6: Fully After (Starts after E)
        ("simple_after", timedelta(hours=1, minutes=10), timedelta(hours=2), 0, None),
        # Case 7: Starts Exactly At Range Start (S)
        ("simple_starts_at_S", timedelta(hours=0), timedelta(minutes=30), 1, "Starts At S"),
        # Case 8: Ends Exactly At Range Start (S) - Should NOT be included as event interval is (start, end] vs query [S, E)
        ("simple_ends_at_S", timedelta(hours=-1), timedelta(hours=0), 0, "Ends At S"),
        # Case 9: Starts Exactly At Range End (E) - Should NOT be included as query interval is [S, E)
        ("simple_starts_at_E", timedelta(hours=1), timedelta(hours=2), 0, "Starts At E"),
        # Case 10: Ends Exactly At Range End (E) - Should be included as event overlaps [S, E)
        ("simple_ends_at_E", timedelta(minutes=30), timedelta(hours=1), 1, "Ends At E"),
    ],
    ids=[
        "case1_simple_before",
        "case2_simple_overlap_start",
        "case3_simple_fully_inside",
        "case4_simple_overlap_end",
        "case5_simple_contains",
        "case6_simple_after",
        "case7_simple_starts_at_S",
        "case8_simple_ends_at_S",
        "case9_simple_starts_at_E",
        "case10_simple_ends_at_E",
    ]
)
async def test_get_events_in_range_simple_boundaries(
    db_service: DatabaseService,
    test_id: str,
    event_start_offset: timedelta,
    event_end_offset: timedelta,
    expected_count: int,
    expected_title_part: str | None,
):
    """Test get_events_in_range for simple events against various boundary conditions."""
    # --- Arrange ---
    event_title = f"Event {test_id}"
    event_start = QUERY_RANGE_START + event_start_offset
    event_end = QUERY_RANGE_START + event_end_offset

    # Ensure start/end are aware UTC datetimes
    event_start = event_start.replace(tzinfo=UTC) if event_start.tzinfo is None else event_start.astimezone(UTC)
    event_end = event_end.replace(tzinfo=UTC) if event_end.tzinfo is None else event_end.astimezone(UTC)

    event_data = EventCreate(
        title=event_title,
        start_time=event_start,
        end_time=event_end,
        time_zone="UTC", # Using UTC for simplicity in boundary tests
    )
    created_event = await db_service.create_event(event_data=event_data)
    assert created_event is not None
    assert created_event.title == event_title

    # --- Act ---
    retrieved_events = await db_service.get_events_in_range(
        start=QUERY_RANGE_START, end=QUERY_RANGE_END
    )

    # --- Assert ---
    assert len(retrieved_events) == expected_count, (
        f"Test ID '{test_id}': Expected {expected_count} events, but got {len(retrieved_events)}. "
        f"Event: [{event_start.isoformat()}, {event_end.isoformat()}), "
        f"Query: [{QUERY_RANGE_START.isoformat()}, {QUERY_RANGE_END.isoformat()})"
    )

    if expected_count == 1:
        assert retrieved_events[0].title == event_title, (
            f"Test ID '{test_id}': Incorrect event returned. Expected title '{event_title}', "
            f"got '{retrieved_events[0].title}'."
        )
    elif expected_count > 0:
        # Optional: Add more specific checks if multiple events were expected for a test case
        retrieved_titles = sorted([e.title for e in retrieved_events])
        # assert retrieved_titles == expected_titles # Define expected_titles if needed
        pass 