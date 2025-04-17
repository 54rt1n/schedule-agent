import pytest
import uuid
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# Models and Enums
from scheduling_agent.task_manager import (
    SchedulingTaskManager,
    TaskState,
    Task,
    TaskStatus,
    Message,
    Part,
    Artifact
)
from scheduling_agent.db.models import Event, EventCreate
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMDateInfo, LLMFilters
from scheduling_agent.core.exceptions import ConflictError

# Services and Components to Mock
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter

# --- Mock Fixtures ---

@pytest.fixture
def mock_nlp_processor() -> AsyncMock:
    mock = AsyncMock(spec=NLPProcessor)
    # Configure default return values if needed for most tests
    mock.parse_natural_language_query.return_value = LLMSchedulingParseResult(
        intent="unknown", confidence=0.1, method="mock_fallback"
    )
    return mock

@pytest.fixture
def mock_db_service() -> AsyncMock:
    mock = AsyncMock(spec=DatabaseService)
    # Mock methods used by TaskManager
    mock.create_event = AsyncMock()
    mock.get_events_in_range = AsyncMock(return_value=[]) # Default to returning no events
    # Mock the date_utils attribute if needed by exporter setup
    mock.date_utils = MagicMock() 
    return mock

@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    mock = AsyncMock(spec=ConflictResolver)
    # Mock methods used by TaskManager
    mock.detect_conflicts = AsyncMock(return_value=[]) # Default to no conflicts
    mock.generate_alternative_times = AsyncMock(return_value=[]) # Default to no alternatives
    return mock

@pytest.fixture
def mock_ical_exporter() -> MagicMock:
    mock = MagicMock(spec=ICalExporter)
    mock.export.return_value = "BEGIN:VCALENDAR...END:VCALENDAR" # Placeholder ICS data
    return mock

@pytest.fixture
def mock_org_exporter() -> MagicMock:
    mock = MagicMock(spec=OrgModeExporter)
    mock.export.return_value = "* Mock Org Event" # Placeholder Org data
    return mock

@pytest.fixture
def task_manager(
    mock_nlp_processor: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    mock_ical_exporter: MagicMock,
    mock_org_exporter: MagicMock,
) -> SchedulingTaskManager:
    """Provides a SchedulingTaskManager instance with mocked dependencies."""
    # Pass mocks for specific exporters
    mock_exporters = {
        "ics": mock_ical_exporter,
        "org": mock_org_exporter
    }
    manager = SchedulingTaskManager(
        nlp_processor=mock_nlp_processor,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters
    )
    return manager

# --- Test Cases --- #

@pytest.mark.asyncio
# Patch the store_task method within the TaskManager for easier assertion
@patch.object(SchedulingTaskManager, 'store_task', new_callable=AsyncMock)
async def test_on_send_task_create_data_success(
    mock_store_task: AsyncMock, # Patched store_task
    task_manager: SchedulingTaskManager, # Task manager with other mocks
    mock_db_service: AsyncMock # Inject db mock directly for configuration
):
    """Test successful event creation from structured data input."""
    # --- Arrange ---
    request_id = "req-create-success"
    task_id = "task-create-success"
    event_title = "Successful Event"
    start_time = datetime(2024, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    end_time = start_time + timedelta(hours=1)

    # Input data mimicking a structured request
    input_event_data = {
        "title": event_title,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "time_zone": "UTC"
    }
    input_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{
                "type": "data",
                "data": input_event_data
            }]
        }
    }

    # Configure mock DB service response
    mock_created_event = Event(
        id=uuid.uuid4(),
        title=event_title,
        start_time=start_time.replace(tzinfo=None), # DB stores naive UTC
        end_time=end_time.replace(tzinfo=None),
        time_zone="UTC",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc)
    )
    mock_db_service.create_event.return_value = mock_created_event

    # --- Act ---
    response = await task_manager.on_send_task(request_id=request_id, params=input_params)

    # --- Assert ---
    # Check JSON-RPC response indicates success
    assert "result" in response
    assert "error" not in response
    assert response["id"] == request_id

    # Check the final task state stored (via the mocked store_task)
    # store_task should be called twice: once initially, once finally
    assert mock_store_task.call_count == 2
    final_task_call = mock_store_task.call_args_list[1] # Get the second call
    final_task_stored = final_task_call.args[0] # First argument to store_task is the task object

    assert isinstance(final_task_stored, Task)
    assert final_task_stored.id == task_id
    assert final_task_stored.status.state == TaskState.COMPLETED
    assert f"Event '{event_title}' created successfully." in final_task_stored.status.message.parts[0].text

    # Check artifacts
    assert len(final_task_stored.artifacts) == 1
    artifact = final_task_stored.artifacts[0]
    assert artifact.name == "created_event"
    assert artifact.parts[0].type == "data"
    # Check artifact data matches mocked created event (using model_dump)
    assert artifact.parts[0].data == mock_created_event.model_dump()

    # Check that db_service.create_event was called correctly
    mock_db_service.create_event.assert_awaited_once()
    call_args = mock_db_service.create_event.call_args.args[0]
    assert isinstance(call_args, EventCreate)
    assert call_args.title == event_title
    assert call_args.start_time == start_time # EventCreate uses aware time

@pytest.mark.asyncio
@patch.object(SchedulingTaskManager, 'store_task', new_callable=AsyncMock)
async def test_on_send_task_create_data_conflict(
    mock_store_task: AsyncMock,
    task_manager: SchedulingTaskManager,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock # Inject conflict resolver mock
):
    """Test conflict detection during event creation from structured data."""
    # --- Arrange ---
    request_id = "req-create-conflict"
    task_id = "task-create-conflict"
    event_title = "Conflicting Event"
    start_time = datetime(2024, 10, 2, 11, 0, 0, tzinfo=timezone.utc)
    end_time = start_time + timedelta(hours=1)

    input_event_data = {
        "title": event_title,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "time_zone": "UTC"
    }
    input_params = {
        "id": task_id,
        "message": {"role": "user", "parts": [{"type": "data", "data": input_event_data}]}
    }

    # --- Configure Mocks ---
    # 1. Mock DB service to raise ConflictError
    mock_conflicts = [
        {"type": "instance", "event": Event(id=uuid.uuid4(), title="Existing", start_time=start_time, end_time=end_time, time_zone="UTC")}
    ]
    # IMPORTANT: Mock create_event to raise ConflictError
    mock_db_service.create_event.side_effect = ConflictError(conflicts=mock_conflicts)

    # 2. Mock ConflictResolver to generate alternatives (even though create_event raises first)
    #    This tests the logic path within TaskManager's except block
    mock_alternatives = [
        {"startTime": "2024-10-02T13:00:00+00:00", "endTime": "2024-10-02T14:00:00+00:00", "label": "Later today"}
    ]
    mock_conflict_resolver.generate_alternative_times.return_value = mock_alternatives

    # --- Act ---
    response = await task_manager.on_send_task(request_id=request_id, params=input_params)

    # --- Assert ---
    # Check JSON-RPC response indicates success (as task state is handled)
    assert "result" in response
    assert "error" not in response
    assert response["id"] == request_id

    # Check final task state stored
    assert mock_store_task.call_count == 2
    final_task_stored = mock_store_task.call_args_list[1].args[0]

    assert isinstance(final_task_stored, Task)
    assert final_task_stored.id == task_id
    assert final_task_stored.status.state == TaskState.INPUT_REQUIRED
    assert "Event creation failed due to conflicts" in final_task_stored.status.message.parts[0].text

    # Check conflict artifact
    assert len(final_task_stored.artifacts) == 1
    artifact = final_task_stored.artifacts[0]
    assert artifact.name == "conflict_info"
    assert artifact.parts[0].type == "data"
    conflict_info_data = artifact.parts[0].data
    assert "message" in conflict_info_data
    assert "conflicts" in conflict_info_data
    assert "alternatives" in conflict_info_data
    assert len(conflict_info_data["alternatives"]) == len(mock_alternatives)
    assert conflict_info_data["alternatives"] == mock_alternatives
    # Simplified check on conflict data (ensure it's the dump of the event)
    assert len(conflict_info_data["conflicts"]) == 1
    assert conflict_info_data["conflicts"][0]['title'] == "Existing"

    # Check that db_service.create_event was called
    mock_db_service.create_event.assert_awaited_once()
    # Check that conflict_resolver.generate_alternative_times was called AFTER the error
    mock_conflict_resolver.generate_alternative_times.assert_awaited_once()
    # Check args passed to generate_alternative_times
    alt_call_args = mock_conflict_resolver.generate_alternative_times.call_args.args
    assert isinstance(alt_call_args[0], EventCreate) # First arg is EventCreate data
    assert alt_call_args[1] == mock_conflicts # Second arg is the conflicts list

@pytest.mark.asyncio
@patch.object(SchedulingTaskManager, 'store_task', new_callable=AsyncMock)
async def test_on_send_task_query_text_success(
    mock_store_task: AsyncMock,
    task_manager: SchedulingTaskManager,
    mock_nlp_processor: AsyncMock,
    mock_db_service: AsyncMock
):
    """Test successful schedule query from text input."""
    # --- Arrange ---
    request_id = "req-query-success"
    task_id = "task-query-success"
    query_text = "What is happening tomorrow?"
    tomorrow_date_str = (datetime.now(timezone.utc) + timedelta(days=1)).strftime('%Y-%m-%d')
    tomorrow_start_dt = datetime.fromisoformat(tomorrow_date_str + "T00:00:00+00:00")
    tomorrow_end_dt = datetime.fromisoformat(tomorrow_date_str + "T23:59:59.999999+00:00")

    input_params = {
        "id": task_id,
        "message": {"role": "user", "parts": [{"type": "text", "text": query_text}]}
    }

    # --- Configure Mocks ---
    # 1. NLP returns query_schedule intent and date range
    mock_nlp_result = LLMSchedulingParseResult(
        intent="query_schedule",
        date_info=LLMDateInfo(start_date=tomorrow_date_str, end_date=tomorrow_date_str),
        confidence=0.9,
        method="llm"
    )
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # 2. DB service returns some events
    mock_event_1 = Event(id=uuid.uuid4(), title="Morning Meeting", start_time=tomorrow_start_dt.replace(hour=9, tzinfo=None), end_time=tomorrow_start_dt.replace(hour=10, tzinfo=None), time_zone="UTC")
    mock_event_2 = Event(id=uuid.uuid4(), title="Afternoon Chat", start_time=tomorrow_start_dt.replace(hour=14, tzinfo=None), end_time=tomorrow_start_dt.replace(hour=14, minute=30, tzinfo=None), time_zone="UTC")
    mock_db_service.get_events_in_range.return_value = [mock_event_1, mock_event_2]

    # --- Act ---
    response = await task_manager.on_send_task(request_id=request_id, params=input_params)

    # --- Assert ---
    # Check response
    assert "result" in response
    assert "error" not in response
    assert response["id"] == request_id

    # Check final task state
    assert mock_store_task.call_count == 2
    final_task_stored = mock_store_task.call_args_list[1].args[0]
    assert isinstance(final_task_stored, Task)
    assert final_task_stored.id == task_id
    assert final_task_stored.status.state == TaskState.COMPLETED
    assert "Found events:" in final_task_stored.status.message.parts[0].text

    # Check artifact
    assert len(final_task_stored.artifacts) == 1
    artifact = final_task_stored.artifacts[0]
    assert artifact.name == "query_results"
    assert artifact.parts[0].type == "data"
    assert len(artifact.parts[0].data) == 2
    assert artifact.parts[0].data[0]['title'] == mock_event_1.title
    assert artifact.parts[0].data[1]['title'] == mock_event_2.title

    # Check mock calls
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once_with(query_text)
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=tomorrow_start_dt,
        end=tomorrow_end_dt
    )

@pytest.mark.asyncio
@patch.object(SchedulingTaskManager, 'store_task', new_callable=AsyncMock)
async def test_on_send_task_export_text_success(
    mock_store_task: AsyncMock,
    task_manager: SchedulingTaskManager,
    mock_nlp_processor: AsyncMock,
    mock_db_service: AsyncMock,
    mock_ical_exporter: MagicMock # Inject exporter mock
):
    """Test successful calendar export from text input (ICS format)."""
    # --- Arrange ---
    request_id = "req-export-success"
    task_id = "task-export-success"
    query_text = "Export my calendar for next month as ics"
    # Define date range for "next month" (adjust logic as needed for exact date)
    today = datetime.now(timezone.utc)
    first_day_current_month = today.replace(day=1)
    # Go to last day of current month, then add 1 day to get first day of next month
    next_month_start_dt = (first_day_current_month + timedelta(days=32)).replace(day=1)
    # Find first day of the month after next, then subtract 1 day
    month_after_next_start_dt = (next_month_start_dt + timedelta(days=32)).replace(day=1)
    next_month_end_dt = month_after_next_start_dt - timedelta(days=1)

    next_month_start_str = next_month_start_dt.strftime('%Y-%m-%d')
    next_month_end_str = next_month_end_dt.strftime('%Y-%m-%d')
    # Datetimes for DB query
    start_query_dt = next_month_start_dt.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    end_query_dt = next_month_end_dt.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)

    input_params = {
        "id": task_id,
        "message": {"role": "user", "parts": [{"type": "text", "text": query_text}]}
    }

    # --- Configure Mocks ---
    # 1. NLP returns export_calendar intent and date range
    mock_nlp_result = LLMSchedulingParseResult(
        intent="export_calendar",
        date_info=LLMDateInfo(start_date=next_month_start_str, end_date=next_month_end_str),
        # TODO: Could add format filter here if NLP extracts it
        confidence=0.8,
        method="llm"
    )
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # 2. DB service returns some events
    mock_event_export = Event(id=uuid.uuid4(), title="Export Me", start_time=start_query_dt.replace(day=5, tzinfo=None), end_time=start_query_dt.replace(day=5, hour=1, tzinfo=None), time_zone="UTC")
    mock_db_service.get_events_in_range.return_value = [mock_event_export]

    # 3. Mock exporter return value
    mock_export_data = "BEGIN:VCALENDAR...DATA...END:VCALENDAR"
    mock_ical_exporter.export.return_value = mock_export_data

    # --- Act ---
    response = await task_manager.on_send_task(request_id=request_id, params=input_params)

    # --- Assert ---
    # Check response
    assert "result" in response
    assert "error" not in response
    assert response["id"] == request_id

    # Check final task state
    assert mock_store_task.call_count == 2
    final_task_stored = mock_store_task.call_args_list[1].args[0]
    assert isinstance(final_task_stored, Task)
    assert final_task_stored.id == task_id
    assert final_task_stored.status.state == TaskState.COMPLETED
    assert "Calendar exported successfully" in final_task_stored.status.message.parts[0].text

    # Check artifact
    assert len(final_task_stored.artifacts) == 1
    artifact = final_task_stored.artifacts[0]
    assert artifact.name == "calendar_export.ics"
    assert artifact.description == "Calendar export (ics)"
    assert len(artifact.parts) == 1
    part = artifact.parts[0]
    assert part.type == "file"
    assert part.mimeType == "text/calendar"
    assert part.file == mock_export_data # Check exported data is in artifact

    # Check mock calls
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once_with(query_text)
    mock_db_service.get_events_in_range.assert_awaited_once_with(
        start=start_query_dt,
        end=end_query_dt
    )
    mock_ical_exporter.export.assert_called_once_with([mock_event_export]) # Check exporter called

# Placeholder test remove
# @pytest.mark.asyncio
# async def test_placeholder():
#     assert True 