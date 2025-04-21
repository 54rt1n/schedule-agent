import uuid
from datetime import UTC, datetime, timedelta
# from unittest.mock import AsyncMock # Unused

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.db.models import Event, Participant
from scheduling_agent.db.schemas import EventCreate # Remove EventUpdate
from scheduling_agent.db.service import DatabaseService

# Tests for basic CRUD operations (Create, Read by ID, Delete)

@pytest.mark.asyncio
async def test_create_event_simple(db_service: DatabaseService, test_session: AsyncSession):
    """Test creating a simple event with no conflicts."""
    # --- Arrange ---
    local_start = datetime(2024, 6, 1, 10, 0, 0)
    local_end = datetime(2024, 6, 1, 11, 0, 0)
    tz_name = "America/New_York"

    expected_start_utc = datetime(2024, 6, 1, 14, 0, 0, tzinfo=UTC)
    expected_end_utc = datetime(2024, 6, 1, 15, 0, 0, tzinfo=UTC)

    event_create_data = EventCreate(
        title="Service Test Meeting",
        start_time=local_start,
        end_time=local_end,
        time_zone=tz_name,
        location="Service Conf Room",
    )

    # --- Act ---
    created_event_db = await db_service.create_event(event_data=event_create_data)

    # --- Assert ---
    mock_conflict_resolver = db_service.conflict_resolver
    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)

    assert created_event_db is not None
    assert created_event_db.id is not None
    assert created_event_db.title == event_create_data.title

    result = await test_session.execute(select(Event).where(Event.id == created_event_db.id))
    retrieved_event = result.scalar_one_or_none()

    assert retrieved_event is not None
    assert retrieved_event.title == "Service Test Meeting"
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
    mock_conflict_resolver = db_service.conflict_resolver
    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)

    assert created_event_db is not None
    assert created_event_db.id is not None

    result = await test_session.execute(select(Event).where(Event.id == created_event_db.id))
    retrieved_event = result.scalar_one_or_none()

    assert retrieved_event is not None
    assert retrieved_event.title == "Project Kickoff"

    part_result = await test_session.execute(
        select(Participant).where(Participant.event_id == created_event_db.id)
    )
    retrieved_participants = part_result.scalars().all()

    assert len(retrieved_participants) == len(participant_ids)
    retrieved_identifiers = sorted([p.identifier for p in retrieved_participants])
    assert retrieved_identifiers == sorted(participant_ids)

    await test_session.refresh(retrieved_event, attribute_names=["participants"])
    assert len(retrieved_event.participants) == len(participant_ids)
    event_side_identifiers = sorted([p.identifier for p in retrieved_event.participants])
    assert event_side_identifiers == sorted(participant_ids)


@pytest.mark.asyncio
async def test_create_event_conflict(db_service: DatabaseService, test_session: AsyncSession):
    """Test that creating an event raises ConflictError when conflicts are detected."""
    # --- Arrange ---
    mock_conflict_resolver = db_service.conflict_resolver
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

    mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data)
    assert exc_info.value.conflicts == mock_conflict_list


@pytest.mark.asyncio
async def test_get_event_by_id(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving an event by its ID."""
    # --- Arrange ---
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
async def test_delete_event(db_service: DatabaseService, test_session: AsyncSession):
    """Test deleting an existing event."""
    # --- Arrange ---
    event_create_data = EventCreate(
        title="Event To Delete",
        start_time=datetime(2024, 10, 1, 10, 0, 0),
        end_time=datetime(2024, 10, 1, 11, 0, 0),
        time_zone="UTC",
    )
    event_to_delete = await db_service.create_event(event_data=event_create_data)
    event_id = event_to_delete.id

    assert await test_session.get(Event, event_id) is not None

    # --- Act ---
    deleted = await db_service.delete_event(event_id=event_id)

    # --- Assert ---
    assert deleted is True
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