import asyncio
from datetime import UTC, datetime

from sqlalchemy.orm import sessionmaker

from scheduling_agent.core.conflict_resolver import ConflictResolver

# Import DateUtils for placeholder parsing
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exporters.base import CalendarExporter  # Base protocol
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter

# Import necessary services and components
from scheduling_agent.core.nlp_processor import NLPProcessor

# Import LLMSchedulingParseResult for type hinting
# Import DB model from .models and DTO schema from .schemas
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger

# Import the canonical A2A types
from .types import (
    Artifact,
    Message,
    Part,  # Might need FileContent too if used directly
    Task,
    TaskNotCancelableError,
    # Import error types if TaskManager needs to create/return them
    TaskNotFoundError,
    TaskSendParams,  # For on_send_task
    TaskState,
    TaskStatus,
)
from scheduling_agent.core.exceptions import TaskNotFoundError # Import directly

logger = get_logger(__name__)

# --- Task Manager Implementation ---


class SchedulingTaskManager:
    """Manages the lifecycle of A2A tasks by orchestrating DB operations."""

    def __init__(
        self,
        # session_factory: sessionmaker, # No longer needed directly
        nlp_processor: NLPProcessor,
        db_service: DatabaseService,
        conflict_resolver: ConflictResolver,
        exporters: dict[str, CalendarExporter] | None = None,
        session_factory: sessionmaker | None = None, # Keep for backward compat if needed, but mark optional
    ):
        """Initialize the task manager with dependencies."""
        self.nlp_processor = nlp_processor
        self.db_service = db_service
        self.conflict_resolver = conflict_resolver
        # self.session_factory = session_factory # Removed direct usage
        self.date_utils = DateUtils() # Keep for exporter initialization for now

        # Default exporters if none provided
        if exporters is None:
            # TODO: Revisit how date_utils is obtained if db_service no longer holds it
            date_utils = self.date_utils 
            self.exporters = {
                "ics": ICalExporter(date_utils=date_utils),
                "org": OrgModeExporter(date_utils=date_utils),
            }
        else:
            self.exporters = exporters

        # Remove in-memory storage
        # self._tasks: dict[str, Task] = {}
        # self._task_locks: dict[str, asyncio.Lock] = {}
        logger.info("SchedulingTaskManager initialized (Persistence via DatabaseService).")

    # Remove locking mechanism, DB handles transaction atomicity
    # async def _get_task_lock(self, task_id: str) -> asyncio.Lock:
    #     ...

    # Remove direct storage method
    # async def store_task(self, task: Task):
    #    ...

    async def get_task(self, task_id: str) -> Task | None:
        """Retrieves a task by ID using the DatabaseService."""
        logger.debug("Retrieving task via DB service", task_id=task_id)
        # Directly delegate to DB service
        task = await self.db_service.get_a2a_task_by_id(task_id)
        logger.debug("Retrieved task from DB", task_id=task_id, found=(task is not None))
        # DB service returns the Task object or None
        return task

    # --- NEW State Management Methods (Refactored for DB Persistence) ---

    async def create_task(self, params: TaskSendParams) -> Task:
        """
        Creates a new task object, saves it via DatabaseService, and returns it.
        Initial state is set to WORKING.
        """
        task_id = params.id
        session_id = params.sessionId
        logger.info("Creating new task state object", task_id=task_id, session_id=session_id)

        # Construct the initial Task object
        task = Task(
            id=task_id,
            sessionId=session_id,
            params=params,  # Store the input parameters
            status=TaskStatus(state=TaskState.WORKING),  # Initial state
            artifacts=[],
            history=[],
            createdAt=datetime.now(UTC).replace(tzinfo=None), # Naive UTC
            updatedAt=datetime.now(UTC).replace(tzinfo=None), # Naive UTC
        )
        
        # Save task state using DatabaseService
        try:
            await self.db_service.save_a2a_task(task)
            logger.info("Successfully saved initial task state to DB", task_id=task_id)
            # Return the created task object 
            # Note: save_a2a_task returns None, so we return the object we constructed.
            return task 
        except Exception as e:
            logger.exception("Failed to save initial task state to DB", task_id=task_id, error=str(e))
            # Decide error handling - re-raise? Return None? Raise specific internal error?
            raise # Re-raise for now

    async def update_task_status(self, task_id: str, status: TaskStatus) -> Task:
        """
        Updates the task status via DatabaseService and returns the updated task.
        Raises TaskNotFoundError if the task doesn't exist in DB.
        """
        logger.debug("Updating task status via DB service", task_id=task_id, new_state=status.state)
        try:
            # Call DB service to update status and history
            await self.db_service.update_a2a_task_status(task_id, status)
            # Fetch the updated task state from DB to return
            updated_task = await self.db_service.get_a2a_task_by_id(task_id)
            if updated_task is None:
                 # This case should ideally not happen if update succeeded, but handle defensively
                 logger.error("Task not found after status update, potential DB inconsistency", task_id=task_id)
                 raise TaskNotFoundError(f"Task {task_id} not found after status update.")
            logger.debug("Successfully updated task status in DB", task_id=task_id)
            return updated_task
        except TaskNotFoundError: # Propagate TaskNotFoundError from DB service
             logger.warning("Task not found in DB for status update", task_id=task_id)
             raise
        except Exception as e:
             logger.exception("Failed to update task status in DB", task_id=task_id, error=str(e))
             raise # Re-raise other exceptions

    async def add_task_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """
        Adds an artifact to a task by fetching, modifying, and saving via DatabaseService.
        Raises TaskNotFoundError if the task doesn't exist.
        """
        logger.debug("Adding artifact to task via DB service", task_id=task_id, artifact_name=artifact.name)
        try:
            # 1. Fetch the current task state
            task = await self.get_task(task_id) # Use the refactored get_task
            if not task:
                # get_task already logs and returns None, but TaskNotFoundError is expected here
                raise TaskNotFoundError(task_id)

            # 2. Modify the task object
            # Ensure artifacts list exists (should be handled by Task model default factory)
            if task.artifacts is None:
                 task.artifacts = [] # Initialize if somehow None
            task.artifacts.append(artifact)
            task.updatedAt = datetime.now(UTC).replace(tzinfo=None) # Update timestamp

            # 3. Save the updated task state back to DB
            await self.db_service.save_a2a_task(task) # save handles upsert
            logger.debug("Successfully saved task with new artifact to DB", task_id=task_id)
            
            # 4. Return the updated task object (the one we modified and saved)
            return task
        except TaskNotFoundError:
             logger.warning("Task not found in DB for adding artifact", task_id=task_id)
             raise
        except Exception as e:
             logger.exception("Failed to add artifact to task in DB", task_id=task_id, error=str(e))
             raise # Re-raise other exceptions

    async def complete_task(
        self, task_id: str, message: Message, artifact: Artifact | None = None
    ) -> Task:
        """
        Sets task state to COMPLETED, adds message, optionally adds artifact.
        Uses refactored state update methods.
        """
        logger.info("Completing task", task_id=task_id)
        # Update status first (this now calls DB service)
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.COMPLETED, message=message)
        )

        # Add artifact if provided (this now calls DB service)
        if artifact:
            # We need to pass the artifact to the DB-aware method
            # Note: update_task_status returns the *updated* task, so we use its ID
            updated_task = await self.add_task_artifact(updated_task.id, artifact)

        return updated_task # Return the final state

    async def fail_task(self, task_id: str, message: Message) -> Task:
        """
        Sets task state to FAILED, adds message.
        Uses refactored update_task_status.
        """
        logger.error(
            "Failing task",
            task_id=task_id,
            reason=message.parts[0].text if message.parts else "Unknown",
        )
        # Update status (this now calls DB service)
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.FAILED, message=message)
        )
        return updated_task # Return the final state

    async def require_input_task(
        self, task_id: str, message: Message, artifact: Artifact | None = None
    ) -> Task:
        """
        Sets task state to INPUT_REQUIRED, adds message, optionally adds artifact.
        Uses refactored state update methods.
        """
        logger.info("Setting task to INPUT_REQUIRED", task_id=task_id)
        # Update status first (this now calls DB service)
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.INPUT_REQUIRED, message=message)
        )

        # Add artifact if provided (this now calls DB service)
        if artifact:
            updated_task = await self.add_task_artifact(updated_task.id, artifact)

        return updated_task # Return the final state

    # --- Placeholder Helper Methods --- #
    # --- These are removed as parsing logic belongs elsewhere --- #

    # --- OLD JSON-RPC handlers REMOVED --- #
    # --- Business logic moved to api/routes/rpc.py --- #

    # --- Add other state management helpers as needed (e.g., update_artifact) --- #

    async def cancel_task(self, task_id: str) -> Task:
        """
        Cancels a task if it's in a cancelable state.
        Uses refactored state update methods.

        Args:
            task_id: The ID of the task to cancel.

        Returns:
            The updated Task object with CANCELED state.

        Raises:
            TaskNotFoundError: If the task doesn't exist.
            TaskNotCancelableError: If the task is in a final state.
        """
        logger.info("Attempting to cancel task", task_id=task_id)
        cancelable_states = {TaskState.WORKING, TaskState.INPUT_REQUIRED, TaskState.SUBMITTED}

        # Get current task state using the refactored method
        task = await self.get_task(task_id)
        if not task:
            # get_task already logs and returns None, raise expected error
            raise TaskNotFoundError(task_id)

        if task.status.state not in cancelable_states:
            logger.warning(
                "Attempted to cancel task in non-cancelable state",
                task_id=task_id,
                current_state=task.status.state,
            )
            raise TaskNotCancelableError(
                f"Task {task_id} is in state {task.status.state} and cannot be canceled."
            )

        # Update state to CANCELED using the refactored method
        cancellation_message = Message(role="agent", parts=[Part(type="text", text="Task canceled by request.")])
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.CANCELED, message=cancellation_message)
        )
        logger.info("Task successfully canceled", task_id=task_id)
        return updated_task
