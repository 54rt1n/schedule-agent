# sa/mcp/handlers.py
# Copyright (C) 2025 Martin Bukowski

"""Handlers for the Scheduling Agent MCP server."""

import uuid
from typing import Any, Dict, Optional, List

from mcp.server.fastmcp import FastMCP
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from sa.db.dao import (
    EventCreate,
    EventDelete,
    EventUpdate,
    ContactCreate,
    ContactDelete,
    ContactQuery,
    ContactUpdate,
    TimeRange,
)
from sa.core.exceptions import ConflictError, DatabaseError, EventNotFoundError, ContactNotFoundError, InvalidInputError
from sa.core.logging_config import get_logger
from sa.core.settings import Settings
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.delete_event import DeleteEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.db.strategies.update_event import UpdateEventStrategy
from sa.db.strategies.contact_create import CreateContactStrategy
from sa.db.strategies.contact_delete import DeleteContactStrategy
from sa.db.strategies.contact_find import FindContactsStrategy
from sa.db.strategies.contact_update import UpdateContactStrategy
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils


logger = get_logger("scheduling_agent.mcp.handlers")

# --- Event Handlers ---

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
    logger.info("Executing schedule_create_event logic", title=title, start=start_time)
    
    event_create = None

    try:
        event_data_dict = {
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "time_zone": time_zone,
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
    db_service: DatabaseService,
    date_utils: DateUtils,
    start_time: Optional[str],
    end_time: Optional[str],
    title_contains: Optional[str],
) -> Dict[str, Any]:
    """
    Queries existing calendar event instances based on provided criteria,
    expanding recurring events within the specified time range.
    (Dependencies are passed explicitly by the tool wrapper).
    """
    logger.info(
        "Executing schedule_query_events logic (using GetEventsInRangeStrategy)",
        start=start_time,
        end=end_time,
        title=title_contains,
    )
    try:
        # --- Parse Input Times ---
        try:
            aware_start_utc, aware_end_utc = date_utils.parse_nlp_date_range(
                start_date_str=start_time,
                end_date_str=end_time
            )
            logger.debug(f"Parsed query range: Start={aware_start_utc}, End={aware_end_utc}")
        except ValueError as pe:
            logger.warning(f"Invalid time format provided for query: {pe}")
            raise InvalidInputError(f"Invalid start or end time format: {pe}")

        # --- Create TimeRange DAO ---
        time_range_dao = TimeRange(
            start_time=aware_start_utc,
            end_time=aware_end_utc
        )

        # --- Instantiate and Execute GetEventsInRangeStrategy ---
        get_events_strategy = GetEventsInRangeStrategy(
            db_service=db_service,
            date_utils=date_utils
        )
        # This strategy returns a list of Event *instances* within the range.
        found_instances = await get_events_strategy.execute(data=time_range_dao)

        # --- Apply Post-Filtering (e.g., title_contains) ---
        if title_contains:
            # Filter the list of instances based on the title
            filtered_instances = [
                instance for instance in found_instances
                if title_contains.lower() in instance.title.lower()
            ]
            logger.info(f"Query found {len(found_instances)} instances initially, filtered to {len(filtered_instances)} by title '{title_contains}'.")
        else:
            filtered_instances = found_instances
            logger.info(f"Query found {len(found_instances)} instances.")

        # --- Format and Return Results ---
        return {
            "success": True,
            "count": len(filtered_instances),
            # Serialize the list of Event instances
            "events": [evt.model_dump(mode="json") for evt in filtered_instances],
        }

    except InvalidInputError as iie:
        logger.warning(f"Invalid input data for query_events: {iie}")
        return {"success": False, "error": str(iie)}
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
            if isinstance(event_id, str):
                event_uuid = uuid.UUID(event_id)
            elif isinstance(event_id, uuid.UUID):
                 event_uuid = event_id
            else:
                 raise ValueError("event_id must be a string or UUID object.")
            if 'id' not in update_data:
                 update_data['id'] = str(event_uuid)
        except (ValueError, TypeError):
            raise ValueError(f"Invalid event_id format: '{event_id}'. Must be a valid UUID string or object.")


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
                "event": updated_event_or_instance.model_dump(mode="json"),
             }
        else:
            logger.warning(f"Update failed for event: {event_id}. Event not found by strategy.")
            raise EventNotFoundError(f"Event with ID '{event_id}' not found.")

    except EventNotFoundError as enfe:
        logger.warning(f"Event not found during update: {enfe}")
        return {"success": False, "error": str(enfe)}
    except ConflictError as ce:
        logger.warning(f"Conflict detected updating event '{event_id}': {ce.conflicts}")
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
                "conflicts": ce.conflicts,
                "alternatives": alternatives,
            }
        }
    except (ValidationError, ValueError) as ve:
        logger.warning(f"Invalid input data for update_event: {ve}")
        error_detail = getattr(ve, 'errors', lambda: str(ve))()
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
            elif isinstance(event_id, uuid.UUID): # Allow UUID object
                event_uuid = event_id
            else:
                 raise ValueError("event_id must be a string or UUID object.")
        except (ValueError, TypeError):
            raise ValueError(f"Invalid event_id format: '{event_id}'. Must be a valid UUID string or object.")

        # Instantiate and execute the DeleteEventStrategy
        delete_event_strategy = DeleteEventStrategy(db_service=db_service)
        # Strategy expects EventDelete (UUID) as data and returns bool
        deleted = await delete_event_strategy.execute(data=EventDelete(id=event_uuid))

        if deleted:
            logger.info(f"Event '{event_id}' deleted successfully.")
            return {"success": True, "event_id": str(event_uuid)}
        else:
            logger.warning(f"Event not found during delete: {event_id}")
            raise EventNotFoundError(f"Event with ID '{event_id}' not found for deletion.")

    except EventNotFoundError as enfe:
        logger.warning(f"Event not found for deletion: {enfe}")
        return {"success": False, "error": str(enfe)}
    except ValueError as ve:
        logger.warning(f"Invalid event ID format for delete: {ve}")
        return {"success": False, "error": str(ve)}
    except DatabaseError as dbe:
        logger.error(f"Database error during event deletion: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_delete_event: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


async def schedule_create_contact(
    db_service: DatabaseService,
    name: str,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Creates a new contact."""
    logger.info("Executing schedule_create_contact logic", name=name, email=email)
    try:
        contact_data = ContactCreate(name=name, email=email, phone=phone, notes=notes)
        strategy = CreateContactStrategy(db_service=db_service)
        created_contact = await strategy.execute(data=contact_data)
        logger.info(f"Contact '{created_contact.name}' created successfully. contact_id={created_contact.id}")
        return {
            "success": True,
            "contact_id": str(created_contact.id),
            "contact": created_contact.model_dump(mode="json"),
        }
    except ValidationError as ve:
        logger.warning(f"Invalid input data for create_contact: {ve.errors()}")
        return {"success": False, "error": f"Invalid input data: {ve.errors()}"}
    except DatabaseError as dbe:
        # Check if the underlying cause is a duplicate email IntegrityError
        original_exc = getattr(dbe, '__cause__', None) or getattr(dbe, 'orig', None)
        error_str = str(original_exc).lower() if original_exc else str(dbe).lower()
        if isinstance(original_exc, IntegrityError) and \
           ("contact" in error_str or "contacts" in error_str) and \
           "email" in error_str and \
           ("unique" in error_str or "duplicate" in error_str or "constraint failed" in error_str):
             logger.warning(f"Failed to create contact '{name}': Email '{email}' already exists.", exc_info=False)
             return {"success": False, "error": f"Email '{email}' already exists for another contact."}
        else:
             # Log other database errors normally
             logger.error(f"Database error during contact creation: {dbe}", exc_info=True)
             return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_create_contact: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}

async def schedule_query_contacts(
    db_service: DatabaseService,
    name_contains: Optional[str] = None,
    email_contains: Optional[str] = None,
) -> Dict[str, Any]:
    """Queries existing contacts based on provided criteria."""
    logger.info(
        "Executing schedule_query_contacts logic",
        name_contains=name_contains,
        email_contains=email_contains,
    )
    try:
        query_params = ContactQuery(name_contains=name_contains, email_contains=email_contains)
        strategy = FindContactsStrategy(db_service=db_service)
        found_contacts = await strategy.execute(data=query_params)
        logger.info(f"Contact query found {len(found_contacts)} contacts.")
        # Return a list of contact details
        return {
            "success": True,
            "count": len(found_contacts),
            "contacts": [contact.model_dump(mode="json") for contact in found_contacts],
        }
    except ValidationError as ve:
        logger.warning(f"Invalid input data for query_contacts: {ve.errors()}")
        return {"success": False, "error": f"Invalid input data: {ve.errors()}"}
    except DatabaseError as dbe:
        logger.error(f"Database error during contact query: {dbe}", exc_info=True)
        return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_query_contacts: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}

async def schedule_update_contact(
    db_service: DatabaseService,
    contact_id: str,
    update_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Updates an existing contact."""
    logger.info(f"Executing schedule_update_contact logic for contact_id={contact_id} with updates={update_data}")
    try:
        try:
            contact_uuid = uuid.UUID(contact_id)
        except ValueError:
             logger.warning(f"Invalid contact_id format provided: {contact_id}")
             return {"success": False, "error": f"Invalid contact_id format: '{contact_id}'. Must be a valid UUID."}

        if "id" in update_data:
             logger.warning("Update data should not contain 'id', removing.")
             del update_data["id"]

        if not update_data:
             return {"success": False, "error": "No fields provided to update."}

        contact_update_dao = ContactUpdate.model_validate(update_data)

        strategy = UpdateContactStrategy(db_service=db_service)
        updated_contact = await strategy.execute(data=(contact_uuid, contact_update_dao))

        # Explicitly check if contact was returned
        if updated_contact:
            logger.info(f"Contact '{contact_id}' updated successfully.")
            return {
                "success": True,
                "contact_id": contact_id,
                "contact": updated_contact.model_dump(mode="json"),
            }
        else:
            # This case handles if strategy returns None/False without raising specific error
            logger.warning(f"Contact '{contact_id}' not found for update (strategy returned None/False)." )
            return {"success": False, "error": f"Contact with ID '{contact_id}' not found."}

    except ContactNotFoundError as nfe: # Keep this catch just in case strategy *does* raise it
        logger.warning(f"Contact not found during update (exception): {nfe}")
        return {"success": False, "error": str(nfe)}
    except ValidationError as ve:
        logger.warning(f"Invalid input data for update_contact: {ve.errors()}")
        return {"success": False, "error": f"Invalid input data: {ve.errors()}"}
    except DatabaseError as dbe:
        # Check for duplicate email IntegrityError within DatabaseError
        original_exc = getattr(dbe, '__cause__', None) or getattr(dbe, 'orig', None)
        error_str = str(original_exc).lower() if original_exc else str(dbe).lower()
        # Check for not found error possibly wrapped in DatabaseError
        if "not found" in error_str and contact_id in str(dbe):
             logger.warning(f"Contact '{contact_id}' not found for update (wrapped DB error).", exc_info=False)
             return {"success": False, "error": f"Contact with ID '{contact_id}' not found."}
        # Check for IntegrityError
        if isinstance(original_exc, IntegrityError) and \
           ("contact" in error_str or "contacts" in error_str) and \
           "email" in error_str and \
           ("unique" in error_str or "duplicate" in error_str or "constraint failed" in error_str):
             logger.warning(f"Failed to update contact '{contact_id}': Email already exists.", exc_info=False)
             return {"success": False, "error": f"Email already exists for another contact."}
        else:
            # Handle other DatabaseErrors
            logger.error(f"Database error during contact update: {dbe}", exc_info=True)
            return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_update_contact: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}


async def schedule_delete_contact(
    db_service: DatabaseService,
    contact_id: str,
) -> Dict[str, Any]:
    """Deletes an existing contact by its ID."""
    logger.info(f"Executing schedule_delete_contact logic for contact_id={contact_id}")
    try:
        try:
            contact_uuid = uuid.UUID(contact_id)
        except ValueError:
             logger.warning(f"Invalid contact_id format provided for delete: {contact_id}")
             return {"success": False, "error": f"Invalid contact_id format: '{contact_id}'. Must be a valid UUID."}

        strategy = DeleteContactStrategy(db_service=db_service)
        # Assume strategy raises ContactNotFoundError if not found, and returns None/unreliable value on success.
        await strategy.execute(data=ContactDelete(id=contact_uuid))

        # If execute completes without error, assume success
        logger.info(f"Contact '{contact_id}' deleted successfully.")
        return {"success": True, "contact_id": contact_id}

    except ContactNotFoundError as nfe:
        logger.warning(f"Contact not found for deletion (exception): {nfe}")
        return {"success": False, "error": str(nfe)}
    except DatabaseError as dbe:
         # Check for not found error possibly wrapped in DatabaseError
        error_str = str(getattr(dbe, '__cause__', None) or getattr(dbe, 'orig', None) or dbe).lower()
        if "not found" in error_str and contact_id in str(dbe):
             logger.warning(f"Contact '{contact_id}' not found for deletion (wrapped DB error).", exc_info=False)
             return {"success": False, "error": f"Contact with ID '{contact_id}' not found for deletion."}
        else:
            logger.error(f"Database error during contact deletion: {dbe}", exc_info=True)
            return {"success": False, "error": "Internal database error occurred."}
    except Exception as e:
        logger.exception(f"Unexpected error in schedule_delete_contact: {e}")
        return {"success": False, "error": f"An unexpected error occurred: {str(e)}"}

# --- Registration ---

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

    # --- Event Tool Wrappers ---

    @mcp.tool()
    async def create_event(
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

    @mcp.tool()
    async def query_events(
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        title_contains: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queries existing calendar events, expanding recurring ones.

        Args:
            start_time: Optional start time/date to filter events (ISO 8601 or natural language like 'today', 'tomorrow 9am'). Filters for event instances overlapping this time. Defaults to now.
            end_time: Optional end time/date to filter events (ISO 8601 or natural language). Filters for event instances overlapping this time. Defaults to 7 days from start_time.
            title_contains: Optional string to filter events by title (case-insensitive partial match).

        Returns:
            A dictionary containing the result, including success status, count, and list of found event instances or error information.
        """
        # Call implementation, passing required dependencies
        return await schedule_query_events(
            start_time=start_time,
            end_time=end_time,
            title_contains=title_contains,
            db_service=db_service,
            date_utils=date_utils,
        )

    @mcp.tool()
    async def update_event(
        event_id: str,
        # Use Dict directly for flexibility, validation happens in schedule_update_event
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Updates an existing event identified by its unique ID.
        The update_data dictionary should contain the fields to update, excluding the ID itself.
        Example: {"title": "New Title", "location": "New Location"}
        """
        # Ensure event_id is included in the update_data dict passed to the impl
        if 'id' not in update_data:
             update_data['id'] = event_id
        elif update_data['id'] != event_id:
             logger.warning(f"Mismatch between event_id argument ({event_id}) and update_data['id'] ({update_data['id']}). Using argument.")
             update_data['id'] = event_id

        return await schedule_update_event(
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils,
            update_data=update_data,
        )

    @mcp.tool()
    async def delete_event(
        event_id: str,
    ) -> Dict[str, Any]:
        """Deletes an existing event identified by its unique ID."""
        return await schedule_delete_event(
            db_service=db_service,
            event_id=event_id
        )

    # --- Contact Tool Wrappers (New) ---

    @mcp.tool()
    async def create_contact(
        name: str,
        email: Optional[str] = None,
        phone: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Creates a new contact.

        Args:
            name: The name of the contact (required).
            email: Optional email address for the contact. Must be unique if provided.
            phone: Optional phone number for the contact.
            notes: Optional notes about the contact.

        Returns:
            A dictionary containing the result, including success status and contact details or error information.
        """
        return await schedule_create_contact(
            db_service=db_service,
            name=name,
            email=email,
            phone=phone,
            notes=notes,
        )

    @mcp.tool()
    async def query_contacts(
        name_contains: Optional[str] = None,
        email_contains: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Queries existing contacts.

        Args:
            name_contains: Optional string to filter contacts by name (case-insensitive partial match).
            email_contains: Optional string to filter contacts by email (case-insensitive partial match).

        Returns:
            A dictionary containing the result, including success status, count, and list of found contacts or error information.
        """
        return await schedule_query_contacts(
            db_service=db_service,
            name_contains=name_contains,
            email_contains=email_contains,
        )

    @mcp.tool()
    async def update_contact(
        contact_id: str,
        update_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Updates an existing contact identified by its unique ID.
        The update_data dictionary should contain the fields to update, excluding the ID itself.
        Example: {"name": "Updated Name", "phone": "123-456-7890"}
        """
        # The implementation function schedule_update_contact handles ID validation
        # and ensures 'id' is not in the update_data passed to the DAO.
        return await schedule_update_contact(
            db_service=db_service,
            contact_id=contact_id,
            update_data=update_data,
        )

    @mcp.tool()
    async def delete_contact(
        contact_id: str,
    ) -> Dict[str, Any]:
        """Deletes an existing contact identified by its unique ID."""
        return await schedule_delete_contact(
            db_service=db_service,
            contact_id=contact_id
        )

    logger.info("Schedule and Contact tool handlers registered.")