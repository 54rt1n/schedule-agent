# sa/db/strategies/update_event.py
"""Strategy for updating an existing event or a recurring instance."""

import uuid
from datetime import datetime, timezone, UTC
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import update as sqlalchemy_update
from sqlmodel import select

from sa.core.exceptions import ConflictError, DatabaseError
from sa.core.logging_config import get_logger
from sa.db.dao import EventUpdate, EventResponse # Keep EventResponse for type hint clarity
from sa.db.models import Event, ModifiedInstance
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.event.conflict_resolver import ConflictResolver # Keep for potential future use
from sa.event.date_utils import DateUtils

logger = get_logger(__name__)

class UpdateEventStrategy(DatabaseExecutionStrategy[EventUpdate, Optional[EventResponse]]):
    """
    Strategy for updating an existing event or a recurring instance using explicit UPDATE statements.
    """

    def __init__(
        self,
        db_service: DatabaseService,
        date_utils: DateUtils,
        conflict_resolver: ConflictResolver, # Keep even if unused for now
    ):
        """Initialize the strategy with dependencies."""
        super().__init__(db_service=db_service)
        self.date_utils = date_utils
        self.conflict_resolver = conflict_resolver

    async def _run(
        self, session: AsyncSession, data: tuple[uuid.UUID, EventUpdate]
    ) -> Optional[EventResponse]:
        """
        Core logic for updating the event within a transaction.

        Args:
            session: The database session provided by the base execute method.
            data: A tuple containing (event_id, update_data).

        Returns:
            The updated Event or ModifiedInstance object, or None if not found.

        Raises:
            ValueError: If input is invalid (e.g., instance update on non-recurring).
            NotImplementedError: For unsupported updates (e.g., series time change).
            ConflictError: If a conflict is detected during update (currently disabled).
        """
        event_id = data.id
        update_data = data
        logger.info(f"Executing UpdateEventStrategy (dict method) for ID: {event_id}")

        # --- Common: Retrieve the existing event first ---
        # We need it to check for existence and for RRULE/timezone context
        stmt_get = select(Event).where(Event.id == event_id)
        result_get = await session.execute(stmt_get)
        db_event = result_get.scalar_one_or_none()

        if not db_event:
            logger.warning(f"Event with ID {event_id} not found for update.")
            return None

        # --- Determine Update Type ---
        is_instance_update = update_data.original_start_time is not None

        if is_instance_update:
            # --- Handle Recurring Instance Update ---
            if not db_event.rrule:
                logger.error(f"Attempted instance update on non-recurring event ID: {event_id}")
                raise ValueError("Cannot specify original_start_time for a non-recurring event.")
            # Delegate to instance handler which will also use dict updates or insert
            updated_instance = await self._handle_recurring_instance_update(session, db_event, update_data)
            return updated_instance # Return result directly

        # --- Handle Series or Non-Recurring Update ---
        update_dict_raw = update_data.model_dump(exclude_none=True)
        values_to_update: dict[str, object | None] = {}

        if db_event.rrule:
            # --- Simple Series Update ---
            logger.debug(f"Handling simple series update for event ID: {event_id}")
            if "start_time" in update_dict_raw or "end_time" in update_dict_raw or "time_zone" in update_dict_raw or "rrule" in update_dict_raw:
                logger.error(f"Updating time/timezone/rrule for the entire series is not supported yet for event ID: {event_id}")
                raise NotImplementedError("Updating time/timezone/rrule for the entire series is not supported yet.")

            allowed_series_fields = {"title", "location", "notes"} # Set for faster lookup
            for key, value in update_dict_raw.items():
                if key in allowed_series_fields:
                    # Compare with current value to only update if changed (optional optimization)
                    if getattr(db_event, key) != value:
                        values_to_update[key] = value

        else:
            # --- Non-Recurring Event Update ---
            logger.debug("Handling update for non-recurring event", event_id=event_id)

            # Handle potential time/timezone updates
            new_start_time_naive: datetime | None = None
            new_end_time_naive: datetime | None = None
            new_time_zone = db_event.time_zone # Start with original timezone
            time_updated = False

            if "time_zone" in update_dict_raw:
                new_time_zone = update_dict_raw["time_zone"]
                values_to_update["time_zone"] = new_time_zone # Add to update dict
                time_updated = True

            if "start_time" in update_dict_raw:
                start_dt = update_dict_raw["start_time"]
                if start_dt is not None:
                    new_start_time_naive = self.date_utils.normalize_to_utc(
                        start_dt, tz_name=new_time_zone # Use potentially new timezone
                    ).replace(tzinfo=None)
                    values_to_update["start_time"] = new_start_time_naive
                else:
                    # We cannot set start_time to None due to NOT NULL constraint
                    logger.warning(f"Received None for start_time update for event {event_id}, skipping update due to NOT NULL constraint.")
                    # Do NOT add "start_time": None to values_to_update
                time_updated = True # Indicate time was part of the request

            if "end_time" in update_dict_raw:
                end_dt = update_dict_raw["end_time"]
                if end_dt is not None:
                    new_end_time_naive = self.date_utils.normalize_to_utc(
                        end_dt, tz_name=new_time_zone # Use potentially new timezone
                    ).replace(tzinfo=None)
                    values_to_update["end_time"] = new_end_time_naive
                else:
                    # We cannot set end_time to None due to NOT NULL constraint
                    logger.warning(f"Received None for end_time update for event {event_id}, skipping update due to NOT NULL constraint.")
                    # Do NOT add "end_time": None to values_to_update
                time_updated = True # Indicate time was part of the request

            # Add other non-time fields from the raw dict
            for key, value in update_dict_raw.items():
                if key not in {"start_time", "end_time", "time_zone"}:
                    # Compare with current value (optional optimization)
                    if hasattr(db_event, key) and getattr(db_event, key) != value:
                         values_to_update[key] = value

            # TODO: Add Conflict Checking logic here if needed, using new times/info
            # if time_updated and self.conflict_resolver:
            #    ...

        # --- Execute Update if changes were prepared ---
        if not values_to_update:
            logger.info(f"No effective changes detected for event update {event_id}, skipping DB update.")
            # Return the original unmodified event since nothing changed
            return db_event

        # Add updated_at timestamp
        values_to_update["updated_at"] = datetime.now(UTC).replace(tzinfo=None)
        if 'id' in values_to_update:
            values_to_update.pop('id')

        logger.debug(f"Executing DB UPDATE for event {event_id} with values: {values_to_update}")
        stmt_update = (
            sqlalchemy_update(Event)
            .where(Event.id == event_id)
            .values(**values_to_update)
        )
        result = await session.execute(stmt_update)

        if result.rowcount == 0:
            # Should not happen if we found the event earlier, but good practice
            logger.error(f"UPDATE statement affected 0 rows for event ID {event_id}. Event might have been deleted concurrently.")
            return None
        elif result.rowcount > 1:
             logger.error(f"UPDATE statement affected multiple ({result.rowcount}) rows for event ID {event_id}. This should not happen.")
             # Continue, but log error

        # Since session.execute(update()) doesn't update the Python object,
        # we need to fetch the updated event again to return the correct state.
        # Use a clean statement, avoid options() unless necessary.
        stmt_refetch = select(Event).where(Event.id == event_id)
        result_refetch = await session.execute(stmt_refetch)
        updated_db_event = result_refetch.scalar_one_or_none()

        if updated_db_event:
            logger.info(f"Successfully updated event {event_id} using dict method.")
        else:
             # This is highly unlikely if rowcount > 0, but handle defensively
            logger.error(f"Failed to refetch event {event_id} after successful UPDATE statement.")

        return updated_db_event # Return the fresh state

    async def _handle_recurring_instance_update(
        self, session: AsyncSession, db_event: Event, update_data: EventUpdate
    ) -> Optional[ModifiedInstance]:
        """Handles creating/updating a ModifiedInstance for a specific recurring event instance."""
        logger.debug(
            "Handling instance update within strategy",
            event_id=db_event.id,
            original_start_time=update_data.original_start_time
        )

        # --- Validation ---
        if not update_data.original_start_time:
            # Should be caught earlier, but double-check
            raise ValueError("original_start_time is required to update a specific recurring instance.")
        # For instance time updates, all new time components are required
        is_time_update = update_data.start_time or update_data.end_time or update_data.time_zone
        if is_time_update and not (update_data.start_time and update_data.end_time and update_data.time_zone):
            raise ValueError("New start_time, end_time, and time_zone are required when updating an instance time.")

        # --- Normalize Times ---
        original_start_naive_utc = self.date_utils.normalize_to_utc(
            update_data.original_start_time, tz_name=db_event.time_zone # Use original event's TZ
        ).replace(tzinfo=None)

        new_start_naive_utc: Optional[datetime] = None
        new_end_naive_utc: Optional[datetime] = None
        new_time_zone: Optional[str] = None

        if is_time_update:
            new_start_naive_utc = self.date_utils.normalize_to_utc(
                update_data.start_time, tz_name=update_data.time_zone # Use NEW timezone
            ).replace(tzinfo=None)
            new_end_naive_utc = self.date_utils.normalize_to_utc(
                update_data.end_time, tz_name=update_data.time_zone # Use NEW timezone
            ).replace(tzinfo=None)
            new_time_zone = update_data.time_zone

        # --- Find Existing Modification or Prepare New ---
        stmt_find_mod = select(ModifiedInstance).where(
            ModifiedInstance.event_id == db_event.id,
            ModifiedInstance.original_date == original_start_naive_utc
        )
        result_find_mod = await session.execute(stmt_find_mod)
        mod_instance = result_find_mod.scalar_one_or_none()

        # Prepare dictionary of updates requested by the user
        update_dict_raw = update_data.model_dump(exclude_none=True)
        update_dict_raw.pop('original_start_time', None) # Not needed for update/insert

        if mod_instance:
            # --- Update Existing ModifiedInstance ---
            logger.debug(f"Found existing ModifiedInstance, updating mod_instance_id={mod_instance.id}")
            values_to_update: dict[str, object | None] = {}

            # Apply specific fields from update_dict_raw
            if 'title' in update_dict_raw: values_to_update['title'] = update_dict_raw['title']
            if 'location' in update_dict_raw: values_to_update['location'] = update_dict_raw['location']
            if 'notes' in update_dict_raw: values_to_update['notes'] = update_dict_raw['notes']

            # Apply time updates if they happened
            if is_time_update:
                values_to_update['start_time'] = new_start_naive_utc
                values_to_update['end_time'] = new_end_naive_utc
                values_to_update['time_zone'] = new_time_zone

            if not values_to_update:
                logger.info(f"No effective changes for existing ModifiedInstance {mod_instance.id}, skipping DB update.")
                return mod_instance # Return existing unmodified instance

            # Add updated_at timestamp
            values_to_update["updated_at"] = datetime.now(UTC).replace(tzinfo=None)

            stmt_update_mod = (
                sqlalchemy_update(ModifiedInstance)
                .where(ModifiedInstance.id == mod_instance.id)
                .values(**values_to_update)
            )
            await session.execute(stmt_update_mod)

            # Refetch the updated instance
            stmt_refetch_mod = select(ModifiedInstance).where(ModifiedInstance.id == mod_instance.id)
            result_refetch_mod = await session.execute(stmt_refetch_mod)
            updated_mod_instance = result_refetch_mod.scalar_one_or_none()
            logger.info(f"Successfully updated ModifiedInstance {mod_instance.id}")
            return updated_mod_instance

        else:
            # --- Create New ModifiedInstance ---
            logger.debug(f"Creating new ModifiedInstance for event_id={db_event.id}")
            # MUST provide all required fields for the new instance
            new_mod_instance = ModifiedInstance(
                event_id=db_event.id,
                original_date=original_start_naive_utc,
                # Use new times if provided, otherwise use original event's times (normalized)
                start_time=new_start_naive_utc if is_time_update else db_event.start_time,
                end_time=new_end_naive_utc if is_time_update else db_event.end_time,
                time_zone=new_time_zone if is_time_update else db_event.time_zone,
                # Use updated values if provided, otherwise fallback to original event values
                title=update_dict_raw.get('title', db_event.title),
                location=update_dict_raw.get('location', db_event.location),
                notes=update_dict_raw.get('notes', db_event.notes),
                # created_at/updated_at have defaults
            )
            session.add(new_mod_instance)
            # Flush to get the ID assigned
            await session.flush()
            # Refresh to load all fields correctly, especially defaults
            await session.refresh(new_mod_instance)
            logger.info(f"Successfully created new ModifiedInstance {new_mod_instance.id}")
            return new_mod_instance

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handles failure, primarily logging."""
        # The base strategy handles rollback. We just log.
        logger.warning(
            f"UpdateEventStrategy failed: {exception.__class__.__name__}. Session rollback initiated by base class."
        )
        # No specific cleanup needed here
