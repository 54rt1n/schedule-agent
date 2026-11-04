# sa/db/strategies/save_a2a_task.py
# Copyright (C) 2025 Martin Bukowski

"""Strategy to save (create or update) an A2A task."""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from schedule_core.db.models import A2ATask
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.core.logging_config import get_logger
from schedule_core.db.dao import A2ATaskCreate

logger = get_logger(__name__)


class SaveA2ATaskStrategy(DatabaseExecutionStrategy[A2ATaskCreate, Optional[A2ATask]]):
    """Strategy to save (create or update) an A2A task using merge."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: A2ATaskCreate) -> Optional[A2ATask]:
        """
        Constructs an A2ATask model instance from the DAO and merges it into the session.
        Handles both INSERT (if task ID is new) and UPDATE (if task ID exists).
        Relies on DB model defaults for created_at/updated_at.

        Args:
            session: The database session.
            data: The DAO containing data for the A2ATask.

        Returns:
            The persisted A2ATask object after merging and flushing, or None if input data is None.
        """
        if data is None:
            logger.warning("SaveA2ATaskStrategy received None data.")
            return None

        logger.debug(f"Executing SaveA2ATaskStrategy for task ID: {data.id}")

        try:
            # Create an A2ATask instance directly from the DAO fields.
            # Let the DB model handle created_at/updated_at via defaults/onupdate.
            task_instance = A2ATask(
                id=data.id,
                session_id=data.session_id,
                state=data.state,
                status_message_json=data.status_message_json,
                status_timestamp=data.status_timestamp,
                artifacts_json=data.artifacts_json,
                history_json=data.history_json,
                metadata_json=data.metadata_json,
            )

            # Use session.merge() which handles both INSERT and UPDATE based on PK.
            merged_task = await session.merge(task_instance)

            # Flush to ensure the operation is processed before commit.
            # Refresh might be needed if relying heavily on server defaults not covered by merge,
            # but merge should handle loading state if it exists.
            await session.flush()
            await session.refresh(merged_task) # Refresh to get any DB-generated values

            logger.debug(f"A2A task {merged_task.id} merged, flushed, and refreshed.")
            return merged_task
        except Exception as e:
            logger.error(f"Error during merge/flush/refresh for task {data.id}: {e}", exc_info=True)
            raise

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure for saving a task."""
        logger.warning(
            f"_failure called for SaveA2ATaskStrategy due to: {exception.__class__.__name__}."
        )
        # No specific failure logic required for this strategy