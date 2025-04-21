import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.db.models import Event, Participant
from scheduling_agent.db.models import A2ATask
from scheduling_agent.db.schemas import EventCreate, EventUpdate
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.a2a import types as a2a_types


# --- New Fixture for DB Service (Uses conftest fixtures) ---
@pytest_asyncio.fixture  # Use pytest_asyncio fixture
async def db_service(test_session_factory) -> DatabaseService:  # Make fixture async
    """Fixture to provide a DatabaseService instance with mocks."""
    # Create mock dependencies
    mock_conflict_resolver = AsyncMock(spec=ConflictResolver)
    mock_conflict_resolver.detect_conflicts.return_value = []  # Default: no conflicts

    service = DatabaseService(
        session_factory=test_session_factory,  # Pass the regular factory
        date_utils=DateUtils(),  # Instantiate real DateUtils
        conflict_resolver=mock_conflict_resolver,  # Use placeholder mock
    )
    return service


# --- Test Cases (Updated Signatures) ---


@pytest.mark.asyncio
async def test_create_event_simple(db_service: DatabaseService, test_session: AsyncSession):
    """Test creating a simple event with no conflicts."""
    # --- Arrange ---


    # Original naive local time for the event
    local_start = datetime(2024, 6, 1, 10, 0, 0)
    local_end = datetime(2024, 6, 1, 11, 0, 0)
    tz_name = "America/New_York"

    # Calculate expected UTC times based on local time and timezone
    # June 1st in NY is EDT (UTC-4)
    expected_start_utc = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
    expected_end_utc = datetime(2024, 6, 1, 15, 0, 0, tzinfo=UTC)

    event_create_data = EventCreate(
        title="Service Test Meeting",
        start_time=local_start,  # Pass naive time as per model expectation (service should handle TZ)
        end_time=local_end,
        time_zone=tz_name,
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
    result = await test_session.execute(select(Event).where(Event.id == created_event_db.id))
    retrieved_event = result.scalar_one_or_none()

    assert retrieved_event is not None
    assert retrieved_event.title == "Service Test Meeting"
    # Assert against the expected naive UTC times, as retrieved from DB
    expected_naive_start_utc = datetime(2024, 6, 1, 14, 0, 0)
    expected_naive_end_utc = datetime(2024, 6, 1, 15, 0, 0)
    assert retrieved_event.start_time == expected_naive_start_utc
    assert retrieved_event.end_time == expected_naive_end_utc
    assert retrieved_event.time_zone == tz_name
    assert retrieved_event.location == "Service Conf Room"
    assert retrieved_event.rrule is None
    assert retrieved_event.notes is None
    assert retrieved_event.created_at is not None
    assert retrieved_event.updated_at is not None
    # Check relationships are initially empty
    await test_session.refresh(retrieved_event, attribute_names=["participants"])
    assert retrieved_event.participants == []


@pytest.mark.asyncio
async def test_create_event_with_participants(
    db_service: DatabaseService, test_session: AsyncSession
):
    """Test creating an event with participants and no conflicts."""
    # --- Arrange ---

    start_naive_utc = datetime(2024, 6, 2, 14, 0, 0)
    end_naive_utc = datetime(2024, 6, 2, 15, 30, 0)
    participant_ids = ["alice@example.com", "bob@test.org", "carol@sample.net"]

    event_create_data = EventCreate(
        title="Project Kickoff",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="Europe/Berlin",
        participant_identifiers=participant_ids,
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
    result = await test_session.execute(select(Event).where(Event.id == created_event_db.id))
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
    mock_conflict_resolver = db_service.conflict_resolver  # Get the mock from the service

    # Simulate detected conflicts
    mock_conflict_list = [
        {
            "type": "instance",
            "event": Event(
                id=uuid.uuid4(),
                title="Existing Event 1",
                start_time=datetime.now(UTC),
                end_time=datetime.now(UTC) + timedelta(hours=1),
                time_zone="UTC",
            ),
        }
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

    # Events to create
    evt1_start = datetime(2024, 8, 15, 10, 0, 0)  # In range
    evt1_end = datetime(2024, 8, 15, 11, 0, 0)
    evt2_start = datetime(2024, 8, 20, 14, 0, 0)  # In range
    evt2_end = datetime(2024, 8, 20, 15, 0, 0)
    evt3_start = datetime(2024, 8, 10, 12, 0, 0)  # Before range
    evt3_end = datetime(2024, 8, 10, 13, 0, 0)
    evt4_start = datetime(2024, 8, 25, 16, 0, 0)  # After range
    evt4_end = datetime(2024, 8, 25, 17, 0, 0)
    evt5_start = datetime(2024, 8, 14, 23, 0, 0)  # Starts before, ends in range
    evt5_end = datetime(2024, 8, 15, 1, 0, 0)
    evt6_start = datetime(2024, 8, 20, 23, 0, 0)  # Starts in range, ends after
    evt6_end = datetime(2024, 8, 21, 1, 0, 0)
    evt7_start = datetime(2024, 8, 12, 10, 0, 0)  # Starts before, ends after (spans range)
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
    # Use db_service.create_event (which handles conflict check mock)
    created_ids = []
    for data in events_data:
        # Reset conflict mock for each creation if necessary, or assume it handles it
        created = await db_service.create_event(event_data=data)
        created_ids.append(created.id)

    # Define the query range (naive UTC)
    range_start = datetime(2024, 8, 15, 0, 0, 0)
    range_end = datetime(2024, 8, 21, 0, 0, 0)

    # --- Act ---
    # Use aware UTC range for the method call
    range_start_aware = range_start.replace(tzinfo=UTC)
    range_end_aware = range_end.replace(tzinfo=UTC)
    retrieved_events = await db_service.get_events_in_range(
        start=range_start_aware, end=range_end_aware
    )

    # --- Assert ---
    # Expected events: Evt1, Evt2, Evt5, Evt6, Evt7 (5 total)
    assert len(retrieved_events) == 5
    retrieved_titles = sorted([evt.title for evt in retrieved_events])
    assert retrieved_titles == sorted(["Evt1", "Evt2", "Evt5", "Evt6", "Evt7"])


@pytest.mark.asyncio
async def test_get_events_in_range_recurring(
    db_service: DatabaseService, test_session: AsyncSession
):
    """Test retrieving recurring event instances within a date range."""
    # --- Arrange ---
    # Base event: Weekly on Wed at 11:00 UTC for 4 weeks, starting 2024-09-04
    base_start_naive = datetime(2024, 9, 4, 11, 0, 0)
    base_end_naive = datetime(2024, 9, 4, 12, 0, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=WE;COUNT=4"
    base_event_data = EventCreate(
        title="Recurring Weekly",
        start_time=base_start_naive,
        end_time=base_end_naive,
        time_zone="UTC",
        rrule=rrule_str,
    )
    # Use db_service to create the base event
    base_event = await db_service.create_event(event_data=base_event_data)
    assert base_event.id is not None

    # Define the query range (naive UTC) - should catch 2nd and 3rd instances
    range_start = datetime(2024, 9, 10, 0, 0, 0)  # Tuesday Sep 10th
    range_end = datetime(2024, 9, 20, 0, 0, 0)  # Friday Sep 20th

    # --- Act ---
    # Use aware UTC range for the method call
    range_start_aware = range_start.replace(tzinfo=UTC)
    range_end_aware = range_end.replace(tzinfo=UTC)
    retrieved_events = await db_service.get_events_in_range(
        start=range_start_aware, end=range_end_aware
    )

    # --- Assert ---
    # Expected instances: Sep 11th, Sep 18th (2 total)
    assert len(retrieved_events) == 2
    retrieved_starts_naive = sorted([evt.start_time for evt in retrieved_events])

    expected_starts_naive = [
        datetime(2024, 9, 11, 11, 0, 0),
        datetime(2024, 9, 18, 11, 0, 0),
    ]
    assert retrieved_starts_naive == expected_starts_naive

    # Check other properties of the retrieved instances
    for instance in retrieved_events:
        assert instance.title == "Recurring Weekly"
        assert instance.time_zone == "UTC"
        assert instance.rrule is None  # Instances should not have rrule


# --- DST Transition Integration Tests (Phase 3, Task 4) ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tz_name", "event_local_start", "rrule_str", "query_range_start_utc", "query_range_end_utc", "expected_naive_utc_starts"),
    [
        # US/New York: Spring Forward (EDT starts Sunday, March 10, 2024) - Event at 9am local
        (
            "America/New_York",
            datetime(2024, 3, 8, 9, 0, 0),  # Fri 9am EST (naive local)
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 3, 8, 0, 0, 0, tzinfo=UTC),  # Query range start
            datetime(2024, 3, 13, 0, 0, 0, tzinfo=UTC),  # Query range end
            [  # Expected NAIVE UTC start times stored in DB / returned by service
                datetime(2024, 3, 8, 14, 0, 0),  # Fri 9am EST = 14:00 UTC
                datetime(2024, 3, 9, 14, 0, 0),  # Sat 9am EST = 14:00 UTC
                datetime(2024, 3, 10, 13, 0, 0),  # Sun 9am EDT = 13:00 UTC
                datetime(2024, 3, 11, 13, 0, 0),  # Mon 9am EDT = 13:00 UTC
                datetime(2024, 3, 12, 13, 0, 0),  # Tue 9am EDT = 13:00 UTC
            ],
        ),
        # US/New York: Fall Back (EST starts Sunday, November 3, 2024) - Event at 9am local
        (
            "America/New_York",
            datetime(2024, 11, 1, 9, 0, 0),  # Fri 9am EDT (naive local)
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),  # Query range start
            datetime(2024, 11, 6, 0, 0, 0, tzinfo=UTC),  # Query range end
            [  # Expected NAIVE UTC start times
                datetime(2024, 11, 1, 13, 0, 0),  # Fri 9am EDT = 13:00 UTC
                datetime(2024, 11, 2, 13, 0, 0),  # Sat 9am EDT = 13:00 UTC
                datetime(2024, 11, 3, 14, 0, 0),  # Sun 9am EST = 14:00 UTC
                datetime(2024, 11, 4, 14, 0, 0),  # Mon 9am EST = 14:00 UTC
                datetime(2024, 11, 5, 14, 0, 0),  # Tue 9am EST = 14:00 UTC
            ],
        ),
        # Europe/London: Spring Forward (BST starts Sunday, 31 March 2024) - Event at 9am local
        (
            "Europe/London",
            datetime(2024, 3, 29, 9, 0, 0),  # Fri 9am GMT (naive local)
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 3, 29, 0, 0, 0, tzinfo=UTC),  # Query range start
            datetime(2024, 4, 3, 0, 0, 0, tzinfo=UTC),  # Query range end
            [  # Expected NAIVE UTC start times
                datetime(2024, 3, 29, 9, 0, 0),  # Fri 9am GMT = 09:00 UTC
                datetime(2024, 3, 30, 9, 0, 0),  # Sat 9am GMT = 09:00 UTC
                datetime(2024, 3, 31, 8, 0, 0),  # Sun 9am BST = 08:00 UTC
                datetime(2024, 4, 1, 8, 0, 0),  # Mon 9am BST = 08:00 UTC
                datetime(2024, 4, 2, 8, 0, 0),  # Tue 9am BST = 08:00 UTC
            ],
        ),
        # Europe/London: Fall Back (GMT starts Sunday, 27 October 2024) - Event at 9am local
        (
            "Europe/London",
            datetime(2024, 10, 25, 9, 0, 0),  # Fri 9am BST (naive local)
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 10, 25, 0, 0, 0, tzinfo=UTC),  # Query range start
            datetime(2024, 10, 30, 0, 0, 0, tzinfo=UTC),  # Query range end
            [  # Expected NAIVE UTC start times
                datetime(2024, 10, 25, 8, 0, 0),  # Fri 9am BST = 08:00 UTC
                datetime(2024, 10, 26, 8, 0, 0),  # Sat 9am BST = 08:00 UTC
                datetime(2024, 10, 27, 9, 0, 0),  # Sun 9am GMT = 09:00 UTC
                datetime(2024, 10, 28, 9, 0, 0),  # Mon 9am GMT = 09:00 UTC
                datetime(2024, 10, 29, 9, 0, 0),  # Tue 9am GMT = 09:00 UTC
            ],
        ),
    ],
)
async def test_get_events_in_range_dst_transitions(
    db_service: DatabaseService,
    test_session: AsyncSession,  # Use separate session for verification if needed
    tz_name: str,
    event_local_start: datetime,
    rrule_str: str,
    query_range_start_utc: datetime,
    query_range_end_utc: datetime,
    expected_naive_utc_starts: list[datetime],
):
    """Test recurrence expansion across DST transitions within DatabaseService."""
    # Arrange: Create base event
    event_duration = timedelta(hours=1)
    event_local_end = event_local_start + event_duration
    base_event_data = EventCreate(
        title=f"DST Integration {tz_name}",
        start_time=event_local_start,  # Assume create_event handles naive local correctly based on TZ
        end_time=event_local_end,
        time_zone=tz_name,  # Store the local timezone
        rrule=rrule_str,
    )
    # Create the event using the service (handles conflict mock)
    created_base_event = await db_service.create_event(event_data=base_event_data)
    assert created_base_event is not None

    # Act: Call the method under test
    retrieved_event_instances = await db_service.get_events_in_range(
        start=query_range_start_utc, end=query_range_end_utc
    )

    # Assert: Check the naive UTC start times of the returned instances
    assert len(retrieved_event_instances) == len(expected_naive_utc_starts)
    retrieved_naive_utc_starts = sorted([evt.start_time for evt in retrieved_event_instances])

    assert retrieved_naive_utc_starts == expected_naive_utc_starts

    # Optional: Verify end times (should also be naive UTC and shifted correctly)
    retrieved_naive_utc_ends = sorted([evt.end_time for evt in retrieved_event_instances])
    expected_naive_utc_ends = sorted(
        [start + event_duration for start in expected_naive_utc_starts]
    )
    assert retrieved_naive_utc_ends == expected_naive_utc_ends

    # Check other instance properties
    for instance in retrieved_event_instances:
        assert instance.title == f"DST Integration {tz_name}"
        assert instance.time_zone == tz_name  # Instance keeps original TZ name
        assert instance.rrule is None  # Instances should not have rrule


@pytest.mark.asyncio
async def test_update_event(db_service: DatabaseService, test_session: AsyncSession):
    """Test updating an existing event."""
    # --- Arrange ---
    # Create initial event
    initial_data = EventCreate(
        title="Initial Meeting",
        start_time=datetime(2024, 9, 1, 10, 0, 0),
        end_time=datetime(2024, 9, 1, 11, 0, 0),
        time_zone="UTC",
        location="Room 1",
    )
    initial_event = await db_service.create_event(event_data=initial_data)
    event_id = initial_event.id

    # Prepare update data (using a placeholder Update model for now)
    # Need to define EventUpdate model later
    # Import from the correct location: db.schemas
    from scheduling_agent.db.schemas import EventUpdate

    update_data = EventUpdate(
        title="Updated Event Title",
        location="Room 2",
        notes="Added some notes.",
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
    assert updated_event.start_time == initial_data.start_time  # Check time wasn't changed
    assert updated_event.time_zone == initial_data.time_zone  # Check timezone wasn't changed
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
    # Create an event to delete
    event_create_data = EventCreate(
        title="Event To Delete",
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


@pytest.mark.asyncio
async def test_update_event_not_found(db_service: DatabaseService):
    """Test updating a non-existent event returns None."""
    # --- Arrange ---
    non_existent_id = uuid.uuid4()
    update_data = EventUpdate(title="This Should Not Update")

    # --- Act ---
    updated_event = await db_service.update_event(event_id=non_existent_id, update_data=update_data)

    # --- Assert ---
    assert updated_event is None


@pytest.mark.asyncio
async def test_get_events_in_range_empty(db_service: DatabaseService):
    """Test get_events_in_range returns empty list when no events are in range."""
    # --- Arrange ---
    # No events created
    range_start_aware = datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC)
    range_end_aware = datetime(2024, 11, 10, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    retrieved_events = await db_service.get_events_in_range(
        start=range_start_aware, end=range_end_aware
    )

    # --- Assert ---
    assert retrieved_events == []


@pytest.mark.asyncio
async def test_get_events_in_range_recurring_outside_range(db_service: DatabaseService):
    """Test that a recurring event entirely outside the range is not returned."""
    # --- Arrange ---
    # (Setup is similar to test_get_events_in_range_recurring)
    event_create_data = EventCreate(
        title="Far Future Daily Meeting",
        start_time=datetime(2025, 1, 1, 10, 0, 0),
        end_time=datetime(2025, 1, 1, 11, 0, 0),
        time_zone="UTC",
        rrule="FREQ=DAILY;COUNT=5", # Jan 1 to Jan 5
    )
    await db_service.create_event(event_data=event_create_data)

    # Query range before the event
    query_start = datetime(2024, 12, 1, 0, 0, 0, tzinfo=UTC)
    query_end = datetime(2024, 12, 31, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    events = await db_service.get_events_in_range(query_start, query_end)

    # --- Assert ---
    assert not events # Expect no events to be returned


# --- A2A Task Persistence Tests ---

@pytest.mark.asyncio
async def test_save_a2a_task(db_service: DatabaseService, test_session: AsyncSession):
    """Test saving a new A2A Task to the database."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    # Fix: Provide required fields for TaskSendParams (id, message)
    user_message = a2a_types.Message(role="user", parts=[a2a_types.Part(type="text", text="Schedule meeting")])
    params = a2a_types.TaskSendParams(id=task_id, message=user_message, intent="create_event", user_prompt="Schedule meeting")
    # Fix: Use valid TaskState enum value (SUBMITTED instead of QUEUED)
    status = a2a_types.TaskStatus(state=a2a_types.TaskState.SUBMITTED, message=a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task submitted")]))

    task_to_save = a2a_types.Task(
        id=task_id,
        params=params,
        status=status,
        artifacts=[], # Initially empty
        history=[]    # Initially empty
        # session_id can be None
    )

    # --- Act ---
    # This method doesn't exist yet, so the test will fail initially (RED)
    await db_service.save_a2a_task(task_to_save)

    # --- Assert ---
    # Verify data was saved in the test DB using a separate session
    retrieved_record = await test_session.get(A2ATask, task_id)

    assert retrieved_record is not None
    assert retrieved_record.id == task_id
    assert retrieved_record.state == task_to_save.status.state.value
    assert retrieved_record.session_id is None # Check default

    # Verify JSON fields were serialized correctly
    assert retrieved_record.params_json == params.model_dump_json(exclude_none=True)
    assert retrieved_record.status_message_json == status.message.model_dump_json(exclude_none=True)
    assert retrieved_record.artifacts_json is None 
    assert retrieved_record.history_json is None # Should be null/None if history was empty

    # Check timestamps (ensure they are set)
    assert retrieved_record.created_at is not None
    assert retrieved_record.updated_at is not None
    assert isinstance(retrieved_record.created_at, datetime)
    assert isinstance(retrieved_record.updated_at, datetime)

# TODO: Add test for updating an existing task via save_a2a_task (upsert)


@pytest.mark.asyncio
async def test_get_a2a_task_by_id_found(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving an existing A2A Task by ID, verifying deserialization."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    # Fix: Provide required fields for TaskSendParams (id, message)
    user_message_for_params = a2a_types.Message(role="user", parts=[a2a_types.Part(type="text", text="Change meeting time")])
    params = a2a_types.TaskSendParams(id=task_id, message=user_message_for_params, intent="update_event", user_prompt="Change meeting time")
    # Fix: Provide required fields for Message (role, parts)
    message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task is working")])
    status = a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING, message=message)
    artifact = a2a_types.Artifact(name="draft_email", parts=[a2a_types.Part(type="text", text="Email body...")])
    # Fix: Provide required fields for Message (role, parts)
    initial_status_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task submitted")])
    initial_status = a2a_types.TaskStatus(state=a2a_types.TaskState.SUBMITTED, message=initial_status_message)

    task_to_save = a2a_types.Task(
        id=task_id,
        sessionId="session_123",
        params=params,
        status=status,
        artifacts=[artifact], 
        history=[initial_status] 
    )

    # Use save_a2a_task to put the data in the DB first
    await db_service.save_a2a_task(task_to_save)

    # --- Act ---
    # This method doesn't exist yet (RED step for get)
    retrieved_task = await db_service.get_a2a_task_by_id(task_id)

    # --- Assert ---
    assert retrieved_task is not None
    assert isinstance(retrieved_task, a2a_types.Task)

    # Compare the retrieved task with the original task object
    # Pydantic models comparison should work if deserialization is correct
    # Remove this check as timestamps will differ slightly causing failure.
    # assert retrieved_task == task_to_save

    # Explicitly check a few deserialized fields
    assert retrieved_task.id == task_id
    assert retrieved_task.sessionId == "session_123"
    assert retrieved_task.status.state == a2a_types.TaskState.WORKING
    assert retrieved_task.status.message.parts[0].text == "Task is working"
    # Verify params object was deserialized correctly by checking its required fields
    assert retrieved_task.params.id == task_id
    assert retrieved_task.params.message.parts[0].text == "Change meeting time"
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0].name == "draft_email"
    # Re-add history checks
    assert len(retrieved_task.history) == 1
    assert retrieved_task.history[0].state == a2a_types.TaskState.SUBMITTED


@pytest.mark.asyncio
async def test_get_a2a_task_by_id_not_found(db_service: DatabaseService):
    """Test retrieving a non-existent A2A Task by ID returns None."""
    # --- Arrange ---
    non_existent_task_id = str(uuid.uuid4())

    # --- Act ---
    # This method doesn't exist yet (RED step for get)
    retrieved_task = await db_service.get_a2a_task_by_id(non_existent_task_id)

    # --- Assert ---
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_update_a2a_task_status(db_service: DatabaseService, test_session: AsyncSession):
    """Test updating the status and history of an A2A Task."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    # Fix: Provide required fields for Message (role, parts)
    initial_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task submitted")])
    initial_status = a2a_types.TaskStatus(state=a2a_types.TaskState.SUBMITTED, message=initial_message)
    # Fix: Provide required fields for TaskSendParams (id, message)
    initial_user_message = a2a_types.Message(role="user", parts=[a2a_types.Part(type="text", text="Find events")])
    initial_params = a2a_types.TaskSendParams(id=task_id, message=initial_user_message, intent="find_event")
    
    task_to_save = a2a_types.Task(
        id=task_id,
        params=initial_params,
        status=initial_status,
        artifacts=[],
        history=[] # History starts empty
    )

    # 1. Save the initial task state
    await db_service.save_a2a_task(task_to_save)

    # 2. Define the new status
    # Fix: Provide required fields for Message (role, parts)
    new_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task is now working")])
    new_status = a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING, message=new_message)

    # --- Act ---
    # This method doesn't exist yet (RED step)
    await db_service.update_a2a_task_status(task_id, new_status)

    # --- Assert ---
    # Retrieve the raw DB record to check state, message, and history
    updated_record = await test_session.get(A2ATask, task_id)

    assert updated_record is not None
    assert updated_record.id == task_id
    
    # Check state and message reflect the new status
    assert updated_record.state == new_status.state.value
    assert updated_record.status_message_json == new_message.model_dump_json(exclude_none=True)

    # Check history contains the *serialized* old status
    assert updated_record.history_json is not None
    # Deserialize history from DB
    history_list = a2a_types.HistoryList.model_validate_json(updated_record.history_json).root
    assert len(history_list) == 1
    # Compare the deserialized history entry with the original status
    assert history_list[0] == initial_status 

    # Check timestamps
    assert updated_record.updated_at > updated_record.created_at


# TODO: Add test_get_a2a_task_by_id_found
# TODO: Add test_get_a2a_task_by_id_not_found
# TODO: Add test_update_a2a_task_status

# TODO: Add tests for:
# - Creating event WITH participants (needs modification to create_event service method)
# - Creating event WITH recurrence
# - Conflict detection logic during creation
# - Event retrieval (get_event_by_id, get_events_in_range)
# - Event update (including instance modification)
# - Event deletion
# - Handling database errors (e.g., constraint violations)
