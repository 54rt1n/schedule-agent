from pydantic import ValidationError

from scheduling_agent.a2a.types import (
    Artifact,
    EventQuery,
    InternalError,
    Message,
    Part,
    Task,
)
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.core.schemas import LLMSchedulingParseResult
from scheduling_agent.db.models import Event # Needed for ambiguity check
from scheduling_agent.db.schemas import EventUpdate
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

async def handle_nlp_update(
    task_id: str,
    nlp_result: LLMSchedulingParseResult,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    date_utils: DateUtils, # Needed for parsing times
    conflict_resolver: ConflictResolver # Needed potentially for conflict check on update
) -> Task:
    """Handles the update_event intent from NLP result."""
    logger.info("Handling NLP update_event intent", task_id=task_id)
    event_to_update: Event | None = None # Define outside try block for use in exception logging
    try:
        # 1. Extract query data from NLP result.filters
        if not nlp_result.filters or not nlp_result.filters.title:
            logger.warning("NLP update intent missing query title", task_id=task_id)
            raise ValueError("Could not determine which event title to update from your request.")

        query = EventQuery(title_contains=nlp_result.filters.title)
        logger.debug("Parsed NLP query data for update", task_id=task_id, query=query)

        # 2. Find candidate events
        found_events = await db_service.find_events(query=query)

        if not found_events:
            logger.warning("No events found matching NLP query for update", task_id=task_id, query=query)
            agent_message = Message(role="agent", parts=[Part(type="text", text=f"Sorry, I couldn't find an event titled '{nlp_result.filters.title}' to update.")])
            # Fail the task directly as the target event doesn't exist
            return await task_manager.fail_task(task_id, agent_message)
        elif len(found_events) > 1:
            logger.warning("Multiple events found matching NLP query for update", task_id=task_id, count=len(found_events), title=nlp_result.filters.title)
            # Create a more informative artifact for ambiguous events
            found_event_summaries = [
                {"id": str(e.id), "title": e.title, "start_time": e.start_time.isoformat() if e.start_time else None}
                for e in found_events
            ]
            artifact = Artifact(name="ambiguous_events", parts=[Part(type="data", data=found_event_summaries)])
            agent_message = Message(role="agent", parts=[Part(type="text", text=f"I found multiple events titled '{nlp_result.filters.title}'. Could you be more specific?")])
            return await task_manager.require_input_task(task_id, agent_message, artifact)

        # 3. Exactly one event found
        event_to_update = found_events[0]
        logger.info("Found unique event to update via NLP", task_id=task_id, event_id=event_to_update.id)

        # 4. Extract and construct update data from NLP result.date_info/filters
        update_fields = {}
        if nlp_result.date_info:
            di = nlp_result.date_info
            new_start = None
            new_end = None
            effective_tz = di.timeZone or event_to_update.time_zone or "UTC"

            # Attempt to construct new start/end datetimes using date_utils.parse_datetime
            try:
                if di.start_date and di.specific_time:
                    start_str = f"{di.start_date} {di.specific_time}"
                    new_start = date_utils.parse_datetime(start_str)
                    update_fields['start_time'] = new_start
                elif di.start_date:
                    start_str = f"{di.start_date} 00:00:00"
                    new_start = date_utils.parse_datetime(start_str)
                    update_fields['start_time'] = new_start

                end_date_str = di.end_date or di.start_date
                end_time_str = di.end_time or di.specific_time

                if end_date_str and end_time_str:
                    end_str = f"{end_date_str} {end_time_str}"
                    new_end = date_utils.parse_datetime(end_str)
                    if new_start and not di.end_date and not di.end_time and (event_to_update.end_time and event_to_update.start_time):
                        duration = event_to_update.end_time - event_to_update.start_time
                        new_end = new_start + duration
                    update_fields['end_time'] = new_end
                elif end_date_str:
                    end_str = f"{end_date_str} 23:59:59"
                    new_end = date_utils.parse_datetime(end_str)
                    update_fields['end_time'] = new_end

                if di.timeZone:
                    update_fields['time_zone'] = di.timeZone

            except ValueError as parse_err:
                logger.warning("Could not parse date/time for NLP update", task_id=task_id, error=parse_err)
                raise ValueError(f"I couldn't understand the new date/time you provided: {parse_err}")

        if nlp_result.filters:
            if nlp_result.filters.location:
                update_fields['location'] = nlp_result.filters.location
            # Title updates are not handled via filters in this logic

        if not update_fields:
            logger.warning("NLP update intent missing specific update data", task_id=task_id)
            raise ValueError("It wasn't clear what changes you wanted to make to the event.")

        # Validate the constructed fields into an EventUpdate object
        try:
            update_data = EventUpdate.model_validate(update_fields)
        except ValidationError as ve:
            logger.error("Validation failed for constructed NLP update data", task_id=task_id, errors=ve.errors(), data=update_fields)
            raise ValueError(f"The update details extracted were invalid: {ve.errors()}")

        logger.debug("Parsed NLP update data", task_id=task_id, update_data=update_data.model_dump(exclude_unset=True))

        # 5. Perform the update via DB service (conflict check happens inside db_service.update_event)
        updated_event_or_instance = await db_service.update_event(
            event_id=event_to_update.id, update_data=update_data
        )

        # 6. Handle success
        if updated_event_or_instance:
            logger.info("Successfully updated event via NLP", task_id=task_id, event_id=event_to_update.id)
            result_message = f"OK, I've updated the event '{event_to_update.title}' based on your request."
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
            artifact_data = updated_event_or_instance.model_dump(mode='json')
            artifact = Artifact(name="updated_event_details", parts=[Part(type="data", data=artifact_data)])
            return await task_manager.complete_task(task_id, agent_message, artifact)
        else:
            # This case *shouldn't* happen if we found the event just before, but handle defensively
            logger.error("Event disappeared between find and update during NLP handling", task_id=task_id, event_id=event_to_update.id)
            raise InternalError("The event could not be updated unexpectedly after being found.")

    except ConflictError as ce:
        logger.warning("Conflict detected during NLP event update", task_id=task_id, event_id=getattr(event_to_update, 'id', 'unknown'), conflicts=ce.conflicts)
        agent_message = Message(role="agent", parts=[Part(type="text", text=f"Updating the event '{getattr(event_to_update, 'title', 'event')}' failed due to scheduling conflicts. Please provide alternatives or cancel.")])
        # Ensure conflict data is serializable (assuming ce.conflicts is already)
        conflict_artifact = Artifact(name="scheduling_conflicts", parts=[Part(type="data", data=ce.conflicts)])
        return await task_manager.require_input_task(task_id, agent_message, conflict_artifact)
    except NotImplementedError as nie:
        logger.error("Unsupported NLP update operation attempted", task_id=task_id, event_id=getattr(event_to_update, 'id', 'unknown'), error=str(nie))
        agent_message = Message(role="agent", parts=[Part(type="text", text=f"Sorry, I can't update the event '{getattr(event_to_update, 'title', 'event')}' in that way: {str(nie)}")])
        return await task_manager.fail_task(task_id, agent_message)
    except (ValueError, ValidationError) as ve: # Catch validation errors for query/update data or logic errors
        logger.error("Validation or logic error during NLP event update", task_id=task_id, error=str(ve))
        agent_message = Message(role="agent", parts=[Part(type="text", text=f"Sorry, I couldn't process the update request: {str(ve)}")])
        # Fail task for bad data/logic issues
        return await task_manager.fail_task(task_id, agent_message)
    # Let other A2A errors (e.g., InternalError from db_service) propagate
    # Let unexpected errors raise InternalError via the outer handler
