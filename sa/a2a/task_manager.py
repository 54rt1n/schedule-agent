# sa/a2a/task_manager.py
# Copyright (C) 2025 Martin Bukowski

"""Manages the lifecycle and persistence of A2A tasks according to the spec.

**Ideal Implementation Goal:**

This module should provide the core logic for managing A2A tasks throughout
their lifecycle, ensuring adherence to the A2A specification defined in
`sa.a2a.dto`. It acts as the intermediary between the API/protocol layer
and the data persistence layer.

**Core Responsibilities:**

1.  **Task Creation:** Handle incoming task requests (`TaskSendParams`),
    initialize the task state according to the spec (`Task` DTO), and persist
    it.
2.  **Task Retrieval:** Fetch task state from persistence by ID, reconstruct
    the `Task` DTO, and handle history length constraints.
3.  **Task State Updates:** Provide methods to update the task's `status`
    (including `state`, `message`, `timestamp`) and persist changes.
4.  **Message History Management:** Append relevant user and agent messages
    (`Message` DTOs) to the task's `history` list and persist.
5.  **Artifact Management:** Provide methods to add generated `Artifact` objects
    to the task's `artifacts` list and persist.
6.  **Lifecycle Management:** Enforce valid state transitions (e.g., checking
    if a task is cancelable before attempting cancellation).
7.  **Persistence Abstraction:** Interact with a data storage mechanism
    (e.g., database service) to save and load task data. The internal storage
    schema might differ slightly (e.g., adding internal timestamps),
    but the TaskManager's public interface MUST consume and produce the
    spec-aligned DTOs from `sa.a2a.dto`.
8.  **Error Handling:** Raise appropriate A2A-specific exceptions
    (e.g., `TaskNotFoundError`, `TaskNotCancelableError`) when operations fail
    or are invalid.

**Information Tracking (per Task):**

The Task Manager is responsible for accurately tracking and updating the
fields defined in the `sa.a2a.dto.Task` model:

-   `id` (str): Unique identifier.
-   `sessionId` (str | None): Session identifier.
-   `status` (TaskStatus): The *current* status object, containing:
    -   `state` (TaskState): The current lifecycle state.
    -   `message` (Message | None): Agent message associated with the *current* state.
    -   `timestamp` (datetime | None): When this status was set.
-   `artifacts` (list[Artifact] | None): Accumulating list of generated artifacts.
-   `history` (list[Message] | None): Accumulating list of user/agent messages
    representing the conversation flow.
-   `metadata` (dict[str, Any] | None): Optional key-value metadata.

**Interaction Flows (Ideal):**

-   **Create Task (`tasks/send`):**
    1.  Receive `TaskSendParams`.
    2.  Construct initial `Task` object:
        - Set `id`, `sessionId`, `metadata` from params.
        - Set initial `status` (e.g., state=SUBMITTED or WORKING, timestamp).
        - Initialize `artifacts` (e.g., `[]` or `None`).
        - Initialize `history` with `[params.message]`.
    3.  Persist the new `Task` data.
    4.  Return the created `Task` DTO.

-   **Get Task (`tasks/get`):**
    1.  Receive `task_id` and optional `history_length`.
    2.  Retrieve task data from persistence using `task_id`.
    3.  Raise `TaskNotFoundError` if not found.
    4.  Reconstruct the `Task` DTO from retrieved data.
    5.  If `history_length` is provided, truncate the `Task.history` list accordingly.
    6.  Return the `Task` DTO.

-   **Update Status (Internal/Agent Action):**
    1.  Receive `task_id`, new `TaskStatus` object (`new_status`).
    2.  Retrieve current `Task` data.
    3.  (Optional: Validate state transition based on `current_task.status.state`
        and `new_status.state`).
    4.  Update the task's `status` field to `new_status` in persistence.
    5.  If `new_status.message` represents an agent message intended for the
        conversation history (e.g., requesting input, reporting completion/
        failure), append `new_status.message` to the task's `history` list
        in persistence.
    6.  Return the updated `Task` DTO (fetched after update).

-   **Add Artifact (Internal/Agent Action):**
    1.  Receive `task_id` and the new `Artifact` object.
    2.  Retrieve current `Task` data.
    3.  Append the new `Artifact` to the task's `artifacts` list in persistence.
    4.  Return the updated `Task` DTO (fetched after update).

-   **Cancel Task (`tasks/cancel`):**
    1.  Receive `task_id`.
    2.  Retrieve current `Task` data (specifically the `status.state`).
    3.  Check if `status.state` is cancelable (e.g., SUBMITTED, WORKING,
        INPUT_REQUIRED). Raise `TaskNotCancelableError` if not.
    4.  Create a cancellation `TaskStatus` (state=CANCELED, timestamp,
        optional cancellation message).
    5.  Call the internal Update Status logic to set the task's `status` to
        the cancellation status (this handles persistence).
    6.  Return the updated `Task` DTO.
"""

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
from sa.a2a.exceptions import TaskNotFoundError as A2ATaskNotFoundError, TaskNotCancelableError as A2ATaskNotCancelableError
from sa.core.exceptions import TaskNotFoundError as CoreTaskNotFoundError
from sa.core.logging_config import get_logger
from sa.db.models import A2ATask
from sa.db.dao import UpdateA2ATaskStatus, A2ATaskCreate, GetA2ATaskById
from sa.db.service import DatabaseService
from sa.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from sa.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from sa.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy

logger = get_logger(__name__)


class SchedulingTaskManager:
    """Manages the lifecycle of A2A tasks by orchestrating DB operations via strategies."""

    def __init__(
        self,
        db_service: DatabaseService,
    ):
        """Initialize the task manager with dependencies and strategies."""
        self.db_service = db_service
        self.get_task_strategy = GetA2ATaskByIdStrategy(db_service=db_service)
        self.save_task_strategy = SaveA2ATaskStrategy(db_service=db_service)
        self.update_status_strategy = UpdateA2ATaskStatusStrategy(db_service=db_service)

        logger.info("SchedulingTaskManager initialized (using DB strategies).")

    # --- Conversion Helper ---

    def _db_task_to_dto(self, db_task: A2ATask) -> Task:
        """Converts the A2ATask DB model to the Task DTO."""
        try:
            status_message = (
                Message.model_validate_json(db_task.status_message_json)
                if db_task.status_message_json
                else None
            )
            artifacts = (
                [Artifact.model_validate(a) for a in json.loads(db_task.artifacts_json)]
                if db_task.artifacts_json
                else None
            )
            history = (
                [Message.model_validate(h) for h in json.loads(db_task.history_json)]
                if db_task.history_json
                else None
            )
            metadata = (
                json.loads(db_task.metadata_json)
                if db_task.metadata_json
                else None
            )

            # Construct the TaskStatus object
            task_status = TaskStatus(
                state=TaskState(db_task.state),
                message=status_message,
                timestamp=db_task.status_timestamp # Assume DB stores naive UTC
                .replace(tzinfo=UTC) if db_task.status_timestamp else None
            )

            # Construct the final Task DTO
            task_dto = Task(
                id=db_task.id,
                sessionId=db_task.session_id,
                status=task_status,
                artifacts=artifacts,
                history=history,
                metadata=metadata,
            )
            return task_dto

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.error(
                f"Error converting A2ATask {db_task.id} to Task DTO: {e}", exc_info=True
            )
            # Re-raise as a runtime error, signaling internal data inconsistency
            raise RuntimeError(f"Failed to convert task {db_task.id} from DB format.") from e

    # --- Core Task Management Methods ---

    async def create_task(self, params: TaskSendParams) -> Task:
        """Handles `tasks/send`: Creates and persists a new task."""
        task_id = params.id
        logger.info(f"Creating new task: {task_id}", session_id=params.sessionId)

        # 1. Define initial state
        initial_state = TaskState.WORKING
        initial_timestamp = datetime.now(UTC)
        initial_status = TaskStatus(
            state=initial_state,
            timestamp=initial_timestamp,
            message=None
        )
        initial_history = [params.message]

        # 2. Prepare DAO for persistence
        try:
            create_dao = A2ATaskCreate(
                id=task_id,
                session_id=params.sessionId,
                state=initial_state.value,
                status_message_json=initial_status.message.model_dump_json() if initial_status.message else None,
                status_timestamp=initial_timestamp.replace(tzinfo=None),
                artifacts_json="[]",
                history_json=json.dumps([msg.model_dump() for msg in initial_history]),
                metadata_json=json.dumps(params.metadata) if params.metadata else None
            )
        except (TypeError, ValueError) as e:
            logger.error(f"Error serializing data for new task {task_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to prepare task {task_id} for saving.") from e

        # 3. Execute save strategy
        try:
            persisted_db_task = await self.save_task_strategy.execute(data=create_dao)
            if not persisted_db_task:
                 # Strategy should ideally raise on error, but handle None just in case
                 logger.error(f"SaveA2ATaskStrategy returned None for new task {task_id}.")
                 raise RuntimeError(f"Failed to save task {task_id} to DB.")
            logger.info(f"Successfully created and saved task {task_id}")
        except Exception as e:
            # Catch potential DB errors from strategy
            logger.exception(f"Database error creating task {task_id}: {e}")
            # Re-raise to allow higher layers to handle DB issues
            raise

        # 4. Convert persisted data back to DTO and return
        return self._db_task_to_dto(persisted_db_task)

    async def get_task(self, task_id: str, history_length: int | None = None) -> Task:
        """Handles `tasks/get`: Retrieves a task by ID, optionally truncating history."""
        logger.debug(f"Getting task: {task_id}", history_length=history_length)

        # 1. Execute get strategy
        try:
            get_dao = GetA2ATaskById(id=task_id)
            db_task = await self.get_task_strategy.execute(data=get_dao)
        except Exception as e:
            logger.exception(f"Database error getting task {task_id}: {e}")
            raise

        # 2. Check if found
        if not db_task:
            logger.warning(f"Task not found: {task_id}")
            raise A2ATaskNotFoundError(task_id)

        # 3. Convert to DTO
        task_dto = self._db_task_to_dto(db_task)

        # 4. Handle history truncation
        if history_length is not None and task_dto.history:
            if history_length <= 0:
                task_dto.history = [] # Empty history if length is 0 or negative
            else:
                # Keep the latest `history_length` messages
                task_dto.history = task_dto.history[-history_length:]
            logger.debug(f"Truncated history for task {task_id} to length {history_length}")

        logger.debug(f"Successfully retrieved task {task_id}")
        return task_dto

    async def update_task_status(
        self, task_id: str, new_status: TaskStatus
    ) -> Task:
        """Updates the task's status and potentially appends a message to history."""
        logger.debug(f"Updating status for task: {task_id} to {new_status.state.value}")

        # Ensure timestamp is set, default to now if not provided by caller
        if new_status.timestamp is None:
            new_status.timestamp = datetime.now(UTC)
            logger.debug(f"Set timestamp for status update: {new_status.timestamp}")

        new_history_json_str: str | None = None

        # Determine if history needs updating (append agent message)
        # Append agent messages that signify a state change meaningful to the user
        should_append_history = (
            new_status.message is not None and
            new_status.message.role == "agent"
            # Add other conditions if needed, e.g., specific states
        )

        if should_append_history:
            logger.debug(f"Appending agent message to history for task {task_id}")
            try:
                # Need to fetch current history to append
                get_dao = GetA2ATaskById(id=task_id)
                current_db_task = await self.get_task_strategy.execute(data=get_dao)
                if not current_db_task:
                    raise A2ATaskNotFoundError(task_id)

                current_history = (
                    [Message.model_validate(h) for h in json.loads(current_db_task.history_json)]
                    if current_db_task.history_json
                    else []
                )
                current_history.append(new_status.message)
                new_history_json_str = json.dumps([msg.model_dump() for msg in current_history])

            except A2ATaskNotFoundError:
                 logger.error(f"Task {task_id} not found when trying to update history for status change.")
                 raise # Re-raise the not found error
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                 logger.error(f"Error handling history update for task {task_id}: {e}", exc_info=True)
                 raise RuntimeError(f"Failed to update history for task {task_id}") from e
            except Exception as e:
                logger.exception(f"Database error getting history for task {task_id} status update: {e}")
                raise

        # Prepare DAO for the update strategy
        try:
            update_dao = UpdateA2ATaskStatus(
                id=task_id,
                new_state=new_status.state.value,
                new_status_message_json=new_status.message.model_dump_json() if new_status.message else None,
                new_status_timestamp=new_status.timestamp.replace(tzinfo=None), # Store naive UTC
                new_history_json=new_history_json_str # Pass updated history if applicable
            )
        except (TypeError, ValueError) as e:
            logger.error(f"Error serializing status update data for task {task_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to prepare status update for task {task_id}") from e

        # Execute update strategy
        try:
            await self.update_status_strategy.execute(data=update_dao)
            logger.info(f"Successfully updated status for task {task_id} to {new_status.state.value}")
        except CoreTaskNotFoundError:
            logger.warning(f"Task {task_id} not found by update strategy.")
            raise A2ATaskNotFoundError(task_id) # Raise A2A specific exception
        except Exception as e:
            logger.exception(f"Database error updating status for task {task_id}: {e}")
            raise

        # Return the updated task state by fetching it again
        return await self.get_task(task_id)

    async def add_task_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """Adds an artifact to the task's artifact list and persists."""
        logger.debug(f"Adding artifact to task: {task_id}", artifact_name=artifact.name)

        # 1. Fetch current DB state
        try:
            get_dao = GetA2ATaskById(id=task_id)
            db_task = await self.get_task_strategy.execute(data=get_dao)
            if not db_task:
                raise A2ATaskNotFoundError(task_id)
        except A2ATaskNotFoundError:
            logger.warning(f"Task {task_id} not found for adding artifact.")
            raise
        except Exception as e:
            logger.exception(f"Database error fetching task {task_id} before adding artifact: {e}")
            raise

        # 2. Update artifacts list
        try:
            current_artifacts = (
                [Artifact.model_validate(a) for a in json.loads(db_task.artifacts_json)]
                if db_task.artifacts_json
                else []
            )
            current_artifacts.append(artifact)
            updated_artifacts_json = json.dumps([art.model_dump() for art in current_artifacts])
        except (json.JSONDecodeError, TypeError, ValueError) as e:
            logger.error(f"Error handling artifact update for task {task_id}: {e}", exc_info=True)
            raise RuntimeError(f"Failed to update artifacts for task {task_id}") from e

        # 3. Prepare DAO for saving (using merge via Save strategy)
        # We need all fields from the current db_task + the updated artifacts_json
        save_dao = A2ATaskCreate(
            id=db_task.id,
            session_id=db_task.session_id,
            state=db_task.state,
            status_message_json=db_task.status_message_json,
            status_timestamp=db_task.status_timestamp,
            artifacts_json=updated_artifacts_json, # Use the updated list
            history_json=db_task.history_json,
            metadata_json=db_task.metadata_json
        )

        # 4. Execute save strategy
        try:
            await self.save_task_strategy.execute(data=save_dao)
            logger.info(f"Successfully added artifact and saved task {task_id}")
        except Exception as e:
            logger.exception(f"Database error saving task {task_id} after adding artifact: {e}")
            raise

        # 5. Return the updated task state
        return await self.get_task(task_id)

    # --- Higher-Level State Transitions ---

    async def cancel_task(self, task_id: str) -> Task:
        """Handles `tasks/cancel`: Sets task state to CANCELED if valid."""
        logger.info(f"Attempting to cancel task: {task_id}")
        cancelable_states = {
            TaskState.SUBMITTED,
            TaskState.WORKING,
            TaskState.INPUT_REQUIRED,
        }

        # 1. Get current task DTO to check state
        try:
            current_task = await self.get_task(task_id)
        except A2ATaskNotFoundError:
             logger.warning(f"Task {task_id} not found for cancellation attempt.")
             raise # Re-raise the specific error

        # 2. Check if cancelable
        if current_task.status.state not in cancelable_states:
            logger.warning(
                f"Task {task_id} is in non-cancelable state: {current_task.status.state}"
            )
            raise A2ATaskNotCancelableError(
                f"Task {task_id} in state {current_task.status.state} cannot be canceled."
            )

        # 3. Create cancellation status and update
        cancellation_message = Message(
            role="agent", parts=[Part(type="text", text="Task canceled by request.")]
        )
        cancellation_status = TaskStatus(
            state=TaskState.CANCELED,
            message=cancellation_message,
            timestamp=datetime.now(UTC)
        )

        try:
            updated_task = await self.update_task_status(task_id, cancellation_status)
            logger.info(f"Task {task_id} successfully canceled.")
            return updated_task
        except A2ATaskNotFoundError:
             # Should ideally not happen if get_task succeeded, but handle defensively
             logger.error(f"Task {task_id} disappeared during cancellation process.")
             raise
        except Exception as e:
            logger.exception(f"Error updating task {task_id} status to CANCELED: {e}")
            raise

    async def complete_task(
        self, task_id: str, message: Message, artifact: Artifact | None = None
    ) -> Task:
        """Convenience method to set task state to COMPLETED."""
        logger.info(f"Completing task {task_id}")
        final_task = await self.update_task_status(
            task_id,
            TaskStatus(
                state=TaskState.COMPLETED,
                message=message,
                timestamp=datetime.now(UTC)
            )
        )
        if artifact:
            logger.info(f"Adding final artifact to completed task {task_id}")
            final_task = await self.add_task_artifact(task_id, artifact)

        return final_task

    async def fail_task(self, task_id: str, message: Message) -> Task:
        """Convenience method to set task state to FAILED."""
        logger.error(f"Failing task {task_id}", reason=message.model_dump_json(indent=2))
        final_task = await self.update_task_status(
            task_id,
            TaskStatus(
                state=TaskState.FAILED,
                message=message,
                timestamp=datetime.now(UTC)
            )
        )
        return final_task

    async def require_input_task(
        self, task_id: str, message: Message, artifact: Artifact | None = None
    ) -> Task:
        """Convenience method to set task state to INPUT_REQUIRED."""
        logger.info(f"Setting task {task_id} to INPUT_REQUIRED")
        final_task = await self.update_task_status(
            task_id,
            TaskStatus(
                state=TaskState.INPUT_REQUIRED,
                message=message,
                timestamp=datetime.now(UTC)
            )
        )
        if artifact:
            logger.info(f"Adding artifact to input-required task {task_id}")
            final_task = await self.add_task_artifact(task_id, artifact)

        return final_task
