import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, sessionmaker
from sqlmodel import select

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.core.exceptions import TaskNotFoundError
from scheduling_agent.logging_config import get_logger

from .database import get_async_session

# Import DB models from .models
from .models import Event, Participant, A2ATask, ModifiedInstance
# Import A2A types
from scheduling_agent.a2a import types as a2a_types

# Import DTO schemas from .schemas
from .schemas import EventCreate, EventUpdate, EventQuery

logger = get_logger(__name__)

# TODO: This service is very complex and needs to be broken down into smaller, more manageable modules.

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

    async def find_events(self, query: EventQuery) -> list[Event]:
        """Finds events based on criteria in the EventQuery object."""
        logger.info("Finding events based on query", query=query.model_dump(exclude_unset=True))
        async with get_async_session(self.session_factory) as session:
            stmt = select(Event)

            # Basic filtering - Start with title if provided
            if query.title:
                # Use ilike for case-insensitive partial matching
                stmt = stmt.where(Event.title.ilike(f"%{query.title}%"))

            # TODO: Add filtering for other EventQuery fields (start_time, end_time, participants, etc.)
            # This might involve logic similar to get_events_in_range for time overlaps

            # Add options to load related data if needed by callers
            stmt = stmt.options(
                selectinload(Event.participants),
                selectinload(Event.excluded_dates),
                selectinload(Event.modified_instances),
            )

            result = await session.execute(stmt)
            events = list(result.scalars().unique().all())
            logger.info(f"Found {len(events)} events matching query", query=query.model_dump(exclude_unset=True))
            return events

    # --- Phase 2 Implementation: Update Event --- #

    async def _handle_recurring_instance_update(
        self,
        session: AsyncSession, # Need the session passed in
        db_event: Event, # Pass the fetched base event
        update_data: EventUpdate
    ) -> ModifiedInstance | None: # Return the actual ModifiedInstance
        """Handles updating a specific instance of a recurring event."""
        logger.info(
            "Handling update for specific instance of recurring event",
            event_id=db_event.id,
            original_start_time=update_data.original_start_time
        )

        # 1. Validate input
        if not update_data.original_start_time:
            # This should ideally be caught earlier or by routing logic
            logger.error("original_start_time missing for recurring instance update", event_id=db_event.id)
            raise ValueError("original_start_time is required to update a specific recurring instance.")
        if not update_data.start_time or not update_data.end_time or not update_data.time_zone:
            logger.error("New start_time, end_time, and time_zone are required when updating an instance time", event_id=db_event.id)
            raise ValueError("New start_time, end_time, and time_zone are required when updating an instance time.")

        # Convert original_start_time to naive UTC for DB comparison
        original_start_naive_utc = self.date_utils.normalize_to_utc(
            update_data.original_start_time, tz_name=db_event.time_zone # Use base event's TZ for original time
        ).replace(tzinfo=None)

        # Convert new times to naive UTC using the *newly* provided timezone
        new_start_naive_utc = self.date_utils.normalize_to_utc(
            update_data.start_time, tz_name=update_data.time_zone
        ).replace(tzinfo=None)
        new_end_naive_utc = self.date_utils.normalize_to_utc(
            update_data.end_time, tz_name=update_data.time_zone
        ).replace(tzinfo=None)

        # TODO: Add conflict checking for the *new* time slot
        # This requires adapting the conflict checker or creating a temporary event data object

        # 2. Find or Create ModifiedInstance record
        stmt = select(ModifiedInstance).where(
            ModifiedInstance.event_id == db_event.id,
            ModifiedInstance.original_date == original_start_naive_utc
        )
        result = await session.execute(stmt)
        mod_instance = result.scalar_one_or_none()

        update_dict = update_data.model_dump(exclude_unset=True)
        # Remove fields specific to identifying the instance
        update_dict.pop('original_start_time', None)

        if mod_instance:
            logger.debug("Found existing ModifiedInstance, updating", mod_instance_id=mod_instance.id)
            # Update existing instance
            mod_instance.start_time = new_start_naive_utc
            mod_instance.end_time = new_end_naive_utc
            mod_instance.time_zone = update_data.time_zone # Update timezone
            # Apply other optional updates
            mod_instance.title = update_dict.get('title', mod_instance.title)
            mod_instance.location = update_dict.get('location', mod_instance.location)
            mod_instance.notes = update_dict.get('notes', mod_instance.notes)
            # updated_at handled by DB trigger
        else:
            logger.debug("Creating new ModifiedInstance")
            # Create new instance
            mod_instance = ModifiedInstance(
                event_id=db_event.id,
                original_date=original_start_naive_utc,
                start_time=new_start_naive_utc,
                end_time=new_end_naive_utc,
                time_zone=update_data.time_zone,
                # Apply optional fields or use base event defaults?
                # Let's use the update payload, falling back to base event
                title=update_dict.get('title', db_event.title),
                location=update_dict.get('location', db_event.location),
                notes=update_dict.get('notes', db_event.notes),
            )
            session.add(mod_instance)

        # 3. Commit changes (assuming outer method handles commit)
        # await session.commit() # Commit happens in the calling update_event

        # Return the persistent ModifiedInstance object
        return mod_instance

    async def update_event(self, event_id: uuid.UUID, update_data: EventUpdate) -> Event | ModifiedInstance | None:
        """Updates an existing event, handling non-recurring and single recurring instances."""
        # Args:
        #     event_id: The ID of the event to update.
        #     update_data: An EventUpdate model with fields to update.
        # Returns:
        #     The updated Event object (or instance representation), or None if not found.
        # Raises:
        #     NotImplementedError: If attempting to update a recurring series or time on non-recurring.
        #     ValueError: If required fields for instance update are missing.

        async with get_async_session(self.session_factory) as session:
            # 1. Retrieve the existing event
            # Eager load modified instances needed for recurrence checks if applicable
            # We might need it if we enhance instance update logic later
            result = await session.execute(
                select(Event).where(Event.id == event_id).options(selectinload(Event.modified_instances))
            )
            db_event = result.scalar_one_or_none()

            if not db_event:
                logger.warning("Attempted to update non-existent event", event_id=event_id)
                return None

            # 2. Determine update type (base, instance, series)
            is_instance_update = update_data.original_start_time is not None
            # is_series_update = ... # Logic to determine series update (e.g., specific flag or only rrule change)

            if is_instance_update:
                if not db_event.rrule:
                    logger.error("Attempted instance update on non-recurring event", event_id=event_id)
                    raise ValueError("Cannot specify original_start_time for a non-recurring event.")
                # Delegate to instance handler
                # Pass the session to the helper
                updated_instance = await self._handle_recurring_instance_update(session, db_event, update_data)
                # updated_instance is now the persistent ModifiedInstance
                await session.commit() # Commit changes made by helper
                # Refresh might be needed if the helper didn't
                if updated_instance: await session.refresh(updated_instance) # Refresh the persistent object
                return updated_instance
            elif db_event.rrule: # Handle attempt to update a recurring event *without* specifying instance/series
                # This is now treated as a potential simple series update
                logger.debug("Handling potential simple series update", event_id=event_id)
                update_dict = update_data.model_dump(exclude_unset=True)

                # Disallow time/timezone updates for the whole series for now
                if "start_time" in update_dict or "end_time" in update_dict or "time_zone" in update_dict:
                    logger.error("Updating time/timezone for the entire series is not supported yet.", event_id=event_id)
                    # TODO: This might require deleting all ModifiedInstances, or more complex logic
                    raise NotImplementedError("Updating time/timezone for the entire series is not supported yet.")

                # Apply allowed updates (title, location, notes, rrule?)
                updated = False
                allowed_series_fields = ["title", "location", "notes", "rrule"]
                for key, value in update_dict.items():
                    if key in allowed_series_fields and hasattr(db_event, key) and getattr(db_event, key) != value:
                        setattr(db_event, key, value)
                        updated = True

                if updated:
                    # If updating the series, should we clear existing ModifiedInstances?
                    # For now, let's assume simple updates don't invalidate instances.
                    # TODO: Revisit this assumption - changing RRULE definitely invalidates instances.
                    # if "rrule" in update_dict:
                    #     logger.warning("Updating RRULE - existing modifications might be invalidated", event_id=event_id)
                    #     # stmt = delete(ModifiedInstance).where(ModifiedInstance.event_id == event_id)
                    #     # await session.execute(stmt)
                    session.add(db_event)
                    await session.commit()
                    await session.refresh(db_event)
                    logger.info("Successfully updated recurring event series (simple fields)", event_id=event_id)
                else:
                    logger.info("No changes detected for recurring series update", event_id=event_id)

                return db_event # Return the updated base event

            else: # Handle non-recurring event update (existing logic)
                logger.debug("Handling update for non-recurring event", event_id=event_id)
                update_dict = update_data.model_dump(exclude_unset=True)

                # Flag to track if time-related fields are updated
                time_updated = False
                new_start_time = db_event.start_time
                new_end_time = db_event.end_time
                new_time_zone = db_event.time_zone

                if "time_zone" in update_dict:
                    new_time_zone = update_dict["time_zone"]
                    time_updated = True
                if "start_time" in update_dict:
                    new_start_time = update_dict["start_time"]
                    time_updated = True
                if "end_time" in update_dict:
                    new_end_time = update_dict["end_time"]
                    time_updated = True

                # Perform conflict check ONLY if time was updated and resolver exists
                if time_updated and self.conflict_resolver:
                    logger.debug("Time fields updated, checking for conflicts", event_id=event_id)
                    # Create a temporary EventCreate object for conflict checking
                    # Use existing non-time fields + new time fields
                    conflict_check_data = EventCreate(
                        title=update_dict.get("title", db_event.title),
                        start_time=new_start_time, # Use potentially updated time
                        end_time=new_end_time,     # Use potentially updated time
                        time_zone=new_time_zone,   # Use potentially updated timezone
                        location=update_dict.get("location", db_event.location),
                        notes=update_dict.get("notes", db_event.notes),
                        rrule=None, # Non-recurring
                        # TODO: Handle participant conflicts?
                    )
                    conflicts = await self.conflict_resolver.detect_conflicts(conflict_check_data, ignore_event_id=event_id)
                    if conflicts:
                        logger.warning(
                            "Conflict detected during non-recurring event update", event_id=event_id, conflicts=conflicts
                        )
                        raise ConflictError(conflicts=conflicts)
                elif time_updated:
                    logger.warning("Time fields updated but no conflict resolver available, skipping check.", event_id=event_id)

                # Apply updates if no conflict error was raised
                updated = False
                for key, value in update_dict.items():
                    if hasattr(db_event, key) and getattr(db_event, key) != value:
                        # Normalize time fields before setting
                        if key in ["start_time", "end_time"]:
                            # Use the potentially updated timezone for normalization
                            normalized_dt = self.date_utils.normalize_to_utc(value, tz_name=new_time_zone)
                            setattr(db_event, key, normalized_dt.replace(tzinfo=None))
                        else:
                            setattr(db_event, key, value)
                        updated = True

                if updated:
                    session.add(db_event)
                    await session.commit()
                    await session.refresh(db_event)
                    logger.info("Successfully updated non-recurring event", event_id=event_id)
                else:
                    logger.info("No changes detected for non-recurring event update", event_id=event_id)

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

    # --- A2A Task Persistence Methods ---

    async def save_a2a_task(self, task: a2a_types.Task) -> None:
        """Saves (upserts) the state of an A2A Task to the database."""
        async with get_async_session(self.session_factory) as session:
            try:
                # Convert the A2A Task object to the DB model representation
                db_task_record = A2ATask(
                    id=task.id,
                    session_id=task.sessionId,
                    state=task.status.state.value, # Store the enum value
                    # Serialize Pydantic models/lists to JSON strings
                    status_message_json=task.status.message.model_dump_json(exclude_none=True)
                    if task.status and task.status.message
                    else None,
                    params_json=task.params.model_dump_json(exclude_none=True)
                    if task.params
                    else None,
                    # Fix: Explicitly check for empty list before serializing
                    artifacts_json=a2a_types.ArtifactsList(root=task.artifacts).model_dump_json() 
                    if task.artifacts 
                    else None, # Store None if list is empty or None
                    history_json=a2a_types.HistoryList(root=task.history).model_dump_json() 
                    if task.history 
                    else None, # Store None if list is empty or None
                    # created_at and updated_at are handled by the model defaults/onupdate
                )

                # Use merge for upsert behavior (insert or update)
                await session.merge(db_task_record)
                await session.commit()
                logger.debug("Saved A2A Task state to DB", task_id=task.id)
            except Exception as e:
                logger.exception(
                    "Database error occurred while saving A2A task",
                    task_id=task.id,
                    error=str(e),
                )
                # Rollback is handled by the async context manager
                raise # Re-raise the exception for upstream handling

    async def get_a2a_task_by_id(self, task_id: str) -> a2a_types.Task | None:
        """Retrieves an A2A Task by its ID, deserializing JSON fields."""
        async with get_async_session(self.session_factory) as session:
            # Fetch the raw DB record
            db_record = await session.get(A2ATask, task_id)

            if not db_record:
                return None

            # Deserialize JSON fields back into Pydantic models
            try:
                task_params = (
                    a2a_types.TaskSendParams.model_validate_json(db_record.params_json)
                    if db_record.params_json
                    else None
                )
                status_message = (
                    a2a_types.Message.model_validate_json(db_record.status_message_json)
                    if db_record.status_message_json
                    else None
                )
                # Use the helper RootModel types for list deserialization
                artifacts = (
                    a2a_types.ArtifactsList.model_validate_json(db_record.artifacts_json).root
                    if db_record.artifacts_json
                    else [] # Default to empty list if None/empty in DB
                )
                history = (
                    a2a_types.HistoryList.model_validate_json(db_record.history_json).root
                    if db_record.history_json
                    else [] # Default to empty list if None/empty in DB
                )

                # Reconstruct the TaskStatus
                task_status = a2a_types.TaskStatus(
                    state=a2a_types.TaskState(db_record.state), # Convert string back to enum
                    message=status_message,
                )

                # Reconstruct the full Task object
                task = a2a_types.Task(
                    id=db_record.id,
                    sessionId=db_record.session_id,
                    status=task_status,
                    params=task_params,
                    artifacts=artifacts,
                    history=history,
                    createdAt=db_record.created_at, # Assign timestamps
                    updatedAt=db_record.updated_at,
                    # message field in Task is optional, not stored directly in A2ATask model
                    message=None 
                )
                return task
            except Exception as e:
                logger.exception(
                    "Failed to deserialize A2A task data from DB",
                    task_id=task_id,
                    error=str(e),
                )
                # If deserialization fails, we can't return a valid task
                return None # Or raise a specific internal error

    async def update_a2a_task_status(self, task_id: str, status: a2a_types.TaskStatus) -> None:
        """Updates the status of an A2A Task and appends the old status to history."""
        async with get_async_session(self.session_factory) as session:
            try:
                # 1. Fetch the existing record
                db_record = await session.get(A2ATask, task_id)
                if not db_record:
                    raise TaskNotFoundError(task_id=task_id)

                # 2. Reconstruct the *old* status to add to history
                old_message = (
                    a2a_types.Message.model_validate_json(db_record.status_message_json)
                    if db_record.status_message_json
                    else None
                )
                old_status = a2a_types.TaskStatus(
                    state=a2a_types.TaskState(db_record.state),
                    message=old_message,
                )

                # 3. Load current history
                current_history = (
                    a2a_types.HistoryList.model_validate_json(db_record.history_json).root
                    if db_record.history_json
                    else []
                )

                # 4. Append the old status to history
                updated_history = current_history + [old_status]

                # 5. Update the record fields
                db_record.state = status.state.value
                db_record.status_message_json = (
                    status.message.model_dump_json(exclude_none=True)
                    if status.message
                    else None
                )
                db_record.history_json = a2a_types.HistoryList(root=updated_history).model_dump_json(exclude_none=True)
                db_record.updated_at = datetime.now(UTC).replace(tzinfo=None) # Explicitly update timestamp

                # 6. Add and commit
                session.add(db_record) # Add the modified record to the session
                await session.commit()
                logger.debug("Updated A2A Task status and history", task_id=task_id, new_state=status.state.value)

            except TaskNotFoundError: # Let TaskNotFound propagate
                logger.warning("Attempted to update status for non-existent task", task_id=task_id)
                raise
            except Exception as e:
                logger.exception(
                    "Database error occurred while updating A2A task status",
                    task_id=task_id,
                    error=str(e),
                )
                # Rollback is handled by the async context manager
                raise # Re-raise other exceptions

    # TODO: Implement other methods:
    # async def find_conflicts(...) -> List[Event]: ...
    # etc.
