from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

# Import the actual models
from scheduling_agent.db.models import Event, ExcludedDate, ModifiedInstance, Participant

# In-memory SQLite engine for testing
# Use a different name to avoid potential conflicts if tests are run in parallel
# or if module-level state persists unexpectedly.
# Using check_same_thread=False for simplicity in testing setup
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})


@pytest.fixture(autouse=True)
def create_test_tables():
    """Create tables before each test function and drop them after."""
    SQLModel.metadata.create_all(engine)
    yield
    SQLModel.metadata.drop_all(engine)


@pytest.fixture(name="session")
def session_fixture():
    """Provide a database session for testing."""
    with Session(engine) as session:
        yield session


def test_create_simple_event(session: Session):
    """Test creating a basic Event instance with naive UTC datetimes."""
    # Define start/end times with timezone info initially
    start_aware = datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
    end_aware = datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)

    # Application logic would convert to naive UTC before saving
    start_naive_utc = start_aware.replace(tzinfo=None)
    end_naive_utc = end_aware.replace(tzinfo=None)

    event_data = {
        "title": "Test Meeting",
        "start_time": start_naive_utc,  # Store naive UTC
        "end_time": end_naive_utc,  # Store naive UTC
        "time_zone": "America/New_York",  # Store original timezone separately
        "location": "Conference Room A",
    }

    # --- Act ---
    db_event = Event(**event_data)
    session.add(db_event)
    session.commit()
    session.refresh(db_event)

    # --- Assert ---
    assert db_event.id is not None
    assert db_event.title == "Test Meeting"

    # Check that retrieved datetimes are naive
    assert db_event.start_time.tzinfo is None
    assert db_event.end_time.tzinfo is None
    assert db_event.created_at.tzinfo is None
    assert db_event.updated_at.tzinfo is None

    # Check that the naive datetimes represent the correct UTC time
    assert db_event.start_time == start_naive_utc
    assert db_event.end_time == end_naive_utc

    # Check other fields
    assert db_event.time_zone == "America/New_York"
    assert db_event.location == "Conference Room A"
    assert db_event.notes is None
    assert db_event.rrule is None
    assert isinstance(db_event.created_at, datetime)
    assert isinstance(db_event.updated_at, datetime)

    # Check that created_at/updated_at are recent (naive UTC)
    now_naive_utc = datetime.now(UTC).replace(tzinfo=None)
    assert now_naive_utc - db_event.created_at < timedelta(seconds=5)
    assert now_naive_utc - db_event.updated_at < timedelta(seconds=5)


def test_create_event_with_participants(session: Session):
    """Test creating an event and associating participants with it."""
    start_naive_utc = datetime(2024, 2, 1, 14, 0, 0)
    end_naive_utc = datetime(2024, 2, 1, 15, 0, 0)

    participant_ids = ["alice@example.com", "bob@example.com"]

    # --- Act ---
    # Create the event first
    event = Event(
        title="Team Sync",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="Europe/London",
    )
    session.add(event)
    session.commit()
    session.refresh(event)  # Get the generated event ID

    # Create and link participants
    for identifier in participant_ids:
        participant = Participant(event_id=event.id, identifier=identifier)
        session.add(participant)

    session.commit()

    # --- Assert ---
    # Retrieve the event again to check relationships
    retrieved_event = session.get(Event, event.id)
    assert retrieved_event is not None
    assert len(retrieved_event.participants) == 2

    retrieved_identifiers = sorted([p.identifier for p in retrieved_event.participants])
    assert retrieved_identifiers == sorted(participant_ids)

    # Check back-population
    participant_1 = session.exec(
        select(Participant).where(Participant.identifier == participant_ids[0])
    ).first()
    assert participant_1 is not None
    assert participant_1.event == retrieved_event
    assert participant_1.event_id == retrieved_event.id


def test_create_event_with_rrule(session: Session):
    """Test creating an event with a recurrence rule."""
    start_naive_utc = datetime(2024, 3, 10, 9, 0, 0)
    end_naive_utc = datetime(2024, 3, 10, 10, 0, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=10"

    # --- Act ---
    event = Event(
        title="Weekly Standup",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="UTC",
        rrule=rrule_str,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    # --- Assert ---
    retrieved_event = session.get(Event, event.id)
    assert retrieved_event is not None
    assert retrieved_event.title == "Weekly Standup"
    assert retrieved_event.start_time == start_naive_utc
    assert retrieved_event.rrule == rrule_str
    assert retrieved_event.location is None


def test_create_excluded_date_for_event(session: Session):
    """Test creating an ExcludedDate for a recurring event."""
    start_naive_utc = datetime(2024, 4, 1, 10, 0, 0)
    end_naive_utc = datetime(2024, 4, 1, 11, 0, 0)
    rrule_str = "FREQ=DAILY;COUNT=5"
    exclusion_date_naive_utc = datetime(2024, 4, 3, 0, 0, 0)  # Exclude the 3rd occurrence

    # --- Act ---
    # Create a recurring event
    event = Event(
        title="Daily Huddle",
        start_time=start_naive_utc,
        end_time=end_naive_utc,
        time_zone="UTC",
        rrule=rrule_str,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    # Create an excluded date for that event
    excluded_date = ExcludedDate(event_id=event.id, excluded_date=exclusion_date_naive_utc)
    session.add(excluded_date)
    session.commit()

    # --- Assert ---
    # Retrieve the event and check its excluded dates
    retrieved_event = session.get(Event, event.id)
    assert retrieved_event is not None
    assert len(retrieved_event.excluded_dates) == 1

    retrieved_exclusion = retrieved_event.excluded_dates[0]
    assert retrieved_exclusion.excluded_date == exclusion_date_naive_utc
    assert retrieved_exclusion.event_id == event.id

    # Check back-population from ExcludedDate
    assert retrieved_exclusion.event == retrieved_event


def test_create_modified_instance_for_event(session: Session):
    """Test creating a ModifiedInstance for a recurring event."""
    # --- Arrange ---
    # Base recurring event (e.g., weekly meeting)
    base_start_naive_utc = datetime(2024, 5, 6, 13, 0, 0)  # Monday May 6th
    base_end_naive_utc = datetime(2024, 5, 6, 14, 0, 0)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=4"

    event = Event(
        title="Weekly Planning",
        start_time=base_start_naive_utc,
        end_time=base_end_naive_utc,
        time_zone="America/Denver",
        location="Main Office",
        rrule=rrule_str,
    )
    session.add(event)
    session.commit()
    session.refresh(event)

    # Modify the second instance (May 13th)
    original_instance_date_naive_utc = datetime(2024, 5, 13, 0, 0, 0)
    modified_start_naive_utc = datetime(2024, 5, 13, 13, 30, 0)  # Move 30 mins later
    modified_end_naive_utc = datetime(2024, 5, 13, 14, 30, 0)

    modified_instance = ModifiedInstance(
        event_id=event.id,
        original_date=original_instance_date_naive_utc,
        start_time=modified_start_naive_utc,
        end_time=modified_end_naive_utc,
        time_zone="America/Denver",  # Keep same timezone for this test
        title="Weekly Planning (Shifted)",  # Give it a new title
        location="Virtual Only",  # Change location
    )

    # --- Act ---
    session.add(modified_instance)
    session.commit()

    # --- Assert ---
    retrieved_event = session.get(Event, event.id)
    assert retrieved_event is not None
    assert len(retrieved_event.modified_instances) == 1

    retrieved_modification = retrieved_event.modified_instances[0]
    assert retrieved_modification.event_id == event.id
    assert retrieved_modification.original_date == original_instance_date_naive_utc
    assert retrieved_modification.start_time == modified_start_naive_utc
    assert retrieved_modification.end_time == modified_end_naive_utc
    assert retrieved_modification.time_zone == "America/Denver"
    assert retrieved_modification.title == "Weekly Planning (Shifted)"
    assert retrieved_modification.location == "Virtual Only"
    assert retrieved_modification.notes is None  # Notes weren't set

    # Check back-population
    assert retrieved_modification.event == retrieved_event


# TODO: Add tests for:
# - Event with excluded dates (requires ExcludedDate model test)
# - Event with modified instances (requires ModifiedInstance model test)
# - Default values (check id creation, created_at/updated_at defaults explicitly)
# - Updating an event (check updated_at changes)
# - Relationship loading (participants, etc.) -> Covered basic case here
# - Constraints (e.g., unique constraints on related models)

# TODO: Add tests for:
# - Event with excluded dates (requires ExcludedDate model test)
# - Event with modified instances (requires ModifiedInstance model test)
# - Default values (created_at, updated_at, id)
# - Updating an event (updated_at changes)
# - Relationship loading (e.g., event.participants)
# - Constraints (e.g., unique constraints if any)
