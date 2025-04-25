# sa/db/strategies/get_a2a_task_by_id.py
"""Strategy to retrieve a single A2A task by its ID."""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from sa.core.logging_config import get_logger
from sa.db.models import A2ATask
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy

logger = get_logger(__name__)


class GetA2ATaskByIdStrategy(DatabaseExecutionStrategy[str, Optional[A2ATask]]):
    """Strategy to retrieve a single A2A task by its ID."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: str) -> Optional[A2ATask]:
        """
        Fetches the A2A task by ID.

        Args:
            session: The database session.
            data: The unique identifier (task_id) of the A2A task.

        Returns:
            The A2ATask object if found, otherwise None.
        """
        task_id = data
        logger.debug(f"Executing GetA2ATaskByIdStrategy for ID: {task_id}")

        # Use session.get for primary key lookup for potential optimization
        # result = await session.get(A2ATask, task_id) # Alternative

        stmt = select(A2ATask).where(A2ATask.id == task_id)
        result = await session.execute(stmt)
        task = result.scalar_one_or_none()

        if task:
            logger.debug(f"Found A2A task with ID {task_id}")
        else:
            logger.debug(f"A2A task with ID {task_id} not found.")
        return task

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure (typically no-op for reads)."""
        logger.warning(
            f"_failure called for GetA2ATaskByIdStrategy due to: {exception.__class__.__name__}."
        )
        # No specific failure logic required for this strategy