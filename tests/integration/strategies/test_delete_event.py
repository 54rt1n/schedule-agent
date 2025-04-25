import pytest
from datetime import datetime
import uuid

from sa.db.service import DatabaseService
from sa.db.strategies.delete_event import DeleteEventStrategy
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_event_by_id import GetEventByIdStrategy # To verify deletion
from sa.db.dao import EventDelete, EventCreate
from sa.db.models import Event


@pytest.mark.asyncio
async def test_delete_event_simple(
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
    get_event_by_id_strategy: GetEventByIdStrategy,
):
    """Test deleting an event using the strategy."""
    # --- Arrange ---
    # Create an event to delete
    event_to_delete = await create_event_strategy.execute(data=EventCreate(
        title="Event To Be Deleted Soon",
        start_time=datetime(2025, 2, 1, 10, 0, 0),
        end_time=datetime(2025, 2, 1, 11, 0, 0),
        time_zone="UTC",
    ))
    assert event_to_delete is not None
    assert event_to_delete.id is not None
    event_id = event_to_delete.id

    delete_strategy = DeleteEventStrategy(db_service=db_service)
    # Pass the UUID directly
    delete_data = event_id

    # --- Act ---
    deleted = await delete_strategy.execute(data=delete_data)

    # --- Assert ---
    assert deleted is True # Strategy now returns bool
    # Verify the event is actually gone by trying to fetch it
    fetched_event = await get_event_by_id_strategy.execute(data=event_id)
    assert fetched_event is None
