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


@pytest.mark.asyncio
async def test_schedule_query_events_success_no_results(
    mcp_test_db_service: DatabaseService,
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
        db_service=mcp_test_db_service
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
    return created_event, now


@pytest.mark.asyncio
async def test_schedule_query_events_success_with_results(
    mcp_test_db_service: DatabaseService,
    queried_event,
):
    """Test querying events when matching events exist."""
    created_event, creation_time = queried_event

    params = {
        "start_time": creation_time.isoformat(),
        "end_time": (creation_time + timedelta(days=1)).isoformat(),
        "title_contains": "Query Test",
    }

    # Action
    result = await schedule_query_events(
        **params,
        db_service=mcp_test_db_service
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 1
    assert len(result.get("events", [])) == 1
    returned_event = result["events"][0]
    assert returned_event["id"] == str(created_event.id)
    assert returned_event["title"] == created_event.title

# TODO: Add test for querying with title_contains filter resulting in no matches
# TODO: Add test for querying with time range filter resulting in no matches
# TODO: Add test for invalid time format
