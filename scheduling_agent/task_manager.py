import uuid
import asyncio
from datetime import datetime, timezone, timedelta, time
from enum import Enum
from typing import List, Dict, Optional, Any, AsyncIterable, Union

from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field

from scheduling_agent.logging_config import get_logger

# Import necessary services and components
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exporters.base import CalendarExporter # Base protocol
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.core.exceptions import ConflictError
# Import DB model from .models and DTO schema from .schemas
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate
# Import DateUtils for placeholder parsing
from scheduling_agent.core.date_utils import DateUtils
# Import LLMSchedulingParseResult for type hinting
from scheduling_agent.core.schemas import LLMSchedulingParseResult

# Import the canonical A2A types
from .common.types import (
    TaskState,
    Part, # Might need FileContent too if used directly
    FileContent, # Import just in case
    Message,
    Artifact,
    TaskStatus,
    Task,
    TaskSendParams, # For on_send_task
    TaskGetParams, # For future on_get_task
    CancelTaskParams, # For future on_cancel_task
    JSONRPCResponse,
    # Import error types if TaskManager needs to create/return them
    TaskNotFoundError,
    TaskNotCancelableError,
    InternalError,
    InvalidParamsError
)

logger = get_logger(__name__)

# --- Task Manager Implementation ---

class SchedulingTaskManager:
    """Manages the lifecycle and storage of A2A tasks."""

    def __init__(
        self,
        session_factory: sessionmaker, # Now required due to DatabaseService change
        nlp_processor: NLPProcessor,
        db_service: DatabaseService,
        conflict_resolver: ConflictResolver,
        exporters: Optional[Dict[str, CalendarExporter]] = None
    ):
        """Initialize the task manager with dependencies and in-memory storage."""
        self.nlp_processor = nlp_processor
        self.db_service = db_service # Service now needs factory injected where TM is created
        self.conflict_resolver = conflict_resolver
        self.session_factory = session_factory # Store factory if needed directly, or ensure db_service has it
        # Add a basic DateUtils instance for placeholder parsing
        self.date_utils = DateUtils()

        # Default exporters if none provided
        if exporters is None:
            # TODO: DateUtils might need to be initialized/passed differently now
            date_utils = self.db_service.date_utils # Assuming db_service still has date_utils
            self.exporters = {
                "ics": ICalExporter(date_utils=date_utils),
                "org": OrgModeExporter(date_utils=date_utils)
            }
        else:
            self.exporters = exporters

        self._tasks: Dict[str, Task] = {}
        self._task_locks: Dict[str, asyncio.Lock] = {}
        logger.info("SchedulingTaskManager initialized with in-memory storage.")

    async def _get_task_lock(self, task_id: str) -> asyncio.Lock:
        """Gets or creates an asyncio lock for a given task ID."""
        if task_id not in self._task_locks:
            self._task_locks[task_id] = asyncio.Lock()
        return self._task_locks[task_id]

    async def store_task(self, task: Task):
        """Stores or updates a task in memory. Assumes lock is already held."""
        task.updatedAt = datetime.now(timezone.utc)
        self._tasks[task.id] = task
        logger.debug("Stored/Updated task", task_id=task.id, state=task.status.state)

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieves a task by ID."""
        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            logger.debug("Retrieved task", task_id=task_id, found=(task is not None))
            return task.model_copy(deep=True) if task else None # Return a copy

    # --- NEW State Management Methods (Phase 1, Task 2) ---

    async def create_task(self, params: TaskSendParams) -> Task:
        """
        Creates a new task, stores it in memory, and returns it.
        Initial state is set to WORKING.
        """
        task_id = params.id
        session_id = params.sessionId
        logger.info("Creating new task", task_id=task_id, session_id=session_id)

        async with await self._get_task_lock(task_id):
            if task_id in self._tasks:
                # Handle potential ID collision, though UUIDs make this unlikely
                logger.warning("Task ID collision detected", task_id=task_id)
                # Depending on requirements, might raise error or overwrite
                # For now, let's stick to the simple path for now and assume no collision.
                # raise ValueError(f"Task with ID {task_id} already exists.")
                pass # Or maybe just log and proceed to overwrite as store_task does?

            task = Task(
                id=task_id,
                sessionId=session_id,
                params=params, # Store the input parameters
                status=TaskStatus(state=TaskState.WORKING), # Initial state
                artifacts=[],
                history=[],
                createdAt=datetime.now(timezone.utc),
                updatedAt=datetime.now(timezone.utc)
            )
            # Use store_task to handle actual storage and locking
            await self.store_task(task)
            # Return a copy to prevent external modification
            return task.model_copy(deep=True)

    async def update_task_status(self, task_id: str, status: TaskStatus) -> Task:
        """
        Updates the status of a task, adds the old status to history, and returns the updated task.
        Raises TaskNotFoundError if the task doesn't exist.
        """
        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            if not task:
                logger.error("Task not found for status update", task_id=task_id)
                raise TaskNotFoundError(f"Task with ID {task_id} not found.")

            # Add previous status to history
            task.history.append(task.status.model_copy(deep=True))
            # Update status
            task.status = status
            # Update timestamp handled by store_task
            await self.store_task(task)
            logger.debug("Updated task status", task_id=task_id, new_state=status.state)
            return task.model_copy(deep=True)

    async def add_task_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """
        Adds an artifact to a task and returns the updated task.
        Raises TaskNotFoundError if the task doesn't exist.
        """
        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            if not task:
                logger.error("Task not found for adding artifact", task_id=task_id)
                raise TaskNotFoundError(f"Task with ID {task_id} not found.")

            task.artifacts.append(artifact)
            # Update timestamp handled by store_task
            await self.store_task(task)
            logger.debug("Added artifact to task", task_id=task_id, artifact_name=artifact.name)
            return task.model_copy(deep=True)

    async def complete_task(self, task_id: str, message: Message, artifact: Optional[Artifact] = None) -> Task:
        """
        Sets task state to COMPLETED, adds message, optionally adds artifact.
        """
        logger.info("Completing task", task_id=task_id)
        # Update status first
        updated_task = await self.update_task_status(task_id, TaskStatus(state=TaskState.COMPLETED, message=message))

        # Add artifact if provided
        if artifact:
            updated_task = await self.add_task_artifact(task_id, artifact)

        return updated_task # Already a copy from add_task_artifact or update_task_status

    async def fail_task(self, task_id: str, message: Message) -> Task:
        """Sets task state to FAILED, adds message."""
        logger.error("Failing task", task_id=task_id, reason=message.parts[0].text if message.parts else "Unknown")
        # Update status
        updated_task = await self.update_task_status(task_id, TaskStatus(state=TaskState.FAILED, message=message))
        return updated_task # Already a copy

    async def require_input_task(self, task_id: str, message: Message, artifact: Optional[Artifact] = None) -> Task:
        """
        Sets task state to INPUT_REQUIRED, adds message, optionally adds artifact.
        """
        logger.info("Setting task to INPUT_REQUIRED", task_id=task_id)
        # Update status first
        updated_task = await self.update_task_status(task_id, TaskStatus(state=TaskState.INPUT_REQUIRED, message=message))

        # Add artifact if provided (e.g., options for input)
        if artifact:
            updated_task = await self.add_task_artifact(task_id, artifact)

        return updated_task # Already a copy

    # --- Placeholder Helper Methods --- #
    # --- These are removed as parsing logic belongs elsewhere --- #

    # --- OLD JSON-RPC handlers REMOVED --- #
    # --- Business logic moved to api/routes/rpc.py --- #

    # --- Add other state management helpers as needed (e.g., update_artifact) --- #

    async def cancel_task(self, task_id: str) -> Task:
        """
        Cancels a task if it's in a cancelable state.

        Args:
            task_id: The ID of the task to cancel.

        Returns:
            The updated Task object with CANCELED state.

        Raises:
            TaskNotFoundError: If the task doesn't exist.
            TaskNotCancelableError: If the task is in a final state.
        """
        logger.info("Attempting to cancel task", task_id=task_id)
        cancelable_states = {TaskState.SUBMITTED, TaskState.WORKING, TaskState.INPUT_REQUIRED}

        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            if not task:
                logger.error("Task not found for cancellation", task_id=task_id)
                raise TaskNotFoundError(f"Task with ID '{task_id}' not found for cancellation")

            if task.status.state not in cancelable_states:
                logger.warning(
                    "Attempted to cancel task in non-cancelable state",
                    task_id=task_id,
                    current_state=task.status.state
                )
                raise TaskNotCancelableError(
                    f"Task {task_id} is in state '{task.status.state}' and cannot be canceled."
                )

            # Update status to CANCELED
            # Use the existing update_task_status which handles history and storage
            updated_task = await self.update_task_status(
                task_id,
                TaskStatus(state=TaskState.CANCELED, message=Message(role="agent", parts=[Part(type="text", text="Task canceled by request.")]))
            )
            logger.info("Task canceled successfully", task_id=task_id)
            return updated_task # Return the updated task (already a copy)
