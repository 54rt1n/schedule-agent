# sa/db/strategies/get_event_by_id.py
"""Strategy to retrieve a single event by its ID, including relationships."""

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from typing import Optional
import uuid

from sa.core.logging_config import get_logger
from sa.db.models import Event, ModifiedInstance
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.db.dao import EventResponse

logger = get_logger(__name__)


class GetEventByIdStrategy(DatabaseExecutionStrategy[uuid.UUID, Optional[EventResponse]]):
    """Strategy to retrieve a single event by its ID, including relationships."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: uuid.UUID) -> Optional[EventResponse]:
        """
        Fetches the event by ID within the transaction managed by the base class.

        Args:
            session: The database session provided by the base execute method.
            data: The unique identifier of the event to retrieve.

        Returns:
            The Event object if found, otherwise None.
        """
        event_id = data # Assign data to the specific variable name
        logger.debug(f"Executing GetEventByIdStrategy for ID: {event_id}")
        result = await session.execute(
            select(Event)
            .where(Event.id == event_id)
            # Eagerly load relationships often needed together
            .options(
                selectinload(Event.participants),
                selectinload(Event.excluded_dates),
                selectinload(Event.modified_instances),
            )
        )
        event = result.scalar_one_or_none()
        if event:
            logger.debug(f"Found event: {event.title} (ID: {event_id})")
        else:
            logger.debug(f"Event with ID {event_id} not found.")
        return event

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure for GetEventByIdStrategy (typically no-op for reads)."""
        logger.warning(
            f"_failure called for GetEventByIdStrategy due to: {exception.__class__.__name__}. "
            f"Session hash: {hash(session)}. Read operations usually don't require specific failure logic."
        )
        # No specific failure logic required for this strategy
