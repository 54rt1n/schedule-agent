import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import base64
from datetime import datetime, timezone, timedelta

# Import types
from scheduling_agent.common.types import (
    TaskSendParams,
    Message,
    Part,
    Task,
    TaskStatus,
    TaskState,
    Artifact,
    JSONRPCResponse,
    JSONRPCRequest, # Need JSONRPCRequest for main handler test
    TaskGetParams,
    TaskNotFoundError,
    InvalidParamsError, # Need InvalidParamsError
    CancelTaskParams,
    TaskNotCancelableError,
    FileContent
)
# Import error types needed for parametrization
from scheduling_agent.common import types as jsonrpc_errors

# Import components to test/mock
from scheduling_agent.api.routes.rpc import handle_rpc_request, handle_tasks_send, handle_tasks_get # Keep base imports
# Conditionally import handle_tasks_cancel for tests
try:
    from scheduling_agent.api.routes.rpc import handle_tasks_cancel
except ImportError:
    handle_tasks_cancel = None # Placeholder if not yet defined

from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.models import Event, EventCreate
from scheduling_agent.core.exceptions import ConflictError # Import potential exceptions
from scheduling_agent.core.conflict_resolver import ConflictResolver # Import for mocking
from scheduling_agent.core.nlp_processor import NLPProcessor # Import NLPProcessor
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMDateInfo, LLMFilters # Corrected DateInfo name
from scheduling_agent.core.date_utils import DateUtils # Import DateUtils

# --- Mocks and Fixtures ---

@pytest.fixture
def mock_task_manager_unit() -> MagicMock:
    mock = MagicMock(spec=SchedulingTaskManager)
    mock.create_task = AsyncMock()
    mock.complete_task = AsyncMock()
    mock.fail_task = AsyncMock()
    mock.require_input_task = AsyncMock()
    mock.add_task_artifact = AsyncMock()
    mock.get_task = AsyncMock()
    # Add cancel_task mock
    mock.cancel_task = AsyncMock()
    # Configure complete_task to return a basic completed task
    # Use AsyncMock for return values to ensure they are awaitable if needed by tests
    mock.complete_task.return_value = AsyncMock(return_value=Task(id="test_task", status=TaskStatus(state=TaskState.COMPLETED)))
    return mock

@pytest.fixture
def mock_db_service_unit() -> AsyncMock:
    mock = AsyncMock(spec=DatabaseService)
    mock.create_event = AsyncMock()
    mock.query_events = AsyncMock()
    return mock

@pytest.fixture
def mock_conflict_resolver_unit() -> AsyncMock:
    """Provides a mock ConflictResolver for unit tests."""
    mock = AsyncMock(spec=ConflictResolver)
    mock.generate_alternative_times = AsyncMock(return_value=["alt1", "alt2"]) # Example return
    return mock

@pytest.fixture
def mock_nlp_processor_unit() -> AsyncMock:
    """Provides a mock NLPProcessor for unit tests."""
    mock = AsyncMock(spec=NLPProcessor)
    mock.parse_natural_language_query = AsyncMock(
        return_value=LLMSchedulingParseResult(intent="unknown", date_info=LLMDateInfo()) # Use LLMDateInfo
    )
    # Add mock for date_utils if needed within handler logic related to NLP
    mock.date_utils = MagicMock()
    return mock

# Fixture for exporters (needed for export intent tests)
@pytest.fixture
def mock_ical_exporter_unit() -> MagicMock:
    mock = MagicMock()
    mock.export = MagicMock(return_value="BEGIN:VCALENDAR...")
    mock.get_mime_type = MagicMock(return_value="text/calendar")
    return mock

@pytest.fixture
def mock_org_exporter_unit() -> MagicMock:
    mock = MagicMock()
    mock.export = MagicMock(return_value="* Event...")
    mock.get_mime_type = MagicMock(return_value="text/plain")
    return mock

@pytest.fixture
def mock_exporters_unit(mock_ical_exporter_unit, mock_org_exporter_unit):
    return {
        "ics": mock_ical_exporter_unit,
        "org": mock_org_exporter_unit,
    }

# --- Unit Tests for handle_tasks_send ---

@pytest.mark.asyncio
async def test_handle_tasks_send_create_success(
    mock_task_manager_unit: MagicMock,
    mock_db_service_unit: AsyncMock
):
    """Unit test handle_tasks_send: Create event success path using structured data."""
    task_id = str(uuid.uuid4())
    event_title = "Board Meeting"
    created_event_id = str(uuid.uuid4())

    # Mock successful event creation
    mock_created_event = Event(
        id=uuid.UUID(created_event_id),
        title=event_title,
        start_time=datetime(2024, 6, 15, 14, 0, 0),
        end_time=datetime(2024, 6, 15, 15, 30, 0),
        calendar_id="work"
    )
    mock_db_service_unit.create_event.return_value = mock_created_event

    # Prepare input params
    event_data = {
        "title": event_title,
        "start_time": "2024-06-15T14:00:00Z",
        "end_time": "2024-06-15T15:30:00Z",
        "calendar_id": "work"
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)])
    )

    # Define the expected final task state that complete_task should return
    expected_final_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED),
        artifacts=[Artifact(name="created_event", parts=[Part(type="data", data=mock_created_event.model_dump())])]
    )
    mock_task_manager_unit.complete_task.return_value = expected_final_task

    # Action: Call the handler function directly
    # Need to pass all dependencies handle_tasks_send expects
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager_unit,
        db_service=mock_db_service_unit,
        conflict_resolver=mock_conflict_resolver_unit, # Pass injected fixture
        nlp_processor=mock_nlp_processor_unit, # Pass injected fixture
        date_utils=DateUtils() # Pass real DateUtils instance
    )

    # Assertions
    # 1. Check TaskManager calls
    mock_task_manager_unit.create_task.assert_awaited_once_with(params=params)
    mock_db_service_unit.create_event.assert_awaited_once()
    # TODO: Assert specific args for create_event (EventCreate model)
    mock_task_manager_unit.complete_task.assert_awaited_once()
    # TODO: Assert specific args for complete_task (message, artifact)

    # 2. Check the returned Task object
    assert isinstance(returned_task, Task)
    assert returned_task.status.state == TaskState.COMPLETED
    # Compare the returned task dump with the expected final task dump
    assert returned_task.model_dump() == expected_final_task.model_dump()

@pytest.mark.asyncio
async def test_handle_tasks_send_create_conflict(
    mock_task_manager_unit: MagicMock,
    mock_db_service_unit: AsyncMock,
    mock_conflict_resolver_unit: AsyncMock # Add mock fixture
):
    """Unit test handle_tasks_send: Create event conflict path."""
    task_id = str(uuid.uuid4())

    # Prepare input params
    event_data = {
        "title": "Conflict Event",
        "start_time": "2024-07-01T09:00:00Z",
        "end_time": "2024-07-01T10:00:00Z",
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)])
    )
    # Also need the EventCreate model for the conflict resolver call
    event_create_data = EventCreate.model_validate(event_data)

    # Mock DB service to raise ConflictError
    conflicting_event = Event(
        id=uuid.uuid4(),
        title="Existing",
        start_time=datetime(2024, 7, 1, 9, 0, 0)
    )
    conflict_error = ConflictError(message="DB Conflict", conflicts=[{"event": conflicting_event}])
    mock_db_service_unit.create_event.side_effect = conflict_error

    # Mock Conflict Resolver Alternatives
    mock_alternatives = ["2024-07-01T11:00:00Z", "2024-07-01T14:00:00Z"]
    mock_conflict_resolver_unit.generate_alternative_times.return_value = mock_alternatives

    # Define the expected artifact and final task state (INPUT_REQUIRED)
    expected_conflict_artifact = Artifact(name="conflict_info", parts=[Part(type="data", data={
        "message": str(conflict_error),
        "conflicts": [conflicting_event.model_dump()],
        "alternatives": mock_alternatives
    })])
    expected_final_input_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.INPUT_REQUIRED),
        artifacts=[expected_conflict_artifact] # Ensure artifact list matches
    )
    mock_task_manager_unit.require_input_task.return_value = expected_final_input_task

    # Action: Call the handler function directly
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager_unit,
        db_service=mock_db_service_unit,
        conflict_resolver=mock_conflict_resolver_unit, # Pass the mock
        nlp_processor=mock_nlp_processor_unit, # Pass the NLP mock
        date_utils=DateUtils() # Pass real DateUtils instance
    )

    # Assertions
    mock_task_manager_unit.create_task.assert_awaited_once_with(params=params)
    mock_db_service_unit.create_event.assert_awaited_once()
    # Assert Conflict Resolver was called correctly
    mock_conflict_resolver_unit.generate_alternative_times.assert_awaited_once_with(event_create_data, conflict_error.conflicts)
    # Assert require_input_task was called
    mock_task_manager_unit.require_input_task.assert_awaited_once()
    # TODO: Assert specific args for require_input_task (message, artifact)
    mock_task_manager_unit.complete_task.assert_not_called()
    mock_task_manager_unit.fail_task.assert_not_called()

    # Assert the returned task is the one from require_input_task
    assert isinstance(returned_task, Task)
    assert returned_task.status.state == TaskState.INPUT_REQUIRED
    assert returned_task.model_dump() == expected_final_input_task.model_dump()

@pytest.mark.asyncio
async def test_handle_tasks_send_query_success(
    mock_task_manager_unit: MagicMock,
    mock_db_service_unit: AsyncMock,
    mock_nlp_processor_unit: AsyncMock,
):
    """Unit test handle_tasks_send: Query schedule success path."""
    task_id = str(uuid.uuid4())
    query = "What is on my calendar for the rest of today?"

    # Prepare input params
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )

    # Mock NLP result
    mock_nlp_result = LLMSchedulingParseResult(
        intent="query_events",
        date_info=LLMDateInfo(start_date="today", end_date="today") # Use LLMDateInfo
        # TODO: Add entities if the handler logic requires them
    )
    mock_nlp_processor_unit.parse_natural_language_query.return_value = mock_nlp_result

    # Mock DB result
    mock_event = Event(
        id=uuid.uuid4(),
        title="Lunch",
        start_time=datetime(2024, 7, 4, 12, 0, 0),
        end_time=datetime(2024, 7, 4, 13, 0, 0)
    )
    mock_db_service_unit.query_events.return_value = [mock_event]

    # Mock expected final task state
    expected_artifact = Artifact(name="queried_events", parts=[Part(type="data", data=[mock_event.model_dump()])])
    expected_final_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED),
        artifacts=[expected_artifact]
    )
    mock_task_manager_unit.complete_task.return_value = expected_final_task

    # Action: Call the handler function directly, passing the NLP mock
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager_unit,
        db_service=mock_db_service_unit,
        conflict_resolver=mock_conflict_resolver_unit, # Pass the mock
        nlp_processor=mock_nlp_processor_unit, # Pass the NLP mock
        date_utils=DateUtils() # Pass real DateUtils instance
    )

    # Assertions (Check calls AFTER the expected success)
    mock_task_manager_unit.create_task.assert_awaited_once_with(params=params)
    # Assert NLP was called
    mock_nlp_processor_unit.parse_natural_language_query.assert_awaited_once_with(query)
    # Assert DB query was called (check args later if needed)
    mock_db_service_unit.query_events.assert_awaited_once()
    # TODO: Check args for query_events based on validated EventQuery

    # Assert complete_task was called
    mock_task_manager_unit.complete_task.assert_awaited_once()
    # TODO: Assert specific args for complete_task (message, artifact)

    # Check returned task
    assert returned_task.status.state == TaskState.COMPLETED
    assert len(returned_task.artifacts) == 1
    assert returned_task.artifacts[0].name == "queried_events"
    assert returned_task.artifacts[0].parts[0].data[0]["id"] == mock_event.id

# --- Unit Tests for handle_tasks_get ---

@pytest.mark.asyncio
async def test_handle_tasks_get_success(
    mock_task_manager_unit: MagicMock
):
    """Unit test handle_tasks_get: Success path."""
    task_id = str(uuid.uuid4())
    # Ensure TaskGetParams is imported if not globally available
    params = TaskGetParams(id=task_id)

    # Mock TaskManager response
    mock_found_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Done")]))
    )
    mock_task_manager_unit.get_task.return_value = mock_found_task

    # Action
    returned_task = await handle_tasks_get(params=params, task_manager=mock_task_manager_unit)

    # Assertions
    mock_task_manager_unit.get_task.assert_awaited_once_with(task_id)
    assert returned_task == mock_found_task

@pytest.mark.asyncio
async def test_handle_tasks_get_not_found(
    mock_task_manager_unit: MagicMock
):
    """Unit test handle_tasks_get: Task not found path."""
    task_id = str(uuid.uuid4())
    # Ensure TaskGetParams is imported if not globally available
    params = TaskGetParams(id=task_id)

    # Mock TaskManager response
    mock_task_manager_unit.get_task.return_value = None

    # Action & Assertion
    with pytest.raises(TaskNotFoundError, match=f"Task with ID '{task_id}' not found."):
        await handle_tasks_get(params=params, task_manager=mock_task_manager_unit)

    # Assertions
    mock_task_manager_unit.get_task.assert_awaited_once_with(task_id)

# --- Unit Tests for handle_tasks_cancel ---

# Import the (yet to be created) handle_tasks_cancel function
# This is now handled by the conditional import at the top
# try:
#     from scheduling_agent.api.routes.rpc import handle_tasks_cancel
# except ImportError:
#     handle_tasks_cancel = None # Placeholder if not yet defined

@pytest.mark.asyncio
async def test_handle_tasks_cancel_success(mock_task_manager_unit: MagicMock):
    """Unit test handle_tasks_cancel: Success path."""
    if not handle_tasks_cancel:
        pytest.skip("handle_tasks_cancel not implemented yet.")

    task_id = str(uuid.uuid4())
    params = CancelTaskParams(id=task_id)

    # Mock TaskManager response for successful cancel
    mock_canceled_task = Task(id=task_id, status=TaskStatus(state=TaskState.CANCELED))
    mock_task_manager_unit.cancel_task.return_value = mock_canceled_task

    # Action
    returned_task = await handle_tasks_cancel(params=params, task_manager=mock_task_manager_unit)

    # Assertions
    mock_task_manager_unit.cancel_task.assert_awaited_once_with(task_id=task_id)
    assert returned_task == mock_canceled_task

@pytest.mark.asyncio
async def test_handle_tasks_cancel_not_found(mock_task_manager_unit: MagicMock):
    """Unit test handle_tasks_cancel: Task not found path."""
    if not handle_tasks_cancel:
        pytest.skip("handle_tasks_cancel not implemented yet.")

    task_id = str(uuid.uuid4())
    params = CancelTaskParams(id=task_id)

    # Mock TaskManager to raise TaskNotFoundError
    mock_task_manager_unit.cancel_task.side_effect = TaskNotFoundError(f"Task {task_id} not found")

    # Action & Assertion
    with pytest.raises(TaskNotFoundError):
        await handle_tasks_cancel(params=params, task_manager=mock_task_manager_unit)

    mock_task_manager_unit.cancel_task.assert_awaited_once_with(task_id=task_id)

@pytest.mark.asyncio
async def test_handle_tasks_cancel_not_cancelable(mock_task_manager_unit: MagicMock):
    """Unit test handle_tasks_cancel: Task not cancelable path."""
    if not handle_tasks_cancel:
        pytest.skip("handle_tasks_cancel not implemented yet.")

    task_id = str(uuid.uuid4())
    params = CancelTaskParams(id=task_id)

    # Mock TaskManager to raise TaskNotCancelableError
    mock_task_manager_unit.cancel_task.side_effect = TaskNotCancelableError(f"Task {task_id} not cancelable")

    # Action & Assertion
    with pytest.raises(TaskNotCancelableError):
        await handle_tasks_cancel(params=params, task_manager=mock_task_manager_unit)

    mock_task_manager_unit.cancel_task.assert_awaited_once_with(task_id=task_id)

# --- Unit Tests for export_calendar intent in handle_tasks_send --- #

@pytest.mark.asyncio
async def test_handle_tasks_send_export_success(
    mock_task_manager_unit: MagicMock,
    mock_db_service_unit: AsyncMock,
    mock_nlp_processor_unit: AsyncMock,
    mock_exporters_unit: dict, # Add exporters fixture
    mock_ical_exporter_unit: MagicMock, # Add specific exporter mock
):
    """Unit test handle_tasks_send: Export calendar successfully."""
    task_id = str(uuid.uuid4())
    query = "Export my calendar as ics"

    # Prepare input params
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )

    # Mock NLP result for export intent
    mock_nlp_result = LLMSchedulingParseResult(
        intent="export_calendar",
        date_info=LLMDateInfo(), # Placeholder date info
        filters={"format": "ics"} # Specify format
    )
    mock_nlp_processor_unit.parse_natural_language_query.return_value = mock_nlp_result

    # Mock DB result
    mock_events_list = [
        Event(id=uuid.uuid4(), title="Event 1", start_time=datetime(2024, 1, 1, 10), end_time=datetime(2024, 1, 1, 11)),
        Event(id=uuid.uuid4(), title="Event 2", start_time=datetime(2024, 1, 2, 14), end_time=datetime(2024, 1, 2, 15)),
    ]
    mock_db_service_unit.get_events_in_range.return_value = mock_events_list

    # Mock exporter behavior
    exported_content = "BEGIN:VCALENDAR..."
    mock_ical_exporter_unit.export.return_value = exported_content
    mock_ical_exporter_unit.get_mime_type.return_value = "text/calendar"
    # Ensure the mock_exporters_unit dict uses the specific mock instance
    mock_exporters_unit['ics'] = mock_ical_exporter_unit

    # Mock TaskManager completion
    expected_base64_bytes = base64.b64encode(exported_content.encode('utf-8')).decode('utf-8')
    expected_artifact = Artifact(
        name="calendar.ics",
        parts=[Part(type="file", file=FileContent(
            name="calendar.ics", mimeType="text/calendar", bytes=expected_base64_bytes
        ))]
    )
    final_task = Task(id=task_id, status=TaskStatus(state=TaskState.COMPLETED), artifacts=[expected_artifact])
    mock_task_manager_unit.complete_task.return_value = final_task

    # Action
    # Pass the mock_exporters_unit fixture to the exporters argument
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager_unit,
        db_service=mock_db_service_unit,
        conflict_resolver=mock_conflict_resolver_unit, # Assuming this exists from other tests
        nlp_processor=mock_nlp_processor_unit,
        exporters=mock_exporters_unit, # <-- Add this argument
        date_utils=DateUtils() # Pass real DateUtils instance
    )

    # Assertions
    assert returned_task is not None
    assert returned_task.id == task_id
    assert returned_task.status.state == TaskState.COMPLETED
    assert len(returned_task.artifacts) == 1
    actual_artifact = returned_task.artifacts[0]
    assert actual_artifact.name == "calendar.ics"
    assert len(actual_artifact.parts) == 1
    file_part = actual_artifact.parts[0]
    assert file_part.type == "file"
    assert file_part.file is not None
    assert file_part.file.name == "calendar.ics"
    assert file_part.file.mimeType == "text/calendar"
    assert file_part.file.bytes == expected_base64_bytes

    # Verify mock calls
    mock_task_manager_unit.create_task.assert_awaited_once_with(params=params)
    mock_nlp_processor_unit.parse_natural_language_query.assert_awaited_once_with(query)
    mock_db_service_unit.get_events_in_range.assert_awaited_once()
    # Ensure the correct exporter from the mock dict was used
    mock_ical_exporter_unit.export.assert_called_once_with(mock_events_list)
    mock_task_manager_unit.complete_task.assert_awaited_once()

@pytest.mark.asyncio
async def test_handle_tasks_send_export_unsupported_format(
    mock_task_manager_unit: MagicMock,
    mock_db_service_unit: AsyncMock,
    mock_nlp_processor_unit: AsyncMock,
    mock_exporters_unit: dict, # Add exporters fixture
):
    """Unit test handle_tasks_send: Export calendar with unsupported format."""
    task_id = str(uuid.uuid4())
    query = "Export my calendar as pdf" # Requesting unsupported format

    # Prepare input params
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )

    # Mock NLP result for export intent with unsupported format
    # Create an LLMFilters instance with the format attribute
    mock_filters = LLMFilters(format="pdf")
    mock_nlp_result = LLMSchedulingParseResult(
        intent="export_calendar",
        date_info=LLMDateInfo(),
        filters=mock_filters # Use the LLMFilters instance
    )
    mock_nlp_processor_unit.parse_natural_language_query.return_value = mock_nlp_result

    # Action & Assert: Expect InvalidParamsError
    with pytest.raises(InvalidParamsError, match="Unsupported export format requested: pdf"):
        # Pass the mock_exporters_unit fixture to the exporters argument
        await handle_tasks_send(
            params=params,
            task_manager=mock_task_manager_unit,
            db_service=mock_db_service_unit,
            conflict_resolver=mock_conflict_resolver_unit, # Assuming this exists
            nlp_processor=mock_nlp_processor_unit,
            exporters=mock_exporters_unit, # <-- Add this argument
            date_utils=DateUtils() # Pass real DateUtils instance
        )

    # Verify relevant mocks were called/not called
    mock_task_manager_unit.create_task.assert_awaited_once_with(params=params)
    mock_nlp_processor_unit.parse_natural_language_query.assert_awaited_once_with(query)
    mock_db_service_unit.get_events_in_range.assert_not_called()
    mock_task_manager_unit.complete_task.assert_not_called()
    mock_task_manager_unit.fail_task.assert_not_called()

# --- Unit Tests for handle_rpc_request (Parameter Validation) ---

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, params, expected_error_code, expected_error_message",
    [
        # Method not found
        ("unknown/method", {}, jsonrpc_errors.MethodNotFoundError.DEFAULT_CODE, "Method not found"),
        # tasks/send invalid params
        ("tasks/send", {"sessionId": 123}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Missing 'message' or 'task'
        ("tasks/send", {"message": {"role": "user", "parts": []}}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Missing 'sessionId'
        ("tasks/send", {"task": {"status": {"state": "QUEUED"}, "message": {"role": "user", "parts": []}}}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Missing 'sessionId' inside task
        ("tasks/send", None, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # params is None
        ("tasks/send", {}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # params is empty dict
        ("tasks/send", {"sessionId": "abc", "message": "not a message object"}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Invalid message type
        ("tasks/send", {"sessionId": "abc", "task": {"status": {"state": "invalid_state"}}}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Invalid task state in task
         ("tasks/send", {"sessionId": "abc", "message": {"role": "invalid", "parts": [{"type": "text", "text": "hi"}]}}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Invalid role in message

        # tasks/get invalid params
        ("tasks/get", {"taskId": "abc"}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # Incorrect field name 'taskId' instead of 'id'
        ("tasks/get", {"id": 123}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # 'id' should be a string
        ("tasks/get", None, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # params is None
        ("tasks/get", {}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # params is empty dict (missing 'id')
        ("tasks/get", {"id": "abc", "historyLength": -1}, jsonrpc_errors.InvalidParamsError.DEFAULT_CODE, "Invalid parameters provided."), # historyLength must be non-negative
    ],
)
async def test_handle_rpc_request_invalid_params(
    method, params, expected_error_code, expected_error_message, mock_task_manager_unit, mock_db_service_unit, mock_conflict_resolver_unit, mock_nlp_processor_unit,
):
    """Tests handle_rpc_request with various invalid parameters or methods."""
    # Mock the actual handlers to isolate handle_rpc_request validation/routing
    # We don't want the actual logic of handle_tasks_send/get to run here.
    with patch("scheduling_agent.api.routes.rpc.handle_tasks_send", new_callable=AsyncMock) as mock_send, \
         patch("scheduling_agent.api.routes.rpc.handle_tasks_get", new_callable=AsyncMock) as mock_get:

        request_id = str(uuid.uuid4())
        rpc_request = JSONRPCRequest(
            jsonrpc="2.0",
            method=method,
            params=params,
            id=request_id
        )

        # Action: Call the main RPC handler
        response = await handle_rpc_request(
            request=rpc_request,
            task_manager=mock_task_manager_unit,
            db_service=mock_db_service_unit,
            conflict_resolver=mock_conflict_resolver_unit,
            nlp_processor=mock_nlp_processor_unit,
            exporters=mock_exporters_unit, # Pass exporters mock
            date_utils=DateUtils() # Pass real DateUtils instance
        )

        # Assertions
        assert isinstance(response, JSONRPCResponse)
        assert response.id == request_id
        assert response.result is None
        assert response.error is not None
        assert response.error.code == expected_error_code
        # Check the primary message. Details might be in response.error.data for validation errors.
        assert response.error.message == expected_error_message

        # Ensure the specific handlers were NOT called when params were invalid
        if method == "tasks/send":
            mock_send.assert_not_called()
        elif method == "tasks/get":
            mock_get.assert_not_called()

# TODO: Add unit test for other intents/paths
# Ensure this comment is the last line if it was before 