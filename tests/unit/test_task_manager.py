from datetime import UTC, datetime, timedelta
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from sa.a2a.dto import (
    TaskSendParams,
    Artifact,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.exceptions import TaskNotFoundError, TaskNotCancelableError
from sa.core.logging_config import get_logger
from sa.db.models import A2ATask
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy
from sa.event.exporters.ical_exporter import ICalExporter
from sa.event.exporters.orgmode_exporter import OrgModeExporter
from sa.nlp.nlp_processor import NLPProcessor
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver
from sa.db.service import DatabaseService

logger = get_logger(__name__)

def create_mock_a2a_task(
    task_id: str,
    session_id: str | None = None,
    state: TaskState = TaskState.WORKING,
    params: TaskSendParams | None = None,
    artifacts: list[Artifact] | None = None,
    history: list[TaskStatus] | None = None,
    status_message: Message | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> A2ATask:
    """Creates an A2ATask DB model instance with JSON fields populated."""
    if session_id is None: session_id = str(uuid.uuid4())
    if params is None: params = TaskSendParams(id=task_id, sessionId=session_id, message=Message(role="user", parts=[Part(type="text", text="test")]))
    if artifacts is None: artifacts = []
    if history is None: history = []
    if created_at is None: created_at = datetime.now(UTC)
    if updated_at is None: updated_at = datetime.now(UTC)

    return A2ATask(
        id=task_id,
        session_id=session_id,
        state=state.value,
        status_message_json=status_message.model_dump_json() if status_message else None,
        params_json=params.model_dump_json() if params else None,
        artifacts_json=json.dumps([a.model_dump() for a in artifacts]),
        history_json=json.dumps([h.model_dump() for h in history]),
        created_at=created_at.replace(tzinfo=None), # Store naive
        updated_at=updated_at.replace(tzinfo=None), # Store naive
    )

# --- Helper Function for Creating Minimal TaskSendParams --- #
def create_minimal_task_params(task_id: str, session_id: str | None = None) -> TaskSendParams:
    """Creates a minimal valid TaskSendParams for mocking."""
    if session_id is None: session_id = str(uuid.uuid4())
    return TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[Part(type="text", text="mock message")])
    )

# --- Mock Fixtures --- #

@pytest.fixture
def mock_nlp_processor() -> AsyncMock:
    mock = AsyncMock(spec=NLPProcessor)
    # TaskManager no longer needs date_utils directly from NLPProcessor
    # mock.date_utils = MagicMock()
    return mock

# Fixtures for the Strategies
@pytest.fixture
def mock_get_task_strategy() -> AsyncMock:
    return AsyncMock(spec=GetA2ATaskByIdStrategy)

@pytest.fixture
def mock_save_task_strategy() -> AsyncMock:
    return AsyncMock(spec=SaveA2ATaskStrategy)

@pytest.fixture
def mock_update_status_strategy() -> AsyncMock:
    return AsyncMock(spec=UpdateA2ATaskStatusStrategy)

# Keep exporter mocks if needed
@pytest.fixture
def mock_ical_exporter() -> MagicMock:
    mock = MagicMock(spec=ICalExporter)
    mock.get_mime_type = MagicMock(return_value="text/calendar")
    return mock

@pytest.fixture
def mock_org_exporter() -> MagicMock:
    mock = MagicMock(spec=OrgModeExporter)
    mock.get_mime_type = MagicMock(return_value="text/plain")
    return mock

@pytest.fixture
def mock_exporters(mock_ical_exporter: MagicMock, mock_org_exporter: MagicMock) -> dict:
    return {"ics": mock_ical_exporter, "org": mock_org_exporter}

# Updated Task Manager Fixture
@pytest.fixture
def task_manager(
    mock_nlp_processor: AsyncMock,
    mock_exporters: dict,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    db_service: DatabaseService,
    initialize_db: None,
) -> SchedulingTaskManager:
    """Provides a SchedulingTaskManager instance using the real db_service."""
    manager = SchedulingTaskManager(
        nlp_processor=mock_nlp_processor,
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        exporters=mock_exporters,
    )
    # The manager now internally creates real strategies using the real db_service
    return manager

# --- Refactored Tests --- #

# This test is now redundant with test_create_task_integration
# We can remove it or keep it as a simpler unit test if desired,
# but it needs significant rework to not conflict with the integration fixture.
# For now, let's remove it to focus on the integration tests.

# @pytest.mark.asyncio
# async def test_create_task_state(
#     task_manager: SchedulingTaskManager,
#     # Removed mock_save_task_strategy: AsyncMock,
# ):
#     """Verify create_task successfully creates and persists a task."""
#     task_id = str(uuid.uuid4())
#     session_id = str(uuid.uuid4())
#     user_message = Message(role="user", parts=[Part(type="text", text="test")])
#     params = TaskSendParams(id=task_id, sessionId=session_id, message=user_message)

#     # --- Act ---
#     # No patching needed for integration test
#     created_task = await task_manager.create_task(params=params)

#     # Retrieve the task to verify persistence
#     retrieved_task = await task_manager.get_task(task_id)

#     # --- Assert ---
#     # 1. Check the returned task from create_task
#     assert created_task is not None
#     assert created_task.id == task_id
#     assert created_task.sessionId == session_id
#     assert created_task.status.state == TaskState.WORKING
#     assert created_task.params == params
#     assert created_task.artifacts == []
#     assert created_task.history == []
#     assert created_task.createdAt is not None
#     assert created_task.updatedAt is not None

#     # 2. Check the retrieved task from get_task
#     assert retrieved_task is not None
#     assert retrieved_task.id == task_id
#     assert retrieved_task.status.state == TaskState.WORKING
#     # Compare model dumps excluding timestamps which might differ slightly
#     assert created_task.model_dump(exclude={'createdAt', 'updatedAt'}) == \
#            retrieved_task.model_dump(exclude={'createdAt', 'updatedAt'})

@pytest.mark.asyncio
async def test_get_task_state_found(
    task_manager: SchedulingTaskManager,
    # Removed mock_get_task_strategy: AsyncMock,
):
    """Verify get_task successfully retrieves a created task."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)

    # --- Arrange ---
    # Create a task first using the task manager
    created_task = await task_manager.create_task(params=params)
    assert created_task is not None, "Task creation failed in Arrange step"

    # --- Act ---
    # Retrieve the task using the task manager
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    # Verify the retrieved task exists and matches the created one
    assert retrieved_task is not None
    assert retrieved_task.id == task_id
    assert retrieved_task.status.state == TaskState.WORKING # Initial state
    assert retrieved_task.params == params
    assert retrieved_task.artifacts == []
    assert retrieved_task.history == []
    # Compare model dumps excluding timestamps
    assert retrieved_task.model_dump(exclude={'createdAt', 'updatedAt'}) == \
           created_task.model_dump(exclude={'createdAt', 'updatedAt'})


@pytest.mark.asyncio
async def test_get_task_state_not_found(
    task_manager: SchedulingTaskManager,
    # Removed mock_get_task_strategy: AsyncMock,
):
    """Verify get_task returns None for a non-existent task ID."""
    task_id = str(uuid.uuid4()) # Use an ID that definitely doesn't exist

    # --- Act ---
    # No patching needed, call the real method
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    # Verify None was returned
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_update_task_status_state(
    task_manager: SchedulingTaskManager,
    # Removed mock_update_status_strategy: AsyncMock,
):
    """Verify update_task_status successfully updates task state in DB."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)

    # --- Arrange ---
    # Create the initial task
    initial_task = await task_manager.create_task(params=params)
    assert initial_task is not None
    assert initial_task.status.state == TaskState.WORKING

    new_status_obj = TaskStatus(
        state=TaskState.COMPLETED,
        message=Message(role="agent", parts=[Part(type="text", text="Task is done.")]),
    )

    # --- Act ---
    # Update the status using the real task manager method
    updated_task_dto = await task_manager.update_task_status(task_id, new_status_obj)

    # Retrieve the task again to verify persistence
    retrieved_task_dto = await task_manager.get_task(task_id)

    # --- Assert ---
    # 1. Check the DTO returned by update_task_status
    assert updated_task_dto is not None
    assert updated_task_dto.id == task_id
    assert updated_task_dto.status.state == TaskState.COMPLETED
    assert updated_task_dto.status.message == new_status_obj.message

    # 2. Check the DTO retrieved from the DB after the update
    assert retrieved_task_dto is not None
    assert retrieved_task_dto.status.state == TaskState.COMPLETED
    assert retrieved_task_dto.status.message == new_status_obj.message

    # 3. Check history update (assuming TaskManager adds status to history)
    assert len(retrieved_task_dto.history) > 0 # Should contain at least the new status
    # Find the completed status in history
    completed_history_entry = next((h for h in retrieved_task_dto.history if h.state == TaskState.COMPLETED), None)
    assert completed_history_entry is not None
    assert completed_history_entry.message == new_status_obj.message

    # 4. Check timestamp updated
    assert retrieved_task_dto.updatedAt is not None
    assert initial_task.updatedAt is not None
    assert retrieved_task_dto.updatedAt > initial_task.updatedAt


@pytest.mark.asyncio
async def test_add_task_artifact_state(
    task_manager: SchedulingTaskManager,
    # Removed mock_get_task_strategy: AsyncMock,
    # Removed mock_save_task_strategy: AsyncMock,
):
    """Verify add_task_artifact successfully adds an artifact to a task."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    artifact_to_add = Artifact(name="result.txt", parts=[Part(type="text", text="content")])

    # --- Arrange ---
    # Create the initial task
    initial_task = await task_manager.create_task(params=params)
    assert initial_task is not None
    assert initial_task.artifacts == []

    # --- Act ---
    # Add the artifact using the real method
    task_with_artifact = await task_manager.add_task_artifact(task_id, artifact_to_add)

    # Retrieve the task again to verify persistence
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    # 1. Check DTO returned by add_task_artifact
    assert task_with_artifact is not None
    assert len(task_with_artifact.artifacts) == 1
    assert task_with_artifact.artifacts[0] == artifact_to_add

    # 2. Check DTO retrieved from DB
    assert retrieved_task is not None
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0] == artifact_to_add

    # 3. Check timestamp updated
    assert retrieved_task.updatedAt is not None
    assert initial_task.updatedAt is not None
    assert retrieved_task.updatedAt > initial_task.updatedAt


@pytest.mark.asyncio
async def test_add_task_artifact_not_found(
    task_manager: SchedulingTaskManager,
    # Removed mock_get_task_strategy: AsyncMock,
):
    """Verify add_task_artifact raises TaskNotFoundError for a non-existent task."""
    task_id = str(uuid.uuid4()) # Use an ID that definitely doesn't exist
    artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])

    # --- Act & Assert ---
    # No patching needed, call the real method and expect TaskNotFoundError
    with pytest.raises(TaskNotFoundError):
        await task_manager.add_task_artifact(task_id, artifact)

# This test is now redundant with test_add_task_artifact_integration_not_found
# Commenting out.
# @pytest.mark.asyncio
# async def test_add_task_artifact_not_found(
#     task_manager: SchedulingTaskManager,
# ):
#     """Verify add_task_artifact raises TaskNotFoundError for a non-existent task."""
#     task_id = str(uuid.uuid4()) # Use an ID that definitely doesn't exist
#     artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])
#
#     # --- Act & Assert ---
#     # No patching needed, call the real method and expect TaskNotFoundError
#     with pytest.raises(TaskNotFoundError):
#         await task_manager.add_task_artifact(task_id, artifact)

# --- Tests for Higher-Level Methods (Rely on previous mocks) --- #
# These tests now verify the correct sequence of calls to the refactored
# lower-level methods (update_task_status, add_task_artifact)

@pytest.mark.asyncio
async def test_complete_task_state(
    task_manager: SchedulingTaskManager,
    # We need mocks for the methods called *within* complete_task
    # i.e., update_task_status and add_task_artifact
):
    """Verify complete_task calls update_task_status and add_task_artifact."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    final_artifact = Artifact(name="final_result", parts=[Part(type="data", data={"value": 123})])

    # Mock the return values of the methods called by complete_task
    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    # Task returned after status update
    task_after_status_update = Task(
        id=task_id, status=TaskStatus(state=TaskState.COMPLETED, message=completion_message),
        sessionId=session_id, params=mock_params, artifacts=[], history=[] # Use valid params
    )
    # Task returned after artifact add
    task_after_artifact_add = task_after_status_update.model_copy(deep=True)
    task_after_artifact_add.artifacts.append(final_artifact)

    # Patch the methods directly on the task_manager instance for this test
    with patch.object(task_manager, 'update_task_status', return_value=task_after_status_update) as mock_update, \
         patch.object(task_manager, 'add_task_artifact', return_value=task_after_artifact_add) as mock_add_artifact:

        final_returned_task = await task_manager.complete_task(task_id, completion_message, final_artifact)

    # --- Assert ---
    # Verify update_task_status was called correctly
    expected_status = TaskStatus(state=TaskState.COMPLETED, message=completion_message)
    mock_update.assert_awaited_once_with(task_id, expected_status)
    # Verify add_task_artifact was called correctly (using the task_id)
    mock_add_artifact.assert_awaited_once_with(task_id, final_artifact)
    # Verify the final task returned is the one from add_task_artifact
    assert final_returned_task == task_after_artifact_add


@pytest.mark.asyncio
async def test_complete_task_state_no_artifact(task_manager: SchedulingTaskManager):
    """Verify complete_task calls update_task_status only."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    expected_status = TaskStatus(state=TaskState.COMPLETED, message=completion_message)

    # Mock the return value of update_task_status
    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    task_after_status_update = Task(
        id=task_id, status=expected_status, sessionId=session_id, params=mock_params, artifacts=[], history=[]
    )

    # Patch the methods
    with patch.object(task_manager, 'update_task_status', return_value=task_after_status_update) as mock_update, \
         patch.object(task_manager, 'add_task_artifact') as mock_add_artifact:

        final_returned_task = await task_manager.complete_task(task_id, completion_message, None) # No artifact

    # --- Assert ---
    mock_update.assert_awaited_once_with(task_id, expected_status)
    mock_add_artifact.assert_not_awaited() # Verify add_task_artifact was NOT called
    assert final_returned_task == task_after_status_update

@pytest.mark.asyncio
async def test_fail_task_state(task_manager: SchedulingTaskManager):
    """Verify fail_task calls update_task_status."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    error_message = Message(role="agent", parts=[Part(type="text", text="Something went wrong.")])
    expected_status = TaskStatus(state=TaskState.FAILED, message=error_message)

    # Mock the return value of update_task_status
    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    task_after_status_update = Task(
        id=task_id, status=expected_status, sessionId=session_id, params=mock_params, artifacts=[], history=[]
    )

    with patch.object(task_manager, 'update_task_status', return_value=task_after_status_update) as mock_update:
        final_returned_task = await task_manager.fail_task(task_id, error_message)

    # --- Assert ---
    mock_update.assert_awaited_once_with(task_id, expected_status)
    assert final_returned_task == task_after_status_update


@pytest.mark.asyncio
async def test_require_input_task_state(task_manager: SchedulingTaskManager):
    """Verify require_input_task calls update_task_status and add_task_artifact."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    prompt_message = Message(role="agent", parts=[Part(type="text", text="Which option?")])
    input_artifact = Artifact(name="options", parts=[Part(type="data", data=["A", "B"])])
    expected_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=prompt_message)

    # Mock return values
    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    task_after_status_update = Task(
        id=task_id, status=expected_status, sessionId=session_id, params=mock_params, artifacts=[], history=[]
    )
    task_after_artifact_add = task_after_status_update.model_copy(deep=True)
    task_after_artifact_add.artifacts.append(input_artifact)

    with patch.object(task_manager, 'update_task_status', return_value=task_after_status_update) as mock_update, \
         patch.object(task_manager, 'add_task_artifact', return_value=task_after_artifact_add) as mock_add_artifact:

        final_returned_task = await task_manager.require_input_task(task_id, prompt_message, input_artifact)

    # --- Assert ---
    mock_update.assert_awaited_once_with(task_id, expected_status)
    mock_add_artifact.assert_awaited_once_with(task_id, input_artifact)
    assert final_returned_task == task_after_artifact_add


@pytest.mark.asyncio
async def test_require_input_task_state_no_artifact(task_manager: SchedulingTaskManager):
    """Verify require_input_task calls update_task_status only."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    prompt_message = Message(role="agent", parts=[Part(type="text", text="Please confirm.")])
    expected_status = TaskStatus(state=TaskState.INPUT_REQUIRED, message=prompt_message)

    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    task_after_status_update = Task(
        id=task_id, status=expected_status, sessionId=session_id, params=mock_params, artifacts=[], history=[]
    )

    with patch.object(task_manager, 'update_task_status', return_value=task_after_status_update) as mock_update, \
         patch.object(task_manager, 'add_task_artifact') as mock_add_artifact:

        final_returned_task = await task_manager.require_input_task(task_id, prompt_message, None)

    # --- Assert ---
    mock_update.assert_awaited_once_with(task_id, expected_status)
    mock_add_artifact.assert_not_awaited()
    assert final_returned_task == task_after_status_update

# --- Test Cancel Task (Refactored) ---
@pytest.mark.asyncio
async def test_cancel_task_success(task_manager: SchedulingTaskManager):
    """Verify cancel_task succeeds for a cancelable state (e.g., WORKING)."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    initial_state = TaskState.WORKING
    cancellation_message = Message(role="agent", parts=[Part(type="text", text="Task canceled by request.")])
    final_expected_status = TaskStatus(state=TaskState.CANCELED, message=cancellation_message)

    # Mock return value for initial get_task call
    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    initial_task = Task(
        id=task_id, status=TaskStatus(state=initial_state), sessionId=session_id, params=mock_params, artifacts=[], history=[]
    )
    # Mock return value for the final update_task_status call
    final_task = Task(
        id=task_id, status=final_expected_status, sessionId=session_id, params=mock_params, artifacts=[], history=[TaskStatus(state=initial_state)]
    )

    with patch.object(task_manager, 'get_task', return_value=initial_task) as mock_get, \
         patch.object(task_manager, 'update_task_status', return_value=final_task) as mock_update:

        canceled_task = await task_manager.cancel_task(task_id)

    # --- Assert ---
    mock_get.assert_awaited_once_with(task_id)
    mock_update.assert_awaited_once_with(task_id, final_expected_status)
    assert canceled_task == final_task

@pytest.mark.asyncio
@pytest.mark.parametrize("non_cancelable_state", [
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELED,
])
async def test_cancel_task_not_cancelable(task_manager: SchedulingTaskManager, non_cancelable_state: TaskState):
    """Verify cancel_task raises TaskNotCancelableError for final states."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    # Mock return value for initial get_task call
    # Create minimal valid params for Task instantiation
    mock_params = create_minimal_task_params(task_id, session_id)
    initial_task = Task(
        id=task_id, status=TaskStatus(state=non_cancelable_state), sessionId=session_id, params=mock_params, artifacts=[], history=[]
    )

    with patch.object(task_manager, 'get_task', return_value=initial_task) as mock_get, \
         patch.object(task_manager, 'update_task_status') as mock_update:

        with pytest.raises(TaskNotCancelableError):
            await task_manager.cancel_task(task_id)

    # --- Assert ---
    mock_get.assert_awaited_once_with(task_id)
    mock_update.assert_not_awaited() # Update should not be called

@pytest.mark.asyncio
async def test_cancel_task_not_found(task_manager: SchedulingTaskManager):
    """Verify cancel_task raises TaskNotFoundError if get_task fails."""
    task_id = str(uuid.uuid4())

    # Mock get_task to return None
    with patch.object(task_manager, 'get_task', return_value=None) as mock_get, \
         patch.object(task_manager, 'update_task_status') as mock_update:

        with pytest.raises(TaskNotFoundError):
            await task_manager.cancel_task(task_id)

    # --- Assert ---
    mock_get.assert_awaited_once_with(task_id)
    mock_update.assert_not_awaited()

# --- Integration Tests (Refactored from Unit Tests) --- #

@pytest.mark.asyncio
async def test_create_task_integration(task_manager: SchedulingTaskManager):
    """Test creating a task and retrieving it using the real DB service."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    user_message = Message(role="user", parts=[Part(type="text", text="Create a test task")])
    params = TaskSendParams(id=task_id, sessionId=session_id, message=user_message)

    # --- Act ---
    # Create the task using the manager
    created_task_dto = await task_manager.create_task(params=params)

    # Retrieve the task using the manager to verify persistence
    retrieved_task_dto = await task_manager.get_task(task_id)

    # --- Assert ---
    assert retrieved_task_dto is not None
    assert retrieved_task_dto.id == task_id
    assert retrieved_task_dto.sessionId == session_id
    assert retrieved_task_dto.status.state == TaskState.WORKING # Initial state set by create_task
    assert retrieved_task_dto.params == params
    assert retrieved_task_dto.artifacts == []
    # History might be tricky depending on conversion logic, check basic structure
    assert isinstance(retrieved_task_dto.history, list)
    # Compare with the initially returned DTO as well
    assert retrieved_task_dto == created_task_dto

@pytest.mark.asyncio
async def test_get_task_integration_found(task_manager: SchedulingTaskManager):
    """Test retrieving a previously created task."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)

    # --- Arrange ---
    # Create a task first
    initial_task = await task_manager.create_task(params=params)
    assert initial_task is not None

    # --- Act ---
    # Retrieve the same task
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    assert retrieved_task is not None
    assert retrieved_task.id == task_id
    assert retrieved_task.status.state == TaskState.WORKING
    assert retrieved_task.params == params
    assert retrieved_task == initial_task # Should match the initially created DTO

@pytest.mark.asyncio
async def test_get_task_integration_not_found(task_manager: SchedulingTaskManager):
    """Test retrieving a non-existent task returns None."""
    task_id = str(uuid.uuid4()) # ID that hasn't been created

    # --- Act ---
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    assert retrieved_task is None

@pytest.mark.asyncio
async def test_update_task_status_integration(task_manager: SchedulingTaskManager):
    """Test updating the status of an existing task."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)

    # --- Arrange ---
    # Create the initial task
    await task_manager.create_task(params=params)

    new_status_obj = TaskStatus(
        state=TaskState.COMPLETED,
        message=Message(role="agent", parts=[Part(type="text", text="Task is done.")]),
    )

    # --- Act ---
    # Update the status
    updated_task = await task_manager.update_task_status(task_id, new_status_obj)

    # Retrieve again to confirm update persisted
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    assert updated_task is not None
    assert updated_task.id == task_id
    assert updated_task.status.state == TaskState.COMPLETED
    assert updated_task.status.message == new_status_obj.message

    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.COMPLETED
    assert retrieved_task.status.message == new_status_obj.message
    # Check history - should contain initial (WORKING) and new (COMPLETED)
    # Note: Exact history content depends on TaskManager internal logic
    assert len(retrieved_task.history) > 0 # Check history exists
    # Find the completed status in history
    completed_history = next((h for h in retrieved_task.history if h.state == TaskState.COMPLETED), None)
    assert completed_history is not None
    assert completed_history.message == new_status_obj.message

@pytest.mark.asyncio
async def test_update_task_status_integration_not_found(task_manager: SchedulingTaskManager):
    """Test updating a non-existent task raises TaskNotFoundError."""
    task_id = str(uuid.uuid4())
    new_status = TaskStatus(state=TaskState.COMPLETED)

    # --- Act & Assert ---
    with pytest.raises(TaskNotFoundError):
        await task_manager.update_task_status(task_id, new_status)

@pytest.mark.asyncio
async def test_add_task_artifact_integration(task_manager: SchedulingTaskManager):
    """Test adding an artifact to an existing task."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    artifact_to_add = Artifact(name="result.txt", parts=[Part(type="text", text="content")])

    # --- Arrange ---
    # Create the initial task
    await task_manager.create_task(params=params)

    # --- Act ---
    # Add the artifact
    task_with_artifact = await task_manager.add_task_artifact(task_id, artifact_to_add)

    # Retrieve again to confirm persistence
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    assert task_with_artifact is not None
    assert len(task_with_artifact.artifacts) == 1
    assert task_with_artifact.artifacts[0].name == artifact_to_add.name
    assert task_with_artifact.artifacts[0].parts == artifact_to_add.parts

    assert retrieved_task is not None
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0].name == artifact_to_add.name
    assert retrieved_task.artifacts[0].parts == artifact_to_add.parts
    assert retrieved_task == task_with_artifact

@pytest.mark.asyncio
async def test_add_task_artifact_integration_not_found(task_manager: SchedulingTaskManager):
    """Test adding an artifact to a non-existent task raises TaskNotFoundError."""
    task_id = str(uuid.uuid4())
    artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])

    # --- Act & Assert ---
    with pytest.raises(TaskNotFoundError):
        await task_manager.add_task_artifact(task_id, artifact)

# --- Tests for Higher-Level Methods --- #
# These tests now call the real TaskManager methods and verify the final DB state

@pytest.mark.asyncio
async def test_complete_task_state(task_manager: SchedulingTaskManager):
    """Verify complete_task updates status and adds artifact via real methods."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])
    final_artifact = Artifact(name="final_result", parts=[Part(type="data", data={"value": 123})])

    # --- Arrange ---
    await task_manager.create_task(params=params)

    # --- Act ---
    # Call the real complete_task method
    final_returned_task = await task_manager.complete_task(task_id, completion_message, final_artifact)

    # --- Assert ---
    # Verify the final state by retrieving the task
    retrieved_task = await task_manager.get_task(task_id)
    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.COMPLETED
    assert retrieved_task.status.message == completion_message
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0] == final_artifact # Compare Artifact objects
    # Check returned task matches retrieved task (excluding timestamps)
    assert final_returned_task.model_dump(exclude={'createdAt', 'updatedAt'}) == \
           retrieved_task.model_dump(exclude={'createdAt', 'updatedAt'})

@pytest.mark.asyncio
async def test_complete_task_state_no_artifact(task_manager: SchedulingTaskManager):
    """Verify complete_task calls update_task_status only (integration)."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    completion_message = Message(role="agent", parts=[Part(type="text", text="Task finished.")])

    # --- Arrange ---
    await task_manager.create_task(params=params)

    # --- Act ---
    final_returned_task = await task_manager.complete_task(task_id, completion_message, None)

    # --- Assert ---
    retrieved_task = await task_manager.get_task(task_id)
    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.COMPLETED
    assert retrieved_task.status.message == completion_message
    assert retrieved_task.artifacts == [] # No artifact added
    assert final_returned_task.status.state == TaskState.COMPLETED
    assert final_returned_task.artifacts == []

@pytest.mark.asyncio
async def test_fail_task_state(task_manager: SchedulingTaskManager):
    """Verify fail_task calls update_task_status (integration)."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    error_message = Message(role="agent", parts=[Part(type="text", text="Something went wrong.")])

    # --- Arrange ---
    await task_manager.create_task(params=params)

    # --- Act ---
    final_returned_task = await task_manager.fail_task(task_id, error_message)

    # --- Assert ---
    retrieved_task = await task_manager.get_task(task_id)
    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.FAILED
    assert retrieved_task.status.message == error_message
    assert final_returned_task.status.state == TaskState.FAILED

@pytest.mark.asyncio
async def test_require_input_task_state(task_manager: SchedulingTaskManager):
    """Verify require_input_task updates status and adds artifact (integration)."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    prompt_message = Message(role="agent", parts=[Part(type="text", text="Which option?")])
    input_artifact = Artifact(name="options", parts=[Part(type="data", data=["A", "B"])])

    # --- Arrange ---
    await task_manager.create_task(params=params)

    # --- Act ---
    final_returned_task = await task_manager.require_input_task(task_id, prompt_message, input_artifact)

    # --- Assert ---
    retrieved_task = await task_manager.get_task(task_id)
    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.INPUT_REQUIRED
    assert retrieved_task.status.message == prompt_message
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0] == input_artifact
    assert final_returned_task.status.state == TaskState.INPUT_REQUIRED
    assert len(final_returned_task.artifacts) == 1

@pytest.mark.asyncio
async def test_require_input_task_state_no_artifact(task_manager: SchedulingTaskManager):
    """Verify require_input_task calls update_task_status only (integration)."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    prompt_message = Message(role="agent", parts=[Part(type="text", text="Please confirm.")])

    # --- Arrange ---
    await task_manager.create_task(params=params)

    # --- Act ---
    final_returned_task = await task_manager.require_input_task(task_id, prompt_message, None)

    # --- Assert ---
    retrieved_task = await task_manager.get_task(task_id)
    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.INPUT_REQUIRED
    assert retrieved_task.status.message == prompt_message
    assert retrieved_task.artifacts == []
    assert final_returned_task.status.state == TaskState.INPUT_REQUIRED
    assert final_returned_task.artifacts == []

# --- Test Cancel Task (Integration) ---
@pytest.mark.asyncio
async def test_cancel_task_success(task_manager: SchedulingTaskManager):
    """Verify cancel_task succeeds for a cancelable state (integration)."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)

    # --- Arrange ---
    initial_task = await task_manager.create_task(params=params)
    assert initial_task.status.state == TaskState.WORKING # Should be cancelable

    # --- Act ---
    canceled_task = await task_manager.cancel_task(task_id)

    # --- Assert ---
    retrieved_task = await task_manager.get_task(task_id)
    assert retrieved_task is not None
    assert retrieved_task.status.state == TaskState.CANCELED
    assert canceled_task.status.state == TaskState.CANCELED
    # Check that a cancellation message was added
    assert retrieved_task.status.message is not None
    assert "canceled" in retrieved_task.status.message.parts[0].text.lower()

@pytest.mark.asyncio
@pytest.mark.parametrize("non_cancelable_state", [
    TaskState.COMPLETED,
    TaskState.FAILED,
    TaskState.CANCELED,
])
async def test_cancel_task_not_cancelable(task_manager: SchedulingTaskManager, non_cancelable_state: TaskState):
    """Verify cancel_task raises TaskNotCancelableError for final states (integration)."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)

    # --- Arrange ---
    # Create task and immediately update its status to a non-cancelable state
    await task_manager.create_task(params=params)
    # Use a simple message for the update
    await task_manager.update_task_status(task_id, TaskStatus(state=non_cancelable_state, message=Message(role="agent", parts=[Part(type="text", text="State set")])))

    # --- Act & Assert ---
    with pytest.raises(TaskNotCancelableError):
        await task_manager.cancel_task(task_id)

@pytest.mark.asyncio
async def test_cancel_task_not_found(task_manager: SchedulingTaskManager):
    """Verify cancel_task raises TaskNotFoundError if task doesn't exist (integration)."""
    task_id = str(uuid.uuid4())

    # --- Act & Assert ---
    with pytest.raises(TaskNotFoundError):
        await task_manager.cancel_task(task_id)
