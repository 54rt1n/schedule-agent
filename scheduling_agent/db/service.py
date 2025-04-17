from typing import List, Optional
import uuid
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.orm import selectinload
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_async_session
from .models import Event, Participant, EventCreate, ExcludedDate, ModifiedInstance, EventUpdate
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.logging_config import get_logger
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exceptions import ConflictError

logger = get_logger(__name__)

class DatabaseService:
    """Handles database operations for the scheduling agent."""

    def __init__(self, date_utils: Optional[DateUtils] = None, conflict_resolver: Optional[ConflictResolver] = None):
        """Initialize the service, optionally injecting dependencies."""
        # Allow injecting DateUtils for easier testing, create if not provided
        self.date_utils = date_utils or DateUtils()
        # Allow injecting ConflictResolver, or initialize it here
        # Note: ConflictResolver depends on DatabaseService, creating a potential circular dependency
        # if initialized here directly. It's better to inject it from where DatabaseService is created.
        # For now, we assume it will be injected.
        if conflict_resolver is None:
            logger.warning("ConflictResolver not injected into DatabaseService. Conflict checking disabled.")
            self.conflict_resolver = None
        else:
            self.conflict_resolver = conflict_resolver

    async def create_event(self, event_data: EventCreate) -> Event:
        """Creates a new event in the database after checking for conflicts.

        Args:
            event_data: Data for the new event.

        Returns:
            The newly created Event database object.

        Raises:
            ConflictError: If a scheduling conflict is detected.
        """
        async with get_async_session() as session:
            # 1. Check for conflicts BEFORE attempting to create
            if self.conflict_resolver:
                conflicts = await self.conflict_resolver.detect_conflicts(event_data)
                if conflicts:
                    logger.warning("Conflict detected, aborting event creation", conflicts=conflicts)
                    # Raise specific error with conflict details
                    raise ConflictError(conflicts=conflicts)
            else:
                logger.warning("ConflictResolver not available, skipping conflict check.")

            # 2. If no conflicts, proceed with creation
            # Convert EventCreate to dict, exclude fields not in Event, then validate
            event_dict = event_data.model_dump(exclude_unset=True)
            event_dict.pop('participant_identifiers', None) # Remove if exists
            db_event = Event.model_validate(event_dict)

            session.add(db_event)
            await session.commit()
            await session.refresh(db_event)

            # Handle participant creation
            if event_data.participant_identifiers:
                for identifier in event_data.participant_identifiers:
                    participant = Participant(event_id=db_event.id, identifier=identifier)
                    session.add(participant)
                # Commit participants and refresh the event to load the relationship
                await session.commit()
                await session.refresh(db_event, attribute_names=["participants"])

            return db_event

    async def get_event_by_id(self, event_id: uuid.UUID) -> Optional[Event]:
        """Retrieves a single event by its unique ID, including relationships."""
        async with get_async_session() as session:
            result = await session.execute(
                select(Event)
                .where(Event.id == event_id)
                # Eagerly load relationships often needed together
                .options(
                    selectinload(Event.participants),
                    selectinload(Event.excluded_dates),
                    selectinload(Event.modified_instances)
                )
            )
            event = result.scalar_one_or_none()
            return event

    async def get_events_in_range(self, start: datetime, end: datetime) -> List[Event]:
        """Retrieves events that overlap with the given time range, including recurrences."""
        # Ensure input datetimes are naive UTC (or handle aware inputs)
        if start.tzinfo is not None:
            start_utc = self.date_utils.normalize_to_utc(start)
            start = start_utc.replace(tzinfo=None)
        if end.tzinfo is not None:
            end_utc = self.date_utils.normalize_to_utc(end)
            end = end_utc.replace(tzinfo=None)

        # Convert range times back to aware UTC for dateutil calculations
        range_start_aware_utc = start.replace(tzinfo=timezone.utc)
        range_end_aware_utc = end.replace(tzinfo=timezone.utc)

        final_event_instances = []
        processed_recurring_base_ids = set()

        async with get_async_session() as session:
            # 1. Get non-recurring events directly overlapping the range
            non_recurring_stmt = (
                select(Event)
                .where(
                    Event.rrule == None,
                    Event.start_time <= end,
                    Event.end_time >= start
                )
            )
            result_non_recurring = await session.execute(non_recurring_stmt)
            final_event_instances.extend(result_non_recurring.scalars().all())

            # 2. Get potentially relevant recurring events (base definitions)
            # We need events whose start time is before the range END, and
            # which don't have an UNTIL date before the range START (this requires parsing rrule - complex)
            # Simplification: Fetch all recurring events for now, filter during expansion.
            # Optimization: A better query would filter based on RRULE properties if possible.
            recurring_base_stmt = (
                select(Event)
                .where(Event.rrule != None)
                # Eager load exceptions needed for expansion
                .options(
                    selectinload(Event.excluded_dates),
                    selectinload(Event.modified_instances)
                )
            )
            result_recurring_base = await session.execute(recurring_base_stmt)
            recurring_base_events = result_recurring_base.scalars().unique().all()

            # 3. Expand recurring events
            for base_event in recurring_base_events:
                if base_event.id in processed_recurring_base_ids:
                    continue

                expanded_occurrences_data = self.date_utils.expand_recurrence(
                    base_event=base_event,
                    range_start_utc=range_start_aware_utc,
                    range_end_utc=range_end_aware_utc,
                    excluded_dates=base_event.excluded_dates, # Pass eagerly loaded data
                    modified_instances=base_event.modified_instances # Pass eagerly loaded data
                )

                # Convert expanded dicts back to Event-like objects (or just use dicts)
                # For simplicity, let's create Event objects for the return type consistency
                # We only include essential fields, not full DB model state.
                for occ_data in expanded_occurrences_data:
                    # Create a temporary Event object from the occurrence data
                    # Ensure datetimes are naive UTC as stored in DB
                    occ_start_naive = occ_data['start_time'].replace(tzinfo=None)
                    occ_end_naive = occ_data['end_time'].replace(tzinfo=None)

                    # Filter again to ensure the calculated occurrence actually overlaps
                    if occ_start_naive <= end and occ_end_naive >= start:
                        instance_event = Event(
                            id=occ_data.get('modified_instance_id') or base_event.id, # Use mod ID if exists
                            title=occ_data['title'],
                            start_time=occ_start_naive,
                            end_time=occ_end_naive,
                            time_zone=occ_data['time_zone'],
                            location=occ_data['location'],
                            notes=occ_data['notes'],
                            rrule=None, # Instances don't have rrule
                            # Copy created_at/updated_at from base? Or leave default?
                            # Let's use base for now, could add instance_start_time field later
                            created_at=base_event.created_at,
                            updated_at=base_event.updated_at,
                            # TODO: How to handle participants for instances? Copy from base?
                        )
                        final_event_instances.append(instance_event)

                processed_recurring_base_ids.add(base_event.id)

        # 4. Sort final list by start time
        final_event_instances.sort(key=lambda evt: evt.start_time)

        return final_event_instances

    async def update_event(self, event_id: uuid.UUID, update_data: EventUpdate) -> Optional[Event]:
        """Updates an existing event.

        Args:
            event_id: The ID of the event to update.
            update_data: An EventUpdate model with fields to update.

        Returns:
            The updated Event object, or None if the event was not found.
        """
        async with get_async_session() as session:
            # Retrieve the existing event
            db_event = await session.get(Event, event_id)
            if not db_event:
                return None

            # Get the update data as a dictionary, excluding unset fields
            update_dict = update_data.model_dump(exclude_unset=True)

            # Update the model fields
            for key, value in update_dict.items():
                setattr(db_event, key, value)
            
            # Mark as modified and commit
            session.add(db_event)
            await session.commit()
            await session.refresh(db_event)
            # TODO: Handle participant updates (add/remove)
            return db_event

    async def delete_event(self, event_id: uuid.UUID) -> bool:
        """Deletes an event by its ID.

        Args:
            event_id: The ID of the event to delete.

        Returns:
            True if the event was deleted, False if it was not found.
        """
        async with get_async_session() as session:
            db_event = await session.get(Event, event_id)
            if not db_event:
                return False
            
            await session.delete(db_event)
            await session.commit()
            return True

    # TODO: Implement other methods:
    # async def find_conflicts(...) -> List[Event]: ...
    # etc.