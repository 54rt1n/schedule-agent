import pytest
from datetime import datetime
import uuid

from sa.db.service import DatabaseService
from sa.db.strategies.update_event import UpdateEventStrategy
from sa.db.strategies.create_event import CreateEventStrategy # Needed for setup
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy # Needed for verification
from sa.event.date_utils import DateUtils # Dependency of UpdateEventStrategy
from sa.event.conflict_resolver import ConflictResolver # Dependency of UpdateEventStrategy
from sa.db.dao import EventCreate, EventUpdate
from sa.db.models import Event


@pytest.mark.asyncio
async def test_update_event_simple(
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
    get_event_by_id_strategy: GetEventByIdStrategy,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
):
    """Test updating a simple event's title."""
    # --- Arrange ---
    # Create an event to update
    event_to_update = await create_event_strategy.execute(data=EventCreate(
        title="Original Title",
        start_time=datetime(2025, 4, 1, 10, 0, 0),
        end_time=datetime(2025, 4, 1, 11, 0, 0),
        time_zone="UTC",
    ))
    assert event_to_update is not None
    event_id = event_to_update.id
    new_title = f"Updated Title {uuid.uuid4()}"

    update_strategy = UpdateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver
    )
    update_payload = EventUpdate(title=new_title)
    strategy_input = (event_id, update_payload)

    # --- Act ---
    # The strategy returns the updated Event or ModifiedInstance
    updated_event_result = await update_strategy.execute(data=strategy_input)

    # --- Assert ---
    assert isinstance(updated_event_result, Event) # Simple update affects Event
    assert updated_event_result.id == event_id
    assert updated_event_result.title == new_title

    # Verify the change in the database
    verified_event = await get_event_by_id_strategy.execute(data=event_id)
    assert verified_event is not None
    assert verified_event.title == new_title
