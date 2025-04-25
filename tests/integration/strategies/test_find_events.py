
from datetime import datetime
import pytest

from sa.core.logging_config import get_logger
from sa.db.strategies.find_events import FindEventsStrategy
from sa.db.service import DatabaseService
from sa.db.dao import EventQuery, EventResponse, EventCreate
from sa.db.strategies.create_event import CreateEventStrategy

logger = get_logger(__name__)

@pytest.mark.asyncio
async def test_find_events_no_criteria(
    db_service: DatabaseService,
    create_event_strategy: CreateEventStrategy,
):
    """Test finding events with no specific criteria (should return all mocked)."""
    query_criteria = EventQuery()
    await create_event_strategy.execute(data=EventCreate(
        title="Test Event 1",
        start_time=datetime(2024, 1, 1, 10, 0, 0),
        end_time=datetime(2024, 1, 1, 11, 0, 0),
        time_zone="UTC",
    ))
    await create_event_strategy.execute(data=EventCreate(
        title="Test Event 2",
        start_time=datetime(2024, 1, 1, 11, 0, 0),
        end_time=datetime(2024, 1, 1, 12, 0, 0),
        time_zone="UTC",
    ))

    strategy = FindEventsStrategy(db_service=db_service)

    # --- Act ---
    results = await strategy.execute(data=query_criteria)

    # --- Assert ---
    assert len(results) == 2
