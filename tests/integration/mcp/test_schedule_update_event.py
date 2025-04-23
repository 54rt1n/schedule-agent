# tests/integration/mcp/test_schedule_update_event.py

import uuid
from datetime import datetime, timezone, timedelta

import pytest
import pytest_asyncio # Import pytest_asyncio
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock # Import MagicMock
import logging # Import logging for type hints
from scheduling_agent.core.conflict_resolver import ConflictResolver # Import for type hints

# Function to test
from scheduling_agent.mcp.handlers import schedule_update_event

# DB Model and Service for setup/assertions
from scheduling_agent.db.models import Event as DBEvent
from scheduling_agent.db.service import DatabaseService # Needed for setup
from scheduling_agent.db.schemas import EventCreate, EventUpdate # Needed for setup
from scheduling_agent.core.exceptions import (
    EventNotFoundError, ConflictError, DatabaseError
)

# Import root conftest fixtures if needed (test_session_factory)
# The service patching fixture in mcp/conftest.py is applied automatically


@pytest_asyncio.fixture(scope="function")
async def existing_event(mcp_test_db_service: DatabaseService) -> DBEvent:
    """Fixture to create a pre-existing event in the test DB."""
    now = datetime.now(timezone.utc)
    event_data = EventCreate(
        title="Existing Event for Update",
        start_time=now + timedelta(days=2),
        end_time=now + timedelta(days=2, hours=1),
        time_zone="Europe/London",
        location="Original Location"
    )
    created_event = await mcp_test_db_service.create_event(event_data=event_data)
    return created_event

@pytest.mark.asyncio
async def test_schedule_update_event_success(
    test_session_factory: sessionmaker,
    existing_event: DBEvent,
    mcp_test_db_service: DatabaseService, # Added fixture
    mcp_test_conflict_resolver: ConflictResolver, # Added fixture
):
    """Test successful update of an existing event."""
    new_title = "Updated MCP Event Title"
    new_location = "New Conference Room"

    update_data = {
        "title": new_title,
        "location": new_location,
        # Not updating time in this test
    }

    params = {
        "event_id": str(existing_event.id),
        "update_data": update_data,
    }

    mock_logger = MagicMock(spec=logging.Logger) # Create mock logger

    # Action
    result = await schedule_update_event(
        **params,
        logger=mock_logger, # Pass mock logger
        db_service=mcp_test_db_service, # Pass fixture
        conflict_resolver=mcp_test_conflict_resolver # Pass fixture
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("event_id") == str(existing_event.id)
    assert "updated_details" in result
    updated_details = result["updated_details"]
    assert updated_details["id"] == str(existing_event.id)
    assert updated_details["title"] == new_title
    assert updated_details["location"] == new_location
    # Check that time hasn't changed (remove tzinfo for comparison)
    original_start_naive = existing_event.start_time # DB stores naive
    updated_start_naive = datetime.fromisoformat(updated_details["start_time"]).replace(tzinfo=None)
    assert updated_start_naive == original_start_naive

    # Assert Database State
    async with test_session_factory() as session:
        # Re-fetch to ensure commit
        db_event = await session.get(DBEvent, existing_event.id)
        assert db_event is not None
        assert db_event.id == existing_event.id
        assert db_event.title == new_title
        assert db_event.location == new_location
        assert db_event.start_time == original_start_naive # Check time unchanged in DB
        assert db_event.time_zone == existing_event.time_zone # Check timezone unchanged


# TODO: Add tests for update not found, conflict, validation errors, updating time, etc. 