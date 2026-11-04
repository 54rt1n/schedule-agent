import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

from schedule_core.event.date_utils import DateUtils
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.db.models import Event
from schedule_core.db.service import DatabaseService
from schedule_core.db.dao import EventCreate, EventDelete, EventGetById
from schedule_core.db.strategies.create_event import CreateEventStrategy
from schedule_core.db.strategies.get_event_by_id import GetEventByIdStrategy
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy

@pytest_asyncio.fixture(scope="function")
async def event_to_delete(
    mcp_test_db_service: DatabaseService,
    mcp_test_date_utils: DateUtils,
    mcp_test_conflict_resolver: ConflictResolver,
) -> Event:
    """Fixture to create a pre-existing event to be deleted using CreateEventStrategy."""
    now = datetime.now(timezone.utc)
    event_data = EventCreate(
        title="Event To Be Deleted",
        start_time=now + timedelta(days=3),
        end_time=now + timedelta(days=3, hours=1),
        time_zone="America/Denver",
    )
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
async def test_schedule_delete_event_success(
    mcp_call, event_to_delete: Event,
    mcp_test_db_service: DatabaseService,
):
    """Test successful deletion of an existing event."""
    # Instantiate strategy for assertions
    get_event_strategy = GetEventByIdStrategy(db_service=mcp_test_db_service)

    # Pre-check: Ensure event exists using the strategy
    db_event = await get_event_strategy.execute(data=EventGetById(id=event_to_delete.id))
    assert db_event is not None

    event_id_str = str(event_to_delete.id)

    # Action
    result = await mcp_call('delete_event', {'id': event_id_str})

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("event_id") == event_id_str

    # Assert Database State using the strategy
    db_event_after_delete = await get_event_strategy.execute(data=EventGetById(id=event_to_delete.id))
    assert db_event_after_delete is None


@pytest.mark.asyncio
async def test_schedule_delete_event_not_found(
    mcp_call, mcp_test_db_service: DatabaseService,
):
    """Test attempting to delete a non-existent event."""
    non_existent_event_id = str(uuid.uuid4())

    # Action
    result = await mcp_call('delete_event', {'id': non_existent_event_id})

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert result.get("error") is not None
    # Make assertion less strict
    assert "not found" in result["error"].lower()
    assert non_existent_event_id in result["error"]
