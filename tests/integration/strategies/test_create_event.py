import pytest
from datetime import datetime

from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.create_event import CreateEventStrategy
from schedule_core.event.date_utils import DateUtils # Dependency of CreateEventStrategy
from schedule_core.event.conflict_resolver import ConflictResolver # Dependency of CreateEventStrategy
# Remove unused import as the fixture handles it
# from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_core.db.dao import EventCreate
from schedule_core.db.models import Event


@pytest.mark.asyncio
async def test_create_event_simple(
    create_event_strategy: CreateEventStrategy,
):
    """Test creating a simple event works."""
    # --- Arrange ---
    # Use the fixture directly instead of manual instantiation
    event_data = EventCreate(
        title="Simple Create Test Event",
        start_time=datetime(2025, 3, 1, 10, 0, 0),
        end_time=datetime(2025, 3, 1, 11, 0, 0),
        time_zone="UTC",
    )

    # --- Act ---
    created_event = await create_event_strategy.execute(data=event_data)

    # --- Assert ---
    # Basic check: Ensure the strategy returned an Event object with an ID
    assert isinstance(created_event, Event)
    assert created_event.id is not None
    assert created_event.title == "Simple Create Test Event"
