import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import selectinload, sessionmaker
from sqlmodel import select

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.logging_config import get_logger

from .database import get_async_session

# Import DB models from .models
from .models import Event, Participant

# Import DTO schemas from .schemas
from .schemas import EventCreate, EventUpdate

logger = get_logger(__name__)


class DatabaseService:
    """Handles database operations for the scheduling agent."""

    def __init__(
        self,
        session_factory: sessionmaker,
        date_utils: DateUtils | None = None,
        conflict_resolver: ConflictResolver | None = None,
    ):
        """Initialize the service, optionally injecting dependencies."""
        if session_factory is None:
            raise ValueError("session_factory must be provided")
        self.session_factory = session_factory
        self.date_utils = date_utils or DateUtils()
        if conflict_resolver is None:
            logger.warning(
                "ConflictResolver not injected into DatabaseService. Conflict checking disabled."
            )
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
        async with get_async_session(self.session_factory) as session:
            # 1. Check for conflicts BEFORE attempting to create
            if self.conflict_resolver:
                conflicts = await self.conflict_resolver.detect_conflicts(event_data)
                if conflicts:
                    logger.warning(
                        "Conflict detected, aborting event creation", conflicts=conflicts
                    )
                    # Raise specific error with conflict details
                    raise ConflictError(conflicts=conflicts)
            else:
                logger.warning("ConflictResolver not available, skipping conflict check.")

            # 2. If no conflicts, proceed with creation
            # **Normalize start/end times to naive UTC before validation/saving**
            start_utc = self.date_utils.normalize_to_utc(
                event_data.start_time, tz_name=event_data.time_zone
            )
            end_utc = self.date_utils.normalize_to_utc(
                event_data.end_time, tz_name=event_data.time_zone
            )

            event_dict = event_data.model_dump(exclude_unset=True)
            event_dict["start_time"] = start_utc.replace(tzinfo=None)  # Store naive UTC
            event_dict["end_time"] = end_utc.replace(tzinfo=None)  # Store naive UTC
            event_dict.pop("participant_identifiers", None)  # Remove participant list if present

            # Validate the dictionary with normalized times into the DB model
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

    async def get_event_by_id(self, event_id: uuid.UUID) -> Event | None:
        """Retrieves a single event by its unique ID, including relationships."""
        async with get_async_session(self.session_factory) as session:
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
            return event

    async def get_events_in_range(self, start: datetime, end: datetime) -> list[Event]:
        """Retrieves events that overlap with the given time range, including recurrences."""
        # Ensure input datetimes are naive UTC (or handle aware inputs)
        if start.tzinfo is not None:
            start_utc = self.date_utils.normalize_to_utc(start)
            start = start_utc.replace(tzinfo=None)
        if end.tzinfo is not None:
            end_utc = self.date_utils.normalize_to_utc(end)
            end = end_utc.replace(tzinfo=None)

        # Convert range times back to aware UTC for dateutil calculations
        range_start_aware_utc = start.replace(tzinfo=UTC)
        range_end_aware_utc = end.replace(tzinfo=UTC)

        final_event_instances = []

        async with get_async_session(self.session_factory) as session:
            # Ensure input datetimes are naive UTC for comparison with DB
            start = start.replace(tzinfo=None) if start.tzinfo else start
            end = end.replace(tzinfo=None) if end.tzinfo else end
            range_start_aware_utc = start.replace(
                tzinfo=UTC
            )  # Keep aware versions for expansion
            range_end_aware_utc = end.replace(tzinfo=UTC)

            # 1. Get strictly non-recurring events within the range
            non_recurring_stmt = select(Event).where(
                Event.rrule.is_(None),  # Explicitly non-recurring
                Event.start_time <= end,  # Overlaps end of range
                Event.end_time >= start,  # Overlaps start of range
            )
            result_non_recurring = await session.execute(non_recurring_stmt)
            # Initialize the results ONLY with non-recurring events
            final_event_instances = list(result_non_recurring.scalars().all())

            # 2. Get base definitions of recurring events that could potentially overlap
            # Optimization: A better query would filter based on RRULE properties if possible.
            # Select recurring events whose start time is before the range ends (they might have instances in the range)
            recurring_base_stmt = (
                select(Event)
                .where(
                    Event.rrule.is_not(None),  # Explicitly recurring
                    Event.start_time <= end,  # Base start time is before range end
                    # Note: We don't filter by end_time here, as recurrence could extend beyond base end
                )
                # Eager load exceptions needed for expansion
                .options(selectinload(Event.excluded_dates), selectinload(Event.modified_instances))
            )
            result_recurring_base = await session.execute(recurring_base_stmt)
            recurring_base_events = result_recurring_base.scalars().unique().all()

            # 3. Expand recurring events and add ONLY valid instances to the list
            for base_event in recurring_base_events:
                # Do not add base_event itself to final_event_instances here

                expanded_occurrences_data = self.date_utils.expand_recurrence(
                    base_event=base_event,
                    range_start_utc=range_start_aware_utc,
                    range_end_utc=range_end_aware_utc,
                    excluded_dates=base_event.excluded_dates,  # Pass eagerly loaded data
                    modified_instances=base_event.modified_instances,  # Pass eagerly loaded data
                )

                # Convert expanded dicts back to Event-like objects (or just use dicts)
                # For simplicity, let's create Event objects for the return type consistency
                # We only include essential fields, not full DB model state.
                for occ_data in expanded_occurrences_data:
                    # Create a temporary Event object from the occurrence data
                    # Ensure datetimes are naive UTC as stored in DB
                    occ_start_naive = occ_data["start_time"].replace(tzinfo=None)
                    occ_end_naive = occ_data["end_time"].replace(tzinfo=None)

                    # Put the overlap check back in place
                    if occ_start_naive <= end and occ_end_naive >= start:
                        instance_event = Event(
                            # Generate a unique-ish ID for the instance? Or use base ID?
                            # Using base ID for now, but might need refinement if instances need unique IDs.
                            # Let's use base_event.id + original_date for uniqueness if needed
                            id=occ_data.get("modified_instance_id")
                            or base_event.id,  # Use modified ID if it exists
                            title=occ_data["title"],
                            start_time=occ_start_naive,
                            end_time=occ_end_naive,
                            time_zone=occ_data["time_zone"],
                            location=occ_data["location"],
                            notes=occ_data["notes"],
                            rrule=None,  # Instances don't have rrule
                            # Copy created_at/updated_at from base? Or leave default?
                            # Let's use base for now, could add instance_start_time field later
                            created_at=base_event.created_at,
                            updated_at=base_event.updated_at,
                            # TODO: How to handle participants for instances? Copy from base?
                        )
                        # Add the generated instance to the list IF it overlaps
                        final_event_instances.append(instance_event)

        # 4. Sort final list by start time
        final_event_instances.sort(key=lambda evt: evt.start_time)

        return final_event_instances

    async def update_event(self, event_id: uuid.UUID, update_data: EventUpdate) -> Event | None:
        """Updates an existing event.

        Args:
            event_id: The ID of the event to update.
            update_data: An EventUpdate model with fields to update.

        Returns:
            The updated Event object, or None if the event was not found.
        """
        async with get_async_session(self.session_factory) as session:
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
        async with get_async_session(self.session_factory) as session:
            db_event = await session.get(Event, event_id)
            if not db_event:
                return False

            await session.delete(db_event)
            await session.commit()
            return True

    # TODO: Implement other methods:
    # async def find_conflicts(...) -> List[Event]: ...
    # etc.
