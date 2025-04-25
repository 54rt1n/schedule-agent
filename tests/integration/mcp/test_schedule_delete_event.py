import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio

from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from sa.db.models import Event
from sa.db.service import DatabaseService
from sa.db.dao import EventCreate
from sa.mcp.handlers import schedule_delete_event
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy

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
    event_to_delete: Event,
    mcp_test_db_service: DatabaseService,
):
    """Test successful deletion of an existing event."""
    event_id_to_delete = str(event_to_delete.id)
    event_id_uuid = event_to_delete.id

    # Instantiate strategy for assertions
    get_event_strategy = GetEventByIdStrategy(db_service=mcp_test_db_service)

    # Pre-check: Ensure event exists using the strategy
    db_event = await get_event_strategy.execute(data=event_id_uuid)
    assert db_event is not None

    # Action
    result = await schedule_delete_event(
        event_id=event_id_to_delete,
        db_service=mcp_test_db_service
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("event_id") == event_id_to_delete

    # Assert Database State using the strategy
    db_event_after_delete = await get_event_strategy.execute(data=event_id_uuid)
    assert db_event_after_delete is None


@pytest.mark.asyncio
async def test_schedule_delete_event_not_found(
    mcp_test_db_service: DatabaseService,
):
    """Test attempting to delete a non-existent event."""
    non_existent_event_id = str(uuid.uuid4())

    # Action
    result = await schedule_delete_event(
        event_id=non_existent_event_id,
        db_service=mcp_test_db_service
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert result.get("error") is not None
    # Make assertion less strict
    assert "not found" in result["error"].lower()
    assert non_existent_event_id in result["error"]
