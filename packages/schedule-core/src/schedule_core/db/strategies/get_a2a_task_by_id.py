# sa/db/strategies/get_a2a_task_by_id.py
# Copyright (C) 2025 Martin Bukowski

"""Strategy to retrieve a single A2A task by its ID."""

from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import A2ATask
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.db.dao import GetA2ATaskById

logger = get_logger(__name__)


class GetA2ATaskByIdStrategy(DatabaseExecutionStrategy[GetA2ATaskById, Optional[A2ATask]]):
    """Strategy to retrieve a single A2A task by its ID."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: GetA2ATaskById) -> Optional[A2ATask]:
        """
        Fetches the A2A task by ID.

        Args:
            session: The database session.
            data: The unique identifier (task_id) of the A2A task.

        Returns:
            The A2ATask object if found, otherwise None.
        """
        task_id = data.id
        logger.debug(f"Executing GetA2ATaskByIdStrategy for ID: {task_id}")

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