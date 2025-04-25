from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import patch, MagicMock, AsyncMock
import uuid

from sa.core.logging_config import get_logger
from sa.db.models import Event, ModifiedInstance, Participant
from sa.db.dao import EventCreate, EventUpdate
from sa.db.service import DatabaseService
from sa.db.strategies.update_event import UpdateEventStrategy
from sa.core.exceptions import DatabaseError
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils

logger = get_logger(__name__)

# --- Test Cases --- #

# Helper to make mocking execute side effects cleaner
def create_execute_side_effect(steps: list[tuple[str, MagicMock]]):
    """Creates an async side effect function for mock_session.execute.
    steps should be a list of tuples: ('select' or 'update', mock_result_object)
    """
    call_counter = 0
    async def execute_side_effect(*args, **kwargs):
        nonlocal call_counter
        current_call = call_counter
        call_counter += 1

        if current_call >= len(steps):
            raise AssertionError(f"Unexpected call ({current_call + 1}) to session.execute")

        expected_type_str, mock_result = steps[current_call]
        statement = args[0]

        # Use string representation for broader compatibility (SQLAlchemy vs SQLModel Select types)
        statement_type_str = str(type(statement)).lower()

        if expected_type_str == 'select' and 'select' in statement_type_str:
            # For select, the mock_result should have a configured .scalar_one_or_none
            return mock_result
        elif expected_type_str == 'update' and ('update' in statement_type_str or 'update=' in str(statement).lower()):
            # For update, the mock_result should have .rowcount configured
            return mock_result
        else:
            raise TypeError(
                f"Mock execute mismatch on call {current_call + 1}: "
                f"Expected '{expected_type_str}', got '{type(statement)}'"
            )
    return execute_side_effect

@pytest.mark.asyncio
async def test_update_non_recurring_event(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test updating fields of a simple non-recurring event via strategy (dict method)."""
    # --- Arrange ---
    event_id = uuid.uuid4()
    initial_start_time = datetime(2024, 9, 1, 10, 0, 0)
    initial_end_time = datetime(2024, 9, 1, 11, 0, 0)
    initial_updated_at = datetime.now(UTC) - timedelta(minutes=5)

    # Mock initial event fetched
    mock_initial_event = MagicMock(spec=Event)
    mock_initial_event.id = event_id
    mock_initial_event.title = "Initial Non-Recurring"
    mock_initial_event.start_time = initial_start_time
    mock_initial_event.end_time = initial_end_time
    mock_initial_event.time_zone = "UTC"
    mock_initial_event.location = "Room 1"
    mock_initial_event.notes = "Original"
    mock_initial_event.rrule = None
    mock_initial_event.updated_at = initial_updated_at
    mock_initial_result_obj = MagicMock()
    mock_initial_result_obj.scalar_one_or_none = MagicMock(return_value=mock_initial_event)

    # Mock the result for the UPDATE statement
    mock_update_execute_result = MagicMock()
    mock_update_execute_result.rowcount = 1

    # Mock the event refetched after update
    mock_refetched_event = MagicMock(spec=Event)
    mock_refetched_event.id = event_id
    mock_refetched_event.title = "Updated Non-Recurring Title"
    mock_refetched_event.start_time = initial_start_time
    mock_refetched_event.end_time = initial_end_time
    mock_refetched_event.time_zone = "UTC"
    mock_refetched_event.location = "Room 2"
    mock_refetched_event.notes = "New Notes"
    mock_refetched_event.rrule = None
    mock_refetched_event.updated_at = datetime.now(UTC)
    mock_refetch_result_obj = MagicMock()
    mock_refetch_result_obj.scalar_one_or_none = MagicMock(return_value=mock_refetched_event)

    # Configure execute side effect using helper
    mock_db_service.mock_session.execute = AsyncMock(side_effect=create_execute_side_effect([
        ('select', mock_initial_result_obj),
        ('update', mock_update_execute_result),
        ('select', mock_refetch_result_obj),
    ]))

    # Mock unused session methods
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock()

    update_payload = EventUpdate(
        title="Updated Non-Recurring Title",
        location="Room 2", notes="New Notes"
    )

    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (event_id, update_payload)

    # --- Act ---
    updated_result = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 3

    # Check the update call details (second call to execute)
    update_call = mock_db_service.mock_session.execute.await_args_list[1]
    update_stmt = update_call.args[0]
    compiled_params = update_stmt.compile().params
    assert compiled_params['title'] == "Updated Non-Recurring Title"
    assert compiled_params['location'] == "Room 2"
    assert compiled_params['notes'] == "New Notes"
    assert 'start_time' not in compiled_params
    assert 'time_zone' not in compiled_params
    assert 'updated_at' in compiled_params

    # Check unused mocks
    mock_db_service.mock_session.add.assert_not_called()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()

    assert updated_result is not None
    assert updated_result is mock_refetched_event
    assert updated_result.id == event_id
    assert updated_result.title == "Updated Non-Recurring Title"
    assert updated_result.location == "Room 2"
    assert updated_result.notes == "New Notes"
    assert updated_result.updated_at > initial_updated_at # Basic check

@pytest.mark.asyncio
async def test_update_recurring_instance(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test updating a single instance of a recurring event creates a ModifiedInstance."""
    # --- Arrange ---
    event_id = uuid.uuid4()
    base_start_naive = datetime(2024, 10, 7, 10, 0, 0)
    base_end_naive = datetime(2024, 10, 7, 11, 0, 0)
    rrule = "FREQ=WEEKLY;COUNT=4"

    # Mock the base event returned by the first session.execute
    mock_base_event = MagicMock(spec=Event)
    mock_base_event.id = event_id
    mock_base_event.title = "Recurring Weekly"
    mock_base_event.start_time = base_start_naive
    mock_base_event.end_time = base_end_naive
    mock_base_event.time_zone = "UTC" # Important for date conversion
    mock_base_event.location = "Office A"
    mock_base_event.rrule = rrule
    # modified_instances no longer needed for selectinload

    # Target the second instance (Oct 14th)
    target_instance_original_start_local = datetime(2024, 10, 14, 10, 0, 0) # Passed in payload
    target_instance_original_start_naive_utc = datetime(2024, 10, 14, 10, 0, 0) # Calculated from base TZ (UTC)

    new_instance_start_local = datetime(2024, 10, 14, 10, 30, 0) # New local time
    new_instance_end_local = datetime(2024, 10, 14, 11, 30, 0)
    new_instance_tz = "America/New_York"

    # Expected naive UTC times after conversion by strategy
    expected_new_start_naive_utc = datetime(2024, 10, 14, 14, 30, 0)
    expected_new_end_naive_utc = datetime(2024, 10, 14, 15, 30, 0)

    # Mock the ModifiedInstance lookup (second session.execute) - assume not found initially
    mock_execute_result_base = MagicMock()
    mock_execute_result_base.scalar_one_or_none = MagicMock(return_value=mock_base_event)
    mock_execute_result_mod_instance_select = MagicMock()
    mock_execute_result_mod_instance_select.scalar_one_or_none = MagicMock(return_value=None) # No existing mod instance

    # Setup side effect for session.execute mock
    mock_db_service.mock_session.execute = AsyncMock(side_effect=[
        mock_execute_result_base,                 # First call gets base event
        mock_execute_result_mod_instance_select,  # Second call checks for existing mod_instance
        # NO third execute call expected, as we add/flush/refresh the new object
    ])

    # We need to capture the object passed to add, flush, and refresh
    added_object = None
    refreshed_object = None

    def capture_add(obj):
        nonlocal added_object
        assert isinstance(obj, ModifiedInstance) # Ensure the correct type is added
        added_object = obj

    async def capture_refresh(obj, attribute_names=None):
        nonlocal refreshed_object
        # Simulate refresh by assigning an ID if missing
        if not getattr(obj, 'id', None):
             obj.id = uuid.uuid4() # Give it an ID
        refreshed_object = obj
        return obj # Important: return the object for the strategy to use

    mock_db_service.mock_session.add = MagicMock(side_effect=capture_add)
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock(side_effect=capture_refresh)
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    update_payload = EventUpdate(
        title="Recurring Weekly (Late Start Instance)",
        location="Office B",
        original_start_time=target_instance_original_start_local, # Identify instance
        start_time=new_instance_start_local, # New time/tz for this instance
        end_time=new_instance_end_local,
        time_zone=new_instance_tz
    )

    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (event_id, update_payload)

    # --- Act ---
    updated_result = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 2 # Base event select, Mod instance select
    mock_db_service.mock_session.add.assert_called_once() # ModifiedInstance should be added
    mock_db_service.mock_session.flush.assert_awaited_once() # Needed to assign ID
    mock_db_service.mock_session.refresh.assert_awaited_once() # Needed to load defaults/ID

    assert updated_result is not None
    assert isinstance(updated_result, ModifiedInstance)
    # Check that the returned object is the one that was added and refreshed
    assert added_object is refreshed_object
    assert updated_result is refreshed_object

    # Check attributes of the returned ModifiedInstance
    assert updated_result.event_id == event_id
    assert updated_result.title == "Recurring Weekly (Late Start Instance)"
    assert updated_result.location == "Office B"
    assert updated_result.start_time == expected_new_start_naive_utc
    assert updated_result.end_time == expected_new_end_naive_utc
    assert updated_result.time_zone == new_instance_tz
    assert updated_result.original_date == target_instance_original_start_naive_utc
    assert updated_result.id is not None # Should have an ID after flush/refresh

@pytest.mark.asyncio
async def test_update_recurring_series_simple_fields(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test updating simple fields (title, location, notes) of a recurring series (dict method)."""
    # --- Arrange ---
    event_id = uuid.uuid4()
    initial_start_time = datetime(2024, 11, 4, 14, 0, 0)
    initial_end_time = datetime(2024, 11, 4, 15, 0, 0)
    rrule = "FREQ=WEEKLY;COUNT=5"
    initial_updated_at = datetime.now(UTC) - timedelta(hours=1)

    # Mock the event object returned by the FIRST execute call (initial select)
    mock_initial_event = MagicMock(spec=Event)
    mock_initial_event.id = event_id
    mock_initial_event.title = "Regular Sync"
    mock_initial_event.start_time = initial_start_time
    mock_initial_event.end_time = initial_end_time
    mock_initial_event.time_zone = "UTC"
    mock_initial_event.location = "Room X"
    mock_initial_event.notes = "Standard"
    mock_initial_event.rrule = rrule
    mock_initial_event.updated_at = initial_updated_at
    # modified_instances no longer needed for selectinload

    # Mock the result object for the SECOND execute call (the UPDATE statement)
    mock_update_execute_result = MagicMock()
    mock_update_execute_result.rowcount = 1 # Simulate successful update

    # Mock the event object returned by the THIRD execute call (refetch select)
    mock_refetched_event = MagicMock(spec=Event)
    mock_refetched_event.id = event_id
    mock_refetched_event.title = "Project Omega Sync" # Expected final state
    mock_refetched_event.start_time = initial_start_time
    mock_refetched_event.end_time = initial_end_time
    mock_refetched_event.time_zone = "UTC"
    mock_refetched_event.location = "Room Y" # Expected final state
    mock_refetched_event.notes = "Standard" # Unchanged
    mock_refetched_event.rrule = rrule # Unchanged
    mock_refetched_event.updated_at = datetime.now(UTC)

    # Configure execute side effect using helper
    mock_db_service.mock_session.execute = AsyncMock(side_effect=create_execute_side_effect([
        ('select', MagicMock(scalar_one_or_none=MagicMock(return_value=mock_initial_event))),
        ('update', mock_update_execute_result),
        ('select', MagicMock(scalar_one_or_none=MagicMock(return_value=mock_refetched_event))),
    ]))

    # Mock unused session methods
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock()

    update_payload = EventUpdate(
        title="Project Omega Sync",
        location="Room Y",
        # NOTE: No original_start_time indicates series update
        # NOTE: No notes included in payload
    )

    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (event_id, update_payload)

    # --- Act ---
    updated_result = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 3 # Select, Update, Refetch

    # Check the update call details (second call to execute)
    update_call = mock_db_service.mock_session.execute.await_args_list[1]
    update_stmt = update_call.args[0]
    compiled_params = update_stmt.compile().params
    assert compiled_params['title'] == "Project Omega Sync"
    assert compiled_params['location'] == "Room Y"
    assert 'notes' not in compiled_params # Should not be updated
    assert 'start_time' not in compiled_params
    assert 'rrule' not in compiled_params
    assert 'updated_at' in compiled_params

    # Check unused mocks
    mock_db_service.mock_session.add.assert_not_called()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()

    assert updated_result is not None
    assert updated_result is mock_refetched_event # Should be the refetched object
    assert updated_result.id == event_id
    assert updated_result.title == "Project Omega Sync"
    assert updated_result.location == "Room Y"
    assert updated_result.start_time == initial_start_time
    assert updated_result.end_time == initial_end_time
    assert updated_result.time_zone == "UTC"
    assert updated_result.rrule == rrule
    assert updated_result.notes == "Standard"
    assert updated_result.updated_at > initial_updated_at

@pytest.mark.asyncio
async def test_update_event_not_found(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test updating a non-existent event returns None."""
    # --- Arrange ---
    non_existent_id = uuid.uuid4()

    # Configure session.execute mock to return None
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=None)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)
    # Ensure commit/rollback are mocked
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()
    # Mock add/flush/refresh just in case
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock()

    update_payload = EventUpdate(title="Should Not Update")
    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (non_existent_id, update_payload)

    # --- Act ---
    updated_result = await strategy.execute(data=strategy_input)

    # --- Assert ---
    mock_db_service.mock_session.execute.assert_awaited_once()
    mock_db_service.mock_session.add.assert_not_called()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()
    assert updated_result is None

@pytest.mark.asyncio
async def test_update_recurring_series_time_raises_not_implemented(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test attempting to update time/rrule for a series raises NotImplementedError."""
    # --- Arrange ---
    event_id = uuid.uuid4()

    # Mock the base event returned by session.execute
    mock_initial_event = MagicMock(spec=Event)
    mock_initial_event.id = event_id
    mock_initial_event.rrule = "FREQ=WEEKLY" # Ensure it's recurring
    mock_initial_event.time_zone = "UTC"
    mock_initial_event.modified_instances = []

    # Configure session.execute mock
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_initial_event)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    update_payload = EventUpdate(start_time=datetime(2025, 1, 6, 9, 30)) # Attempt time change
    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (event_id, update_payload)

    # --- Act & Assert ---
    # Expect DatabaseError wrapping NotImplementedError
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=strategy_input)

    mock_db_service.mock_session.execute.assert_awaited_once()
    assert isinstance(exc_info.value.__cause__, NotImplementedError)

@pytest.mark.asyncio
async def test_update_instance_on_non_recurring_raises_value_error(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test attempting an instance update on non-recurring event raises ValueError."""
    # --- Arrange ---
    event_id = uuid.uuid4()
    initial_start_time = datetime(2025, 2, 3, 11, 0)

    # Mock a NON-recurring event returned by session.execute
    mock_initial_event = MagicMock(spec=Event)
    mock_initial_event.id = event_id
    mock_initial_event.rrule = None # Ensure it's NOT recurring
    mock_initial_event.start_time = initial_start_time
    mock_initial_event.time_zone = "UTC"
    mock_initial_event.modified_instances = []

    # Configure session.execute mock
    mock_execute_result = MagicMock()
    mock_execute_result.scalar_one_or_none = MagicMock(return_value=mock_initial_event)
    mock_db_service.mock_session.execute = AsyncMock(return_value=mock_execute_result)

    update_payload = EventUpdate(
        original_start_time=initial_start_time, # Provide original_start_time
        title="This Should Fail"
        # NOTE: start_time, end_time, time_zone are missing which will trigger
        # the *second* ValueError inside _handle_recurring_instance_update
        # if the first check (if not db_event.rrule) is bypassed or fails.
        # --> Let's stick to the original logic, the check should raise ValueError("Cannot specify...")
    )
    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (event_id, update_payload)

    # --- Act & Assert ---
    # Expect DatabaseError wrapping ValueError
    with pytest.raises(DatabaseError) as exc_info:
        await strategy.execute(data=strategy_input)

    mock_db_service.mock_session.execute.assert_awaited_once()
    assert isinstance(exc_info.value.__cause__, ValueError)
    # Correct assertion based on the code logic: the check for rrule should fire first.
    assert "Cannot specify original_start_time for a non-recurring event" in str(exc_info.value.__cause__)

# --- NEW TEST --- #
@pytest.mark.asyncio
async def test_update_existing_recurring_instance(
    mock_db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils
):
    """Test updating an existing ModifiedInstance (dict method)."""
    # --- Arrange ---
    event_id = uuid.uuid4()
    mod_instance_id = uuid.uuid4()
    base_start_naive = datetime(2024, 10, 7, 10, 0, 0)
    base_end_naive = datetime(2024, 10, 7, 11, 0, 0)
    rrule = "FREQ=WEEKLY;COUNT=4"

    # Mock the base event
    mock_base_event = MagicMock(spec=Event)
    mock_base_event.id = event_id
    mock_base_event.rrule = rrule
    mock_base_event.time_zone = "UTC"

    # Target the second instance (Oct 14th)
    target_instance_original_start_naive_utc = datetime(2024, 10, 14, 10, 0, 0)

    # Mock the EXISTING ModifiedInstance returned by the second execute call
    mock_existing_mod_instance = MagicMock(spec=ModifiedInstance)
    mock_existing_mod_instance.id = mod_instance_id
    mock_existing_mod_instance.event_id = event_id
    mock_existing_mod_instance.original_date = target_instance_original_start_naive_utc
    mock_existing_mod_instance.start_time = datetime(2024, 10, 14, 10, 5, 0) # Slightly different original time
    mock_existing_mod_instance.end_time = datetime(2024, 10, 14, 11, 5, 0)
    mock_existing_mod_instance.time_zone = "UTC"
    mock_existing_mod_instance.title = "Existing Mod"
    mock_existing_mod_instance.location = "Room Z"

    # Mock the result object for the THIRD execute call (the UPDATE ModifiedInstance)
    mock_update_mod_execute_result = MagicMock()
    mock_update_mod_execute_result.rowcount = 1

    # Mock the ModifiedInstance object returned by the FOURTH execute call (refetch)
    mock_refetched_mod_instance = MagicMock(spec=ModifiedInstance)
    mock_refetched_mod_instance.id = mod_instance_id
    mock_refetched_mod_instance.event_id = event_id
    mock_refetched_mod_instance.original_date = target_instance_original_start_naive_utc
    mock_refetched_mod_instance.start_time = datetime(2024, 10, 14, 14, 30, 0) # Expected new naive UTC
    mock_refetched_mod_instance.end_time = datetime(2024, 10, 14, 15, 30, 0) # Expected new naive UTC
    mock_refetched_mod_instance.time_zone = "America/New_York" # Expected new TZ
    mock_refetched_mod_instance.title = "Updated Existing Mod" # Expected new title
    mock_refetched_mod_instance.location = "Room Z" # Unchanged
    mock_refetched_mod_instance.updated_at = datetime.now(UTC)

    # Configure execute side effect using helper
    mock_db_service.mock_session.execute = AsyncMock(side_effect=create_execute_side_effect([
        ('select', MagicMock(scalar_one_or_none=MagicMock(return_value=mock_base_event))),
        ('select', MagicMock(scalar_one_or_none=MagicMock(return_value=mock_existing_mod_instance))),
        ('update', mock_update_mod_execute_result),
        ('select', MagicMock(scalar_one_or_none=MagicMock(return_value=mock_refetched_mod_instance))),
    ]))

    # Mock unused session methods
    mock_db_service.mock_session.add = MagicMock()
    mock_db_service.mock_session.flush = AsyncMock()
    mock_db_service.mock_session.refresh = AsyncMock()
    mock_db_service.mock_session.commit = AsyncMock()
    mock_db_service.mock_session.rollback = AsyncMock()

    # New instance time/tz and title
    update_payload = EventUpdate(
        original_start_time=datetime(2024, 10, 14, 10, 0, 0), # Target instance
        start_time=datetime(2024, 10, 14, 10, 30, 0),
        end_time=datetime(2024, 10, 14, 11, 30, 0),
        time_zone="America/New_York",
        title="Updated Existing Mod",
        # Location is NOT updated in this payload
    )

    strategy = UpdateEventStrategy(db_service=mock_db_service, date_utils=date_utils, conflict_resolver=conflict_resolver)
    strategy_input = (event_id, update_payload)

    # --- Act ---
    updated_result = await strategy.execute(data=strategy_input)

    # --- Assert ---
    assert mock_db_service.mock_session.execute.await_count == 4 # Select Event, Select Mod, Update Mod, Refetch Mod
    mock_db_service.mock_session.add.assert_not_called()
    mock_db_service.mock_session.flush.assert_not_awaited()
    mock_db_service.mock_session.refresh.assert_not_awaited()

    assert updated_result is not None
    assert updated_result is mock_refetched_mod_instance
    assert isinstance(updated_result, ModifiedInstance)
    assert updated_result.id == mod_instance_id
    assert updated_result.title == "Updated Existing Mod"
    assert updated_result.location == "Room Z" # Unchanged
    assert updated_result.time_zone == "America/New_York"
    assert updated_result.start_time == datetime(2024, 10, 14, 14, 30, 0) # Naive UTC
    assert updated_result.end_time == datetime(2024, 10, 14, 15, 30, 0) # Naive UTC
    assert updated_result.original_date == target_instance_original_start_naive_utc
