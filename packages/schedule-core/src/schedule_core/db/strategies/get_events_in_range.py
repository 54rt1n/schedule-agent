# sa/db/strategies/get_events_in_range.py
# Copyright (C) 2025 Martin Bukowski

"""Strategy to retrieve event instances overlapping a given time range."""

from datetime import UTC
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select
from typing import List

from schedule_core.core.logging_config import get_logger
from schedule_core.db.models import Event
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.db.dao import EventResponse, TimeRange
from schedule_core.event.date_utils import DateUtils

logger = get_logger(__name__)

class GetEventsInRangeStrategy(DatabaseExecutionStrategy[TimeRange, List[EventResponse]]):
    """Strategy to retrieve event instances overlapping a given time range."""

    def __init__(self, db_service: DatabaseService, date_utils: DateUtils):
        """Initialize the strategy with dependencies."""
        super().__init__(db_service=db_service)
        self.date_utils = date_utils

    async def _run(self, session: AsyncSession, data: TimeRange) -> List[EventResponse]:
        """
        Fetches event instances overlapping the range within the transaction.

        Args:
            session: The database session.
            data: A TimeRange object containing start_time and end_time.
                  Input times should be timezone-aware.

        Returns:
            A list of Event objects representing instances within the range.
            These Event objects are transient (not representing persisted base events
            unless non-recurring) and contain the calculated instance times (naive UTC).
        """
        start = data.start_time
        end = data.end_time
        logger.debug(f"Executing GetEventsInRangeStrategy: Start={start.isoformat()}, End={end.isoformat()}")

        # 1. Normalize input range to naive UTC for DB queries
        start_naive_utc = self.date_utils.normalize_to_utc(start).replace(tzinfo=None)
        end_naive_utc = self.date_utils.normalize_to_utc(end).replace(tzinfo=None)
        logger.debug(f"Querying DB with naive UTC range: Start={start_naive_utc.isoformat()}, End={end_naive_utc.isoformat()}")

        # Keep aware UTC versions for recurrence expansion
        range_start_aware_utc = start_naive_utc.replace(tzinfo=UTC)
        range_end_aware_utc = end_naive_utc.replace(tzinfo=UTC)

        final_event_instances = []

        # 2. Get strictly non-recurring events directly overlapping the range
        non_recurring_stmt = select(Event).where(
            Event.rrule.is_(None),
            Event.start_time < end_naive_utc,
            Event.end_time > start_naive_utc,
        ).options(selectinload(Event.participants)) # Load participants if needed
        result_non_recurring = await session.execute(non_recurring_stmt)
        scalars = result_non_recurring.scalars()
        non_recurring_events = list(scalars.all())
        final_event_instances.extend(non_recurring_events)
        logger.debug(f"Found {len(non_recurring_events)} non-recurring events.")

        # 3. Get base definitions of potentially relevant recurring events
        recurring_base_stmt = (
            select(Event)
            .where(
                Event.rrule.is_not(None),
                Event.start_time < end_naive_utc, # Base start must be before range end
                # Add condition for UNTIL/COUNT if possible (optimisation)
            )
            .options(
                selectinload(Event.participants),
                selectinload(Event.excluded_dates),
                selectinload(Event.modified_instances)
            )
        )
        result_recurring_base = await session.execute(recurring_base_stmt)
        scalars = result_recurring_base.scalars()
        unique_scalars = scalars.unique()
        recurring_base_events = list(unique_scalars.all())
        logger.debug(f"Found {len(recurring_base_events)} potential recurring base events.")

        # 4. Expand recurrences and filter instances within the range
        for base_event in recurring_base_events:
            logger.debug(f"Expanding recurrence for base event {base_event.id}: {base_event.title}")
            try:
                expanded_occurrences_data = self.date_utils.expand_recurrence(
                    rrule_str=base_event.rrule,
                    base_event_id=base_event.id,
                    base_start_naive_utc=base_event.start_time,
                    base_end_naive_utc=base_event.end_time,
                    base_title=base_event.title,
                    base_location=base_event.location,
                    base_notes=base_event.notes,
                    base_timezone=base_event.time_zone,
                    range_start_utc=range_start_aware_utc,
                    range_end_utc=range_end_aware_utc,
                    excluded_dates=base_event.excluded_dates,
                    modified_instances=base_event.modified_instances,
                )
            except Exception as e:
                logger.exception(f"Error expanding recurrence for event {base_event.id}: {e}")
                continue # Skip this event if expansion fails

            # Filter expanded occurrences for overlap with the *original aware* query range
            for occ_data in expanded_occurrences_data:
                # Instance times from expand_recurrence are aware UTC
                occ_start_aware_utc = occ_data["start_time"]
                occ_end_aware_utc = occ_data["end_time"]

                # Overlap check with original aware range
                if occ_start_aware_utc < range_end_aware_utc and occ_end_aware_utc > range_start_aware_utc:
                    # Create a transient Event object for the instance
                    instance_event = Event(
                        # Use modified ID if it exists, else base ID
                        id=occ_data.get("modified_instance_id") or base_event.id,
                        title=occ_data["title"],
                        start_time=occ_start_aware_utc.replace(tzinfo=None), # Store naive UTC
                        end_time=occ_end_aware_utc.replace(tzinfo=None),   # Store naive UTC
                        time_zone=occ_data["time_zone"],
                        location=occ_data["location"],
                        notes=occ_data["notes"],
                        rrule=None, # Instances don't have RRULE
                        created_at=base_event.created_at,
                        updated_at=base_event.updated_at,
                        participants=base_event.participants, # Carry over participants
                    )
                    final_event_instances.append(instance_event)
                    logger.debug(
                        f"Added instance for event {base_event.id}: "
                        f"Start={instance_event.start_time.isoformat()}, "
                        f"End={instance_event.end_time.isoformat()}"
                    )

        # 5. Sort final list by start time
        final_event_instances.sort(key=lambda evt: evt.start_time)
        logger.info(f"GetEventsInRangeStrategy returning {len(final_event_instances)} instances.")

        return final_event_instances

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure (typically no-op for reads)."""
        logger.warning(
            f"_failure called for GetEventsInRangeStrategy due to: {exception.__class__.__name__}. "
            f"Read operations usually don't require specific failure logic."
        )
        # No specific failure logic required for this strategy