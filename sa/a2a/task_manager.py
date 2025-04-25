# sa/a2a/task_manager.py
"""Manages the lifecycle of A2A tasks by orchestrating DB operations via strategies."""

from datetime import UTC, datetime
import json

from sa.a2a.dto import (
    Artifact,
    Message,
    Part,
    Task,
    TaskSendParams,
    TaskState,
    TaskStatus,
)
from sa.core.exceptions import TaskNotFoundError, TaskNotCancelableError
from sa.core.logging_config import get_logger
from sa.db.models import A2ATask
from sa.db.service import DatabaseService
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.nlp.nlp_processor import NLPProcessor

logger = get_logger(__name__)


class SchedulingTaskManager:
    """Manages the lifecycle of A2A tasks by orchestrating DB operations via strategies."""

    def __init__(
        self,
        nlp_processor: NLPProcessor,
        db_service: DatabaseService,
        conflict_resolver: ConflictResolver,
        date_utils: DateUtils,
        exporters: dict[str, CalendarExporter] | None = None,
    ):
        """Initialize the task manager with dependencies and strategies."""
        self.nlp_processor = nlp_processor
        self.db_service = db_service
        self.get_task_strategy = GetA2ATaskByIdStrategy(db_service=db_service)
        self.save_task_strategy = SaveA2ATaskStrategy(db_service=db_service)
        self.update_status_strategy = UpdateA2ATaskStatusStrategy(db_service=db_service)
        self.date_utils = date_utils
        self.exporters = exporters
        self.conflict_resolver = conflict_resolver

        logger.info("SchedulingTaskManager initialized (using DB strategies).")

    # --- Conversion Helpers ---

    def _a2a_task_to_task(self, db_task: A2ATask | None) -> Task | None:
        """Converts A2ATask (DB model) to Task (Pydantic model)."""
        if db_task is None:
            return None
        try:
            status_message = Message.model_validate_json(db_task.status_message_json) if db_task.status_message_json else None
            params = TaskSendParams.model_validate_json(db_task.params_json) if db_task.params_json else None
            artifacts = [Artifact.model_validate(a) for a in json.loads(db_task.artifacts_json or '[]')]
            history = [TaskStatus.model_validate(h) for h in json.loads(db_task.history_json or '[]')]

            return Task(
                id=db_task.id,
                sessionId=db_task.session_id,
                status=TaskStatus(
                    state=TaskState(db_task.state), # Convert string state back to Enum
                    message=status_message
                ),
                params=params,
                artifacts=artifacts,
                history=history,
                createdAt=db_task.created_at, # Already datetime
                updatedAt=db_task.updated_at, # Already datetime
            )
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(f"Error converting A2ATask {db_task.id} to Task: {e}", exc_info=True)
            # Decide on error handling: return None? Raise a specific ConversionError?
            # For now, return None to indicate conversion failure.
            return None

    def _task_to_a2a_task(self, task: Task | None) -> A2ATask | None:
        """Converts Task (Pydantic model) to A2ATask (DB model)."""
        if task is None:
            return None
        try:
            # Ensure timestamps are naive UTC as expected by the DB model default factories
            created_at_naive = task.createdAt.replace(tzinfo=None) if task.createdAt else datetime.now(UTC).replace(tzinfo=None)
            updated_at_naive = task.updatedAt.replace(tzinfo=None) if task.updatedAt else datetime.now(UTC).replace(tzinfo=None)

            return A2ATask(
                id=task.id,
                session_id=task.sessionId,
                state=task.status.state.value, # Store Enum value as string
                status_message_json=task.status.message.model_dump_json() if task.status.message else None,
                params_json=task.params.model_dump_json() if task.params else None,
                artifacts_json=json.dumps([a.model_dump() for a in task.artifacts]),
                history_json=json.dumps([h.model_dump() for h in task.history]),
                created_at=created_at_naive,
                updated_at=updated_at_naive,
            )
        except (TypeError) as e: # Catch potential errors during dump/serialization
            logger.error(f"Error converting Task {task.id} to A2ATask: {e}", exc_info=True)
            return None

    # --- Refactored State Management Methods ---

    async def get_task(self, task_id: str) -> Task | None:
        """Retrieves a task by ID using the GetA2ATaskByIdStrategy."""
        logger.debug("Retrieving task via GetA2ATaskByIdStrategy", task_id=task_id)
        try:
            db_task = await self.get_task_strategy.execute(task_id)
            task = self._a2a_task_to_task(db_task)
            logger.debug("Retrieved and converted task from DB", task_id=task_id, found=(task is not None))
            return task
        except Exception as e:
            # Strategy base class already logs errors, but we might want to log context here
            logger.error(f"Error executing GetA2ATaskByIdStrategy for {task_id}: {e}", exc_info=True)
            # Decide: re-raise? Return None?
            return None # Return None on strategy execution error

    async def create_task(self, params: TaskSendParams) -> Task:
        """
        Creates a new task object, saves it via SaveA2ATaskStrategy, and returns the Pydantic Task.
        Initial state is set to WORKING.
        """
        task_id = params.id
        session_id = params.sessionId
        logger.info("Creating new task state object", task_id=task_id, session_id=session_id)

        # 1. Construct the initial Pydantic Task object
        initial_task = Task(
            id=task_id,
            sessionId=session_id,
            params=params,
            status=TaskStatus(state=TaskState.WORKING),
            artifacts=[],
            history=[],
            # Timestamps will be set by DB model defaults if not provided here explicitly
            # Set them here to be consistent before saving
            createdAt=datetime.now(UTC), # Store TZ Aware UTC initially
            updatedAt=datetime.now(UTC), # Store TZ Aware UTC initially
        )

        # 2. Convert to DB model format
        db_task_to_save = self._task_to_a2a_task(initial_task)
        if not db_task_to_save:
            # Handle conversion error
            logger.error("Failed to convert initial Task to A2ATask for saving", task_id=task_id)
            # Decide error handling: raise specific error?
            raise ValueError(f"Failed to create A2ATask object for task {task_id}")

        # 3. Save task state using SaveA2ATaskStrategy
        try:
            # Save strategy expects A2ATask, returns A2ATask
            persisted_db_task = await self.save_task_strategy.execute(db_task_to_save)
            if not persisted_db_task:
                 # This might happen if strategy returns None on error or input was None
                 logger.error("SaveA2ATaskStrategy returned None after saving.", task_id=task_id)
                 raise RuntimeError(f"Failed to save task {task_id} to DB.") # Raise internal error

            logger.info("Successfully saved initial task state via strategy", task_id=task_id)
            # 4. Convert back to Pydantic Task to return (optional, could return initial_task)
            # Returning the converted persisted task ensures we have DB defaults (like exact timestamps)
            returned_task = self._a2a_task_to_task(persisted_db_task)
            if not returned_task:
                 logger.error("Failed to convert persisted A2ATask back to Task", task_id=task_id)
                 # Fallback or raise error - returning initial might be misleading if DB changed things
                 raise RuntimeError(f"Failed to convert saved task {task_id} back for return.")
            return returned_task

        except Exception as e:
            logger.exception("Failed to save initial task state via strategy", task_id=task_id, error=str(e))
            raise # Re-raise strategy execution error

    async def update_task_status(self, task_id: str, status: TaskStatus) -> Task:
        """
        Updates the task status via UpdateA2ATaskStatusStrategy and returns the updated Pydantic Task.
        Raises TaskNotFoundError if the task doesn't exist in DB.
        """
        logger.debug("Updating task status via UpdateA2ATaskStatusStrategy", task_id=task_id, new_state=status.state)
        try:
            # 1. Call strategy to update status and history in DB
            # Strategy input is (task_id, TaskStatus Pydantic object)
            await self.update_status_strategy.execute((task_id, status))
            logger.debug("Successfully executed UpdateA2ATaskStatusStrategy", task_id=task_id)

            # 2. Fetch the updated task state using get_task (which uses strategy and conversion)
            updated_task = await self.get_task(task_id)
            if updated_task is None:
                 # This could happen if get_task fails after update or if task somehow disappeared
                 logger.error("Task not found via get_task after status update, potential issue", task_id=task_id)
                 # Raise TaskNotFoundError as the contract implies the task should exist
                 raise TaskNotFoundError(f"Task {task_id} not found after status update.")

            logger.debug("Successfully retrieved and converted updated task", task_id=task_id)
            return updated_task

        except TaskNotFoundError: # Propagate TaskNotFoundError from update_status_strategy
             logger.warning("Task not found by UpdateA2ATaskStatusStrategy", task_id=task_id)
             raise
        except Exception as e:
             logger.exception("Failed during update_task_status execution", task_id=task_id, error=str(e))
             raise # Re-raise other exceptions

    async def add_task_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """
        Adds an artifact to a task by fetching (get_task), modifying Pydantic Task,
        converting back to DB model, and saving (SaveA2ATaskStrategy).
        Raises TaskNotFoundError if the task doesn't exist.
        """
        logger.debug("Adding artifact to task via strategies", task_id=task_id, artifact_name=artifact.name)
        try:
            # 1. Fetch the current Pydantic Task state using get_task
            task = await self.get_task(task_id)
            if not task:
                raise TaskNotFoundError(task_id) # get_task handles logging

            # 2. Modify the Pydantic Task object
            if task.artifacts is None: # Should be initialized by Pydantic model
                 task.artifacts = []
            task.artifacts.append(artifact)
            task.updatedAt = datetime.now(UTC) # Update timestamp on Pydantic model

            # 3. Convert modified Pydantic Task to DB model
            db_task_to_save = self._task_to_a2a_task(task)
            if not db_task_to_save:
                logger.error("Failed to convert Task to A2ATask for saving artifact", task_id=task_id)
                raise RuntimeError(f"Conversion error adding artifact to task {task_id}")

            # 4. Save the updated task state back using SaveA2ATaskStrategy
            persisted_db_task = await self.save_task_strategy.execute(db_task_to_save)
            if not persisted_db_task:
                logger.error("SaveA2ATaskStrategy returned None after saving artifact.", task_id=task_id)
                raise RuntimeError(f"Failed to save task {task_id} with new artifact.")

            logger.debug("Successfully saved task with new artifact via strategy", task_id=task_id)

            # 5. Convert persisted DB Task back to Pydantic Task to return
            returned_task = self._a2a_task_to_task(persisted_db_task)
            if not returned_task:
                 logger.error("Failed to convert saved A2ATask (with artifact) back to Task", task_id=task_id)
                 raise RuntimeError(f"Failed to convert saved task {task_id} back for return after adding artifact.")
            return returned_task

        except TaskNotFoundError:
             logger.warning("Task not found for adding artifact", task_id=task_id)
             raise
        except Exception as e:
             logger.exception("Failed to add artifact to task via strategies", task_id=task_id, error=str(e))
             raise # Re-raise other exceptions

    # --- Higher-Level State Transitions (Rely on refactored methods) ---

    async def complete_task(
        self, task_id: str, message: Message, artifact: Artifact | None = None
    ) -> Task:
        """
        Sets task state to COMPLETED using refactored methods.
        """
        logger.info("Completing task", task_id=task_id)
        # Update status first (uses update_task_status -> update_status_strategy)
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.COMPLETED, message=message)
        )
        # Add artifact if provided (uses add_task_artifact -> get_task -> save_task_strategy)
        if artifact:
            # add_task_artifact needs the ID and the artifact itself
            # It fetches the latest state internally
            updated_task = await self.add_task_artifact(task_id, artifact) # Pass task_id

        return updated_task # Return the final Pydantic Task

    async def fail_task(self, task_id: str, message: Message) -> Task:
        """
        Sets task state to FAILED using refactored update_task_status.
        """
        logger.error(
            "Failing task",
            task_id=task_id,
            reason=message.parts[0].text if message.parts else "Unknown",
        )
        # Update status (uses update_task_status -> update_status_strategy)
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.FAILED, message=message)
        )
        return updated_task # Return the final Pydantic Task

    async def require_input_task(
        self, task_id: str, message: Message, artifact: Artifact | None = None
    ) -> Task:
        """
        Sets task state to INPUT_REQUIRED using refactored methods.
        """
        logger.info("Setting task to INPUT_REQUIRED", task_id=task_id)
        # Update status first (uses update_task_status -> update_status_strategy)
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.INPUT_REQUIRED, message=message)
        )
        # Add artifact if provided (uses add_task_artifact -> get_task -> save_task_strategy)
        if artifact:
            updated_task = await self.add_task_artifact(task_id, artifact) # Pass task_id

        return updated_task # Return the final Pydantic Task

    async def cancel_task(self, task_id: str) -> Task:
        """
        Cancels a task if it's in a cancelable state, using refactored methods.
        """
        logger.info("Attempting to cancel task", task_id=task_id)
        cancelable_states = {TaskState.WORKING, TaskState.INPUT_REQUIRED, TaskState.SUBMITTED}

        # 1. Get current task state using the refactored get_task
        task = await self.get_task(task_id)
        if not task:
            raise TaskNotFoundError(task_id) # get_task handles logging

        # 2. Check if cancelable
        if task.status.state not in cancelable_states:
            logger.warning(
                "Attempted to cancel task in non-cancelable state",
                task_id=task_id,
                current_state=task.status.state,
            )
            raise TaskNotCancelableError(
                f"Task {task_id} is in state {task.status.state} and cannot be canceled."
            )

        # 3. Update state to CANCELED using refactored update_task_status
        cancellation_message = Message(role="agent", parts=[Part(type="text", text="Task canceled by request.")])
        updated_task = await self.update_task_status(
            task_id, TaskStatus(state=TaskState.CANCELED, message=cancellation_message)
        )
        logger.info("Task successfully canceled", task_id=task_id)
        return updated_task # Return final Pydantic Task
