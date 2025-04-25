# sa/db/strategies/save_a2a_task.py
"""Strategy to save (create or update) an A2A task."""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from sa.db.models import A2ATask
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.core.logging_config import get_logger

logger = get_logger(__name__)


class SaveA2ATaskStrategy(DatabaseExecutionStrategy[A2ATask, Optional[A2ATask]]):
    """Strategy to save (create or update) an A2A task."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: A2ATask) -> Optional[A2ATask]:
        """
        Adds the task to the session for persistence.
        The base class handles commit.

        Args:
            session: The database session.
            data: The A2ATask object to save.

        Returns:
            The persisted A2ATask object after flushing/refreshing, or None if data is None.
        """
        if data is None:
            logger.warning("SaveA2ATaskStrategy received None data.")
            return None

        task_to_save = data
        logger.debug(f"Executing SaveA2ATaskStrategy for task ID: {task_to_save.id}")

        # Use session.merge() which handles both INSERT and UPDATE based on PK.
        # It fetches from DB if PK exists, updates the fetched object, and adds it.
        # If PK doesn't exist, it adds the passed object.
        try:
            # Merge returns the persisted object attached to the session
            merged_task = await session.merge(task_to_save)
            # We need to flush to ensure the object is in the DB before commit
            # and refresh might be needed if there are server-side defaults/triggers
            await session.flush()
            await session.refresh(merged_task)
            logger.debug(f"A2A task {merged_task.id} merged/saved, flushed, and refreshed.")
            return merged_task
        except Exception as e:
            # Log the specific error during merge/flush/refresh
            logger.error(f"Error during merge/flush/refresh for task {task_to_save.id}: {e}", exc_info=True)
            # Re-raise the exception to be caught by the base class's execute method
            raise

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure for saving a task."""
        logger.warning(
            f"_failure called for SaveA2ATaskStrategy due to: {exception.__class__.__name__}."
        )
        # No specific failure logic required for this strategy