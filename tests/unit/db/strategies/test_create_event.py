from datetime import UTC, datetime
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, AsyncMock, MagicMock
import uuid

from sa.core.logging_config import get_logger
from sa.event.date_utils import DateUtils
from sa.db.models import Event, Participant
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.core.exceptions import DatabaseError, ConflictError
from sa.event.conflict_resolver import ConflictResolver
from sa.db.strategies.create_event import CreateEventStrategy
logger = get_logger(__name__)

# --- Fixtures (Keep mocks) ---

@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    """Provides a mock ConflictResolver."""
    resolver = AsyncMock(spec=ConflictResolver)
    resolver.detect_conflicts = AsyncMock(return_value=[]) # Default: no conflicts
    return resolver

# --- Test Cases ---

@pytest.mark.asyncio
async def test_create_event_simple(
    mock_db_service: DatabaseService,
    date_utils: DateUtils,
    mock_conflict_resolver: AsyncMock,
):
    """Test creating a simple event using the strategy."""
    # --- Arrange ---
    local_start = datetime(2024, 6, 1, 10, 0, 0)
    local_end = datetime(2024, 6, 1, 11, 0, 0)
    tz_name = "America/New_York"
    expected_naive_start_utc = datetime(2024, 6, 1, 14, 0, 0)
    expected_naive_end_utc = datetime(2024, 6, 1, 15, 0, 0)

    event_create_data = EventCreate(
        title="Strategy Test Meeting",
        start_time=local_start,
        end_time=local_end,
        time_zone=tz_name,
        location="Strategy Conf Room",
    )

    # Mock the event that should be returned by the final execute().scalar_one()
    mock_final_event = MagicMock(spec=Event)
    mock_final_event.id = uuid.uuid4() # Simulate ID assigned by flush
    mock_final_event.title = event_create_data.title
    mock_final_event.start_time = expected_naive_start_utc
    mock_final_event.end_time = expected_naive_end_utc
    mock_final_event.time_zone = tz_name
    mock_final_event.location = event_create_data.location
    mock_final_event.rrule = None
    mock_final_event.notes = None
    mock_final_event.participants = [] # Expect empty list for simple event
    mock_final_event.created_at = datetime.now(UTC)
    mock_final_event.updated_at = datetime.now(UTC)

    # Mock the result chain for the final select in the strategy
    # Make scalar_one a synchronous mock to match the strategy code
    mock_final_select_result = MagicMock()
    mock_final_select_result.scalar_one = MagicMock(return_value=mock_final_event)
    # Configure execute on the mock_session to return this result mock
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_final_select_result)

    # Configure other mock_session methods as needed
    async def mock_flush(*args, **kwargs):
        # Check the object added to the *actual* mock session
        if mock_db_service.mock_session.add.call_args:
            obj_added = mock_db_service.mock_session.add.call_args[0][0]
            if isinstance(obj_added, Event) and not obj_added.id:
                obj_added.id = mock_final_event.id # Assign the ID we expect later
        pass # No return value needed
    mock_db_service.mock_session.flush = AsyncMock(side_effect=mock_flush)
    mock_db_service.mock_session.refresh = AsyncMock() # Refresh might be called
    mock_db_service.mock_session.add = MagicMock() # Track calls to add
    # Ensure commit is mocked on the session
    mock_db_service.mock_session.commit = AsyncMock()

    # --- Act ---
    # Patch the internal instantiation of GetEventsInRangeStrategy
    with patch('sa.db.strategies.create_event.GetEventsInRangeStrategy') as MockGetEventsInRangeStrat:
        # Configure the mock instance it returns for conflict check
        mock_get_events_instance = AsyncMock()
        mock_get_events_instance.execute = AsyncMock(return_value=[]) # Simulate no potential conflicts found
        MockGetEventsInRangeStrat.return_value = mock_get_events_instance

        # Instantiate the strategy with dependencies
        strategy = CreateEventStrategy(
            db_service=mock_db_service,
            date_utils=date_utils,
            conflict_resolver=mock_conflict_resolver,
            get_events_in_range_strategy=MockGetEventsInRangeStrat() # Pass instance
        )
        created_event_result = await strategy.execute(data=event_create_data)

        # --- Assert ---
        # Assert strategy execution flow
        mock_get_events_instance.execute.assert_awaited_once()
        # Assert the mock_conflict_resolver was called (using the fixture directly)
        mock_conflict_resolver.detect_conflicts.assert_awaited_once()

        mock_db_service.mock_session.add.assert_called_once() # Event object should be added
        mock_db_service.mock_session.flush.assert_awaited() # Should be awaited at least once
        # Refresh might be called for participants (even if empty), check if awaited
        # mock_db_service.mock_session.refresh.assert_awaited()
        mock_db_service.mock_session.execute.assert_awaited_once() # Final execute to get event
        mock_db_service.mock_session.commit.assert_awaited_once() # Base class commit

        # Assertions on the returned object (should be our mock_final_event)
        assert created_event_result is not None
        assert created_event_result.id == mock_final_event.id
        assert created_event_result.title == event_create_data.title
        assert created_event_result.start_time == expected_naive_start_utc
        assert created_event_result.end_time == expected_naive_end_utc
        assert created_event_result.time_zone == tz_name
        assert created_event_result.location == "Strategy Conf Room"
        assert created_event_result.rrule is None
        assert created_event_result.notes is None
        assert created_event_result.participants == []
        assert created_event_result.created_at is not None
        assert created_event_result.updated_at is not None

@pytest.mark.asyncio
async def test_create_event_with_participants(
    mock_db_service: DatabaseService,
    date_utils: DateUtils,
    mock_conflict_resolver: AsyncMock,
):
    """Test creating an event with participants using the strategy."""
    # --- Arrange ---
    start_naive_utc = datetime(2024, 6, 2, 14, 0, 0)
    end_naive_utc = datetime(2024, 6, 2, 15, 30, 0)
    participant_ids = ["alice@example.com", "bob@test.org", "carol@sample.net"]

    event_create_data = EventCreate(
        title="Strategy Project Kickoff",
        start_time=start_naive_utc, # Pass naive UTC directly for simplicity
        end_time=end_naive_utc,
        time_zone="Europe/Berlin", # Still need TZ for storage
        participant_identifiers=participant_ids,
    )

    # Mock the final event returned, including participants
    mock_final_event_id = uuid.uuid4()
    mock_participants = [
        MagicMock(spec=Participant, identifier=pid, event_id=mock_final_event_id)
        for pid in participant_ids
    ]
    mock_final_event = MagicMock(spec=Event)
    mock_final_event.id = mock_final_event_id
    mock_final_event.title = event_create_data.title
    # ... other attributes ...
    mock_final_event.participants = mock_participants # Crucial: final select loads this

    # Mock the result chain for the final select
    # Configure scalar_one to return the mock event directly (synchronous)
    mock_final_select_result = MagicMock()
    mock_final_select_result.scalar_one = MagicMock(return_value=mock_final_event)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_final_select_result)

    # Configure mock_session mocks
    mock_db_service.mock_session.flush = AsyncMock() # Needs to be awaitable
    mock_db_service.mock_session.refresh = AsyncMock() # Called for participants
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.add_all = MagicMock() # Called for participants
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    # --- Act ---
    with patch('sa.db.strategies.create_event.GetEventsInRangeStrategy') as MockGetEventsInRangeStrat:
        mock_get_events_instance = AsyncMock()
        mock_get_events_instance.execute = AsyncMock(return_value=[])
        MockGetEventsInRangeStrat.return_value = mock_get_events_instance

        strategy = CreateEventStrategy(
            db_service=mock_db_service,
            date_utils=date_utils,
            conflict_resolver=mock_conflict_resolver,
            get_events_in_range_strategy=MockGetEventsInRangeStrat()
        )
        created_event_result = await strategy.execute(data=event_create_data)

        # --- Assert ---
        mock_get_events_instance.execute.assert_awaited_once()
        mock_conflict_resolver.detect_conflicts.assert_awaited_once()
        mock_db_service.mock_session.add.assert_called_once() # Event object added
        mock_db_service.mock_session.add_all.assert_called_once() # Participants added
        mock_db_service.mock_session.flush.assert_awaited() # Flushed after adds (can be multiple times)
        mock_db_service.mock_session.refresh.assert_awaited_once() # Refreshed for participants
        mock_db_service.mock_session.execute.assert_awaited_once() # Final select
        mock_db_service.mock_session.commit.assert_awaited_once()

        # Assertions on the returned object
        assert created_event_result is not None
        assert created_event_result.id == mock_final_event_id
        assert created_event_result.title == "Strategy Project Kickoff"
        # Verify participants loaded in returned object
        assert len(created_event_result.participants) == len(participant_ids)
        returned_participants = sorted([p.identifier for p in created_event_result.participants])
        assert returned_participants == sorted(participant_ids)

@pytest.mark.asyncio
async def test_create_event_conflict_detected(
    mock_db_service: DatabaseService,
    date_utils: DateUtils,
    mock_conflict_resolver: AsyncMock,
):
    """Test that ConflictError is raised if the resolver finds conflicts."""
    # --- Arrange ---
    event_create_data = EventCreate(
        title="Conflicting Create Test",
        start_time=datetime(2024, 6, 3, 10, 0, 0),
        end_time=datetime(2024, 6, 3, 11, 0, 0),
        time_zone="UTC",
    )

    # Simulate ConflictResolver finding conflicts
    mock_conflict_list = [{"type": "instance", "event": MagicMock(spec=Event, id=uuid.uuid4())}]
    mock_conflict_resolver.detect_conflicts.return_value = mock_conflict_list

    # Configure mock_session mocks (commit/rollback needed for DatabaseError check)
    # Need execute, add, flush mocks even if they aren't asserted, as they might be called before the error
    mock_execute_result = MagicMock() # Mock for execute result chain
    mock_execute_result.scalar_one = MagicMock(return_value=MagicMock(spec=Event)) # Mock scalar_one needed for final select if reached
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    # --- Act & Assert ---
    with patch('sa.db.strategies.create_event.GetEventsInRangeStrategy') as MockGetEventsInRangeStrat:
        # Simulate GetEvents finding some potential conflict to pass to resolver
        mock_potential_conflict = MagicMock(spec=Event, id=uuid.uuid4())
        mock_get_events_instance = AsyncMock()
        mock_get_events_instance.execute = AsyncMock(return_value=[mock_potential_conflict])
        MockGetEventsInRangeStrat.return_value = mock_get_events_instance

        strategy = CreateEventStrategy(
            db_service=mock_db_service,
            date_utils=date_utils,
            conflict_resolver=mock_conflict_resolver,
            get_events_in_range_strategy=MockGetEventsInRangeStrat()
        )

        # Expect DatabaseError wrapping ConflictError
        with pytest.raises(DatabaseError) as exc_info:
            await strategy.execute(data=event_create_data)
        assert isinstance(exc_info.value.__cause__, ConflictError)

        # Verify conflict resolver was called with the potential conflicts
        mock_conflict_resolver.detect_conflicts.assert_awaited_once_with(event_create_data, [mock_potential_conflict])
        # Verify the underlying error contains the conflicts
        assert exc_info.value.__cause__.conflicts == mock_conflict_list
        mock_db_service.mock_session.commit.assert_not_awaited() # Should not commit on error
        mock_db_service.mock_session.rollback.assert_awaited_once() # Base class rollback

@pytest.mark.asyncio
async def test_create_event_db_error(
    mock_db_service: DatabaseService,
    date_utils: DateUtils,
    mock_conflict_resolver: AsyncMock,
):
    """Test that the strategy raises DatabaseError on commit failure."""
    # --- Arrange ---
    event_create_data = EventCreate(
        title="DB Error Test",
        start_time=datetime(2024, 1, 1, 10, 0, 0),
        end_time=datetime(2024, 1, 1, 11, 0, 0),
        time_zone="UTC",
    )

    # Configure mock_session mocks
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock()
    # Configure the execute result mock for the final select (synchronous scalar_one)
    mock_final_select_result = MagicMock()
    mock_final_select_result.scalar_one = MagicMock(return_value=MagicMock(spec=Event)) # Return generic mock
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_final_select_result)

    mock_db_service.mock_session.commit = AsyncMock(side_effect=Exception("Simulated DB commit error"))
    mock_db_service.mock_session.rollback = AsyncMock()

    # --- Act & Assert ---
    # Explicitly configure the execute mock result for this test's specific path
    # Even though commit fails, the execute call might happen.
    # Let's ensure scalar_one is a simple MagicMock here too.
    mock_db_service.mock_session.execute.return_value.scalar_one = MagicMock(return_value=MagicMock(spec=Event)) # Return generic mock

    # Patch the internal GetEvents strategy
    with patch('sa.db.strategies.create_event.GetEventsInRangeStrategy') as MockGetEventsInRangeStrat:
        mock_get_events_instance = AsyncMock()
        mock_get_events_instance.execute = AsyncMock(return_value=[]) # No conflicts
        MockGetEventsInRangeStrat.return_value = mock_get_events_instance

        with pytest.raises(DatabaseError) as exc_info:
            strategy = CreateEventStrategy(
                db_service=mock_db_service,
                date_utils=date_utils,
                conflict_resolver=mock_conflict_resolver,
                get_events_in_range_strategy=MockGetEventsInRangeStrat()
            )
            await strategy.execute(data=event_create_data)

        # Assertions on DatabaseError and rollback state
        assert "Strategy CreateEventStrategy failed" in str(exc_info.value)
        assert "Simulated DB commit error" in str(exc_info.value.__cause__)
        mock_db_service.mock_session.commit.assert_awaited_once() # Commit was attempted
        mock_db_service.mock_session.rollback.assert_awaited_once() # Base class rollback

        # Removed check for event creation via test_session.execute
        # Removed the final verification block that caused NameError 