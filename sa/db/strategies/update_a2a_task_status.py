# sa/db/strategies/update_a2a_task_status.py
"""Strategy to update the status and history of an A2A task."""

from datetime import UTC, datetime
import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from typing import Tuple

from sa.core.logging_config import get_logger
from sa.core.exceptions import TaskNotFoundError
from sa.db.models import A2ATask
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.db.dao import TaskStatus

logger = get_logger(__name__)


# Type alias for input data
UpdateStatusInput = Tuple[str, TaskStatus]

class UpdateA2ATaskStatusStrategy(DatabaseExecutionStrategy[UpdateStatusInput, None]):
    """Strategy to update the status and history of an A2A task."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: UpdateStatusInput) -> None:
        """
        Fetches the task, updates its status fields, and adds to history.

        Args:
            session: The database session.
            data: A tuple containing (task_id, new_status).

        Raises:
            TaskNotFoundError: If the task with the given ID is not found.
            json.JSONDecodeError: If history JSON is invalid.
            Exception: For other database errors.
        """
        task_id, new_status = data
        logger.debug(f"Executing UpdateA2ATaskStatusStrategy for ID: {task_id}, New State: {new_status.state}")

        # 1. Fetch the task
        # Use select for clarity, could use session.get as well
        stmt = select(A2ATask).where(A2ATask.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        if not task:
            logger.warning(f"A2A task with ID {task_id} not found for status update.")
            raise TaskNotFoundError(task_id)

        logger.debug(f"Found task {task_id} for status update.")

        # 2. Update task fields
        task.state = new_status.state.value # Store enum value
        task.status_message_json = new_status.message.model_dump_json() if new_status.message else None
        task.updated_at = datetime.now(UTC).replace(tzinfo=None) # Update timestamp

        # 3. Update history
        try:
            history = json.loads(task.history_json) if task.history_json else []
            # Append the new status (as a dict/JSON representation)
            history.append(new_status.model_dump())
            task.history_json = json.dumps(history)
            logger.debug(f"Updated history for task {task_id}. New length: {len(history)}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to decode history JSON for task {task_id}: {task.history_json}", exc_info=True)
            raise # Re-raise to be caught by the base class
        except Exception as e:
            logger.error(f"Error updating history for task {task_id}", exc_info=True)
            raise # Re-raise other errors

        # 4. Add to session (session.merge might also work here if task was detached)
        session.add(task)
        await session.flush() # Ensure changes are flushed before potential commit
        logger.debug(f"Task {task_id} status updated and flushed.")

        # No return value needed

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure for updating task status."""
        logger.warning(
            f"_failure called for UpdateA2ATaskStatusStrategy due to: {exception.__class__.__name__}."
        )
        # No specific failure logic required for this strategy