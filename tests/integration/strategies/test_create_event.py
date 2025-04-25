import pytest
from datetime import datetime

from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.event.date_utils import DateUtils # Dependency of CreateEventStrategy
from sa.event.conflict_resolver import ConflictResolver # Dependency of CreateEventStrategy
# Remove unused import as the fixture handles it
# from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.db.dao import EventCreate
from sa.db.models import Event


@pytest.mark.asyncio
async def test_create_event_simple(
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    # get_events_in_range_strategy: GetEventsInRangeStrategy, # Removed fixture parameter
    create_event_strategy: CreateEventStrategy, # Need the fixture itself now
):
    """Test creating a simple event works."""
    # --- Arrange ---
    # Use the fixture directly instead of manual instantiation
    strategy = create_event_strategy
    event_data = EventCreate(
        title="Simple Create Test Event",
        start_time=datetime(2025, 3, 1, 10, 0, 0),
        end_time=datetime(2025, 3, 1, 11, 0, 0),
        time_zone="UTC",
    )

    # --- Act ---
    created_event = await strategy.execute(data=event_data)

    # --- Assert ---
    # Basic check: Ensure the strategy returned an Event object with an ID
    assert isinstance(created_event, Event)
    assert created_event.id is not None
    assert created_event.title == "Simple Create Test Event"
