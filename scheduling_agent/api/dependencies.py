from functools import lru_cache
from unittest.mock import MagicMock, AsyncMock
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.core.llm_provider import LLMProvider
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.common.types import Task, TaskStatus, TaskState, TaskSendParams, Message, Artifact
import uuid
from typing import Optional

# TODO: Implement proper singleton/factory logic, potentially using app state or lifespan context.

# Placeholder instances - these should be managed properly (e.g., singletons)
# Ensure DatabaseService is initialized correctly, possibly using settings
@lru_cache()
def get_db_service() -> DatabaseService:
    # TODO: Get DB URL from settings/environment
    return DatabaseService("schedule_agent.db")

@lru_cache()
def get_date_utils() -> DateUtils:
    return DateUtils()

@lru_cache()
def get_llm_provider() -> LLMProvider:
    # TODO: Configure from settings/environment
    return LLMProvider()

@lru_cache()
def get_nlp_processor() -> NLPProcessor:
    return NLPProcessor(date_utils=get_date_utils(), llm_provider=get_llm_provider())

@lru_cache()
def get_conflict_resolver() -> ConflictResolver:
    return ConflictResolver(db_service=get_db_service(), date_utils=get_date_utils())

# Task Manager (Primary dependency for RPC)
@lru_cache()
def get_task_manager() -> SchedulingTaskManager:
    """Dependency function to get the TaskManager instance.

    TEMPORARY: Returns a mock for testing routing logic.
    TODO: Correctly instantiate SchedulingTaskManager with its dependencies.
    """
    print("WARNING: Returning mock Task Manager for dependency testing!")
    # Use AsyncMock and configure async methods to return awaitable Task objects
    mock = AsyncMock(spec=SchedulingTaskManager)

    # Define functions for mock methods to use the correct task_id
    async def mock_create(params: TaskSendParams) -> Task:
        return Task(id=params.id, status=TaskStatus(state=TaskState.WORKING))

    async def mock_complete(task_id: str, message: Message, artifact: Optional[Artifact] = None) -> Task:
        return Task(id=task_id, status=TaskStatus(state=TaskState.COMPLETED, message=message), artifacts=[artifact] if artifact else [])

    async def mock_fail(task_id: str, message: Message) -> Task:
        return Task(id=task_id, status=TaskStatus(state=TaskState.FAILED, message=message))

    async def mock_require_input(task_id: str, message: Message, artifact: Optional[Artifact] = None) -> Task:
        return Task(id=task_id, status=TaskStatus(state=TaskState.INPUT_REQUIRED, message=message), artifacts=[artifact] if artifact else [])

    async def mock_get(task_id: str) -> Optional[Task]:
        # Return a default task or None for get, depending on test needs
        # For now, return a basic task for simplicity
        return Task(id=task_id, status=TaskStatus(state=TaskState.WORKING))

    # Assign the mock methods
    mock.create_task = AsyncMock(side_effect=mock_create)
    mock.complete_task = AsyncMock(side_effect=mock_complete)
    mock.fail_task = AsyncMock(side_effect=mock_fail)
    mock.require_input_task = AsyncMock(side_effect=mock_require_input)
    mock.get_task = AsyncMock(side_effect=mock_get)

    return mock
    # raise NotImplementedError("TaskManager singleton/factory not yet implemented.") 