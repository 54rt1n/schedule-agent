# tests/integration/mcp/test_schedule_create_event.py

import uuid
from datetime import datetime, timezone, timedelta
import pytest

from schedule_core.event.date_utils import DateUtils
from schedule_core.db.models import Event
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.db.service import DatabaseService


@pytest.mark.asyncio
async def test_schedule_create_event_success(
    mcp_call, mcp_test_db_service: DatabaseService,
    mcp_test_conflict_resolver: ConflictResolver,
    mcp_test_date_utils: DateUtils,
):
    """Test successful creation of a new event via schedule_create_event."""
    start_time = datetime.now(timezone.utc) + timedelta(days=1)
    end_time = start_time + timedelta(hours=1)

    params = {
        "title": "MCP Create Test Event", # Unique title
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "time_zone": "UTC",
        "location": "MCP Test Location",
        "notes": "Integration test via MCP handler for create.",
        "participant_identifiers": ["mcp-create-test@example.com"],
        "rrule": None,
    }

    result = await mcp_call('create_event', {'event': {'kind': 'timed', **params}})

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert "event_id" in result
    event_id_str = result["event_id"]
    assert isinstance(uuid.UUID(event_id_str), uuid.UUID)
    assert "event" in result
    assert result["event"]["title"] == params["title"]
    # Remove timezone for comparison as DB stores naive UTC
    assert datetime.fromisoformat(result["event"]["start_time"]).replace(tzinfo=None) == start_time.replace(tzinfo=None)
    assert result["event"]["location"] == params["location"]

    # Assert Database State
    event_id = uuid.UUID(event_id_str)
    async with mcp_test_db_service.get_session() as session:
        db_event = await session.get(Event, event_id)
        assert db_event is not None
        assert db_event.id == event_id
        assert db_event.title == params["title"]
        # Compare naive UTC times from DB
        assert db_event.start_time == start_time.replace(tzinfo=None)
        assert db_event.end_time == end_time.replace(tzinfo=None)
        assert db_event.time_zone == params["time_zone"]
        assert db_event.location == params["location"]
        assert db_event.notes == params["notes"]
        # Participant check might require joining relationship or separate query
