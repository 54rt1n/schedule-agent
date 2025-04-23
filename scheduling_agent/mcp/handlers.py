# scheduling_agent/mcp/handlers.py
import uuid
import logging
from typing import Any, Dict, Optional, List

from mcp.server.fastmcp import FastMCP # Needed for type hint in register_handlers
from pydantic import ValidationError

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exceptions import (
    ConflictError,
    DatabaseError,
    EventNotFoundError,
)
from scheduling_agent.db.schemas import (
    EventCreate,
    EventDelete,
    EventQuery,
    EventUpdate,
)
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.settings import settings

# Note: logger is now implicitly available or needs to be handled differently.
# Dependencies like db_service, conflict_resolver are now accessed via mcp.state

# These functions still accept dependencies explicitly for potential direct use/testing
# but the wrappers below will pull them from mcp.state.
async def schedule_create_event(
    title: str,
    start_time: str,
    end_time: str,
    time_zone: str,
    location: Optional[str],
    notes: Optional[str],
    rrule: Optional[str],
    participant_identifiers: Optional[list[str]],
    # Explicit dependencies
    logger: logging.Logger,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
) -> Dict[str, Any]:
    """
    Creates a new calendar event using structured data.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    # Logger is passed in, use it directly
    logger.info("Executing schedule_create_event logic", title=title, start=start_time)
    # Timezone is guaranteed by the wrapper now
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
        event_create_data = EventCreate.model_validate(event_data_dict)

        # Call the database service (passed as argument)
        created_event = await db_service.create_event(event_data=event_create_data)

        logger.info(f"Event '{created_event.title}' created successfully.", event_id=created_event.id)
        return {
            "success": True,
            "event_id": str(created_event.id),
            "event": created_event.model_dump(mode="json"),
        }

    except ConflictError as ce:
        logger.warning(f"Conflict detected creating event '{title}': {ce.conflicts}")
        alternatives = []
        # Use conflict_resolver passed as argument
        if 'event_create_data' in locals():
            try:
                alternatives = await conflict_resolver.generate_alternative_times(
                    event_create_data
                )
            except Exception as alt_err:
                logger.error(f"Failed to generate alternatives after conflict: {alt_err}")

        return {
            "success": False,
            "error": "Scheduling conflict detected.",
            "details": {
                "conflicts": ce.conflicts, # Assuming conflicts are serializable
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
    # Explicit dependencies
    logger: logging.Logger,
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

        # Call the database service (passed as argument)
        found_events = await db_service.find_events(query=event_query_data)

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
    event_id: str,
    update_data: Dict[str, Any],
    # Explicit dependencies
    logger: logging.Logger,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
) -> Dict[str, Any]:
    """
    Updates an existing calendar event using structured data.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    logger.info("Executing schedule_update_event logic", event_id=event_id, updates=update_data)
    try:
        # Validate UUID format
        try:
            event_uuid = uuid.UUID(event_id)
        except ValueError:
            raise ValueError(f"Invalid event_id format: '{event_id}'. Must be a valid UUID.")

        # Validate update_data using Pydantic schema
        event_update_data = EventUpdate.model_validate(update_data)

        # Call the database service (passed as argument)
        updated_event_or_instance = await db_service.update_event(
            event_id=event_uuid, update_data=event_update_data
        )

        if updated_event_or_instance:
             logger.info(f"Event '{event_id}' updated successfully.")
             return {
                "success": True,
                "event_id": event_id,
                "updated_details": updated_event_or_instance.model_dump(mode="json"),
             }
        else:
            # update_event returns None if event_id not found or other non-exception failure
            logger.warning(f"Update failed for event: {event_id}. Event might not exist or update was invalid.")
            # We rely on update_event to raise EventNotFoundError if appropriate
            # If it returns None without raising, treat as generic failure for now.
            # Consider specific exceptions from update_event if needed later.
            return {"success": False, "error": f"Failed to update event '{event_id}'. It may not exist or the update data was problematic."}


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
    event_id: str,
    # Explicit dependencies
    logger: logging.Logger,
    db_service: DatabaseService,
) -> Dict[str, Any]:
    """
    Deletes an existing calendar event by its ID.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    logger.info("Executing schedule_delete_event logic", event_id=event_id)
    try:
        # Validate UUID format
        try:
            event_uuid = uuid.UUID(event_id)
        except ValueError:
            raise ValueError(f"Invalid event_id format: '{event_id}'. Must be a valid UUID.")

        # Call the database service (passed as argument)
        deleted = await db_service.delete_event(event_id=event_uuid)

        if deleted:
            logger.info(f"Event '{event_id}' deleted successfully.")
            return {"success": True, "event_id": event_id}
        else:
            # delete_event returns False if not found
            logger.warning(f"Event not found for deletion: {event_id}")
            raise EventNotFoundError(event_id=event_id) # Raise consistent exception

    except EventNotFoundError as enfe:
        logger.warning(f"Event not found during deletion: {enfe}")
        return {"success": False, "error": str(enfe)}
    except ValueError as ve: # Catch UUID validation error
        logger.warning(f"Invalid input data for delete_event: {ve}")
        return {"success": False, "error": str(ve)}
    except DatabaseError as dbe:
        logger.error(f"Database error during event deletion: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_delete_event: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


# Updated register_handlers function
def register_handlers(
    mcp: FastMCP,
    # db_service: DatabaseService, # Removed
    # conflict_resolver: ConflictResolver, # Removed
    # logger: logging.Logger, # Removed
):
    """Registers the schedule tool handlers with the MCP instance.\n       Tool wrappers will get session factory from shared state and create services.\n    """
    # Get a logger for the handlers module itself
    handler_logger = logging.getLogger(__name__)
    handler_logger.info("Registering schedule tool handlers via wrappers accessing shared state...")

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
        try:
            from .server import _shared_state
            session_factory = _shared_state["session_factory"]
            date_utils = DateUtils()
            db_service = DatabaseService(session_factory=session_factory, date_utils=date_utils)
            conflict_resolver = ConflictResolver(db_service=db_service, date_utils=date_utils)
            db_service.conflict_resolver = conflict_resolver
        except KeyError:
            handler_logger.exception("Session factory not found in shared state.")
            return {"success": False, "error": "Internal server configuration error: Session factory not found."}
        except Exception as e:
            handler_logger.exception(f"Error creating services in create_event_tool: {e}")
            return {"success": False, "error": "Internal server configuration error creating services."}

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
            logger=handler_logger,
            db_service=db_service,
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
        # Get dependencies
        try:
            from .server import _shared_state
            session_factory = _shared_state["session_factory"]
            date_utils = DateUtils()
            db_service = DatabaseService(session_factory=session_factory, date_utils=date_utils)
            # Inject resolver if needed by db_service methods used in query?
            # conflict_resolver = ConflictResolver(db_service=db_service, date_utils=date_utils)
            # db_service.conflict_resolver = conflict_resolver
        except KeyError:
            handler_logger.exception("Session factory not found in shared state.")
            return {"success": False, "error": "Internal server configuration error: Session factory not found."}
        except Exception as e:
            handler_logger.exception(f"Error creating services in query_events_tool: {e}")
            return {"success": False, "error": "Internal server configuration error creating services."}

        # Call implementation
        return await schedule_query_events(
            start_time=start_time,
            end_time=end_time,
            title_contains=title_contains,
            logger=handler_logger,
            db_service=db_service,
        )

    # Wrapper for schedule_update_event
    @mcp.tool()
    async def update_event_tool(
        event_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Updates an existing calendar event.

        Args:
            event_id: The UUID of the event to update.
            update_data: A dictionary containing the fields to update (e.g., {"title": "New Title", "location": "New Location"}). 
                         See EventUpdate schema for valid fields.

        Returns:
            A dictionary containing the result, including success status and updated event details or error information.
        """
        # Get dependencies
        try:
            from .server import _shared_state
            session_factory = _shared_state["session_factory"]
            date_utils = DateUtils()
            db_service = DatabaseService(session_factory=session_factory, date_utils=date_utils)
            conflict_resolver = ConflictResolver(db_service=db_service, date_utils=date_utils)
            db_service.conflict_resolver = conflict_resolver
        except KeyError:
            handler_logger.exception("Session factory not found in shared state.")
            return {"success": False, "error": "Internal server configuration error: Session factory not found."}
        except Exception as e:
            handler_logger.exception(f"Error creating services in update_event_tool: {e}")
            return {"success": False, "error": "Internal server configuration error creating services."}
        
        # Call implementation
        return await schedule_update_event(
            event_id=event_id,
            update_data=update_data,
            logger=handler_logger,
            db_service=db_service,
            conflict_resolver=conflict_resolver,
        )

    # Wrapper for schedule_delete_event
    @mcp.tool()
    async def delete_event_tool(
        event_id: str,
    ) -> Dict[str, Any]:
        """Deletes an existing calendar event.

        Args:
            event_id: The UUID of the event to delete.

        Returns:
            A dictionary containing the result, including success status or error information.
        """
        # Get dependencies
        try:
            from .server import _shared_state
            session_factory = _shared_state["session_factory"]
            date_utils = DateUtils()
            db_service = DatabaseService(session_factory=session_factory, date_utils=date_utils)
            # Inject resolver if needed by db_service methods used in delete?
            # conflict_resolver = ConflictResolver(db_service=db_service, date_utils=date_utils)
            # db_service.conflict_resolver = conflict_resolver
        except KeyError:
            handler_logger.exception("Session factory not found in shared state.")
            return {"success": False, "error": "Internal server configuration error: Session factory not found."}
        except Exception as e:
            handler_logger.exception(f"Error creating services in delete_event_tool: {e}")
            return {"success": False, "error": "Internal server configuration error creating services."}

        # Call implementation
        return await schedule_delete_event(
            event_id=event_id,
            logger=handler_logger,
            db_service=db_service,
        )

    handler_logger.info("Schedule tool handlers registered.") 