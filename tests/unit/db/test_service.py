import pytest
import uuid
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock # Import AsyncMock

from sqlmodel import SQLModel, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from scheduling_agent.db.models import Event, EventCreate, Participant, EventUpdate # Import all models needed
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.db.database import get_async_session as app_get_async_session # Import original
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.conflict_resolver import ConflictResolver

# --- Test Database Setup ---
DATABASE_URL_TEST = "sqlite+aiosqlite:///:memory:" # In-memory SQLite for tests

test_engine = create_async_engine(DATABASE_URL_TEST, echo=False, future=True)
TestAsyncSessionFactory = sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

@asynccontextmanager
async def get_test_async_session() -> AsyncSession:
    """Async context manager for test sessions."""
    async with TestAsyncSessionFactory() as session:
        try:
            yield session
            # No auto-commit for tests, assert explicitly if needed
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

@pytest.fixture(scope="function", autouse=True)
async def setup_test_db():
    """Fixture to create and drop tables for each test function."""
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield # Run the test
    async with test_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.drop_all)

@pytest.fixture(scope="function")
async def test_session() -> AsyncSession:
    """Fixture to provide a test session directly for assertions."""
    async with get_test_async_session() as session:
        yield session

@pytest.fixture
def db_service_with_mocks() -> tuple[DatabaseService, AsyncMock]:
    """Fixture to create a DatabaseService instance with a mocked ConflictResolver."""
    # Create mock dependency
    mock_conflict_resolver = AsyncMock(spec=ConflictResolver)
    # Instantiate the service, injecting the mock
    service = DatabaseService(conflict_resolver=mock_conflict_resolver)
    # Yield both for use in tests
    yield service, mock_conflict_resolver

# --- Test Cases ---

@pytest.mark.asyncio
async def test_create_event_simple(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test creating a simple event with no conflicts."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # No conflicts

    start_naive_utc = datetime(2024, 6, 1, 10, 0, 0)
    end_naive_utc = datetime(2024, 6, 1, 11, 0, 0)

    event_create_data = EventCreate(
        title="Service Test Meeting",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="America/New_York",
        location="Service Conf Room",
    )

    # Patch the service's dependency to use our test session factory
    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

    # --- Act ---
    created_event_db = await db_service.create_event(event_data=event_create_data)

    # --- Assert ---
    # Verify conflict check was called
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
async def test_create_event_with_participants(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test creating an event with participants and no conflicts."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # No conflicts

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

    # Patch the service's dependency
    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

    # --- Act ---
    created_event_db = await db_service.create_event(event_data=event_create_data)

    # --- Assert ---
    # Verify conflict check was called
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
async def test_create_event_conflict(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test that creating an event raises ConflictError when conflicts are detected."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks

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

    # Patch the session factory
    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

    # --- Act & Assert ---
    with pytest.raises(ConflictError) as exc_info:
        await db_service.create_event(event_data=event_create_data)

    # Verify conflict check was called
    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)

    # Verify the exception contains the conflicts
    assert exc_info.value.conflicts == mock_conflict_list

    # Verify no event was actually created in the DB
    result = await test_session.execute(
        select(Event).where(Event.title == event_create_data.title)
    )
    retrieved_event = result.scalar_one_or_none()
    assert retrieved_event is None

@pytest.mark.asyncio
async def test_get_event_by_id(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test retrieving an event by its ID."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # Assume no conflict for creation

    # Create an event first using the existing method
    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)
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
async def test_get_event_by_id_not_found(db_service_with_mocks: tuple[DatabaseService, AsyncMock], monkeypatch):
    """Test retrieving a non-existent event by ID returns None."""
    # --- Arrange ---
    db_service, _ = db_service_with_mocks # Don't need the mock here
    non_existent_id = uuid.uuid4()
    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

    # --- Act ---
    retrieved_event = await db_service.get_event_by_id(event_id=non_existent_id)

    # --- Assert ---
    assert retrieved_event is None

@pytest.mark.asyncio
async def test_get_events_in_range_simple(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test retrieving simple, non-recurring events within a date range."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # Assume no conflict for creation

    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

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
async def test_get_events_in_range_recurring(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test retrieving recurring events within a date range."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # Assume no conflict for creation

    # Mock DateUtils expand_recurrence for consistent results
    # Need to import DateUtils first
    from scheduling_agent.core.date_utils import DateUtils
    db_service.date_utils = DateUtils()

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
async def test_update_event(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test updating an existing event."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # Assume no conflict for creation

    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

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
async def test_delete_event(db_service_with_mocks: tuple[DatabaseService, AsyncMock], test_session: AsyncSession, monkeypatch):
    """Test deleting an existing event."""
    # --- Arrange ---
    db_service, mock_conflict_resolver = db_service_with_mocks
    mock_conflict_resolver.detect_conflicts.return_value = [] # Assume no conflict for creation

    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)

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
async def test_delete_event_not_found(db_service_with_mocks: tuple[DatabaseService, AsyncMock], monkeypatch):
    """Test deleting a non-existent event returns False."""
    # --- Arrange ---
    db_service, _ = db_service_with_mocks # Don't need the mock here
    monkeypatch.setattr("scheduling_agent.db.service.get_async_session", get_test_async_session)
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