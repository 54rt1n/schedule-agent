import pytest
import pytest_asyncio
import httpx
import uuid
from unittest.mock import AsyncMock, MagicMock

# Import the app instance and dependency overrides
from fastapi.testclient import TestClient # Can use this for overrides simpler? No, httpx is better for async.
from scheduling_agent.api.main import app
from scheduling_agent.api.dependencies import get_db_service, get_task_manager, get_conflict_resolver, get_nlp_processor

# Import necessary types
from scheduling_agent.common.types import (
    JSONRPCRequest,
    JSONRPCResponse,
    TaskSendParams,
    TaskState,
    Task,
    TaskStatus,
    Message,
    Part,
    Artifact,
    TaskGetParams,
)
# Import db models/services for mocking
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.db.models import Event
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.core.conflict_resolver import ConflictResolver
# Corrected import for NLP schemas
from scheduling_agent.core.nlp_processor import NLPProcessor # Import NLPProcessor
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMDateInfo # Corrected DateInfo name

# Constants
BASE_URL = "http://test"
RPC_URL = "/api/rpc/"

# --- Fixtures ---

@pytest_asyncio.fixture
async def async_client() -> httpx.AsyncClient:
    """Provides an httpx client configured for the FastAPI app."""
    # Use dependency_overrides for mocking within the client context
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL) as client:
        yield client

@pytest_asyncio.fixture
def mock_db_service() -> AsyncMock:
    mock = AsyncMock(spec=DatabaseService)
    # Configure default behaviors if needed
    mock.create_event = AsyncMock()
    mock.query_events = AsyncMock()
    return mock

@pytest_asyncio.fixture
def mock_task_manager() -> MagicMock: # TaskManager methods are sync for state
    mock = MagicMock(spec=SchedulingTaskManager)
    # Configure default behaviors if needed
    # Note: get_task should probably be async if it involves locks/copies
    # Let's make relevant ones async for future proofing handler interaction
    mock.create_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING))) # Placeholder Task
    mock.complete_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.COMPLETED))) # Placeholder Task
    mock.get_task = AsyncMock(return_value=None) # Default to not found
    return mock

@pytest_asyncio.fixture
def mock_nlp_processor() -> AsyncMock:
    """Provides a mock NLPProcessor."""
    mock = AsyncMock(spec=NLPProcessor)
    # Configure default behaviors if needed
    # Example: Default parse result
    mock.parse_natural_language_query = AsyncMock(
        return_value=LLMSchedulingParseResult(
            intent="unknown",
            date_info=LLMDateInfo() # Use LLMDateInfo
        )
    )
    return mock

# --- Tests ---

@pytest.mark.asyncio
async def test_send_task_create_success(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock, # Use MagicMock here
):
    """Test tasks/send for creating an event successfully (structured data input)."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    event_title = "Team Meeting"
    created_event_id = str(uuid.uuid4())

    # Mock the successful event creation in DB service
    mock_created_event = Event(
        id=created_event_id,
        title=event_title,
        start_time="2025-01-01T10:00:00Z",
        end_time="2025-01-01T11:00:00Z",
        description="Sync up",
        calendar_id="primary" # Example
    )
    mock_db_service.create_event.return_value = mock_created_event

    # Mock the final task state returned by task_manager.complete_task
    final_task = Task(
        id=task_id,
        sessionId=None,
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role="agent", parts=[Part(type="text", text=f"Event '{event_title}' created successfully.")])
        ),
        params=None, # Params might be set in actual implementation
        artifacts=[
            Artifact(name="created_event", parts=[Part(type="data", data=mock_created_event.model_dump())])
        ],
        history=[] # History might be populated
    )
    mock_task_manager.complete_task.return_value = final_task # Make complete_task return final state

    # Prepare the structured data input for the event
    event_data = {
        "title": event_title,
        "start_time": "2025-01-01T10:00:00Z",
        "end_time": "2025-01-01T11:00:00Z",
        "description": "Sync up",
        "calendar_id": "primary"
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0", # Use 2.0
        method="tasks/send",
        params=params.model_dump(), # Send Pydantic model as dict
        id=request_id,
    )

    # Override dependencies for this test
    app.dependency_overrides[get_db_service] = lambda: mock_db_service
    app.dependency_overrides[get_task_manager] = lambda: mock_task_manager

    # Action: Send the request
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    # Assert the result matches the final task state
    # We compare the Pydantic model dump for easier comparison
    result_task_data = response_data["result"]
    expected_task_data = final_task.model_dump()

    # Basic check on key fields
    assert result_task_data.get("id") == expected_task_data.get("id")
    assert result_task_data.get("status", {}).get("state") == TaskState.COMPLETED.value
    assert len(result_task_data.get("artifacts", [])) == 1
    assert result_task_data["artifacts"][0].get("name") == "created_event"
    # Detailed artifact data check
    assert result_task_data["artifacts"][0]["parts"][0].get("data", {}).get("id") == created_event_id
    assert result_task_data["artifacts"][0]["parts"][0]["data"]["title"] == event_title

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    # TODO: Check call args for create_task if needed
    mock_db_service.create_event.assert_awaited_once()
    # TODO: Check call args for create_event if needed (validate EventCreate model)
    mock_task_manager.complete_task.assert_awaited_once()
    # TODO: Check call args for complete_task (message, artifact)

    # Clean up overrides
    app.dependency_overrides = {}

@pytest.mark.asyncio
async def test_send_task_create_conflict(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock, # Use MagicMock here
):
    """Test tasks/send for creating an event when DB raises ConflictError."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    event_title = "Conflicting Meeting"

    # Prepare the structured data input for the event
    event_data = {
        "title": event_title,
        "start_time": "2025-01-02T11:00:00Z",
        "end_time": "2025-01-02T12:00:00Z",
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id,
    )

    # Mock DB service to raise ConflictError
    conflicting_event = Event(id=str(uuid.uuid4()), title="Existing Event", start_time="2025-01-02T10:30:00Z", end_time="2025-01-02T11:30:00Z")
    conflict_error = ConflictError(message="Database conflict", conflicts=[{"event": conflicting_event}])
    mock_db_service.create_event.side_effect = conflict_error

    # Mock the conflict resolver (dependency of TaskManager, but logic moved to handler)
    # We need a ConflictResolver mock if the handler uses it directly via DI
    # Let's assume the handler will need ConflictResolver via DI later.
    mock_conflict_resolver = AsyncMock(spec=ConflictResolver)
    mock_conflict_resolver.generate_alternative_times = AsyncMock(return_value=["2025-01-02T13:00:00Z"])

    # Mock the final task state returned by task_manager.require_input_task
    expected_artifact = Artifact(
        name="conflict_info",
        parts=[Part(type="data", data={
            "message": str(conflict_error),
            "conflicts": [conflicting_event.model_dump()],
            "alternatives": ["2025-01-02T13:00:00Z"]
        })]
    )
    final_task = Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.INPUT_REQUIRED,
            message=Message(role="agent", parts=[Part(type="text", text="Event creation failed due to conflicts. Please provide an alternative time or confirm override.")])
        ),
        artifacts=[expected_artifact]
    )
    mock_task_manager.require_input_task.return_value = final_task

    # Override dependencies (including ConflictResolver, anticipating handler needs)
    app.dependency_overrides[get_db_service] = lambda: mock_db_service
    app.dependency_overrides[get_task_manager] = lambda: mock_task_manager
    # Add override for ConflictResolver IF handle_tasks_send will depend on it directly
    # from scheduling_agent.api.dependencies import get_conflict_resolver
    # app.dependency_overrides[get_conflict_resolver] = lambda: mock_conflict_resolver

    # Action: Send the request
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    # Assert the result matches the final task state (INPUT_REQUIRED)
    result_task_data = response_data["result"]
    expected_task_data = final_task.model_dump()

    assert result_task_data.get("id") == expected_task_data.get("id")
    assert result_task_data.get("status", {}).get("state") == TaskState.INPUT_REQUIRED.value
    assert len(result_task_data.get("artifacts", [])) == 1
    assert result_task_data["artifacts"][0].get("name") == "conflict_info"
    # Check artifact content
    artifact_data = result_task_data["artifacts"][0]["parts"][0].get("data", {})
    assert artifact_data.get("message") == str(conflict_error)
    assert len(artifact_data.get("conflicts", [])) == 1
    assert artifact_data["conflicts"][0]["id"] == conflicting_event.id
    assert artifact_data.get("alternatives") == ["2025-01-02T13:00:00Z"]

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_db_service.create_event.assert_awaited_once()
    # mock_conflict_resolver.generate_alternative_times.assert_awaited_once() # Assert once implemented in handler
    mock_task_manager.require_input_task.assert_awaited_once()
    # TODO: Check args for require_input_task (message, artifact)
    mock_task_manager.complete_task.assert_not_called() # Ensure complete wasn't called

    # Clean up overrides
    app.dependency_overrides = {}

@pytest.mark.asyncio
async def test_send_task_query_success(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_nlp_processor: AsyncMock
):
    """Test tasks/send for querying the schedule successfully (natural language input)."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "What do I have tomorrow?"

    # Mock NLP result for query intent
    mock_nlp_result = LLMSchedulingParseResult(
        intent="query_events",
        date_info=LLMDateInfo(start_date="tomorrow", end_date="tomorrow") # Use LLMDateInfo
    )
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # Mock the final task state returned by task_manager.complete_task
    final_task = Task(
        id=task_id,
        sessionId=None,
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role="agent", parts=[Part(type="text", text="Query successful.")])
        ),
        params=None, # Params might be set in actual implementation
        artifacts=[
            Artifact(name="query_result", parts=[Part(type="data", data={"events": []})])
        ],
        history=[] # History might be populated
    )
    mock_task_manager.complete_task.return_value = final_task # Make complete_task return final state

    # Prepare the natural language input for the query
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0", # Use 2.0
        method="tasks/send",
        params=params.model_dump(), # Send Pydantic model as dict
        id=request_id,
    )

    # Override dependencies for this test
    app.dependency_overrides[get_db_service] = lambda: mock_db_service
    app.dependency_overrides[get_task_manager] = lambda: mock_task_manager
    app.dependency_overrides[get_nlp_processor] = lambda: mock_nlp_processor

    # Action: Send the request
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    # Assert the result matches the final task state
    # We compare the Pydantic model dump for easier comparison
    result_task_data = response_data["result"]
    expected_task_data = final_task.model_dump()

    # Basic check on key fields
    assert result_task_data.get("id") == expected_task_data.get("id")
    assert result_task_data.get("status", {}).get("state") == TaskState.COMPLETED.value
    assert len(result_task_data.get("artifacts", [])) == 1
    assert result_task_data["artifacts"][0].get("name") == "query_result"
    # Detailed artifact data check
    assert result_task_data["artifacts"][0]["parts"][0].get("data", {}).get("events") == []

    # Assert mock calls
    mock_task_manager.create_task.assert_called_once()
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once()
    mock_db_service.query_events.assert_awaited_once()
    mock_task_manager.complete_task.assert_awaited_once()

    # Clean up overrides
    app.dependency_overrides = {}

# --- Integration Tests for tasks/get --- 

@pytest.mark.asyncio
async def test_get_task_success(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock, # Use existing fixture
): 
    """Test tasks/get successfully retrieves a task."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock the TaskManager response
    mock_retrieved_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.WORKING, message=Message(role="agent", parts=[Part(type="text", text="Processing...")]))
        # Add other fields like artifacts, history if needed for assertion
    )
    mock_task_manager.get_task.return_value = mock_retrieved_task

    # Prepare the RPC request
    params = TaskGetParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get",
        params=params.model_dump(),
        id=request_id
    )

    # Override the TaskManager dependency
    app.dependency_overrides[get_task_manager] = lambda: mock_task_manager

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "error" not in response_data or response_data["error"] is None
    assert "result" in response_data

    result_task_data = response_data["result"]
    assert result_task_data.get("id") == task_id
    assert result_task_data.get("status", {}).get("state") == TaskState.WORKING.value

    # Assert mock calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id)

    # Clean up overrides
    app.dependency_overrides = {}


@pytest.mark.asyncio
async def test_get_task_not_found(
    async_client: httpx.AsyncClient,
    mock_task_manager: MagicMock, # Use existing fixture
): 
    """Test tasks/get returns correct error when task is not found."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())

    # Mock TaskManager to return None
    mock_task_manager.get_task.return_value = None 

    # Prepare the RPC request
    params = TaskGetParams(id=task_id)
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/get",
        params=params.model_dump(),
        id=request_id
    )

    # Override the TaskManager dependency
    app.dependency_overrides[get_task_manager] = lambda: mock_task_manager

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200 # JSON-RPC always returns 200 OK for valid requests
    response_data = response.json()
    assert response_data.get("jsonrpc") == "2.0"
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    assert response_data["error"] is not None

    # Validate the specific A2A error (TaskNotFoundError)
    from scheduling_agent.common.types import TaskNotFoundError # Import error type
    error_data = response_data["error"]
    assert error_data.get("code") == TaskNotFoundError.DEFAULT_CODE
    # Check that the message contains the task ID for clarity
    assert f"Task with ID '{task_id}' not found." in error_data.get("message", "")

    # Assert mock calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id)

    # Clean up overrides
    app.dependency_overrides = {}