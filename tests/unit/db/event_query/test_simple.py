from datetime import UTC, datetime, timedelta
import pytest
from typing import Dict, Any

from sa.core.logging_config import get_logger
from sa.db.models import Event
from sa.db.dao import EventCreate, TimeRange
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy

logger = get_logger(__name__)

# Helper function adapted for integration tests
async def create_test_event_for_query(
    db_service: DatabaseService,
    data: dict,
    date_utils: DateUtils
) -> Event:
    """Helper to create event for query tests using db_service."""
    start_naive_local = data.get("start_time")
    end_naive_local = data.get("end_time")
    tz_name = data.get("time_zone", "UTC")
    if not start_naive_local or not end_naive_local: raise ValueError("start/end required")

    # Convert input times (assumed naive local) to naive UTC for storage
    start_aware_utc = date_utils.normalize_to_utc(start_naive_local, tz_name=tz_name)
    end_aware_utc = date_utils.normalize_to_utc(end_naive_local, tz_name=tz_name)
    start_naive_utc = start_aware_utc.replace(tzinfo=None)
    end_naive_utc = end_aware_utc.replace(tzinfo=None)

    event_data_for_model = data.copy()
    event_data_for_model["start_time"] = start_naive_utc
    event_data_for_model["end_time"] = end_naive_utc
    event_data_for_model["time_zone"] = tz_name

    # Import Event model here to avoid potential top-level circular issues
    event = Event(**event_data_for_model)

    # Use db_service session manager to add and commit
    async with db_service.get_session() as session:
        session.add(event)
        await session.commit() # Commit within the helper session
        await session.refresh(event) # Refresh to get ID etc.
    return event

@pytest.mark.asyncio
async def test_get_events_in_range_simple(
    db_service: DatabaseService,
    date_utils: DateUtils,
    initialize_db: None
):
    """Test retrieving simple, non-recurring events using the strategy."""
    # --- Arrange ---
    evt1_start = datetime(2024, 8, 15, 10, 0, 0, tzinfo=UTC)
    evt1_end = datetime(2024, 8, 15, 11, 0, 0, tzinfo=UTC)
    evt2_start = datetime(2024, 8, 20, 14, 0, 0, tzinfo=UTC)
    evt2_end = datetime(2024, 8, 20, 15, 0, 0, tzinfo=UTC)
    evt3_start = datetime(2024, 8, 10, 12, 0, 0, tzinfo=UTC)
    evt3_end = datetime(2024, 8, 10, 13, 0, 0, tzinfo=UTC)
    evt4_start = datetime(2024, 8, 25, 16, 0, 0, tzinfo=UTC)
    evt4_end = datetime(2024, 8, 25, 17, 0, 0, tzinfo=UTC)
    evt5_start = datetime(2024, 8, 14, 23, 0, 0, tzinfo=UTC)
    evt5_end = datetime(2024, 8, 15, 1, 0, 0, tzinfo=UTC)
    evt6_start = datetime(2024, 8, 20, 23, 0, 0, tzinfo=UTC)
    evt6_end = datetime(2024, 8, 21, 1, 0, 0, tzinfo=UTC)
    evt7_start = datetime(2024, 8, 12, 10, 0, 0, tzinfo=UTC)
    evt7_end = datetime(2024, 8, 22, 10, 0, 0, tzinfo=UTC)

    events_data = [
        EventCreate(title="Evt1", start_time=evt1_start.replace(tzinfo=None), end_time=evt1_end.replace(tzinfo=None), time_zone="UTC"),
        EventCreate(title="Evt2", start_time=evt2_start.replace(tzinfo=None), end_time=evt2_end.replace(tzinfo=None), time_zone="UTC"),
        EventCreate(title="Evt3", start_time=evt3_start.replace(tzinfo=None), end_time=evt3_end.replace(tzinfo=None), time_zone="UTC"),
        EventCreate(title="Evt4", start_time=evt4_start.replace(tzinfo=None), end_time=evt4_end.replace(tzinfo=None), time_zone="UTC"),
        EventCreate(title="Evt5", start_time=evt5_start.replace(tzinfo=None), end_time=evt5_end.replace(tzinfo=None), time_zone="UTC"),
        EventCreate(title="Evt6", start_time=evt6_start.replace(tzinfo=None), end_time=evt6_end.replace(tzinfo=None), time_zone="UTC"),
        EventCreate(title="Evt7", start_time=evt7_start.replace(tzinfo=None), end_time=evt7_end.replace(tzinfo=None), time_zone="UTC"),
    ]
    # Create events using the helper and real db_service
    for data_obj in events_data:
        # Convert EventCreate to dict before passing to helper
        await create_test_event_for_query(db_service, data_obj.model_dump(), date_utils)

    range_start = datetime(2024, 8, 15, 0, 0, 0)
    range_end = datetime(2024, 8, 21, 0, 0, 0)
    range_start_aware = range_start.replace(tzinfo=UTC)
    range_end_aware = range_end.replace(tzinfo=UTC)

    # Instantiate the strategy using the real db_service
    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=range_start_aware, end_time=range_end_aware)

    # --- Act ---
    # Execute the strategy
    retrieved_events = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert len(retrieved_events) == 5
    retrieved_titles = sorted([evt.title for evt in retrieved_events])
    assert retrieved_titles == sorted(["Evt1", "Evt2", "Evt5", "Evt6", "Evt7"])

@pytest.mark.asyncio
async def test_get_events_in_range_empty(
    db_service: DatabaseService,
    date_utils: DateUtils,
    initialize_db: None
):
    """Test strategy returns empty list when no events are in range."""
    # --- Arrange ---
    range_start_aware = datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC)
    range_end_aware = datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC)

    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=range_start_aware, end_time=range_end_aware)

    # --- Act ---
    retrieved_events = await strategy.execute(data=strategy_input)

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
    date_utils: DateUtils,
    initialize_db: None,
    test_id: str,
    event_start_offset: timedelta,
    event_end_offset: timedelta,
    expected_count: int,
    expected_title_part: str | None,
):
    """Test GetEventsInRangeStrategy for simple event boundaries."""
    # --- Arrange ---
    event_title = f"Event {test_id}"
    event_start = QUERY_RANGE_START + event_start_offset
    event_end = QUERY_RANGE_START + event_end_offset

    # Ensure start/end are naive UTC for EventCreate via helper
    event_start_naive = event_start.replace(tzinfo=None)
    event_end_naive = event_end.replace(tzinfo=None)

    event_data = {
        "title": event_title,
        "start_time": event_start_naive,
        "end_time": event_end_naive,
        "time_zone": "UTC",
    }
    # Create event using helper
    created_event = await create_test_event_for_query(db_service, event_data, date_utils)
    # Commit happened inside helper
    assert created_event is not None
    assert created_event.title == event_title

    strategy = GetEventsInRangeStrategy(
        db_service=db_service,
        date_utils=date_utils
    )
    strategy_input = TimeRange(start_time=QUERY_RANGE_START, end_time=QUERY_RANGE_END)

    # --- Act ---
    retrieved_events = await strategy.execute(data=strategy_input)

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