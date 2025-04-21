import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from scheduling_agent.db.models import Event, ModifiedInstance
from scheduling_agent.db.schemas import EventCreate, EventUpdate
from scheduling_agent.db.service import DatabaseService


@pytest.mark.asyncio
async def test_update_event_non_recurring(
    db_service: DatabaseService, test_session: AsyncSession
):
    """Test updating a simple non-recurring event's fields."""
    # --- Arrange ---
    initial_data = EventCreate(
        title="Initial Meeting",
        start_time=datetime(2024, 9, 1, 10, 0, 0),
        end_time=datetime(2024, 9, 1, 11, 0, 0),
        time_zone="UTC",
        location="Room 101",
        notes="Original notes.",
    )
    created_event = await db_service.create_event(event_data=initial_data)
    event_id = created_event.id
    initial_updated_at = created_event.updated_at

    update_payload = EventUpdate(
        title="Updated Meeting Title",
        location="Room 202",
        notes="New notes added.",
    )

    # --- Act ---
    updated_event_obj = await db_service.update_event(
        event_id=event_id, update_data=update_payload
    )

    # --- Assert ---
    assert updated_event_obj is not None
    assert isinstance(updated_event_obj, Event)
    assert updated_event_obj.id == event_id
    assert updated_event_obj.title == "Updated Meeting Title"
    assert updated_event_obj.location == "Room 202"
    assert updated_event_obj.notes == "New notes added."
    assert updated_event_obj.start_time == initial_data.start_time
    assert updated_event_obj.end_time == initial_data.end_time
    assert updated_event_obj.time_zone == initial_data.time_zone
    assert updated_event_obj.rrule is None
    assert updated_event_obj.updated_at > initial_updated_at

    db_check = await test_session.get(Event, event_id)
    assert db_check is not None
    assert db_check.title == "Updated Meeting Title"
    assert db_check.location == "Room 202"
    assert db_check.notes == "New notes added."
    assert db_check.updated_at == updated_event_obj.updated_at


@pytest.mark.asyncio
async def test_update_event_recurring_single_instance(
    db_service: DatabaseService, test_session: AsyncSession
):
    """Test updating a single instance of a recurring event creates/updates a ModifiedInstance."""
    # --- Arrange ---
    base_start_naive = datetime(2024, 10, 7, 10, 0, 0)
    base_end_naive = datetime(2024, 10, 7, 11, 0, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=4"
    initial_data = EventCreate(
        title="Weekly Standup",
        start_time=base_start_naive,
        end_time=base_end_naive,
        time_zone="UTC",
        rrule=rrule_str,
        location="Main Office",
    )
    base_event = await db_service.create_event(event_data=initial_data)
    event_id = base_event.id

    target_instance_original_date = datetime(2024, 10, 14, 10, 0, 0)
    new_start_time_for_instance = datetime(2024, 10, 14, 10, 30, 0)
    new_end_time_for_instance = datetime(2024, 10, 14, 11, 30, 0)

    update_payload = EventUpdate(
        title="Weekly Standup (Late Start)",
        location="Side Room",
        original_start_time=target_instance_original_date,
        start_time=new_start_time_for_instance,
        end_time=new_end_time_for_instance,
        time_zone="UTC"
    )

    # --- Act ---
    updated_instance_obj = await db_service.update_event(
        event_id=event_id, update_data=update_payload
    )

    # --- Assert ---
    assert updated_instance_obj is not None
    assert isinstance(updated_instance_obj, ModifiedInstance)
    assert updated_instance_obj.event_id == event_id
    assert updated_instance_obj.title == "Weekly Standup (Late Start)"
    assert updated_instance_obj.location == "Side Room"
    assert updated_instance_obj.start_time == new_start_time_for_instance
    assert updated_instance_obj.end_time == new_end_time_for_instance
    assert updated_instance_obj.time_zone == "UTC"

    stmt = select(ModifiedInstance).where(
        ModifiedInstance.event_id == event_id,
        ModifiedInstance.original_date == db_service.date_utils.normalize_to_utc(target_instance_original_date, tz_name="UTC").replace(tzinfo=None)
    )
    result = await test_session.execute(stmt)
    mod_instance_db = result.scalar_one_or_none()

    assert mod_instance_db is not None
    assert mod_instance_db.id == updated_instance_obj.id
    assert mod_instance_db.title == "Weekly Standup (Late Start)"

    base_event_check = await test_session.get(Event, event_id)
    assert base_event_check is not None
    assert base_event_check.title == "Weekly Standup"
    assert base_event_check.location == "Main Office"


@pytest.mark.asyncio
async def test_update_event_recurring_series_simple(
    db_service: DatabaseService, test_session: AsyncSession
):
    """Test updating simple fields (title, location) of a recurring series."""
    # --- Arrange ---
    base_start_naive = datetime(2024, 11, 4, 14, 0, 0)
    base_end_naive = datetime(2024, 11, 4, 15, 0, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=5"
    initial_data = EventCreate(
        title="Regular Team Sync",
        start_time=base_start_naive,
        end_time=base_end_naive,
        time_zone="Europe/London",
        rrule=rrule_str,
        location="Main Conference Room",
        notes="Standard sync notes",
    )
    base_event = await db_service.create_event(event_data=initial_data)
    event_id = base_event.id
    initial_updated_at = base_event.updated_at

    update_payload = EventUpdate(
        title="Project Alpha Weekly Sync",
        location="Project Alpha War Room",
    )

    # --- Act ---
    updated_event_obj = await db_service.update_event(
        event_id=event_id, update_data=update_payload
    )

    # --- Assert ---
    assert updated_event_obj is not None
    assert isinstance(updated_event_obj, Event)
    assert updated_event_obj.id == event_id
    assert updated_event_obj.title == "Project Alpha Weekly Sync"
    assert updated_event_obj.location == "Project Alpha War Room"
    assert updated_event_obj.start_time == base_start_naive
    assert updated_event_obj.end_time == base_end_naive
    assert updated_event_obj.time_zone == "Europe/London"
    assert updated_event_obj.rrule == rrule_str
    assert updated_event_obj.notes == "Standard sync notes"
    assert updated_event_obj.updated_at > initial_updated_at

    db_check = await test_session.get(Event, event_id)
    assert db_check is not None
    assert db_check.title == "Project Alpha Weekly Sync"
    assert db_check.location == "Project Alpha War Room"
    assert db_check.updated_at == updated_event_obj.updated_at

    stmt = select(ModifiedInstance).where(ModifiedInstance.event_id == event_id)
    result = await test_session.execute(stmt)
    mod_instances = result.scalars().all()
    assert len(mod_instances) == 0

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

# TODO: Add tests for more complex update scenarios:
# - Updating recurring instance that already has a ModifiedInstance
# - Updating recurring series time/rrule (currently NotImplementedError)
# - Updating event with conflict checking enabled for time changes
