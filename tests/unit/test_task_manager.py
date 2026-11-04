from datetime import UTC, datetime, timedelta
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from schedule_a2a.dto import (
    TaskSendParams,
    Artifact,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from schedule_a2a.exceptions import TaskNotFoundError as A2ATaskNotFoundError, TaskNotCancelableError as A2ATaskNotCancelableError
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.core.exceptions import TaskNotFoundError as CoreTaskNotFoundError
from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import A2ATask
from schedule_core.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from schedule_core.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from schedule_core.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy
from schedule_core.event.exporters.ical_exporter import ICalExporter
from schedule_core.event.exporters.orgmode_exporter import OrgModeExporter
from schedule_nlp.nlp_processor import NLPProcessor
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.db.service import DatabaseService

logger = get_logger(__name__)

def create_mock_a2a_task(
    task_id: str,
    session_id: str | None = None,
    state: TaskState = TaskState.WORKING,
    artifacts: list[Artifact] | None = None,
    history: list[Message] | None = None,
    status_message: Message | None = None,
    status_timestamp: datetime | None = None,
    metadata: dict | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> A2ATask:
    """Creates an A2ATask DB model instance with JSON fields populated."""
    if session_id is None: session_id = str(uuid.uuid4())
    if artifacts is None: artifacts = []
    if history is None: history = []
    if created_at is None: created_at = datetime.now(UTC)
    if updated_at is None: updated_at = datetime.now(UTC)
    effective_status_timestamp = status_timestamp if status_timestamp else updated_at

    return A2ATask(
        id=task_id,
        session_id=session_id,
        state=state.value,
        status_message_json=status_message.model_dump_json() if status_message else None,
        status_timestamp=effective_status_timestamp.replace(tzinfo=None),
        artifacts_json=json.dumps([a.model_dump() for a in artifacts]),
        history_json=json.dumps([h.model_dump() for h in history]),
        metadata_json=json.dumps(metadata) if metadata else None,
        created_at=created_at.replace(tzinfo=None),
        updated_at=updated_at.replace(tzinfo=None),
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
    db_service: DatabaseService,
    initialize_db: None,
) -> SchedulingTaskManager:
    """Provides a SchedulingTaskManager instance using the real db_service."""
    return SchedulingTaskManager(db_service=db_service)

@pytest.mark.asyncio
async def test_get_task_integration_found(
    task_manager: SchedulingTaskManager,
):
    """Verify get_task successfully retrieves a created task."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    user_message = Message(role="user", parts=[Part(type="text", text="Test Get")])
    params = TaskSendParams(id=task_id, sessionId=session_id, message=user_message)

    # --- Arrange ---
    created_task_dto = await task_manager.create_task(params=params)
    assert created_task_dto is not None, "Task creation failed in Arrange step"
    # We need the timestamp generated during creation for comparison
    initial_status_timestamp = created_task_dto.status.timestamp

    # --- Act ---
    retrieved_task_dto = await task_manager.get_task(task_id)

    # --- Assert ---
    assert retrieved_task_dto is not None
    assert retrieved_task_dto.id == task_id
    assert retrieved_task_dto.sessionId == session_id
    assert retrieved_task_dto.status.state == TaskState.WORKING # Initial state
    assert retrieved_task_dto.status.message is None # Initial status has no message
    # Compare timestamps (should be timezone-aware UTC)
    assert retrieved_task_dto.status.timestamp is not None
    assert retrieved_task_dto.status.timestamp.tzinfo == UTC
    assert retrieved_task_dto.status.timestamp == initial_status_timestamp
    assert retrieved_task_dto.artifacts == [] # Initial artifacts are empty list stored as JSON
    assert retrieved_task_dto.history is not None
    assert len(retrieved_task_dto.history) == 1
    assert retrieved_task_dto.history[0] == user_message # History starts with input message
    assert retrieved_task_dto.metadata is None # Initial metadata is None

@pytest.mark.asyncio
async def test_get_task_integration_not_found(
    task_manager: SchedulingTaskManager,
):
    """Verify get_task raises A2ATaskNotFoundError for a non-existent task ID."""
    task_id = str(uuid.uuid4()) # Use an ID that definitely doesn't exist

    # --- Act & Assert ---
    with pytest.raises(A2ATaskNotFoundError):
        await task_manager.get_task(task_id)

@pytest.mark.asyncio
async def test_update_task_status_integration(
    task_manager: SchedulingTaskManager,
):
    """Verify update_task_status successfully updates task state and history in DB."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    user_message = Message(role="user", parts=[Part(type="text", text="Test Update")])
    params = TaskSendParams(id=task_id, sessionId=session_id, message=user_message)

    # --- Arrange ---
    await task_manager.create_task(params=params)

    agent_message = Message(role="agent", parts=[Part(type="text", text="Task is done.")])
    new_status_obj = TaskStatus(
        state=TaskState.COMPLETED,
        message=agent_message,
        # timestamp will be set by update_task_status if None
    )

    # --- Act ---
    updated_task_dto = await task_manager.update_task_status(task_id, new_status_obj)

    # Retrieve the task again to verify persistence
    retrieved_task_dto = await task_manager.get_task(task_id)

    # --- Assert ---
    # 1. Check the DTO returned by update_task_status
    assert updated_task_dto is not None
    assert updated_task_dto.id == task_id
    assert updated_task_dto.status.state == TaskState.COMPLETED
    assert updated_task_dto.status.message == agent_message
    assert updated_task_dto.status.timestamp is not None # Timestamp should be set

    # 2. Check the DTO retrieved from the DB after the update
    assert retrieved_task_dto is not None
    assert retrieved_task_dto.status.state == TaskState.COMPLETED
    assert retrieved_task_dto.status.message == agent_message
    assert retrieved_task_dto.status.timestamp == updated_task_dto.status.timestamp

    # 3. Check history update
    assert retrieved_task_dto.history is not None
    assert len(retrieved_task_dto.history) == 2 # Initial user msg + agent status msg
    assert retrieved_task_dto.history[0] == user_message
    assert retrieved_task_dto.history[1] == agent_message

@pytest.mark.asyncio
async def test_add_task_artifact_integration(
    task_manager: SchedulingTaskManager,
):
    """Verify add_task_artifact successfully adds an artifact to a task."""
    task_id = str(uuid.uuid4())
    params = create_minimal_task_params(task_id)
    artifact_to_add = Artifact(name="result.txt", parts=[Part(type="text", text="content")])

    # --- Arrange ---
    await task_manager.create_task(params=params)

    # --- Act ---
    task_with_artifact = await task_manager.add_task_artifact(task_id, artifact_to_add)

    # Retrieve the task again to verify persistence
    retrieved_task = await task_manager.get_task(task_id)

    # --- Assert ---
    # 1. Check DTO returned by add_task_artifact
    assert task_with_artifact is not None
    assert task_with_artifact.artifacts is not None
    assert len(task_with_artifact.artifacts) == 1
    assert task_with_artifact.artifacts[0] == artifact_to_add

    # 2. Check DTO retrieved from DB
    assert retrieved_task is not None
    assert retrieved_task.artifacts is not None
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0] == artifact_to_add

@pytest.mark.asyncio
async def test_add_task_artifact_integration_not_found(task_manager: SchedulingTaskManager):
    """Test adding an artifact to a non-existent task raises TaskNotFoundError."""
    task_id = str(uuid.uuid4())
    artifact = Artifact(name="result", parts=[Part(type="text", text="some data")])

    # --- Act & Assert ---
    with pytest.raises(A2ATaskNotFoundError):
        await task_manager.add_task_artifact(task_id, artifact)
