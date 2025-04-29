# tests/integration/mcp/test_schedule_query_events.py

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

# Function to test
from sa.mcp.handlers import schedule_query_events

# DB Model and Service for setup
from sa.db.service import DatabaseService
from sa.db.dao import EventCreate
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from sa.db.models import Event


@pytest.mark.asyncio
async def test_schedule_query_events_success_no_results(
    mcp_test_db_service: DatabaseService,
    mcp_test_date_utils: DateUtils, # Added date_utils fixture
):
    """Test querying events when no events exist."""
    params = {
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        "title_contains": None,
    }

    # Action
    result = await schedule_query_events(
        **params,
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils, # Pass date_utils
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 0
    assert result.get("events") == []


@pytest_asyncio.fixture(scope="function")
async def queried_event(mcp_test_db_service: DatabaseService, mcp_test_date_utils: DateUtils, mcp_test_conflict_resolver: ConflictResolver):
    """Fixture to create an event for querying."""
    now = datetime.now(timezone.utc)
    event_data = EventCreate(
        title="Query Test Event Fixture",
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=2),
        time_zone="UTC",
    )
    get_events_in_range_strategy = GetEventsInRangeStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
    )
    strategy = CreateEventStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
        conflict_resolver=mcp_test_conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy,
    )
    created_event = await strategy.execute(data=event_data)
    assert created_event is not None, "Fixture failed to create event"
    assert created_event.id is not None
    # Return the created Event model instance and the time
    return created_event, now


@pytest.mark.asyncio
async def test_schedule_query_events_success_with_results(
    mcp_test_db_service: DatabaseService,
    mcp_test_date_utils: DateUtils, # Added date_utils fixture
    queried_event: tuple[Event, datetime], # Added type hint
):
    """Test querying events when matching events exist."""
    created_event, creation_time = queried_event

    params = {
        # Query range starting slightly before the event
        "start_time": (created_event.start_time - timedelta(minutes=5)).isoformat(),
        # Query range ending slightly after the event
        "end_time": (created_event.end_time + timedelta(minutes=5)).isoformat(),
        "title_contains": "Query Test", # Filter by title
    }

    # Action
    result = await schedule_query_events(
        **params,
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils, # Pass date_utils
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 1
    assert len(result.get("events", [])) == 1
    returned_event = result["events"][0]
    # The ID should match the created event's ID
    assert returned_event["id"] == str(created_event.id) 
    assert returned_event["title"] == created_event.title
    # The returned event is an *instance*, it should not have an rrule
    assert returned_event.get("rrule") is None 

# TODO: Add test for querying with title_contains filter resulting in no matches
# TODO: Add test for querying with time range filter resulting in no matches
# TODO: Add test for invalid time format


@pytest.mark.asyncio
async def test_schedule_query_events_returns_recurring_instance( # Renamed: test now expects success
    mcp_test_db_service: DatabaseService,
    mcp_test_date_utils: DateUtils,
    mcp_test_conflict_resolver: ConflictResolver,
):
    """
    GREEN Test: This test SHOULD now pass.
    Tests that querying a time range returns instances of recurring events
    that fall within that range, even if the base event's start time is outside it.
    """
    # Setup: Create a daily event starting yesterday, ending today
    now = datetime.now(timezone.utc)
    yesterday_start = now.replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_end = yesterday_start + timedelta(hours=1)
    # Daily rule, occurs yesterday and today
    rrule = "FREQ=DAILY;COUNT=2" 

    # 1. Create the recurring event base
    event_data = EventCreate(
        title="Daily Recurring Meeting",
        start_time=yesterday_start,
        end_time=yesterday_end,
        time_zone="UTC",
        rrule=rrule,
    )
    # Need CreateEventStrategy dependencies
    get_events_in_range_strategy = GetEventsInRangeStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
    )
    create_strategy = CreateEventStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
        conflict_resolver=mcp_test_conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy,
    )
    created_event = await create_strategy.execute(data=event_data)
    assert created_event is not None, "Fixture failed to create recurring event"

    # 2. Define query range covering ONLY today's instance
    today_query_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_query_end = today_query_start + timedelta(days=1)

    query_params = {
        "start_time": today_query_start.isoformat(),
        "end_time": today_query_end.isoformat(),
        "title_contains": None,
    }

    # 3. Action: Query for events today
    result = await schedule_query_events(
        **query_params,
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils # Pass date_utils
    )

    # 4. Assert: Expecting SUCCESS (count == 1) now
    assert result is not None
    assert result.get("success") is True
    
    # --- UNCOMMENTED GREEN Assertions ---
    # These assertions should pass *after* the fix is implemented.
    assert result.get("count") == 1, "Recurring instance should be found after fix"
    assert len(result.get("events", [])) == 1, "One recurring instance should be returned after fix"
    returned_event = result["events"][0]
    assert returned_event["title"] == "Daily Recurring Meeting"
    # Check if the returned instance start time is today
    expected_instance_start = yesterday_start + timedelta(days=1)
    # Parse the ISO string back to an aware datetime object for comparison
    returned_start_str = returned_event["start_time"]
    # More robust parsing: parse, then ensure UTC
    parsed_dt = datetime.fromisoformat(returned_start_str.replace("Z", "+00:00"))
    if parsed_dt.tzinfo is None:
        returned_start = parsed_dt.replace(tzinfo=timezone.utc)
    else:
        returned_start = parsed_dt.astimezone(timezone.utc)
    
    assert returned_start.tzinfo is not None, "Parsed returned start time MUST be timezone-aware UTC"

    # Explicitly normalize both to UTC for robust comparison (Expected is already UTC)
    expected_instance_start_utc = expected_instance_start.astimezone(timezone.utc)
    
    assert returned_start == expected_instance_start_utc, f"Returned instance start time ({returned_start}) does not match expected ({expected_instance_start_utc})"
    # Instances returned by GetEventsInRangeStrategy should have rrule=None
    assert returned_event.get("rrule") is None, "Returned instance should not have an rrule" 
    # --- End UNCOMMENTED GREEN Assertions ---

# TODO: Add test for title_contains filtering on instances
@pytest.mark.asyncio
async def test_schedule_query_events_recurring_with_title_filter(
    mcp_test_db_service: DatabaseService,
    mcp_test_date_utils: DateUtils,
    mcp_test_conflict_resolver: ConflictResolver,
):
    """Tests title filtering works correctly on returned instances."""
    # Setup: Create two daily events starting yesterday
    now = datetime.now(timezone.utc)
    yesterday_start1 = now.replace(hour=11, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_end1 = yesterday_start1 + timedelta(hours=1)
    yesterday_start2 = now.replace(hour=14, minute=0, second=0, microsecond=0) - timedelta(days=1)
    yesterday_end2 = yesterday_start2 + timedelta(hours=1)
    rrule = "FREQ=DAILY;COUNT=2" 

    get_events_in_range_strategy = GetEventsInRangeStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
    )
    create_strategy = CreateEventStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
        conflict_resolver=mcp_test_conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy,
    )
    # Event 1
    await create_strategy.execute(data=EventCreate(
        title="Daily Standup Alpha", start_time=yesterday_start1,
        end_time=yesterday_end1, time_zone="UTC", rrule=rrule))
    # Event 2
    await create_strategy.execute(data=EventCreate(
        title="Daily Standup Beta", start_time=yesterday_start2,
        end_time=yesterday_end2, time_zone="UTC", rrule=rrule))

    # Query range covering today's instances
    today_query_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_query_end = today_query_start + timedelta(days=1)

    # Action: Query for events today with title filter
    result = await schedule_query_events(
        start_time=today_query_start.isoformat(),
        end_time=today_query_end.isoformat(),
        title_contains="alpha", # Case-insensitive filter
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
    )

    # Assert: Only the 'Alpha' instance should be returned
    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 1
    assert len(result.get("events", [])) == 1
    returned_event = result["events"][0]
    assert returned_event["title"] == "Daily Standup Alpha"
