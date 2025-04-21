import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock

# Imports required for A2A Task DB tests
from scheduling_agent.db.models import A2ATask
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.a2a import types as a2a_types
from scheduling_agent.core.exceptions import TaskNotFoundError
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils

# --- Fixture for DB Service (Copied from test_service.py, adjust if needed) ---
# Assumes test_session_factory is available from conftest.py
@pytest_asyncio.fixture
async def db_service(test_session_factory) -> DatabaseService:
    """Fixture to provide a DatabaseService instance with minimal mocks for A2A tests."""
    # Only include mocks if DatabaseService init strictly requires them
    # For A2A tests, conflict resolver might not be needed unless save methods use it.
    mock_conflict_resolver = AsyncMock(spec=ConflictResolver)
    mock_conflict_resolver.detect_conflicts.return_value = [] 

    service = DatabaseService(
        session_factory=test_session_factory, 
        date_utils=DateUtils(), 
        conflict_resolver=mock_conflict_resolver, # Include if needed
    )
    return service

# --- A2A Task Persistence Tests ---

@pytest.mark.asyncio
async def test_save_a2a_task(db_service: DatabaseService, test_session: AsyncSession):
    """Test saving a new A2A Task to the database."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    user_message = a2a_types.Message(role="user", parts=[a2a_types.Part(type="text", text="Schedule meeting")])
    params = a2a_types.TaskSendParams(id=task_id, message=user_message, intent="create_event", user_prompt="Schedule meeting") # Added intent/prompt for completeness
    status = a2a_types.TaskStatus(state=a2a_types.TaskState.SUBMITTED, message=a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task submitted")]))

    task_to_save = a2a_types.Task(
        id=task_id,
        params=params,
        status=status,
        artifacts=[], # Initially empty
        history=[]    # Initially empty
    )

    # --- Act ---
    await db_service.save_a2a_task(task_to_save)

    # --- Assert ---
    retrieved_record = await test_session.get(A2ATask, task_id)

    assert retrieved_record is not None
    assert retrieved_record.id == task_id
    assert retrieved_record.state == task_to_save.status.state.value
    assert retrieved_record.session_id is None 

    assert retrieved_record.params_json == params.model_dump_json(exclude_none=True)
    assert retrieved_record.status_message_json == status.message.model_dump_json(exclude_none=True)
    assert retrieved_record.artifacts_json is None 
    assert retrieved_record.history_json is None 

    assert retrieved_record.created_at is not None
    assert retrieved_record.updated_at is not None
    assert isinstance(retrieved_record.created_at, datetime)
    assert isinstance(retrieved_record.updated_at, datetime)

# TODO: Add test for updating an existing task via save_a2a_task (upsert)

@pytest.mark.asyncio
async def test_get_a2a_task_by_id_found(db_service: DatabaseService, test_session: AsyncSession):
    """Test retrieving an existing A2A Task by ID, verifying deserialization."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    user_message_for_params = a2a_types.Message(role="user", parts=[a2a_types.Part(type="text", text="Change meeting time")])
    params = a2a_types.TaskSendParams(id=task_id, message=user_message_for_params, intent="update_event", user_prompt="Change meeting time") # Added intent/prompt
    message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task is working")])
    status = a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING, message=message)
    artifact = a2a_types.Artifact(name="draft_email", parts=[a2a_types.Part(type="text", text="Email body...")])
    initial_status_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task submitted")])
    initial_status = a2a_types.TaskStatus(state=a2a_types.TaskState.SUBMITTED, message=initial_status_message)

    task_to_save = a2a_types.Task(
        id=task_id,
        sessionId="session_123",
        params=params,
        status=status,
        artifacts=[artifact], 
        history=[initial_status] 
    )

    await db_service.save_a2a_task(task_to_save)

    # --- Act ---
    retrieved_task = await db_service.get_a2a_task_by_id(task_id)

    # --- Assert ---
    assert retrieved_task is not None
    assert isinstance(retrieved_task, a2a_types.Task)

    assert retrieved_task.id == task_id
    assert retrieved_task.sessionId == "session_123"
    assert retrieved_task.status.state == a2a_types.TaskState.WORKING
    assert retrieved_task.status.message.parts[0].text == "Task is working"
    assert retrieved_task.params.id == task_id
    assert retrieved_task.params.message.parts[0].text == "Change meeting time"
    assert len(retrieved_task.artifacts) == 1
    assert retrieved_task.artifacts[0].name == "draft_email"
    assert len(retrieved_task.history) == 1
    assert retrieved_task.history[0].state == a2a_types.TaskState.SUBMITTED
    # Compare the deserialized initial status from history
    assert retrieved_task.history[0] == initial_status 

@pytest.mark.asyncio
async def test_get_a2a_task_by_id_not_found(db_service: DatabaseService):
    """Test retrieving a non-existent A2A Task by ID returns None."""
    # --- Arrange ---
    non_existent_task_id = str(uuid.uuid4())

    # --- Act ---
    retrieved_task = await db_service.get_a2a_task_by_id(non_existent_task_id)

    # --- Assert ---
    assert retrieved_task is None

@pytest.mark.asyncio
async def test_update_a2a_task_status(db_service: DatabaseService, test_session: AsyncSession):
    """Test updating the status and history of an A2A Task."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    initial_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task submitted")])
    initial_status = a2a_types.TaskStatus(state=a2a_types.TaskState.SUBMITTED, message=initial_message)
    initial_user_message = a2a_types.Message(role="user", parts=[a2a_types.Part(type="text", text="Find events")])
    initial_params = a2a_types.TaskSendParams(id=task_id, message=initial_user_message, intent="find_event") # Added intent
    
    task_to_save = a2a_types.Task(
        id=task_id,
        params=initial_params,
        status=initial_status,
        artifacts=[],
        history=[] 
    )

    await db_service.save_a2a_task(task_to_save)

    new_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Task is now working")])
    new_status = a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING, message=new_message)

    # --- Act ---
    await db_service.update_a2a_task_status(task_id, new_status)

    # --- Assert ---
    updated_record = await test_session.get(A2ATask, task_id)

    assert updated_record is not None
    assert updated_record.id == task_id
    
    assert updated_record.state == new_status.state.value
    assert updated_record.status_message_json == new_message.model_dump_json(exclude_none=True)

    assert updated_record.history_json is not None
    history_list = a2a_types.HistoryList.model_validate_json(updated_record.history_json).root
    assert len(history_list) == 1
    assert history_list[0] == initial_status 

    assert updated_record.updated_at > updated_record.created_at

@pytest.mark.asyncio
async def test_update_a2a_task_status_not_found(db_service: DatabaseService):
    """Test updating status for a non-existent task raises TaskNotFoundError."""
    task_id = str(uuid.uuid4())
    new_status = a2a_types.TaskStatus(state=a2a_types.TaskState.COMPLETED)

    with pytest.raises(TaskNotFoundError) as exc_info:
        await db_service.update_a2a_task_status(task_id, new_status)
    
    assert task_id in str(exc_info.value) 