import pytest
from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
# Import DB model from .models and DTO schema from .schemas
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate
# Mock the DatabaseService for these unit tests
from scheduling_agent.db.service import DatabaseService


@pytest.fixture
def date_utils() -> DateUtils:
    return DateUtils()

@pytest.fixture
def mock_db_service() -> MagicMock:
    """Fixture providing a mock DatabaseService."""
    mock = MagicMock(spec=DatabaseService)
    # Mock the async methods used by the resolver
    mock.get_events_in_range = AsyncMock()
    # Add mocks for other methods as needed (e.g., get_event_by_id if used)
    return mock

@pytest.fixture
def conflict_resolver(mock_db_service: MagicMock, date_utils: DateUtils) -> ConflictResolver:
    """Fixture providing a ConflictResolver with mocked DB service."""
    return ConflictResolver(db_service=mock_db_service, date_utils=date_utils)

# --- Test Cases ---

@pytest.mark.asyncio
async def test_detect_simple_overlap(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test detecting a direct overlap between two non-recurring events."""
    # --- Arrange ---
    # Proposed event
    proposed_start = datetime(2024, 10, 10, 14, 0, 0, tzinfo=timezone.utc)
    proposed_end = datetime(2024, 10, 10, 15, 0, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Proposed Meeting",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Existing event that overlaps
    existing_start = datetime(2024, 10, 10, 14, 30, 0, tzinfo=timezone.utc)
    existing_end = datetime(2024, 10, 10, 15, 30, 0, tzinfo=timezone.utc)
    existing_event = Event(
        id=uuid.uuid4(),
        title="Existing Booking",
        start_time=existing_start,
        end_time=existing_end,
        time_zone="UTC",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )

    # Configure mock DB service to return the existing event
    mock_db_service.get_events_in_range.return_value = [existing_event]

    # --- Act ---
    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    # --- Assert ---
    # Verify get_events_in_range was called correctly
    # The resolver should query a slightly wider range to catch overlaps
    mock_db_service.get_events_in_range.assert_awaited_once()
    call_args = mock_db_service.get_events_in_range.call_args
    assert call_args.kwargs['start'] == proposed_start
    assert call_args.kwargs['end'] == proposed_end

    # Check the results
    assert len(conflicts) == 1
    assert conflicts[0]['event'].id == existing_event.id
    assert conflicts[0]['event'].title == existing_event.title
    # The conflict structure might evolve, but check basic identification

@pytest.mark.asyncio
async def test_detect_no_overlap(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test conflict detection when there is no overlap."""
     # --- Arrange ---
    proposed_start = datetime(2024, 10, 10, 14, 0, 0)
    proposed_end = datetime(2024, 10, 10, 15, 0, 0)
    proposed_event = EventCreate(
        title="Proposed Meeting",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Existing event that does NOT overlap
    existing_start = datetime(2024, 10, 10, 15, 30, 0)
    existing_end = datetime(2024, 10, 10, 16, 30, 0)
    existing_event = Event(
        id=uuid.uuid4(),
        title="Existing Booking",
        start_time=existing_start,
        end_time=existing_end,
        time_zone="UTC",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )

    # Configure mock DB service
    mock_db_service.get_events_in_range.return_value = [existing_event]

    # --- Act ---
    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    # --- Assert ---
    assert len(conflicts) == 0
    mock_db_service.get_events_in_range.assert_awaited_once()

@pytest.mark.asyncio
async def test_detect_recurring_overlap(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test detecting overlap with an instance of a recurring event."""
    # --- Arrange ---
    # Proposed event
    proposed_start = datetime(2024, 5, 14, 11, 15, 0) # Overlaps with 2nd instance
    proposed_end = datetime(2024, 5, 14, 11, 45, 0)
    proposed_event = EventCreate(
        title="Quick Sync",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Existing recurring event instance (as returned by get_events_in_range)
    # Represents the May 14th occurrence of a weekly Tuesday event
    recurring_instance_start = datetime(2024, 5, 14, 11, 0, 0)
    recurring_instance_end = datetime(2024, 5, 14, 11, 30, 0)
    recurring_instance = Event(
        id=uuid.uuid4(), # Could be base event ID or specific instance ID
        title="Weekly Recurring",
        start_time=recurring_instance_start,
        end_time=recurring_instance_end,
        time_zone="UTC",
        # Note: rrule would be None for an expanded instance representation
        rrule=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )

    # Configure mock DB service to return this instance
    mock_db_service.get_events_in_range.return_value = [recurring_instance]

    # --- Act ---
    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    # --- Assert ---
    mock_db_service.get_events_in_range.assert_awaited_once()
    assert len(conflicts) == 1
    assert conflicts[0]['event'].id == recurring_instance.id
    assert conflicts[0]['event'].title == recurring_instance.title

# TODO: Add tests for:
# - Conflicts involving recurring events (requires expansion)
# - Conflicts involving modified instances
# - Conflicts ignoring excluded dates
# - Pattern conflict detection (recurring vs recurring)
# - Alternative time generation

# --- Tests for Alternative Time Generation Helpers ---

def test_merge_overlapping_periods(conflict_resolver: ConflictResolver):
    """Test the helper function for merging busy time periods."""
    dt = lambda h, m: datetime(2024, 1, 1, h, m, 0, tzinfo=timezone.utc)

    # Test case 1: Basic overlap
    periods1 = [(dt(9, 0), dt(10, 0)), (dt(9, 30), dt(10, 30))]
    merged1 = conflict_resolver._merge_overlapping_periods(periods1)
    assert merged1 == [(dt(9, 0), dt(10, 30))]

    # Test case 2: Adjacent periods
    periods2 = [(dt(9, 0), dt(10, 0)), (dt(10, 0), dt(11, 0))]
    merged2 = conflict_resolver._merge_overlapping_periods(periods2)
    assert merged2 == [(dt(9, 0), dt(11, 0))]

    # Test case 3: Non-overlapping periods
    periods3 = [(dt(9, 0), dt(10, 0)), (dt(11, 0), dt(12, 0))]
    merged3 = conflict_resolver._merge_overlapping_periods(periods3)
    assert merged3 == [(dt(9, 0), dt(10, 0)), (dt(11, 0), dt(12, 0))]

    # Test case 4: Multiple overlaps
    periods4 = [(dt(9, 0), dt(10, 0)), (dt(11, 0), dt(12, 0)), (dt(9, 30), dt(11, 30))]
    merged4 = conflict_resolver._merge_overlapping_periods(periods4)
    assert merged4 == [(dt(9, 0), dt(12, 0))]

    # Test case 5: Empty list
    periods5 = []
    merged5 = conflict_resolver._merge_overlapping_periods(periods5)
    assert merged5 == []

    # Test case 6: Single period
    periods6 = [(dt(14, 0), dt(15, 0))]
    merged6 = conflict_resolver._merge_overlapping_periods(periods6)
    assert merged6 == [(dt(14, 0), dt(15, 0))]


# --- More Alternative Time Generation Tests (using pytest parametrize might be good here) ---
# TODO: Add tests for _find_next_available_slot_same_day
# TODO: Add tests for _find_next_business_day_slot
# TODO: Add tests for _find_available_slots
# TODO: Add tests for the main generate_alternative_times orchestrator method

# --- Fixtures/Constants for Alt Time Tests ---
@pytest.fixture
def default_duration() -> timedelta:
    return timedelta(hours=1)

@pytest.fixture
def default_business_hours() -> tuple[time, time]:
    return time(9, 0), time(17, 0)

@pytest.fixture
def default_business_days() -> list[int]:
    return [0, 1, 2, 3, 4] # Mon-Fri

# --- Test _find_next_available_slot_same_day ---

def test_find_slot_same_day_available(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test finding an available slot later on the same day."""
    tz_str = "America/New_York"
    local_tz = ZoneInfo(tz_str)
    # Monday, 10:00 AM NY time
    start_dt_utc = datetime(2024, 5, 13, 14, 0, 0, tzinfo=timezone.utc)
    # Busy from 11 AM to 12 PM NY time (15:00 to 16:00 UTC)
    busy_periods = [
        (datetime(2024, 5, 13, 15, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 13, 16, 0, 0, tzinfo=timezone.utc))
    ]

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_available_slot_same_day(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )

    assert result is not None
    # Expected: 12 PM NY time (16:00 UTC)
    expected_utc = datetime(2024, 5, 13, 16, 0, 0, tzinfo=timezone.utc)
    assert result == expected_utc
    # Check it's within business hours in local time
    result_local = result.astimezone(local_tz)
    assert result_local.time() >= bh_start
    assert (result_local + default_duration).time() <= bh_end

def test_find_slot_same_day_none_available(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test when no slot is available on the same day due to busy schedule."""
    tz_str = "America/New_York"
    local_tz = ZoneInfo(tz_str)
    # Monday, 4:30 PM NY time (past business hours for a 1hr meeting)
    start_dt_utc = datetime(2024, 5, 13, 20, 30, 0, tzinfo=timezone.utc)
    busy_periods = [
        (datetime(2024, 5, 13, 13, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 13, 21, 0, 0, tzinfo=timezone.utc)) # Busy 9am-5pm NY
    ]

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_available_slot_same_day(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )
    assert result is None

def test_find_slot_same_day_not_business_day(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test when the starting day is not a business day."""
    tz_str = "America/New_York"
    # Saturday, 10:00 AM NY time
    start_dt_utc = datetime(2024, 5, 18, 14, 0, 0, tzinfo=timezone.utc)
    busy_periods = [] # No conflicts needed

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_available_slot_same_day(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )
    assert result is None

# --- Test _find_next_business_day_slot ---

def test_find_slot_next_business_day_available(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test finding the same time slot on the next business day."""
    tz_str = "America/New_York"
    # Monday 10:00 AM NY (14:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 14, 0, 0, tzinfo=timezone.utc)
    busy_periods = [] # Assume next day is free at that time

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_business_day_slot(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )

    assert result is not None
    # Expected: Tuesday 10:00 AM NY (14:00 UTC)
    expected_utc = datetime(2024, 5, 14, 14, 0, 0, tzinfo=timezone.utc)
    assert result == expected_utc

def test_find_slot_next_business_day_skip_weekend(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test finding the next slot when the next day is a weekend."""
    tz_str = "America/New_York"
    # Friday 3:00 PM NY (19:00 UTC)
    start_dt_utc = datetime(2024, 5, 17, 19, 0, 0, tzinfo=timezone.utc)
    busy_periods = [] # Assume Monday is free

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_business_day_slot(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )

    assert result is not None
    # Expected: Monday 3:00 PM NY (19:00 UTC)
    expected_utc = datetime(2024, 5, 20, 19, 0, 0, tzinfo=timezone.utc)
    assert result == expected_utc

def test_find_slot_next_business_day_busy(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test when the same slot is busy on the next few business days."""
    tz_str = "America/New_York"
    # Monday 10:00 AM NY (14:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 14, 0, 0, tzinfo=timezone.utc)
    # Busy at 10 AM NY time on Tue, Wed, Thu
    busy_periods = [
        (datetime(2024, 5, 14, 14, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 14, 15, 0, 0, tzinfo=timezone.utc)),
        (datetime(2024, 5, 15, 14, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 15, 15, 0, 0, tzinfo=timezone.utc)),
        (datetime(2024, 5, 16, 14, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 16, 15, 0, 0, tzinfo=timezone.utc)),
    ]

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_business_day_slot(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )

    assert result is not None
    # Expected: Friday 10:00 AM NY (14:00 UTC)
    expected_utc = datetime(2024, 5, 17, 14, 0, 0, tzinfo=timezone.utc)
    assert result == expected_utc

def test_find_slot_next_business_day_none_available(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test when the same slot is busy for the entire search window."""
    tz_str = "America/New_York"
    # Monday 10:00 AM NY (14:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 14, 0, 0, tzinfo=timezone.utc)
    # Busy at 10 AM for the next 14 days
    busy_periods = []
    for i in range(1, 15):
        day_dt = start_dt_utc + timedelta(days=i)
        if day_dt.weekday() < 5: # Only add for weekdays
             busy_periods.append((day_dt, day_dt + default_duration))

    bh_start, bh_end = default_business_hours
    result = conflict_resolver._find_next_business_day_slot(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str
    )

    assert result is None 

# --- Test _find_available_slots ---

def test_find_available_slots_finds_multiple(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test finding multiple available slots when the next day is mostly free."""
    tz_str = "America/New_York"
    # Monday 4:00 PM NY (20:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 20, 0, 0, tzinfo=timezone.utc)
    # Busy Tuesday 11am-12pm NY (15:00-16:00 UTC)
    busy_periods = [
        (datetime(2024, 5, 14, 15, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 14, 16, 0, 0, tzinfo=timezone.utc))
    ]
    max_slots_to_find = 3
    max_days = 5

    bh_start, bh_end = default_business_hours
    results = conflict_resolver._find_available_slots(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str,
        max_slots=max_slots_to_find, max_days_ahead=max_days
    )

    assert len(results) == max_slots_to_find
    # Expected slots on Tuesday (UTC): 9am(13:00), 10am(14:00), 12pm(16:00)
    expected_slots_utc = [
        datetime(2024, 5, 14, 13, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 14, 14, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 14, 16, 0, 0, tzinfo=timezone.utc),
    ]
    # Results should be sorted already by the function
    assert results == expected_slots_utc

def test_find_available_slots_limited_availability(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test finding slots when availability is limited."""
    tz_str = "America/New_York"
    # Monday 4:00 PM NY (20:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 20, 0, 0, tzinfo=timezone.utc)
    # Busy Tuesday 9am-4pm NY (13:00-20:00 UTC)
    busy_periods = [
        (datetime(2024, 5, 14, 13, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 14, 20, 0, 0, tzinfo=timezone.utc))
    ]
    max_slots_to_find = 3
    max_days = 5

    bh_start, bh_end = default_business_hours
    results = conflict_resolver._find_available_slots(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str,
        max_slots=max_slots_to_find, max_days_ahead=max_days
    )

    # Only one slot available on Tuesday: 4 PM NY (20:00 UTC)
    # Then slots on Wednesday: 9 AM NY (13:00 UTC), 10 AM NY (14:00 UTC)
    assert len(results) == max_slots_to_find
    expected_slots_utc = [
        datetime(2024, 5, 14, 20, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 15, 13, 0, 0, tzinfo=timezone.utc),
        datetime(2024, 5, 15, 14, 0, 0, tzinfo=timezone.utc),
    ]
    assert results == expected_slots_utc

def test_find_available_slots_none_available(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration, default_business_hours, default_business_days):
    """Test finding slots when the search window is fully booked."""
    tz_str = "America/New_York"
    # Monday 4:00 PM NY (20:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 20, 0, 0, tzinfo=timezone.utc)
    # Busy Tue 9am-5pm, Wed 9am-5pm
    busy_periods = [
        (datetime(2024, 5, 14, 13, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 14, 21, 0, 0, tzinfo=timezone.utc)),
        (datetime(2024, 5, 15, 13, 0, 0, tzinfo=timezone.utc), datetime(2024, 5, 15, 21, 0, 0, tzinfo=timezone.utc)),
    ]
    max_slots_to_find = 3
    max_days = 2 # Only check Tue/Wed

    bh_start, bh_end = default_business_hours
    results = conflict_resolver._find_available_slots(
        start_dt_utc, default_duration, busy_periods,
        bh_start, bh_end, default_business_days, tz_str,
        max_slots=max_slots_to_find, max_days_ahead=max_days
    )

    assert len(results) == 0 

# --- Test generate_alternative_times Orchestrator ---

@pytest.mark.asyncio
async def test_generate_alternatives_all_strategies(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration):
    """Test generating alternatives using all three strategies."""
    tz_str = "America/Denver"
    # Monday 10:00 AM Denver (16:00 UTC)
    start_dt_utc = datetime(2024, 5, 13, 16, 0, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Conflict Event",
        start_time=start_dt_utc, # Provide aware datetime
        end_time=start_dt_utc + default_duration,
        time_zone=tz_str
    )
    # Assume some conflicts were detected (details don't matter for this test)
    conflicts = [{"event": MagicMock()}]

    # Mock the internal methods to return specific results
    conflict_resolver._get_busy_periods = AsyncMock(return_value=[]) # Assume no busy periods for simplicity
    # Strategy 1: Find later same day -> 1 PM Denver (19:00 UTC)
    slot1 = datetime(2024, 5, 13, 19, 0, 0, tzinfo=timezone.utc)
    conflict_resolver._find_next_available_slot_same_day = MagicMock(return_value=slot1)
    # Strategy 2: Find next business day -> Tue 10 AM Denver (16:00 UTC)
    slot2 = datetime(2024, 5, 14, 16, 0, 0, tzinfo=timezone.utc)
    conflict_resolver._find_next_business_day_slot = MagicMock(return_value=slot2)
    # Strategy 3: Find available slots -> Wed 9 AM Denver (15:00 UTC)
    slot3 = datetime(2024, 5, 15, 15, 0, 0, tzinfo=timezone.utc)
    conflict_resolver._find_available_slots = MagicMock(return_value=[slot3])

    alternatives = await conflict_resolver.generate_alternative_times(proposed_event, conflicts)

    assert len(alternatives) == 3
    # Check labels and times (times should be UTC ISO strings)
    assert alternatives[0]["label"] == "Later today at 1:00 PM"
    assert alternatives[0]["startTime"] == slot1.isoformat()
    assert alternatives[1]["label"] == "Same time on Tuesday, May 14"
    assert alternatives[1]["startTime"] == slot2.isoformat()
    assert alternatives[2]["label"] == "Wednesday, May 15 at 9:00 AM"
    assert alternatives[2]["startTime"] == slot3.isoformat()
    assert alternatives[0]["timeZone"] == tz_str

@pytest.mark.asyncio
async def test_generate_alternatives_limited_results(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration):
    """Test generating alternatives when some strategies yield no results."""
    tz_str = "UTC"
    start_dt_utc = datetime(2024, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Conflict Event",
        start_time=start_dt_utc,
        end_time=start_dt_utc + default_duration,
        time_zone=tz_str
    )
    conflicts = [{"event": MagicMock()}]

    # Mock internal methods
    conflict_resolver._get_busy_periods = AsyncMock(return_value=[])
    conflict_resolver._find_next_available_slot_same_day = MagicMock(return_value=None) # No slot same day
    slot2 = datetime(2024, 5, 14, 10, 0, 0, tzinfo=timezone.utc) # Next day available
    conflict_resolver._find_next_business_day_slot = MagicMock(return_value=slot2)
    conflict_resolver._find_available_slots = MagicMock(return_value=[]) # No other slots found

    alternatives = await conflict_resolver.generate_alternative_times(proposed_event, conflicts)

    assert len(alternatives) == 1
    assert alternatives[0]["startTime"] == slot2.isoformat()
    # Label format might vary slightly based on locale/strftime directives, test existence and basic content
    assert "Tuesday, May 14" in alternatives[0]["label"]

@pytest.mark.asyncio
async def test_generate_alternatives_error_fetching_busy(conflict_resolver: ConflictResolver, date_utils: DateUtils, default_duration):
    """Test graceful handling when fetching busy periods fails."""
    tz_str = "UTC"
    start_dt_utc = datetime(2024, 5, 13, 10, 0, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Conflict Event",
        start_time=start_dt_utc,
        end_time=start_dt_utc + default_duration,
        time_zone=tz_str
    )
    conflicts = [{"event": MagicMock()}]

    # Mock _get_busy_periods to raise an exception
    conflict_resolver._get_busy_periods = AsyncMock(side_effect=Exception("DB error"))

    alternatives = await conflict_resolver.generate_alternative_times(proposed_event, conflicts)

    # Should return an empty list and log an error (logging check is harder in unit test)
    assert alternatives == [] 

@pytest.mark.asyncio
async def test_generate_alternatives_schedule_full(
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    default_duration
):
    """Test generating alternatives when the schedule is fully booked."""
    tz_str = "Europe/Berlin"
    # Tuesday 11:00 AM Berlin (09:00 UTC)
    start_dt_utc = datetime(2024, 6, 18, 9, 0, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Impossible Event",
        start_time=start_dt_utc,
        end_time=start_dt_utc + default_duration,
        time_zone=tz_str
    )
    conflicts = [{"event": MagicMock()}] # Assume conflicts detected

    # Mock _get_busy_periods to return a block covering a long time
    very_busy_start = start_dt_utc - timedelta(days=1)
    very_busy_end = start_dt_utc + timedelta(days=30) # Cover search window
    conflict_resolver._get_busy_periods = AsyncMock(return_value=[(very_busy_start, very_busy_end)])

    # Mock the helper find methods to ensure they respect the busy period
    conflict_resolver._find_next_available_slot_same_day = MagicMock(return_value=None)
    conflict_resolver._find_next_business_day_slot = MagicMock(return_value=None)
    conflict_resolver._find_available_slots = MagicMock(return_value=[])

    alternatives = await conflict_resolver.generate_alternative_times(proposed_event, conflicts)

    # Assert: No alternatives should be found
    assert alternatives == []
    # Verify busy periods were fetched
    conflict_resolver._get_busy_periods.assert_awaited_once()
    # Verify find helpers were called (they should be, even if they return None/[])
    conflict_resolver._find_next_available_slot_same_day.assert_called_once()
    conflict_resolver._find_next_business_day_slot.assert_called_once()
    conflict_resolver._find_available_slots.assert_called_once()

@pytest.mark.asyncio
async def test_generate_alternatives_non_standard_hours(
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    default_duration,
    # We don't need default_business_hours/days fixtures here
):
    """Test generating alternatives with non-standard business hours/days."""
    tz_str = "Asia/Tokyo"
    # Tuesday 10:00 AM JST (01:00 UTC)
    start_dt_utc = datetime(2024, 6, 18, 1, 0, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Late Shift Meeting",
        start_time=start_dt_utc,
        end_time=start_dt_utc + default_duration,
        time_zone=tz_str
    )
    conflicts = [{"event": MagicMock()}]

    # Define non-standard hours/days
    non_standard_hours = (time(10, 0), time(16, 0)) # 10am - 4pm
    non_standard_days = [1, 2, 3, 4, 5] # Tue - Sat

    # Mock _get_busy_periods to return empty
    conflict_resolver._get_busy_periods = AsyncMock(return_value=[])

    # Mock the find helpers to return dummy values
    dummy_slot = datetime(2024, 6, 19, 1, 0, 0, tzinfo=timezone.utc) # Dummy Wed 10am JST
    conflict_resolver._find_next_available_slot_same_day = MagicMock(return_value=None)
    conflict_resolver._find_next_business_day_slot = MagicMock(return_value=dummy_slot)
    conflict_resolver._find_available_slots = MagicMock(return_value=[])

    # Act: Call the actual function and let it use its internal defaults
    alternatives = await conflict_resolver.generate_alternative_times(proposed_event, conflicts)

    # Assert that the find methods were called with the *default* hours/days
    # This verifies the current state, highlighting the need for refactoring.
    # Define expected default values used internally by the function
    expected_default_bh_start = time(9, 0)
    expected_default_bh_end = time(17, 0)
    expected_default_biz_days = [0, 1, 2, 3, 4] # Mon-Fri

    conflict_resolver._find_next_available_slot_same_day.assert_called_once_with(
        start_dt_utc, default_duration, [],
        expected_default_bh_start, expected_default_bh_end, expected_default_biz_days, tz_str
    )
    conflict_resolver._find_next_business_day_slot.assert_called_once_with(
        start_dt_utc, default_duration, [],
        expected_default_bh_start, expected_default_bh_end, expected_default_biz_days, tz_str
    )
    conflict_resolver._find_available_slots.assert_called_once_with(
        start_dt_utc, default_duration, [],
        expected_default_bh_start, expected_default_bh_end, expected_default_biz_days, tz_str,
        max_slots=2, max_days_ahead=14 # Check max_slots logic (3 - 1 found)
    )


# --- Conflict Detection Edge Cases ---

@pytest.mark.asyncio
async def test_detect_conflict_with_specific_recurring_instance(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test conflict with a specific instance returned by get_events_in_range."""
    # Proposed event: Tue 11:15-11:45 UTC
    proposed_start = datetime(2024, 5, 14, 11, 15, 0, tzinfo=timezone.utc)
    proposed_end = datetime(2024, 5, 14, 11, 45, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Quick Sync",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Mock get_events_in_range returns the specific conflicting instance
    # Represents the Tue 11:00-11:30 UTC occurrence of a recurring event
    recurring_instance = Event(
        id=uuid.uuid4(),
        title="Weekly Recurring",
        start_time=datetime(2024, 5, 14, 11, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 5, 14, 11, 30, 0, tzinfo=timezone.utc),
        time_zone="UTC",
        rrule=None, # Important: represents an expanded instance
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    mock_db_service.get_events_in_range.return_value = [recurring_instance]

    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    assert len(conflicts) == 1
    assert conflicts[0]['event'].id == recurring_instance.id
    assert conflicts[0].get('type') == 'instance' # Check type explicitly
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=proposed_event.start_time, end=proposed_event.end_time
    )

@pytest.mark.asyncio
async def test_no_conflict_due_to_exclusion(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test that no conflict is detected if the overlapping instance was excluded."""
    # Proposed event: Tue 11:15-11:45 UTC
    proposed_start = datetime(2024, 5, 14, 11, 15, 0, tzinfo=timezone.utc)
    proposed_end = datetime(2024, 5, 14, 11, 45, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Quick Sync",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Mock get_events_in_range returns an empty list because the potential
    # conflict at 11:00-11:30 on May 14th was excluded by the DB service.
    mock_db_service.get_events_in_range.return_value = []

    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    assert len(conflicts) == 0
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=proposed_event.start_time, end=proposed_event.end_time
    )

@pytest.mark.asyncio
async def test_conflict_with_modified_instance(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test conflict with an instance that was modified to overlap."""
    # Proposed event: Tue 15:30-16:30 UTC
    proposed_start = datetime(2024, 5, 14, 15, 30, 0, tzinfo=timezone.utc)
    proposed_end = datetime(2024, 5, 14, 16, 30, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Afternoon Session",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Mock get_events_in_range returns a modified instance that now conflicts
    # Original might have been 14:00-15:00, but modified to 16:00-17:00 UTC
    modified_instance = Event(
        id=uuid.uuid4(), # This might be the ID of the modified_instances table row
        title="Modified Weekly",
        start_time=datetime(2024, 5, 14, 16, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 5, 14, 17, 0, 0, tzinfo=timezone.utc),
        time_zone="UTC",
        rrule=None, # Represents a specific instance
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
        # Could potentially include original_date if needed by resolver, but current resolver doesn't use it
    )
    mock_db_service.get_events_in_range.return_value = [modified_instance]

    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    assert len(conflicts) == 1
    assert conflicts[0]['event'].id == modified_instance.id
    assert conflicts[0].get('type') == 'instance' # Check type explicitly
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=proposed_event.start_time, end=proposed_event.end_time
    )

@pytest.mark.asyncio
async def test_no_conflict_with_original_of_modified(conflict_resolver: ConflictResolver, mock_db_service: MagicMock):
    """Test no conflict if an instance's original time overlaps, but it was modified."""
    # Proposed event: Tue 14:30-15:30 UTC (overlaps original time)
    proposed_start = datetime(2024, 5, 14, 14, 30, 0, tzinfo=timezone.utc)
    proposed_end = datetime(2024, 5, 14, 15, 30, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Midday Meeting",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC"
    )

    # Mock get_events_in_range returns nothing, because the instance that *would*
    # have occurred at 14:00-15:00 was modified (e.g., moved to 16:00).
    # The modified instance itself doesn't overlap the proposed time.
    mock_db_service.get_events_in_range.return_value = []

    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    assert len(conflicts) == 0
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=proposed_event.start_time, end=proposed_event.end_time
    )

@pytest.mark.asyncio
async def test_pattern_conflict_detected_daily(conflict_resolver: ConflictResolver, mock_db_service: MagicMock, default_duration):
    """Test detecting a pattern conflict with a daily recurring event."""
    # Proposed event: Daily @ 11:15 UTC
    proposed_start = datetime(2024, 5, 14, 11, 15, 0, tzinfo=timezone.utc)
    proposed_end = datetime(2024, 5, 14, 11, 45, 0, tzinfo=timezone.utc)
    proposed_event = EventCreate(
        title="Quick Sync Daily",
        start_time=proposed_start,
        end_time=proposed_end,
        time_zone="UTC",
        rrule="FREQ=DAILY" # Mark proposed as recurring
    )

    # Mock get_events_in_range returns a daily recurring event definition that overlaps
    daily_event_id = uuid.uuid4()
    daily_event = Event(
        id=daily_event_id,
        title="Daily Recurring Conflict",
        start_time=datetime(2024, 5, 14, 11, 0, 0, tzinfo=timezone.utc),
        end_time=datetime(2024, 5, 14, 11, 30, 0, tzinfo=timezone.utc),
        time_zone="UTC",
        rrule="FREQ=DAILY", # Mark existing as recurring
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    # IMPORTANT: Mock DB returns the *definition* for pattern check
    mock_db_service.get_events_in_range.return_value = [daily_event]

    # Mock the date_utils expansion to simulate many overlaps
    def mock_expand(*args, **kwargs):
        start = args[0]
        # Return many dates for the daily rules
        return [start + timedelta(days=i) for i in range(180)]
    # Ensure the mock is applied to the specific instance for this test
    conflict_resolver.date_utils.expand_recurrence = MagicMock(side_effect=mock_expand)

    conflicts = await conflict_resolver.detect_conflicts(proposed_event)

    assert len(conflicts) == 1
    assert conflicts[0]['event'].id == daily_event_id
    # The logic first finds an instance conflict for the first occurrence, then upgrades it
    assert conflicts[0]['type'] == 'pattern' # Verify it was upgraded
    assert 'pattern_overlap_count' in conflicts[0]
    # Check count against default threshold (3). Many overlaps expected.
    assert conflicts[0]['pattern_overlap_count'] >= 3
    # Verify expand_recurrence was called by _is_pattern_conflict
    assert conflict_resolver.date_utils.expand_recurrence.call_count >= 2