# sa/db/strategies/update_a2a_task_status.py
# Copyright (C) 2025 Martin Bukowski

"""Strategy to update the status and potentially history of an A2A task."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from schedule_core.core.logging_config import get_logger
from schedule_core.core.exceptions import TaskNotFoundError as CoreTaskNotFoundError
from schedule_core.db.models import A2ATask
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.db.dao import UpdateA2ATaskStatus

logger = get_logger(__name__)


class UpdateA2ATaskStatusStrategy(DatabaseExecutionStrategy[UpdateA2ATaskStatus, None]):
    """Strategy to update the status fields and optionally history of an A2ATask."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: UpdateA2ATaskStatus) -> None:
        """
        Fetches the task, updates its status fields based on the DAO.
        If new_history_json is provided in the DAO, updates the history field.
        Relies on the DB model's onupdate trigger for updated_at.

        Args:
            session: The database session.
            data: UpdateA2ATaskStatus DAO containing task_id and new status/history info.

        Raises:
            CoreTaskNotFoundError: If the task with the given ID is not found.
            Exception: For other database errors.
        """
        task_id = data.id
        logger.debug(f"Executing UpdateA2ATaskStatusStrategy for ID: {task_id}, New State: {data.new_state}")

        # 1. Fetch the task
        stmt = select(A2ATask).where(A2ATask.id == task_id)
        result = await session.execute(stmt)
        task : A2ATask | None = result.scalar_one_or_none()

        if not task:
            logger.warning(f"A2A task with ID {task_id} not found for status update.")
            raise CoreTaskNotFoundError(task_id)

        logger.debug(f"Found task {task_id} for status update.")

        # 2. Update task fields from DAO
        task.state = data.new_state
        task.status_message_json = data.new_status_message_json
        task.status_timestamp = data.new_status_timestamp
        # Only update history if the manager provided a new serialized list
        if data.new_history_json is not None:
            task.history_json = data.new_history_json
            logger.debug(f"Updating history for task {task_id}.")

        # Note: task.updated_at is handled by the DB model's onupdate mechanism.

        # 3. Add to session (marks the object as dirty)
        session.add(task)
        await session.flush() # Ensure changes are flushed before potential commit
        logger.debug(f"Task {task_id} status/history updated and flushed.")

        # No return value needed

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure for updating task status."""
        logger.warning(
            f"_failure called for UpdateA2ATaskStatusStrategy due to: {exception.__class__.__name__}."
        )
        # No specific failure logic required for this strategy