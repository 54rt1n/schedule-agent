# sa/db/strategies/find_events.py
"""Strategy to find events based on query criteria."""

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from typing import List

from sa.core.logging_config import get_logger
from sa.db.models import Event
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.db.dao import EventQuery, EventResponse

logger = get_logger(__name__)

class FindEventsStrategy(DatabaseExecutionStrategy[EventQuery, List[EventResponse]]):
    """Strategy to find events based on query criteria."""

    # Note: No DateUtils needed for this strategy as it only queries persisted data
    def __init__(self, db_service: DatabaseService):
        """Initialize the strategy with dependencies."""
        super().__init__(db_service=db_service)

    async def _run(self, session: AsyncSession, data: EventQuery) -> List[EventResponse]:
        """
        Finds events based on criteria in the EventQuery object within the transaction.

        Args:
            session: The database session.
            data: An EventQuery object containing the search criteria.

        Returns:
            A list of EventResponse objects matching the criteria.
        """
        query = data # Use 'query' internally for clarity matching original code
        logger.info("Executing FindEventsStrategy", query=query.model_dump(exclude_unset=True))

        stmt = select(Event)

        # Apply filters based on the query object
        if query.start_time and query.end_time:
            # Assumes input times are already appropriate (e.g., UTC aware)
            # The strategy works with the naive UTC times stored in the DB
            # TODO: Clarify timezone handling for find_events query parameters if needed
            naive_start = query.start_time.replace(tzinfo=None)
            naive_end = query.end_time.replace(tzinfo=None)
            # Find events that *overlap* the given naive range
            stmt = stmt.where(Event.start_time < naive_end, Event.end_time > naive_start)
            logger.debug(f"Applied time range filter: {naive_start.isoformat()} - {naive_end.isoformat()}")

        if query.title_contains:
            stmt = stmt.where(func.lower(Event.title).contains(query.title_contains.lower()))
            logger.debug(f"Applied title filter: contains '{query.title_contains}'")

        # TODO: Add filters for other EventQuery fields if they exist (participants, location)

        # Eager load relationships
        stmt = stmt.options(
            selectinload(Event.participants),
            selectinload(Event.excluded_dates),
            selectinload(Event.modified_instances),
        )

        result = await session.execute(stmt)
        scalars = result.scalars()
        unique_scalars = scalars.unique()
        events = list(unique_scalars.all())
        logger.info(f"FindEventsStrategy found {len(events)} events.")
        return events

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure (typically no-op for reads)."""
        logger.warning(
            f"_failure called for FindEventsStrategy due to: {exception.__class__.__name__}. "
            f"Read operations usually don't require specific failure logic."
        )
        # No specific failure logic required for this strategy