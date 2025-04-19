import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
import uuid

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
    TaskGetParams,
    TaskNotFoundError
)

# Import components to test/mock
from scheduling_agent.api.routes.rpc import handle_tasks_send, handle_tasks_get # The functions to test
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.models import Event, EventCreate
from scheduling_agent.core.exceptions import ConflictError # Import potential exceptions
from scheduling_agent.core.conflict_resolver import ConflictResolver # Import for mocking
from scheduling_agent.core.nlp_processor import NLPProcessor # Import NLPProcessor
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMDateInfo # Corrected DateInfo name

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
    # Configure complete_task to return a basic completed task
    mock.complete_task.return_value = Task(id="test_task", status=TaskStatus(state=TaskState.COMPLETED))
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
        id=created_event_id,
        title=event_title,
        start_time="2024-06-15T14:00:00Z",
        end_time="2024-06-15T15:30:00Z",
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
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager_unit,
        db_service=mock_db_service_unit # Explicitly pass the mock db_service
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
    conflicting_event = Event(id=str(uuid.uuid4()), title="Existing", start_time="...")
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
        conflict_resolver=mock_conflict_resolver_unit # Pass the mock
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
    mock_event = Event(id=str(uuid.uuid4()), title="Lunch", start_time="2024-07-04T12:00:00Z", end_time="2024-07-04T13:00:00Z")
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
        # Pass other mocks if/when needed by the handler for NLP path
        nlp_processor=mock_nlp_processor_unit # Pass the NLP mock
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
    from scheduling_agent.common.types import TaskGetParams, Task, TaskStatus, Message, Part
    params = TaskGetParams(id=task_id)

    # Mock TaskManager response
    mock_found_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Done")]))
    )
    mock_task_manager_unit.get_task.return_value = mock_found_task

    # Import the function to test
    from scheduling_agent.api.routes.rpc import handle_tasks_get

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
    from scheduling_agent.common.types import TaskGetParams
    params = TaskGetParams(id=task_id)

    # Mock TaskManager response
    mock_task_manager_unit.get_task.return_value = None

    # Import the function to test and the expected exception
    from scheduling_agent.api.routes.rpc import handle_tasks_get
    from scheduling_agent.common.types import TaskNotFoundError

    # Action & Assertion
    with pytest.raises(TaskNotFoundError, match=f"Task with ID '{task_id}' not found."):
        await handle_tasks_get(params=params, task_manager=mock_task_manager_unit)

    # Assertions
    mock_task_manager_unit.get_task.assert_awaited_once_with(task_id)

# TODO: Add unit test for other intents/paths
# Ensure this comment is the last line if it was before 