import pytest
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

# Models and Enums to use (assuming they are defined or imported in TaskManager)
from scheduling_agent.task_manager import (
    SchedulingTaskManager,
    TaskState,
    Task,
    TaskStatus,
    Message,
    Part,
    Artifact
)
from scheduling_agent.common.types import TaskSendParams, TaskNotFoundError # For create_task and error check

# Services and Components to Mock (needed for TaskManager.__init__)
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter

# Keep necessary fixtures for TaskManager instantiation, even if not used by tests
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession

# --- Mock Fixtures (Minimal Set for Instantiation - May need adjustments) ---
# Keep these fixtures ONLY because TaskManager __init__ requires them.
# The new tests should NOT rely on the behavior of these mocks.

@pytest.fixture
def mock_nlp_processor() -> AsyncMock:
    # Add date_utils mock if needed by TaskManager init process
    mock = AsyncMock(spec=NLPProcessor)
    mock.date_utils = MagicMock() 
    return mock

@pytest.fixture
def mock_db_service() -> AsyncMock:
    # Add minimal spec if needed by __init__
    mock = AsyncMock(spec=DatabaseService)
    # Add date_utils mock if needed by TaskManager init process (e.g., for default exporters)
    mock.date_utils = MagicMock() 
    return mock

@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    return AsyncMock(spec=ConflictResolver)

@pytest.fixture
def mock_ical_exporter() -> MagicMock:
    mock = MagicMock(spec=ICalExporter)
    mock.get_mime_type = MagicMock(return_value="text/calendar")
    return mock

@pytest.fixture
def mock_org_exporter() -> MagicMock:
    mock = MagicMock(spec=OrgModeExporter)
    mock.get_mime_type = MagicMock(return_value="text/plain") # Assuming org exporter mime type
    return mock

@pytest.fixture
def mock_exporters(mock_ical_exporter: MagicMock, mock_org_exporter: MagicMock) -> dict:
    # Provide a basic dictionary structure required by __init__
    return {"ics": mock_ical_exporter, "org": mock_org_exporter}

@pytest.fixture
def task_manager(
    mock_nlp_processor: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    mock_exporters: dict,
    test_session_factory: sessionmaker # Assuming this exists in conftest
) -> SchedulingTaskManager:
    """Provides a SchedulingTaskManager instance."""
    # Instantiate with mocks, even though new tests primarily check internal state
    manager = SchedulingTaskManager(
        session_factory=test_session_factory,
        nlp_processor=mock_nlp_processor,
        db_service=mock_db_service,
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters
    )
    # Clear any potential tasks from previous tests if state persists across test functions
    manager._tasks = {}
    return manager

# --- New State Management Unit Tests ---

@pytest.mark.asyncio
async def test_create_task_state(task_manager: SchedulingTaskManager):
    """Verify create_task adds a task to the internal dictionary."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    # Minimal params needed for state creation
    params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[Part(type="text", text="test")])
    )

    assert task_id not in task_manager._tasks
    created_task = await task_manager.create_task(params=params)

    assert task_id in task_manager._tasks
    # Retrieve from dict directly to check what was stored
    stored_task = task_manager._tasks[task_id]
    assert stored_task.id == task_id
    assert stored_task.sessionId == session_id
    assert stored_task.status.state == TaskState.WORKING # Check initial state per implementation
    assert stored_task.params == params
    # Check that the returned task is a deep copy
    assert created_task == stored_task
    assert created_task is not stored_task

@pytest.mark.asyncio
async def test_get_task_state_found(task_manager: SchedulingTaskManager):
    """Verify get_task retrieves an existing task from the internal dictionary."""
    task_id = str(uuid.uuid4())
    task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = task # Manually insert for testing get

    retrieved_task = await task_manager.get_task(task_id)

    assert retrieved_task is not None
    assert retrieved_task == task
    # Check it's a copy
    assert retrieved_task is not task

@pytest.mark.asyncio
async def test_get_task_state_not_found(task_manager: SchedulingTaskManager):
    """Verify get_task returns None for a non-existent task."""
    task_id = str(uuid.uuid4())
    assert task_id not in task_manager._tasks

    retrieved_task = await task_manager.get_task(task_id)

    assert retrieved_task is None

@pytest.mark.asyncio
async def test_update_task_status_state(task_manager: SchedulingTaskManager):
    """Verify update_task_status modifies the status of an existing task."""
    task_id = str(uuid.uuid4())
    initial_status = TaskStatus(state=TaskState.WORKING, message=Message(role="agent", parts=[Part(type="text", text="Working...")]))
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=initial_status,
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task

    new_status = TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[Part(type="text", text="Done!")]))
    updated_task = await task_manager.update_task_status(task_id, new_status)

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert stored_task.status == new_status
    assert updated_task.status == new_status
    assert len(stored_task.history) == 1
    assert stored_task.history[0] == initial_status
    assert len(updated_task.history) == 1
    assert updated_task.history[0] == initial_status
    # Check it's a copy
    assert updated_task is not stored_task

@pytest.mark.asyncio
async def test_update_task_status_not_found(task_manager: SchedulingTaskManager):
    """Verify update_task_status raises error for non-existent task."""
    task_id = str(uuid.uuid4())
    new_status = TaskStatus(state=TaskState.COMPLETED)
    # Assuming TaskNotFoundError is defined and inherits from Exception
    with pytest.raises(TaskNotFoundError):
        await task_manager.update_task_status(task_id, new_status)

@pytest.mark.asyncio
async def test_add_task_artifact_state(task_manager: SchedulingTaskManager):
    """Verify add_task_artifact adds an artifact to an existing task."""
    task_id = str(uuid.uuid4())
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task


    artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])
    updated_task = await task_manager.add_task_artifact(task_id, artifact)

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert len(stored_task.artifacts) == 1
    assert stored_task.artifacts[0] == artifact
    assert updated_task.artifacts[0] == artifact
    # Check it's a copy
    assert updated_task is not stored_task

@pytest.mark.asyncio
async def test_add_task_artifact_not_found(task_manager: SchedulingTaskManager):
    """Verify add_task_artifact raises error for non-existent task."""
    task_id = str(uuid.uuid4())
    artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])
    # Assuming TaskNotFoundError is defined and inherits from Exception
    with pytest.raises(TaskNotFoundError):
        await task_manager.add_task_artifact(task_id, artifact)


@pytest.mark.asyncio
async def test_complete_task_state(task_manager: SchedulingTaskManager):
    """Verify complete_task sets state to COMPLETED and adds optional artifact."""
    task_id = str(uuid.uuid4())
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task


    final_artifact = Artifact(name="final_result", parts=[Part(type="data", data={"value": 123})])
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    updated_task = await task_manager.complete_task(task_id, completion_message, final_artifact)

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert stored_task.status.state == TaskState.COMPLETED
    assert stored_task.status.message == completion_message
    assert len(stored_task.artifacts) == 1
    assert stored_task.artifacts[0] == final_artifact
    assert updated_task == stored_task

@pytest.mark.asyncio
async def test_complete_task_state_no_artifact(task_manager: SchedulingTaskManager):
    """Verify complete_task works without an artifact."""
    task_id = str(uuid.uuid4())
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task

    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    updated_task = await task_manager.complete_task(task_id, completion_message, None) # Pass None for artifact

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert stored_task.status.state == TaskState.COMPLETED
    assert stored_task.status.message == completion_message
    assert len(stored_task.artifacts) == 0 # No artifact added
    assert updated_task == stored_task

@pytest.mark.asyncio
async def test_fail_task_state(task_manager: SchedulingTaskManager):
    """Verify fail_task sets state to FAILED and adds error message."""
    task_id = str(uuid.uuid4())
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task

    error_message = Message(role="agent", parts=[Part(type="text", text="Something went wrong.")])
    updated_task = await task_manager.fail_task(task_id, error_message)

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert stored_task.status.state == TaskState.FAILED
    assert stored_task.status.message == error_message
    assert updated_task == stored_task

@pytest.mark.asyncio
async def test_require_input_task_state(task_manager: SchedulingTaskManager):
    """Verify require_input_task sets state to INPUT_REQUIRED and adds message/artifact."""
    task_id = str(uuid.uuid4())
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task

    prompt_message = Message(role="agent", parts=[Part(type="text", text="Which option?")])
    input_artifact = Artifact(name="options", parts=[Part(type="data", data=["A", "B"])])
    updated_task = await task_manager.require_input_task(task_id, prompt_message, input_artifact)

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert stored_task.status.state == TaskState.INPUT_REQUIRED
    assert stored_task.status.message == prompt_message
    assert len(stored_task.artifacts) == 1
    assert stored_task.artifacts[0] == input_artifact
    assert updated_task == stored_task

@pytest.mark.asyncio
async def test_require_input_task_state_no_artifact(task_manager: SchedulingTaskManager):
    """Verify require_input_task works without an artifact."""
    task_id = str(uuid.uuid4())
    initial_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[]
    )
    task_manager._tasks[task_id] = initial_task

    prompt_message = Message(role="agent", parts=[Part(type="text", text="Please confirm.")])
    updated_task = await task_manager.require_input_task(task_id, prompt_message, None) # Pass None for artifact

    assert task_id in task_manager._tasks
    stored_task = task_manager._tasks[task_id]
    assert stored_task.status.state == TaskState.INPUT_REQUIRED
    assert stored_task.status.message == prompt_message
    assert len(stored_task.artifacts) == 0 # No artifact added
    assert updated_task == stored_task

# TODO: Add tests for edge cases like updating/getting non-existent tasks if methods should raise errors. 