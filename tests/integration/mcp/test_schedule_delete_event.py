# tests/integration/mcp/test_schedule_delete_event.py

import uuid
from datetime import datetime, timezone, timedelta
import logging # Import logging for type hints

import pytest
import pytest_asyncio # Import pytest_asyncio
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock # Import MagicMock

# Function to test
from scheduling_agent.mcp.handlers import schedule_delete_event

# DB Model and Service for setup/assertions
from scheduling_agent.db.models import Event as DBEvent
from scheduling_agent.db.service import DatabaseService # Needed for setup
from scheduling_agent.db.schemas import EventCreate # Needed for setup
from scheduling_agent.core.exceptions import EventNotFoundError, DatabaseError

# Import root conftest fixtures if needed (test_session_factory)
# The service patching fixture in mcp/conftest.py is applied automatically


@pytest_asyncio.fixture(scope="function")
async def event_to_delete(mcp_test_db_service: DatabaseService) -> DBEvent:
    """Fixture to create a pre-existing event to be deleted."""
    now = datetime.now(timezone.utc)
    event_data = EventCreate(
        title="Event To Be Deleted",
        start_time=now + timedelta(days=3),
        end_time=now + timedelta(days=3, hours=1),
        time_zone="America/Denver",
    )
    created_event = await mcp_test_db_service.create_event(event_data=event_data)
    return created_event

@pytest.mark.asyncio
async def test_schedule_delete_event_success(
    test_session_factory: sessionmaker,
    event_to_delete: DBEvent,
    mcp_test_db_service: DatabaseService,
):
    """Test successful deletion of an existing event."""
    event_id_to_delete = str(event_to_delete.id)

    # Pre-check: Ensure event exists
    async with test_session_factory() as session:
        db_event = await session.get(DBEvent, event_to_delete.id)
        assert db_event is not None

    params = {
        "event_id": event_id_to_delete,
    }

    mock_logger = MagicMock(spec=logging.Logger) # Create mock logger

    # Action
    result = await schedule_delete_event(
        **params,
        logger=mock_logger, # Pass mock logger
        db_service=mcp_test_db_service
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("event_id") == event_id_to_delete

    # Assert Database State
    async with test_session_factory() as session:
        db_event_after_delete = await session.get(DBEvent, event_to_delete.id)
        assert db_event_after_delete is None


@pytest.mark.asyncio
async def test_schedule_delete_event_not_found(
    test_session_factory: sessionmaker,
    mcp_test_db_service: DatabaseService,
):
    """Test attempting to delete a non-existent event."""
    non_existent_event_id = str(uuid.uuid4())

    params = {
        "event_id": non_existent_event_id,
    }

    mock_logger = MagicMock(spec=logging.Logger) # Create mock logger

    # Action
    result = await schedule_delete_event(
        **params,
        logger=mock_logger, # Pass mock logger
        db_service=mcp_test_db_service
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert result.get("error") is not None
    # Make assertion less strict
    assert "not found" in result["error"].lower()
    assert non_existent_event_id in result["error"]


# TODO: Add tests for invalid UUID format. 