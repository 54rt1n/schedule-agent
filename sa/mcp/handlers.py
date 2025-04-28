# sa/mcp/handlers.py
"""Handlers for the Scheduling Agent MCP server."""

import uuid
import logging
from typing import Any, Dict, Optional, List

from mcp.server.fastmcp import FastMCP # Needed for type hint in register_handlers
from pydantic import ValidationError

from sa.db.dao import (
    EventCreate,
    EventDelete,
    EventQuery,
    EventUpdate,
    EventGetById,
)
from sa.core.exceptions import ConflictError, DatabaseError, EventNotFoundError
from sa.core.logging_config import get_logger
from sa.core.settings import Settings
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.delete_event import DeleteEventStrategy
from sa.db.strategies.find_events import FindEventsStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.db.strategies.update_event import UpdateEventStrategy
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils


logger = get_logger("scheduling_agent.mcp.handlers")

async def schedule_create_event(
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    title: str,
    start_time: str,
    end_time: str,
    time_zone: str,
    location: Optional[str],
    notes: Optional[str],
    rrule: Optional[str],
    participant_identifiers: Optional[list[str]],
) -> Dict[str, Any]:
    """
    Creates a new calendar event using structured data.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    # Logger is passed in, use it directly
    logger.info("Executing schedule_create_event logic", title=title, start=start_time)
    
    event_create = None

    try:
        event_data_dict = {
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "time_zone": time_zone, # Use passed-in timezone
            "location": location,
            "notes": notes,
            "rrule": rrule,
            "participant_identifiers": participant_identifiers,
        }
        # Validate input using Pydantic schema
        event_create = EventCreate.model_validate(event_data_dict)

        get_events_in_range_strategy = GetEventsInRangeStrategy(
            db_service=db_service,
            date_utils=date_utils,
        )

        create_event_strategy = CreateEventStrategy(
            db_service=db_service,
            date_utils=date_utils,
            conflict_resolver=conflict_resolver,
            get_events_in_range_strategy=get_events_in_range_strategy,
        )

        # Call the database service (passed as argument)
        created_event = await create_event_strategy.execute(data=event_create)

        logger.info(f"Event '{created_event.title}' created successfully. event_id={created_event.id}")
        return {
            "success": True,
            "event_id": str(created_event.id),
            "event": created_event.model_dump(mode="json"),
        }

    except ConflictError as ce:
        logger.warning(f"Conflict detected creating event '{title}': {ce.conflicts}")
        alternatives = []
        # Use conflict_resolver passed as argument
        if event_create:
            try:
                time_ranges = conflict_resolver.get_alternative_ranges(
                    proposed_event=event_create,
                    potential_conflicts=ce.conflicts,
                )

                events = []
                for time_range in time_ranges:
                    get_events = (time_range[0], time_range[1])
                    events = await get_events_in_range_strategy.execute(data=get_events)

                alternatives = await conflict_resolver.generate_alternative_times(
                    event_create,
                    time_ranges,
                    events,
                )
            except Exception as alt_err:
                logger.error(f"Failed to generate alternatives after conflict: {alt_err}")

        return {
            "success": False,
            "error": "Scheduling conflict detected.",
            "details": {
                "conflicts": ce.conflicts,
                "alternatives": alternatives,
            }
        }
    except ValidationError as ve:
        logger.warning(f"Invalid input data for create_event: {ve.errors()}")
        return {"success": False, "error": f"Invalid input data: {ve.errors()}"}
    except DatabaseError as dbe:
        logger.error(f"Database error during event creation: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_create_event: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


async def schedule_query_events(
    start_time: Optional[str],
    end_time: Optional[str],
    title_contains: Optional[str],
    db_service: DatabaseService,
) -> Dict[str, Any]:
    """
    Queries existing calendar events based on provided criteria.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    logger.info(
        "Executing schedule_query_events logic",
        start=start_time,
        end=end_time,
        title=title_contains,
    )
    try:
        query_data_dict = {
            "start_time": start_time,
            "end_time": end_time,
            "title_contains": title_contains,
        }
        # Validate input using Pydantic schema
        event_query_data = EventQuery.model_validate(query_data_dict)

        # Instantiate and execute the FindEventsStrategy
        find_events_strategy = FindEventsStrategy(db_service=db_service)
        # Note: FindEventsStrategy takes EventQuery as data and returns List[EventResponse]
        # EventResponse is compatible with Event model for serialization here.
        found_events = await find_events_strategy.execute(data=event_query_data)

        logger.info(f"Query found {len(found_events)} events.")
        return {
            "success": True,
            "count": len(found_events),
            "events": [evt.model_dump(mode="json") for evt in found_events],
        }

    except ValidationError as ve:
        logger.warning(f"Invalid input data for query_events: {ve.errors()}")
        return {"success": False, "error": f"Invalid input data: {ve.errors()}"}
    except DatabaseError as dbe:
        logger.error(f"Database error during event query: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_query_events: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


async def schedule_update_event(
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
    update_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Updates an existing calendar event using structured data.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    event_id = update_data.get("id", None)
    logger.info(f"Executing schedule_update_event logic for event_id={event_id} with updates={update_data}")
    try:
        # Validate UUID format
        try:
            event_uuid = uuid.UUID(event_id)
        except ValueError:
            raise ValueError(f"Invalid event_id format: '{event_id}'. Must be a valid UUID.")

        # Validate update_data using Pydantic schema
        event_update_data = EventUpdate.model_validate(update_data)

        # Instantiate and execute the UpdateEventStrategy
        update_event_strategy = UpdateEventStrategy(
            db_service=db_service,
            date_utils=date_utils,
            conflict_resolver=conflict_resolver,
        )

        # Execute the strategy
        updated_event_or_instance = await update_event_strategy.execute(data=event_update_data)

        if updated_event_or_instance:
             logger.info(f"Event '{event_id}' updated successfully.")
             return {
                "success": True,
                "event_id": event_id,
                # The strategy returns EventResponse, which is serializable
                "event": updated_event_or_instance.model_dump(mode="json"),
             }
        else:
            # Strategy returns None if event not found
            logger.warning(f"Update failed for event: {event_id}. Event not found by strategy.")
            # Raise EventNotFoundError to be caught by the handler's exception block
            raise EventNotFoundError(f"Event with ID '{event_id}' not found.")

    except EventNotFoundError as enfe:
        logger.warning(f"Event not found during update: {enfe}")
        return {"success": False, "error": str(enfe)}
    except ConflictError as ce:
        logger.warning(f"Conflict detected updating event '{event_id}': {ce.conflicts}")
        # Use conflict_resolver passed as argument
        alternatives = []
        # if conflict_resolver:
        #     try:
        #         # Fetch original event? Calculate proposed new times? Complex.
        #         # alternatives = await conflict_resolver.generate_alternative_times(...)
        #         pass # Placeholder
        #     except Exception as alt_err:
        #         logger.error(f"Failed to generate alternatives after update conflict: {alt_err}")
        return {
            "success": False,
            "error": "Scheduling conflict detected during update.",
            "details": {
                "conflicts": ce.conflicts, # Assuming conflicts are serializable
                "alternatives": alternatives, # Likely empty here
            }
        }
    except (ValidationError, ValueError) as ve: # Catch both Pydantic and manual ValueErrors
        logger.warning(f"Invalid input data for update_event: {ve}")
        error_detail = getattr(ve, 'errors', lambda: str(ve))() # Get Pydantic errors if available
        return {"success": False, "error": f"Invalid input data: {error_detail}"}
    except NotImplementedError as nie:
        logger.warning(f"Unsupported update operation for event '{event_id}': {nie}")
        return {"success": False, "error": str(nie)}
    except DatabaseError as dbe:
        logger.error(f"Database error during event update: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_update_event: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


async def schedule_delete_event(
    db_service: DatabaseService,
    event_id: str | uuid.UUID,
) -> Dict[str, Any]:
    """
    Deletes an existing calendar event by its ID.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    logger.info(f"Executing schedule_delete_event logic for event_id={event_id}")
    try:
        # Validate UUID format
        try:
            if isinstance(event_id, str):
                event_uuid = uuid.UUID(event_id)
            else:
                event_uuid = event_id
        except ValueError:
            raise ValueError(f"Invalid event_id format: '{event_id}'. Must be a valid UUID.")

        # Instantiate and execute the DeleteEventStrategy
        delete_event_strategy = DeleteEventStrategy(db_service=db_service)
        # Strategy expects EventDelete (UUID) as data and returns bool
        deleted = await delete_event_strategy.execute(data=EventDelete(id=event_uuid))

        if deleted:
            logger.info(f"Event '{event_id}' deleted successfully.")
            return {"success": True, "event_id": event_id}
        else:
            # Strategy returns False if event not found
            logger.warning(f"Event not found during delete: {event_id}")
            # Raise EventNotFoundError to be handled by the handler's exception block
            raise EventNotFoundError(f"Event with ID '{event_id}' not found for deletion.")

    except EventNotFoundError as enfe:
        logger.warning(f"Event not found for deletion: {enfe}")
        return {"success": False, "error": str(enfe)}
    except ValueError as ve: # Catch UUID validation error
        logger.warning(f"Invalid event ID format for delete: {ve}")
        return {"success": False, "error": str(ve)}
    except DatabaseError as dbe:
        logger.error(f"Database error during event deletion: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_delete_event: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


def register_handlers(
    mcp: FastMCP,
    db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
    settings: Settings,
):
    """Registers the schedule tool handlers with the MCP instance.
       Tool wrappers will get session factory from shared state and create services.
    """
    logger.info("Registering schedule tool handlers via wrappers accessing shared state...")

    # Wrapper for schedule_create_event
    @mcp.tool()
    async def create_event_tool(
        title: str,
        start_time: str,
        end_time: str,
        time_zone: Optional[str] = None,
        location: Optional[str] = None,
        notes: Optional[str] = None,
        rrule: Optional[str] = None,
        participant_identifiers: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        """Creates a new calendar event.
        
        Args:
            title: The title of the event.
            start_time: The start time of the event (ISO 8601 format, e.g., "2024-04-23T10:00:00Z").
            end_time: The end time of the event (ISO 8601 format).
            time_zone: The time zone for the event (e.g., "America/New_York"). Defaults to server setting if None.
            location: Optional location of the event.
            notes: Optional notes or description for the event.
            rrule: Optional recurrence rule (RFC 5545 RRULE string).
            participant_identifiers: Optional list of participant identifiers (e.g., email addresses).

        Returns:
            A dictionary containing the result, including success status and event details or error information.
        """
        # Get dependencies

        effective_time_zone = time_zone or settings.default_timezone
        # Call implementation
        return await schedule_create_event(
            title=title,
            start_time=start_time,
            end_time=end_time,
            time_zone=effective_time_zone,
            location=location,
            notes=notes,
            rrule=rrule,
            participant_identifiers=participant_identifiers,
            db_service=db_service,
            date_utils=date_utils,
            conflict_resolver=conflict_resolver,
        )

    # Wrapper for schedule_query_events
    @mcp.tool()
    async def query_events_tool(
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        title_contains: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queries existing calendar events.

        Args:
            start_time: Optional start time to filter events (ISO 8601 format). Filters for events overlapping this time.
            end_time: Optional end time to filter events (ISO 8601 format). Filters for events overlapping this time.
            title_contains: Optional string to filter events by title (case-insensitive partial match).

        Returns:
            A dictionary containing the result, including success status, count, and list of found events or error information.
        """
        # Call implementation
        return await schedule_query_events(
            start_time=start_time,
            end_time=end_time,
            title_contains=title_contains,
            db_service=db_service,
        )

    # Wrapper for schedule_update_event
    @mcp.tool()
    async def update_event_tool(
        event_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Updates an existing event identified by its unique ID."""
        return await schedule_update_event(
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
            event_id=event_id,
            update_data=update_data,
        )

    # Wrapper for schedule_delete_event
    @mcp.tool()
    async def delete_event_tool(
        event_id: str,
    ) -> Dict[str, Any]:
        """Deletes an existing event identified by its unique ID."""
        return await schedule_delete_event(
            db_service=db_service,
            event_id=event_id
        )

    logger.info("Schedule tool handlers registered.") 