# tests/integration/mcp/test_schedule_create_event.py

import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock
import logging
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.conflict_resolver import ConflictResolver

# Function to test
from scheduling_agent.mcp.handlers import schedule_create_event
from scheduling_agent.db.schemas import EventRead, EventCreate
from scheduling_agent.core.exceptions import ConflictError, DatabaseError

# DB Model for assertions
from scheduling_agent.db.models import Event

# Import root conftest fixtures if needed (test_session_factory)
# The service patching fixture in mcp/conftest.py is applied automatically


@pytest.mark.asyncio
async def test_schedule_create_event_success(
    test_session_factory: sessionmaker,
    mcp_test_db_service: DatabaseService,
    mcp_test_conflict_resolver: ConflictResolver,
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
    }

    mock_logger = MagicMock(spec=logging.Logger)

    # Action: Call the MCP handler function directly, passing dependencies
    result = await schedule_create_event(
        **params,
        rrule=None, # Explicitly pass None for optional arg
        logger=mock_logger,
        db_service=mcp_test_db_service,
        conflict_resolver=mcp_test_conflict_resolver
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert "event_id" in result
    event_id_str = result["event_id"]
    assert isinstance(uuid.UUID(event_id_str), uuid.UUID) # Check valid UUID string
    assert "event" in result
    assert result["event"]["title"] == params["title"]
    # Remove timezone for comparison as DB stores naive UTC
    assert datetime.fromisoformat(result["event"]["start_time"]).replace(tzinfo=None) == start_time.replace(tzinfo=None)
    assert result["event"]["location"] == params["location"]

    # Assert Database State
    event_id = uuid.UUID(event_id_str)
    async with test_session_factory() as session:
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

# TODO: Add tests for create conflict and error cases. 