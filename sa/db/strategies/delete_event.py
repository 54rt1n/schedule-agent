# sa/db/strategies/delete_event.py
# Copyright (C) 2025 Martin Bukowski

"""Strategy to delete an event by its ID."""

import uuid
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from sa.core.logging_config import get_logger
from sa.db.models import Event
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.db.dao import EventDelete

logger = get_logger(__name__)


class DeleteEventStrategy(DatabaseExecutionStrategy[EventDelete, bool]):
    """Strategy to delete an event by its ID."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: EventDelete) -> bool:
        """
        Deletes the event by ID within the transaction managed by the base class.

        Args:
            session: The database session provided by the base execute method.
            data: The unique identifier (UUID) of the event to delete.

        Returns:
            True if an event was deleted, False otherwise.
        """
        event_id = data.id
        logger.debug(f"Executing DeleteEventStrategy for ID: {event_id}")

        stmt = delete(Event).where(Event.id == event_id)
        result = await session.execute(stmt)

        was_deleted = result.rowcount > 0

        if was_deleted:
            logger.debug(f"Deleted event with ID: {event_id}")
        else:
            logger.warning(f"Event with ID {event_id} not found for deletion.")

        return was_deleted

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """
        Handles failure for DeleteEventStrategy.

        The actual database rollback is handled by the session context manager
        in the base class.
        """
        logger.warning(
            f"_failure called for DeleteEventStrategy due to: {exception.__class__.__name__}. "
            f"Session hash: {hash(session)}. Deletion for {exception} failed."
        )
        # No specific cleanup logic required for this strategy