import pytest
import uuid
# Remove os and tempfile as they are no longer needed for DB setup
# import os
# import tempfile
from datetime import datetime, timezone, timedelta
# Remove contextmanager as it's no longer needed for patching
# from contextlib import asynccontextmanager
from unittest.mock import AsyncMock # Import AsyncMock

from sqlmodel import SQLModel, select # Keep SQLModel import
# Remove engine/session imports handled by conftest
# from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
# from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession # Keep for type hints

from scheduling_agent.db.models import Event, EventCreate, Participant, EventUpdate, ExcludedDate, ModifiedInstance
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.exceptions import ConflictError
# Remove patching related imports
# import scheduling_agent.db.database as db_module
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.conflict_resolver import ConflictResolver
# Import pytest-asyncio decorator
import pytest_asyncio

# --- Remove Old Test Database Setup Fixtures --- #
# (These are now handled in tests/conftest.py)

# --- New Fixture for DB Service (Uses conftest fixtures) ---
@pytest_asyncio.fixture # Use pytest_asyncio fixture
async def db_service(test_session_factory) -> DatabaseService: # Make fixture async
    """Fixture to create a DatabaseService instance using the test session factory.
       Injects a basic DateUtils and placeholder ConflictResolver.
    """
    # test_session_factory is now an async fixture, but the fixture itself
    # returns a synchronous sessionmaker, so we don't await it here.
    # factory = await test_session_factory # No longer needed if factory fixture returns non-async

    # TODO: You might want a separate fixture for a mock conflict resolver
    #       if tests need to assert calls to it.
    mock_conflict_resolver = AsyncMock(spec=ConflictResolver)
    mock_conflict_resolver.detect_conflicts.return_value = [] # Default: no conflicts

    service = DatabaseService(
        session_factory=test_session_factory, # Pass the regular factory
        date_utils=DateUtils(), # Instantiate real DateUtils
        conflict_resolver=mock_conflict_resolver # Use placeholder mock
    )
    return service

# --- Test Cases (Updated Signatures) ---

@pytest.mark.asyncio
async def test_create_event_simple(db_service: DatabaseService, test_session: AsyncSession):
    """Test creating a simple event with no conflicts."""
    # --- Arrange ---
    # db_service already has a mock conflict resolver injected by the fixture
    # Access the mock via the service if needed for assertions AFTER the call
    # mock_conflict_resolver = db_service.conflict_resolver 

    start_naive_utc = datetime(2024, 6, 1, 10, 0, 0)
    end_naive_utc = datetime(2024, 6, 1, 11, 0, 0)

    event_create_data = EventCreate(
        title="Service Test Meeting",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="America/New_York",
        location="Service Conf Room",
    )

    # --- Act ---
    created_event_db = await db_service.create_event(event_data=event_create_data)

    # --- Assert ---
    # Verify conflict check was called
    mock_conflict_resolver = db_service.conflict_resolver
    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)

    assert created_event_db is not None
    assert created_event_db.id is not None
    assert created_event_db.title == event_create_data.title

    # Verify data was actually saved in the test DB using a separate session
    result = await test_session.execute(
        select(Event).where(Event.id == created_event_db.id)
    )
    retrieved_event = result.scalar_one_or_none()

    assert retrieved_event is not None
    assert retrieved_event.title == "Service Test Meeting"
    assert retrieved_event.start_time == start_naive_utc
    assert retrieved_event.end_time == end_naive_utc
    assert retrieved_event.time_zone == "America/New_York"
    assert retrieved_event.location == "Service Conf Room"
    assert retrieved_event.rrule is None
    assert retrieved_event.notes is None
    assert retrieved_event.created_at is not None
    assert retrieved_event.updated_at is not None
    # Check relationships are initially empty
    await test_session.refresh(retrieved_event, attribute_names=["participants"])
    assert retrieved_event.participants == []

@pytest.mark.asyncio
async def test_create_event_with_participants(db_service: DatabaseService, test_session: AsyncSession):
    """Test creating an event with participants and no conflicts."""
    # --- Arrange ---
    # mock_conflict_resolver = db_service.conflict_resolver 
    start_naive_utc = datetime(2024, 6, 2, 14, 0, 0)
    end_naive_utc = datetime(2024, 6, 2, 15, 30, 0)
    participant_ids = ["alice@example.com", "bob@test.org", "carol@sample.net"]

    event_create_data = EventCreate(
        title="Project Kickoff",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="Europe/Berlin",
        participant_identifiers=participant_ids
    )

    # --- Act ---
    created_event_db = await db_service.create_event(event_data=event_create_data)

    # --- Assert ---
    # Verify conflict check was called
    mock_conflict_resolver = db_service.conflict_resolver
    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)

    assert created_event_db is not None
    assert created_event_db.id is not None

    # Verify event and participants in the DB using a separate session
    result = await test_session.execute(
        select(Event).where(Event.id == created_event_db.id)
    )
    retrieved_event = result.scalar_one_or_none()

    assert retrieved_event is not None
    assert retrieved_event.title == "Project Kickoff"

    # Query participants separately to ensure they were created
    part_result = await test_session.execute(
        select(Participant).where(Participant.event_id == created_event_db.id)
    )
    retrieved_participants = part_result.scalars().all()

    assert len(retrieved_participants) == len(participant_ids)
    retrieved_identifiers = sorted([p.identifier for p in retrieved_participants])
    assert retrieved_identifiers == sorted(participant_ids)

    # Check the relationship loaded from the event side (may require a refresh)
    # The service method might need to refresh this relationship after adding participants
    await test_session.refresh(retrieved_event, attribute_names=["participants"])
    assert len(retrieved_event.participants) == len(participant_ids)
    event_side_identifiers = sorted([p.identifier for p in retrieved_event.participants])
    assert event_side_identifiers == sorted(participant_ids)

@pytest.mark.asyncio
async def test_create_event_conflict(db_service: DatabaseService, test_session: AsyncSession):
    """Test that creating an event raises ConflictError when conflicts are detected."""
    # --- Arrange ---
    mock_conflict_resolver = db_service.conflict_resolver # Get the mock from the service

    # Simulate detected conflicts
    mock_conflict_list = [
        {"type": "instance", "event": Event(id=uuid.uuid4(), title="Existing Event 1", start_time=datetime.now(timezone.utc), end_time=datetime.now(timezone.utc) + timedelta(hours=1), time_zone="UTC")}
    ]
    mock_conflict_resolver.detect_conflicts.return_value = mock_conflict_list

    event_create_data = EventCreate(
        title="Conflicting Meeting",
        start_time=datetime(2024, 6, 3, 10, 0, 0),
        end_time=datetime(2024, 6, 3, 11, 0, 0),
        time_zone="UTC",
    )

    # --- Act & Assert ---
    with pytest.raises(ConflictError) as exc_info:
        await db_service.create_event(event_data=event_create_data)

    # Verify conflict check was called
    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)

    # Verify the exception contains the conflicts
    assert exc_info.value.conflicts == mock_conflict_list

@pytest.mark.asyncio
async def test_get_event_by_id(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving an event by its ID."""
    # --- Arrange ---
    # Create an event first using the service
    event_create_data = EventCreate(
        title="Get Me By ID",
        start_time=datetime(2024, 7, 1, 9, 0, 0),
        end_time=datetime(2024, 7, 1, 10, 0, 0),
        time_zone="UTC",
    )
    created_event = await db_service.create_event(event_data=event_create_data)
    assert created_event is not None
    created_id = created_event.id

    # --- Act ---
    # Use the method under test
    retrieved_event = await db_service.get_event_by_id(event_id=created_id)

    # --- Assert ---
    assert retrieved_event is not None
    assert retrieved_event.id == created_id
    assert retrieved_event.title == "Get Me By ID"

@pytest.mark.asyncio
async def test_get_event_by_id_not_found(db_service: DatabaseService):
    """Test retrieving a non-existent event by ID."""
    # --- Arrange ---
    non_existent_id = uuid.uuid4()

    # --- Act ---
    retrieved_event = await db_service.get_event_by_id(event_id=non_existent_id)

    # --- Assert ---
    assert retrieved_event is None

@pytest.mark.asyncio
async def test_get_events_in_range_simple(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving simple, non-recurring events within a date range."""
    # --- Arrange ---
    # mock_conflict_resolver = db_service.conflict_resolver
    # Events to create
    evt1_start = datetime(2024, 8, 15, 10, 0, 0) # In range
    evt1_end = datetime(2024, 8, 15, 11, 0, 0)
    evt2_start = datetime(2024, 8, 20, 14, 0, 0) # In range
    evt2_end = datetime(2024, 8, 20, 15, 0, 0)
    evt3_start = datetime(2024, 8, 10, 12, 0, 0) # Before range
    evt3_end = datetime(2024, 8, 10, 13, 0, 0)
    evt4_start = datetime(2024, 8, 25, 16, 0, 0) # After range
    evt4_end = datetime(2024, 8, 25, 17, 0, 0)
    evt5_start = datetime(2024, 8, 14, 23, 0, 0) # Starts before, ends in range
    evt5_end = datetime(2024, 8, 15, 1, 0, 0)
    evt6_start = datetime(2024, 8, 20, 23, 0, 0) # Starts in range, ends after
    evt6_end = datetime(2024, 8, 21, 1, 0, 0)
    evt7_start = datetime(2024, 8, 12, 10, 0, 0) # Starts before, ends after (spans range)
    evt7_end = datetime(2024, 8, 22, 10, 0, 0)

    events_data = [
        EventCreate(title="Evt1", start_time=evt1_start, end_time=evt1_end, time_zone="UTC"),
        EventCreate(title="Evt2", start_time=evt2_start, end_time=evt2_end, time_zone="UTC"),
        EventCreate(title="Evt3", start_time=evt3_start, end_time=evt3_end, time_zone="UTC"),
        EventCreate(title="Evt4", start_time=evt4_start, end_time=evt4_end, time_zone="UTC"),
        EventCreate(title="Evt5", start_time=evt5_start, end_time=evt5_end, time_zone="UTC"),
        EventCreate(title="Evt6", start_time=evt6_start, end_time=evt6_end, time_zone="UTC"),
        EventCreate(title="Evt7", start_time=evt7_start, end_time=evt7_end, time_zone="UTC"),
    ]

    created_ids = []
    for data in events_data:
        evt = await db_service.create_event(event_data=data)
        created_ids.append(evt.id)

    range_start = datetime(2024, 8, 15, 0, 0, 0) # Inclusive start
    range_end = datetime(2024, 8, 20, 23, 59, 59) # Inclusive end

    # Expected events (must overlap the range)
    expected_titles = {"Evt1", "Evt2", "Evt5", "Evt6", "Evt7"}

    # --- Act ---
    retrieved_events = await db_service.get_events_in_range(start=range_start, end=range_end)

    # --- Assert ---
    assert len(retrieved_events) == len(expected_titles)
    retrieved_titles = {evt.title for evt in retrieved_events}
    assert retrieved_titles == expected_titles

@pytest.mark.asyncio
async def test_get_events_in_range_recurring(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving recurring events within a date range."""
    # --- Arrange ---
    # mock_conflict_resolver = db_service.conflict_resolver
    # Mock DateUtils expand_recurrence for consistent results (No longer needed - using real DateUtils)
    # from scheduling_agent.core.date_utils import DateUtils
    # db_service.date_utils = DateUtils()

    # Event: Weekly on Tuesday at 11:00 UTC, starting May 7th
    start_naive_utc = datetime(2024, 5, 7, 11, 0, 0)
    end_naive_utc = datetime(2024, 5, 7, 11, 30, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=TU;UNTIL=20240601T000000Z"
    base_event = await db_service.create_event(EventCreate(
        title="Recurring Tue",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="UTC",
        rrule=rrule_str
    ))

    # Create another non-recurring event within the range
    non_recurring_event = await db_service.create_event(EventCreate(
        title="One-off May 15th",
        start_time=datetime(2024, 5, 15, 15, 0, 0),
        end_time=datetime(2024, 5, 15, 16, 0, 0),
        time_zone="UTC"
    ))

    # Define range that includes some but not all recurrences
    range_start = datetime(2024, 5, 10, 0, 0, 0) # Friday May 10th
    range_end = datetime(2024, 5, 25, 23, 59, 59) # Saturday May 25th

    # Expected occurrences in range: May 14th, May 21st
    # Expected events: The two recurring instances + the one-off event
    expected_count = 3
    expected_titles = {"Recurring Tue", "One-off May 15th"}
    # Note: Service should return naive UTC datetimes as stored in DB
    expected_start_times = {
        datetime(2024, 5, 14, 11, 0, 0), # Recurring
        datetime(2024, 5, 21, 11, 0, 0), # Recurring
        datetime(2024, 5, 15, 15, 0, 0), # One-off
    }

    # --- Act ---
    # Pass naive UTC datetimes to the service method
    retrieved_events = await db_service.get_events_in_range(start=range_start, end=range_end)

    # --- Assert ---
    assert len(retrieved_events) == expected_count

    # Check titles and start times (expecting naive UTC)
    retrieved_titles = {evt.title for evt in retrieved_events}
    # For this test, recurring instances should keep the base title
    assert retrieved_titles == expected_titles

    retrieved_start_times = {evt.start_time for evt in retrieved_events}
    assert retrieved_start_times == expected_start_times

    # Verify the type of the returned times (should be naive)
    for evt in retrieved_events:
        assert evt.start_time.tzinfo is None
        assert evt.end_time.tzinfo is None

@pytest.mark.asyncio
async def test_update_event(db_service: DatabaseService, test_session: AsyncSession):
    """Test updating an existing event."""
    # --- Arrange ---
    # mock_conflict_resolver = db_service.conflict_resolver
    # Create initial event
    initial_data = EventCreate(
        title="Initial Event",
        start_time=datetime(2024, 9, 1, 10, 0, 0),
        end_time=datetime(2024, 9, 1, 11, 0, 0),
        time_zone="UTC",
        location="Room 1"
    )
    initial_event = await db_service.create_event(event_data=initial_data)
    event_id = initial_event.id

    # Prepare update data (using a placeholder Update model for now)
    # Need to define EventUpdate model later
    from scheduling_agent.db.models import EventUpdate # Assuming this exists
    update_data = EventUpdate(
        title="Updated Event Title",
        location="Room 2",
        notes="Added some notes."
        # Intentionally not updating times or timezone
    )

    # --- Act ---
    updated_event = await db_service.update_event(event_id=event_id, update_data=update_data)

    # --- Assert ---
    assert updated_event is not None
    assert updated_event.id == event_id
    assert updated_event.title == "Updated Event Title"
    assert updated_event.location == "Room 2"
    assert updated_event.notes == "Added some notes."
    assert updated_event.start_time == initial_data.start_time # Check time wasn't changed
    assert updated_event.time_zone == initial_data.time_zone # Check timezone wasn't changed
    # Check if updated_at changed (allow small difference)
    assert updated_event.updated_at > initial_event.updated_at
    assert updated_event.updated_at - initial_event.updated_at < timedelta(seconds=5)

    # Verify in DB
    db_check = await test_session.get(Event, event_id)
    assert db_check is not None
    assert db_check.title == "Updated Event Title"
    assert db_check.location == "Room 2"
    assert db_check.notes == "Added some notes."

@pytest.mark.asyncio
async def test_delete_event(db_service: DatabaseService, test_session: AsyncSession):
    """Test deleting an existing event."""
    # --- Arrange ---
    # mock_conflict_resolver = db_service.conflict_resolver
    # Create an event to delete
    event_create_data = EventCreate(
        title="To Be Deleted",
        start_time=datetime(2024, 10, 1, 10, 0, 0),
        end_time=datetime(2024, 10, 1, 11, 0, 0),
        time_zone="UTC",
    )
    event_to_delete = await db_service.create_event(event_data=event_create_data)
    event_id = event_to_delete.id

    # Verify it exists initially
    assert await test_session.get(Event, event_id) is not None

    # --- Act ---
    deleted = await db_service.delete_event(event_id=event_id)

    # --- Assert ---
    assert deleted is True

    # Verify it no longer exists in DB
    assert await test_session.get(Event, event_id) is None

@pytest.mark.asyncio
async def test_delete_event_not_found(db_service: DatabaseService):
    """Test deleting a non-existent event returns False."""
    # --- Arrange ---
    non_existent_id = uuid.uuid4()

    # --- Act ---
    deleted = await db_service.delete_event(event_id=non_existent_id)

    # --- Assert ---
    assert deleted is False

# TODO: Add tests for:
# - Creating event WITH participants (needs modification to create_event service method)
# - Creating event WITH recurrence
# - Conflict detection logic during creation
# - Event retrieval (get_event_by_id, get_events_in_range)
# - Event update (including instance modification)
# - Event deletion
# - Handling database errors (e.g., constraint violations)