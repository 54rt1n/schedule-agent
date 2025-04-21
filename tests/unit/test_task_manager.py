import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

# Keep necessary fixtures for TaskManager instantiation, even if not used by tests
from sqlalchemy.orm import sessionmaker

from scheduling_agent.a2a.types import (
    TaskSendParams,
)  # For create_task and error check
from scheduling_agent.core.exceptions import TaskNotFoundError
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter

# Services and Components to Mock (needed for TaskManager.__init__)
from scheduling_agent.core.nlp_processor import NLPProcessor
# We will mock DatabaseService specifically for TaskManager tests
# from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.service import DatabaseService # Keep this import for spec

# Models and Enums to use (assuming they are defined or imported in TaskManager)
from scheduling_agent.a2a.task_manager import (
    Artifact,
    Message,
    Part,
    SchedulingTaskManager,
    Task,
    TaskState,
    TaskStatus,
)

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
    # Return a new AsyncMock for TaskManager tests
    # Add spec=DatabaseService to ensure mock follows the service interface
    return AsyncMock(spec=DatabaseService)


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
    mock.get_mime_type = MagicMock(return_value="text/plain")  # Assuming org exporter mime type
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
) -> SchedulingTaskManager:
    """Provides a SchedulingTaskManager instance with a mocked DB service."""
    # Instantiate with mocks, TaskManager will use the injected mock_db_service
    manager = SchedulingTaskManager(
        # session_factory is no longer directly used by TaskManager
        session_factory=None, # Pass None as TaskManager init expects it
        nlp_processor=mock_nlp_processor,
        db_service=mock_db_service, # Inject the mock
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters,
    )
    # Remove internal state clearing, as it relies on DB now
    # manager._tasks = {}
    return manager


# --- New State Management Unit Tests (Refactored for DB Mocking) ---


@pytest.mark.asyncio
async def test_create_task_state(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify create_task calls db_service.save_a2a_task."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    user_message = Message(role="user", parts=[Part(type="text", text="test")])
    params = TaskSendParams(id=task_id, sessionId=session_id, message=user_message)

    # Mock the return value expected from save_a2a_task (if create_task uses it)
    # In this refactor, create_task constructs the task and calls save, then returns it.
    # Let's assume create_task returns the constructed task object directly after saving.
    # The test will primarily check the call to save_a2a_task.

    # --- Act ---
    created_task = await task_manager.create_task(params=params)

    # --- Assert ---
    # Verify save_a2a_task was called correctly
    mock_db_service.save_a2a_task.assert_awaited_once()
    call_args, _ = mock_db_service.save_a2a_task.await_args
    saved_task_arg = call_args[0]
    assert isinstance(saved_task_arg, Task)
    assert saved_task_arg.id == task_id
    assert saved_task_arg.sessionId == session_id
    assert saved_task_arg.params == params
    # Check the initial state set by create_task (implementation detail)
    assert saved_task_arg.status.state == TaskState.WORKING

    # Check the returned task matches expected structure (id, state)
    assert created_task.id == task_id
    assert created_task.status.state == TaskState.WORKING
    assert created_task.params == params


@pytest.mark.asyncio
async def test_get_task_state_found(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify get_task calls db_service.get_a2a_task_by_id and returns the result."""
    task_id = str(uuid.uuid4())
    # Mock the return value of the DB service call
    mock_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[],
    )
    mock_db_service.get_a2a_task_by_id.return_value = mock_task

    # --- Act ---
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    # Verify get_a2a_task_by_id was called correctly
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    # Verify the returned task is the one from the DB service
    assert retrieved_task is not None
    # TaskManager.get_task should return the object directly from DB service
    assert retrieved_task == mock_task
    # Check if get_task returns a copy or the original (depends on implementation)
    # If it returns the direct object, this assertion is fine.
    # assert retrieved_task is not mock_task


@pytest.mark.asyncio
async def test_get_task_state_not_found(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify get_task returns None when db_service.get_a2a_task_by_id returns None."""
    task_id = str(uuid.uuid4())
    # Mock the DB service to return None
    mock_db_service.get_a2a_task_by_id.return_value = None

    # --- Act ---
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    # Verify get_a2a_task_by_id was called
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    # Verify None was returned
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_update_task_status_state(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify update_task_status calls db_service.update_a2a_task_status and get_a2a_task_by_id."""
    task_id = str(uuid.uuid4())
    new_status = TaskStatus(
        state=TaskState.COMPLETED,
        message=Message(role="agent", parts=[Part(type="text", text="Done!")]),
    )
    # Mock the task that get_a2a_task_by_id will return *after* the update
    updated_mock_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=new_status, # Status reflects the update
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[],
        history=[TaskStatus(state=TaskState.WORKING)], # Simulate history being added by DB layer
    )
    mock_db_service.get_a2a_task_by_id.return_value = updated_mock_task
    # We don't need to mock update_a2a_task_status return value as it returns None

    # --- Act ---
    updated_task_result = await task_manager.update_task_status(task_id, new_status)

    # --- Assert ---
    # Verify update_a2a_task_status was called
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, new_status)
    # Verify get_a2a_task_by_id was called to fetch the updated task
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    # Verify the returned task is the one fetched after update
    assert updated_task_result == updated_mock_task
    # Check if update_task_status returns a copy or the original (depends on implementation)
    # assert updated_task_result is not updated_mock_task


@pytest.mark.asyncio
async def test_update_task_status_not_found(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify update_task_status raises TaskNotFoundError if db_service raises it."""
    task_id = str(uuid.uuid4())
    new_status = TaskStatus(state=TaskState.COMPLETED)
    # Mock db_service.update_a2a_task_status to raise the error
    mock_db_service.update_a2a_task_status.side_effect = TaskNotFoundError(task_id)

    # --- Act & Assert ---
    with pytest.raises(TaskNotFoundError):
        await task_manager.update_task_status(task_id, new_status)
    # Verify the DB service method was called
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, new_status)
    # Verify get_task was NOT called after the error
    mock_db_service.get_a2a_task_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_add_task_artifact_state(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify add_task_artifact calls get_task and save_a2a_task."""
    task_id = str(uuid.uuid4())
    artifact_to_add = Artifact(name="result", parts=[Part(type="text", text="some data")])

    # Mock the task returned by the initial get_task call
    initial_mock_task = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.WORKING),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[], # Starts empty
        history=[],
    )
    mock_db_service.get_a2a_task_by_id.return_value = initial_mock_task
    # save_a2a_task returns None

    # Mock the task returned by get_task *after* save_a2a_task is called (if add_task_artifact re-fetches)
    # Or, more simply, check the object passed to save_a2a_task and the object returned by add_task_artifact

    # --- Act ---
    returned_task = await task_manager.add_task_artifact(task_id, artifact_to_add)

    # --- Assert ---
    # Verify get_a2a_task_by_id was called first
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)

    # Verify save_a2a_task was called with the updated task object
    mock_db_service.save_a2a_task.assert_awaited_once()
    call_args, _ = mock_db_service.save_a2a_task.await_args
    saved_task_arg = call_args[0]
    assert isinstance(saved_task_arg, Task)
    assert saved_task_arg.id == task_id
    assert len(saved_task_arg.artifacts) == 1
    assert saved_task_arg.artifacts[0] == artifact_to_add # Check artifact was added

    # Verify the returned task has the artifact added
    assert returned_task is not None
    assert len(returned_task.artifacts) == 1
    assert returned_task.artifacts[0] == artifact_to_add
    # Check the returned task state matches the saved task state
    assert returned_task == saved_task_arg


@pytest.mark.asyncio
async def test_add_task_artifact_not_found(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify add_task_artifact raises TaskNotFoundError if get_task fails."""
    task_id = str(uuid.uuid4())
    artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])
    # Mock get_a2a_task_by_id to return None
    mock_db_service.get_a2a_task_by_id.return_value = None

    # --- Act & Assert ---
    # Fix: Expect core.exceptions.TaskNotFoundError
    with pytest.raises(TaskNotFoundError):
        await task_manager.add_task_artifact(task_id, artifact)

    # Verify get was called, save was not
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    mock_db_service.save_a2a_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_complete_task_state(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify complete_task calls update_task_status and add_task_artifact (via db mocks)."""
    task_id = str(uuid.uuid4())
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    final_artifact = Artifact(name="final_result", parts=[Part(type="data", data={"value": 123})])

    # Mock the task returned after the status update
    task_after_status_update = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=TaskStatus(state=TaskState.COMPLETED, message=completion_message),
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[], # Artifact not added yet
        history=[TaskStatus(state=TaskState.WORKING)]
    )
    # Mock the task returned after the artifact is added (final state)
    task_after_artifact_add = task_after_status_update.model_copy(deep=True)
    task_after_artifact_add.artifacts.append(final_artifact)

    # Mock get_a2a_task_by_id: First call (in update_task_status) returns task after status update,
    # second call (in add_task_artifact) also returns it (before artifact is appended internally)
    # third call (in add_task_artifact again after save) returns the final state.
    # Let's simplify: update_task_status calls update and get. add_task_artifact calls get and save.
    # Mock the get call within add_task_artifact
    mock_db_service.get_a2a_task_by_id.side_effect = [
        task_after_status_update, # Return for get call inside update_task_status
        task_after_status_update, # Return for get call inside add_task_artifact
    ]
    # Mock update_a2a_task_status (returns None)
    # Mock save_a2a_task (returns None)

    # --- Act ---
    final_returned_task = await task_manager.complete_task(task_id, completion_message, final_artifact)

    # --- Assert ---
    # Verify update_task_status was called
    expected_status_update = TaskStatus(state=TaskState.COMPLETED, message=completion_message)
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, expected_status_update)
    # Verify get was called (twice: once in update_task_status, once in add_task_artifact)
    assert mock_db_service.get_a2a_task_by_id.await_count == 2
    mock_db_service.get_a2a_task_by_id.assert_any_await(task_id)
    # Verify save was called by add_task_artifact
    mock_db_service.save_a2a_task.assert_awaited_once()
    call_args, _ = mock_db_service.save_a2a_task.await_args
    saved_task = call_args[0]
    assert len(saved_task.artifacts) == 1
    assert saved_task.artifacts[0] == final_artifact

    # Verify the final returned task state
    assert final_returned_task.status.state == TaskState.COMPLETED
    assert final_returned_task.status.message == completion_message
    assert len(final_returned_task.artifacts) == 1
    assert final_returned_task.artifacts[0] == final_artifact


@pytest.mark.asyncio
async def test_complete_task_state_no_artifact(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify complete_task calls update_task_status only (via db mock)."""
    task_id = str(uuid.uuid4())
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    expected_status_update = TaskStatus(state=TaskState.COMPLETED, message=completion_message)

    # Mock the task returned after the status update
    task_after_status_update = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=expected_status_update,
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[], 
        history=[TaskStatus(state=TaskState.WORKING)]
    )
    mock_db_service.get_a2a_task_by_id.return_value = task_after_status_update

    # --- Act ---
    final_returned_task = await task_manager.complete_task(task_id, completion_message, None) # No artifact

    # --- Assert ---
    # Verify update_task_status was called
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, expected_status_update)
    # Verify get was called by update_task_status
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    # Verify add_task_artifact (and thus save_a2a_task) was NOT called
    mock_db_service.save_a2a_task.assert_not_awaited()

    # Verify the final returned task state
    assert final_returned_task.status.state == TaskState.COMPLETED
    assert final_returned_task.status.message == completion_message
    assert len(final_returned_task.artifacts) == 0


@pytest.mark.asyncio
async def test_fail_task_state(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify fail_task calls update_task_status (via db mock)."""
    task_id = str(uuid.uuid4())
    error_message = Message(role="agent", parts=[Part(type="text", text="Something went wrong.")])
    expected_status_update = TaskStatus(state=TaskState.FAILED, message=error_message)

    # Mock the task returned after the status update
    task_after_status_update = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=expected_status_update,
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[], 
        history=[TaskStatus(state=TaskState.WORKING)]
    )
    mock_db_service.get_a2a_task_by_id.return_value = task_after_status_update

    # --- Act ---
    final_returned_task = await task_manager.fail_task(task_id, error_message)

    # --- Assert ---
    # Verify update_task_status was called
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, expected_status_update)
    # Verify get was called by update_task_status
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    # Verify add_task_artifact was NOT called
    mock_db_service.save_a2a_task.assert_not_awaited()

    # Verify the final returned task state
    assert final_returned_task.status.state == TaskState.FAILED
    assert final_returned_task.status.message == error_message


@pytest.mark.asyncio
async def test_require_input_task_state(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify require_input_task calls update and add_artifact (via db mocks)."""
    task_id = str(uuid.uuid4())
    prompt_message = Message(role="agent", parts=[Part(type="text", text="Which option?")])
    input_artifact = Artifact(name="options", parts=[Part(type="data", data=["A", "B"])])
    expected_status_update = TaskStatus(state=TaskState.INPUT_REQUIRED, message=prompt_message)

    # Mock the task returned after the status update
    task_after_status_update = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=expected_status_update,
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[], # Artifact not added yet
        history=[TaskStatus(state=TaskState.WORKING)]
    )
    # Mock the final task state after artifact is added
    task_after_artifact_add = task_after_status_update.model_copy(deep=True)
    task_after_artifact_add.artifacts.append(input_artifact)

    # Mock get calls
    mock_db_service.get_a2a_task_by_id.side_effect = [
        task_after_status_update, # For update_task_status
        task_after_status_update, # For add_task_artifact
    ]

    # --- Act ---
    final_returned_task = await task_manager.require_input_task(task_id, prompt_message, input_artifact)

    # --- Assert ---
    # Verify update_task_status was called
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, expected_status_update)
    # Verify get was called twice
    assert mock_db_service.get_a2a_task_by_id.await_count == 2
    mock_db_service.get_a2a_task_by_id.assert_any_await(task_id)
    # Verify save was called by add_task_artifact
    mock_db_service.save_a2a_task.assert_awaited_once()
    call_args, _ = mock_db_service.save_a2a_task.await_args
    saved_task = call_args[0]
    assert len(saved_task.artifacts) == 1
    assert saved_task.artifacts[0] == input_artifact

    # Verify the final returned task state
    assert final_returned_task.status.state == TaskState.INPUT_REQUIRED
    assert final_returned_task.status.message == prompt_message
    assert len(final_returned_task.artifacts) == 1
    assert final_returned_task.artifacts[0] == input_artifact


@pytest.mark.asyncio
async def test_require_input_task_state_no_artifact(task_manager: SchedulingTaskManager, mock_db_service: AsyncMock):
    """Verify require_input_task calls update_task_status only (via db mock)."""
    task_id = str(uuid.uuid4())
    prompt_message = Message(role="agent", parts=[Part(type="text", text="Please confirm.")])
    expected_status_update = TaskStatus(state=TaskState.INPUT_REQUIRED, message=prompt_message)

    # Mock the task returned after the status update
    task_after_status_update = Task(
        id=task_id,
        sessionId=str(uuid.uuid4()),
        status=expected_status_update,
        params=TaskSendParams(id=task_id, message=Message(role="user", parts=[Part(type="text", text="test")])),
        artifacts=[], 
        history=[TaskStatus(state=TaskState.WORKING)]
    )
    mock_db_service.get_a2a_task_by_id.return_value = task_after_status_update

    # --- Act ---
    final_returned_task = await task_manager.require_input_task(task_id, prompt_message, None) # No artifact

    # --- Assert ---
    # Verify update_task_status was called
    mock_db_service.update_a2a_task_status.assert_awaited_once_with(task_id, expected_status_update)
    # Verify get was called by update_task_status
    mock_db_service.get_a2a_task_by_id.assert_awaited_once_with(task_id)
    # Verify add_task_artifact (and thus save_a2a_task) was NOT called
    mock_db_service.save_a2a_task.assert_not_awaited()

    # Verify the final returned task state
    assert final_returned_task.status.state == TaskState.INPUT_REQUIRED
    assert final_returned_task.status.message == prompt_message
    assert len(final_returned_task.artifacts) == 0

# TODO: Refactor test_cancel_task
