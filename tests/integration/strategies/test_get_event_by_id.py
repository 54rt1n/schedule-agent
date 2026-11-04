import pytest
from datetime import datetime
import uuid

from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.get_event_by_id import GetEventByIdStrategy
from schedule_core.db.strategies.create_event import CreateEventStrategy # Needed for setup
from schedule_core.db.dao import EventCreate, EventGetById
from schedule_core.db.models import Event


@pytest.mark.asyncio
async def test_get_event_by_id_simple(
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
):
    """Test retrieving an existing event by ID."""
    # --- Arrange ---
    # Create an event to retrieve
    event_title = f"Get Event Test {uuid.uuid4()}"
    event_to_create = EventCreate(
        title=event_title,
        start_time=datetime(2025, 5, 1, 10, 0, 0),
        end_time=datetime(2025, 5, 1, 11, 0, 0),
        time_zone="UTC",
    )
    created_event = await create_event_strategy.execute(data=event_to_create)
    assert created_event is not None
    event_id_to_find = created_event.id

    strategy = GetEventByIdStrategy(db_service=db_service)

    # --- Act ---
    retrieved_event = await strategy.execute(data=EventGetById(id=event_id_to_find))

    # --- Assert ---
    assert retrieved_event is not None
    assert isinstance(retrieved_event, Event)
    assert retrieved_event.id == event_id_to_find
    assert retrieved_event.title == event_title

    # Test retrieving non-existent returns None
    non_existent_id = uuid.uuid4()
    retrieved_none = await strategy.execute(data=EventGetById(id=non_existent_id))
    assert retrieved_none is None 