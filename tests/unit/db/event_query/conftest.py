import pytest
import pytest_asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone, UTC
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import MagicMock, AsyncMock

from sa.db.models import Event


async def create_test_event(
    session: AsyncSession,
    title: str,
    start_time: datetime,
    end_time: datetime,
    rrule: str | None = None, # Use rrule to indicate series
    time_zone: str = "UTC"
) -> Event: # Return type is Event
    """Helper to create and add an Event to the session.
       Stores times as naive UTC in the database.
    """
    # Ensure input datetimes are timezone-aware UTC before converting to naive
    if start_time.tzinfo is None:
        start_time = start_time.replace(tzinfo=UTC)
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=UTC)

    start_naive_utc = start_time.astimezone(UTC).replace(tzinfo=None)
    end_naive_utc = end_time.astimezone(UTC).replace(tzinfo=None)

    event = Event(
        title=title,
        start_time=start_naive_utc, # Store naive UTC
        end_time=end_naive_utc,   # Store naive UTC
        rrule=rrule,
        time_zone=time_zone # Store original time_zone
    )
    session.add(event)
    await session.flush()
    await session.refresh(event)
    return event 