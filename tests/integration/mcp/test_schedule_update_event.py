# tests/integration/mcp/test_schedule_update_event.py

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.db.strategies.create_event import CreateEventStrategy
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_core.db.strategies.get_event_by_id import GetEventByIdStrategy
from schedule_core.db.models import Event
from schedule_core.db.service import DatabaseService
from schedule_core.db.dao import EventCreate, EventGetById
from schedule_core.core.exceptions import (
    EventNotFoundError, ConflictError, DatabaseError
)



@pytest_asyncio.fixture(scope="function")
async def existing_event(
    mcp_test_db_service: DatabaseService,
    mcp_test_date_utils: DateUtils, # Add dependency
    mcp_test_conflict_resolver: ConflictResolver, # Add dependency
) -> Event:
    """Fixture to create a pre-existing event in the test DB using CreateEventStrategy."""
    now = datetime.now(timezone.utc)
    event_data = EventCreate(
        title="Existing Event for Update",
        start_time=now + timedelta(days=2),
        end_time=now + timedelta(days=2, hours=1),
        time_zone="Europe/London",
        location="Original Location"
    )
    # Use the strategy to create the event
    get_events_strategy = GetEventsInRangeStrategy(mcp_test_db_service, mcp_test_date_utils)
    create_strategy = CreateEventStrategy(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils,
        conflict_resolver=mcp_test_conflict_resolver,
        get_events_in_range_strategy=get_events_strategy,
    )
    created_event = await create_strategy.execute(data=event_data)
    assert created_event is not None, "Fixture failed to create event"
    assert created_event.id is not None
    return created_event

@pytest.mark.asyncio
async def test_schedule_update_event_success(
    mcp_call, existing_event: Event,
    mcp_test_db_service: DatabaseService,
    mcp_test_conflict_resolver: ConflictResolver,
    mcp_test_date_utils: DateUtils,
):
    """Test successful update of an existing event."""
    new_title = "Updated MCP Event Title"
    new_location = "New Conference Room"
    event_id_str = str(existing_event.id)
    event_id_uuid = existing_event.id # Keep UUID form

    # Only include fields that are actually being updated
    update_data = {
        "id": event_id_str,
        "title": new_title,
        "location": new_location,
        # "start_time": existing_event.start_time.isoformat(), # DO NOT SEND if not changing
        # "end_time": existing_event.end_time.isoformat(),   # DO NOT SEND if not changing
        # "time_zone": existing_event.time_zone,             # DO NOT SEND if not changing
        # "notes": existing_event.notes, # Only include if updating
        # "rrule": existing_event.rrule, # Only include if updating
    }

    # Action - Use schedule_update_event handler
    result = await mcp_call('update_event', {'id': update_data['id'], 'patch': {k: v for k, v in update_data.items() if k != 'id'}})

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("event_id") == event_id_str
    assert "event" in result # Handler should return the updated event object
    updated_event_dict = result["event"]
    assert updated_event_dict["id"] == event_id_str
    assert updated_event_dict["title"] == new_title
    assert updated_event_dict["location"] == new_location
    # Check that time hasn't changed (remove tzinfo for comparison)
    original_start_naive = existing_event.start_time # DB stores naive UTC
    updated_start_naive = datetime.fromisoformat(updated_event_dict["start_time"]).replace(tzinfo=None)
    assert updated_start_naive == original_start_naive

    # Assert Database State using the strategy
    get_event_strategy = GetEventByIdStrategy(db_service=mcp_test_db_service)
    db_event = await get_event_strategy.execute(data=EventGetById(id=event_id_uuid))
    assert db_event is not None
    assert db_event.id == existing_event.id
    assert db_event.title == new_title
    assert db_event.location == new_location
    assert db_event.start_time == original_start_naive # Check time unchanged in DB
    assert db_event.time_zone == existing_event.time_zone # Check timezone unchanged

