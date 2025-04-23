# tests/integration/mcp/test_schedule_query_events.py

import uuid
from datetime import datetime, timezone, timedelta
import logging

import pytest
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock

# Function to test
from scheduling_agent.mcp.handlers import schedule_query_events

# DB Model and Service for setup
from scheduling_agent.db.models import Event as DBEvent
from scheduling_agent.db.service import DatabaseService # Needed for setup
from scheduling_agent.db.schemas import EventCreate # Needed for setup
from scheduling_agent.core.exceptions import DatabaseError

# Import root conftest fixtures if needed (test_session_factory)
# The service patching fixture in mcp/conftest.py is applied automatically


@pytest.mark.asyncio
async def test_schedule_query_events_success_no_results(
    test_session_factory: sessionmaker,
    mcp_test_db_service: DatabaseService,
):
    """Test querying events when no events exist."""
    params = {
        "start_time": datetime.now(timezone.utc).isoformat(),
        "end_time": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
    }

    mock_logger = MagicMock(spec=logging.Logger)

    # Action
    result = await schedule_query_events(
        **params,
        title_contains=None,
        logger=mock_logger,
        db_service=mcp_test_db_service
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 0
    assert result.get("events") == []


@pytest.mark.asyncio
async def test_schedule_query_events_success_with_results(
    test_session_factory: sessionmaker,
    mcp_test_db_service: DatabaseService,
):
    """Test querying events when matching events exist."""
    # Setup: Create an event in the test DB
    now = datetime.now(timezone.utc)
    event_data = EventCreate(
        title="Query Test Event",
        start_time=now + timedelta(hours=1),
        end_time=now + timedelta(hours=2),
        time_zone="UTC",
    )
    created_event = await mcp_test_db_service.create_event(event_data=event_data)

    params = {
        "start_time": now.isoformat(),
        "end_time": (now + timedelta(days=1)).isoformat(),
        "title_contains": "Query Test",
    }

    mock_logger = MagicMock(spec=logging.Logger)

    # Action
    result = await schedule_query_events(
        **params,
        logger=mock_logger,
        db_service=mcp_test_db_service
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 1
    assert len(result.get("events", [])) == 1
    queried_event = result["events"][0]
    assert queried_event["id"] == str(created_event.id)
    assert queried_event["title"] == event_data.title

# TODO: Add tests for different query combinations (time only, title only, etc.) and error cases. 