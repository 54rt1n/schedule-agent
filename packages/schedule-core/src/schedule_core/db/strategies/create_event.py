# sa/db/strategies/create_event.py
# Copyright (C) 2025 Martin Bukowski

"""Strategy for creating event records in the database."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from schedule_core.core.exceptions import ConflictError
from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import Event, Participant
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_core.db.dao import EventCreate, TimeRange
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils

logger = get_logger(__name__)


class CreateEventStrategy(DatabaseExecutionStrategy[EventCreate, Event]):
    """Strategy for creating a new event and its participants in the database."""

    def __init__(
        self,
        db_service: DatabaseService,
        date_utils: DateUtils,
        conflict_resolver: ConflictResolver,
        get_events_in_range_strategy: GetEventsInRangeStrategy,
    ):
        """Initialize the strategy with its dependencies."""
        super().__init__(db_service=db_service)
        self.date_utils = date_utils
        self.conflict_resolver = conflict_resolver
        self.get_events_in_range_strategy = get_events_in_range_strategy

    async def _run(
        self, session: AsyncSession, data: EventCreate
    ) -> Event:
        """
        Core logic for creating the event, including conflict check.
        Args:
            session: The database session provided by the base execute method.
            data: The EventCreate object containing event details.
        Returns:
            The newly created Event object, including participants.
        Raises:
            ConflictError: If conflicts are detected.
        """
        logger.info(f"Executing CreateEventStrategy for: {data.title}")

        # 1. Conflict Check (moved inside _run to be part of the transaction)
        logger.debug("Performing conflict check within CreateEventStrategy._run")
        # Determine time range for fetching potential conflicts (aware UTC)
        start_aware_utc = self.date_utils.normalize_to_utc(data.start_time, data.time_zone)
        end_aware_utc = self.date_utils.normalize_to_utc(data.end_time, data.time_zone)

        get_events = TimeRange(start_time=start_aware_utc, end_time=end_aware_utc)

        potential_conflicts = await self.get_events_in_range_strategy.execute(data=get_events)
        logger.debug(f"Fetched {len(potential_conflicts)} potential conflicts for check.")

        # Perform check using the refactored resolver
        conflicts = await self.conflict_resolver.detect_conflicts(data, potential_conflicts)
        if conflicts:
            # Format conflicts into the message string
            logger.warning(f"Conflict detected by strategy. Conflicts: {conflicts}")
            raise ConflictError(conflicts=conflicts)
        logger.debug("No conflicts detected.")

        # 2. Convert times and Create Event (original logic)
        start_utc_aware = self.date_utils.normalize_to_utc(data.start_time, tz_name=data.time_zone)
        end_utc_aware = self.date_utils.normalize_to_utc(data.end_time, tz_name=data.time_zone)
        start_utc_naive = start_utc_aware.replace(tzinfo=None)
        end_utc_naive = end_utc_aware.replace(tzinfo=None)

        db_event = Event(
            title=data.title,
            start_time=start_utc_naive,
            end_time=end_utc_naive,
            time_zone=data.time_zone,
            location=data.location,
            rrule=data.rrule,
            notes=data.notes,
        )
        session.add(db_event)
        await session.flush()
        logger.debug(f"Flushed event '{db_event.title}', assigned ID: {db_event.id}")

        # 3. Create Participants
        if data.participant_identifiers:
            participants = [
                Participant(event_id=db_event.id, identifier=identifier)
                for identifier in data.participant_identifiers
            ]
            session.add_all(participants)
            logger.debug(f"Added {len(participants)} participants for event ID: {db_event.id}")
            await session.flush()
            await session.refresh(db_event, attribute_names=["participants"])
            logger.debug(f"Refreshed participants relation for event ID: {db_event.id}")

        # 4. Refresh and Return
        # Re-query to ensure all relationships are loaded correctly before returning
        # This might be slightly inefficient but ensures the returned object is complete
        stmt = select(Event).options(
            selectinload(Event.participants)
            # Add other relationships if needed
            ).where(Event.id == db_event.id)
        result = await session.execute(stmt)
        refreshed_event = result.scalar_one() # Use scalar_one assuming event must exist
        logger.info(f"Refreshed event '{refreshed_event.title}' (ID: {refreshed_event.id}) with participants.")

        return refreshed_event

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """
        Handle failure for the CreateEventStrategy.

        Currently, only logs the event. The actual database rollback is handled
        by the session context manager in the base class.

        Args:
            session: The database session.
            exception: The exception that triggered the rollback.
        """
        logger.warning(
            f"_failure called for CreateEventStrategy due to: {exception.__class__.__name__}. "
            f"Session hash: {hash(session)}."
        )
        # No specific cleanup logic required for this strategy