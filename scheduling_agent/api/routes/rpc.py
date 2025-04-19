import base64 # Add base64 import
from typing import Dict # Add Dict
from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError # Import ValidationError
from scheduling_agent.common.types import (
    JSONRPCRequest, JSONRPCResponse, MethodNotFoundError, TaskSendParams, TaskGetParams, InternalError,
    TaskState, Message, Part, Artifact, InvalidParamsError, Task, EventQuery, FileContent, # Add FileContent
    TaskNotFoundError, # Added TaskNotFoundError
    CancelTaskParams,  # Add CancelTaskParams
    TaskNotCancelableError, # Add TaskNotCancelableError
)
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.api.dependencies import get_task_manager, get_db_service, get_conflict_resolver, get_nlp_processor, get_exporters, get_date_utils # Added get_exporters and get_date_utils
from scheduling_agent.logging_config import get_logger
# Import db models/services for logic
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.models import Event, EventCreate # Removed EventQuery
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.core.conflict_resolver import ConflictResolver # Import ConflictResolver
from scheduling_agent.core.nlp_processor import NLPProcessor # Corrected case to NLPProcessor
from datetime import datetime, timedelta, timezone, time # Added datetime, timedelta, timezone and time
from scheduling_agent.core.exceptions import NLPError # Added NLPError
from scheduling_agent.core.exporters.base import CalendarExporter # Import base exporter for type hinting
from scheduling_agent.core.date_utils import DateUtils # Import DateUtils
import dateparser # Import dateparser

logger = get_logger(__name__)
router = APIRouter()

# Placeholder for actual task sending logic (Phase 2, Task 3)
async def handle_tasks_send(
    params: TaskSendParams, # Expect parsed params
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService = Depends(get_db_service), # Add db_service dependency
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver), # Add conflict_resolver dependency
    nlp_processor: NLPProcessor = Depends(get_nlp_processor), # Add nlp_processor dependency
    exporters: Dict[str, CalendarExporter] = Depends(get_exporters), # Inject exporters
    date_utils: DateUtils = Depends(get_date_utils) # Inject DateUtils
) -> Task:
    """Handles the core logic for the tasks/send method.

    Returns:
        Task: The final state of the task on success.
    Raises:
        InvalidParamsError: If input data is invalid.
        InternalError: For unexpected issues.
        NotImplementedError: For paths not yet implemented (e.g., conflict, NLP).
    """
    logger.info("handle_tasks_send called", task_id=params.id)
    task_id = params.id
    initial_message = params.message

    # 1. Create the initial task entry
    # This call is now expected to be done by the caller/router if needed
    # Or we decide if the handler itself should ensure task creation.
    # Per plan, API handler calls task_manager.create_task.
    try:
        await task_manager.create_task(params=params)
    except Exception as e:
        # If task creation fails (e.g., duplicate ID, though unlikely), return error
        logger.error("Failed to create initial task entry", task_id=task_id, error=e)
        err = InternalError(message=f"Failed to initialize task: {e}")
        # Note: JSONRPCResponse requires request_id, which isn't directly available here.
        # The main handler needs to construct the final response.
        # Let's re-raise or handle differently.
        # For now, let's assume the main handler deals with final JSONRPCResponse creation.
        # We should return the final Task object or raise A2A exceptions.
        raise err # Re-raise for the main handler

    # TODO: Add try...except block around the main logic

    input_text = next((part.text for part in initial_message.parts if part.type == 'text' and part.text), None)
    input_data = next((part.data for part in initial_message.parts if part.type == 'data'), None)

    # --- Logic moved from TaskManager.on_send_task --- #

    if input_data and isinstance(input_data, dict):
        # Logic for handling structured data (Create Event Path)
        if 'title' in input_data and ('start_time' in input_data or 'startTime' in input_data):
            try:
                # Validate input data against EventCreate model
                event_create_data = EventCreate.model_validate(input_data)
                # Call DB service to create event
                created_event = await db_service.create_event(event_data=event_create_data)

                # Prepare success message and artifact
                result_message = f"Event '{created_event.title}' created successfully."
                agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
                artifact = Artifact(name="created_event", parts=[Part(type="data", data=created_event.model_dump())])

                # Update task state via TaskManager
                final_task = await task_manager.complete_task(task_id, agent_message, artifact)
                return final_task # Return the completed Task object

            except ConflictError as ce:
                # --- Conflict Handling Path (Moved from TaskManager) --- #
                logger.warning("Conflict detected during event creation", task_id=task_id, conflicts=ce.conflicts)
                try:
                    # Ensure EventCreate data is parsed before calling resolver
                    # It was already parsed in the outer try block
                    # event_create_data_for_conflict = EventCreate.model_validate(input_data)
                    alternatives = await conflict_resolver.generate_alternative_times(event_create_data, ce.conflicts)
                    conflict_info = {
                        "message": str(ce),
                        "conflicts": [c["event"].model_dump() for c in ce.conflicts if isinstance(c.get("event"), Event)],
                        "alternatives": alternatives
                    }
                    artifact = Artifact(name="conflict_info", parts=[Part(type="data", data=conflict_info)])
                    result_message = "Event creation failed due to conflicts. Please provide an alternative time or confirm override."
                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
                    # Update task state via TaskManager
                    final_task = await task_manager.require_input_task(task_id, agent_message, artifact)
                    return final_task # Return the INPUT_REQUIRED Task object
                except Exception as alt_err:
                    logger.error("Failed to generate alternatives after conflict", task_id=task_id, error=alt_err)
                    result_message = f"Event creation failed due to conflicts, and generating alternatives also failed: {alt_err}"
                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
                    # Don't fail task here, let the exception propagate
                    # await task_manager.fail_task(task_id, agent_message)
                    raise InternalError(message=result_message) # Raise specific A2A error if possible, or just re-raise alt_err

            except ValidationError as ve:
                # This specifically catches validation errors for EventCreate model
                logger.error("Invalid event data provided", task_id=task_id, validation_error=ve.errors())
                # Re-raise as InvalidParamsError for the main handler
                raise InvalidParamsError(message="Invalid data provided for event creation.", data=ve.errors())

        else:
            # Structured data didn't match create event criteria
            logger.warning("Could not determine action from structured data", task_id=task_id, data=input_data)
            await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text="Could not determine action from structured data.")]))
            raise InvalidParamsError(message="Could not determine action from structured data.")

    elif input_text:
        # --- NLP Intent Handling Path ---
        logger.info("Processing text input via NLP", task_id=task_id, text=input_text)
        try:
            nlp_result = await nlp_processor.parse_natural_language_query(input_text)
            intent = nlp_result.intent
            entities = {} # TODO: Extract entities properly from nlp_result if needed
            # Example: entities = nlp_result.entities if hasattr(nlp_result, 'entities') else {}
            # If nlp_result is just a dict:
            # intent = nlp_result.get("intent")
            entities = nlp_result.filters or {} # Use filters as entities for now

            logger.info("NLP processing result", task_id=task_id, intent=intent, entities=entities)

            if intent == "query_events":
                try:
                    # Validate extracted entities against EventQuery model
                    # Map NLP entities to EventQuery fields (adjust mapping as needed)
                    query_data = {
                        # Access attributes directly
                        "start_time": entities.start_time if hasattr(entities, 'start_time') else None,
                        "end_time": entities.end_time if hasattr(entities, 'end_time') else None,
                        "title_contains": entities.title if hasattr(entities, 'title') else None,
                        # Add other mappings if your NLP model extracts them
                    }
                    # Filter out None values before validation
                    query_data = {k: v for k, v in query_data.items() if v is not None}

                    event_query = EventQuery.model_validate(query_data)

                    # Call DB service to query events
                    queried_events = await db_service.query_events(query=event_query)

                    # Prepare success message and artifact
                    if queried_events:
                        result_message = f"Found {len(queried_events)} event(s) matching your query."
                        # Format events for the artifact
                        events_data = [event.model_dump() for event in queried_events]
                        artifact = Artifact(name="queried_events", parts=[Part(type="data", data=events_data)])
                    else:
                        result_message = "No events found matching your query."
                        artifact = None # No artifact if no events found

                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])

                    # Update task state via TaskManager
                    final_task = await task_manager.complete_task(task_id, agent_message, artifact)
                    return final_task # Return the completed Task object

                except ValidationError as ve:
                    # Catches validation errors for EventQuery model
                    logger.error("Invalid query parameters extracted from NLP", task_id=task_id, validation_error=ve.errors(), extracted_entities=entities)
                    # Ask for clarification if validation fails
                    clarification_msg = f"I understood you wanted to query events, but I couldn't understand the details. Could you please rephrase your query? (Details: {ve.errors()})"
                    agent_message = Message(role="agent", parts=[Part(type="text", text=clarification_msg)])
                    final_task = await task_manager.require_input_task(task_id, agent_message)
                    return final_task

            elif intent == "export_calendar":
                logger.info("Handling export_calendar intent", task_id=task_id, entities=entities)
                try:
                    # 1. Parse format and date range from NLP entities/filters
                    # Revert to attribute access on LLMFilters object, check if it exists
                    export_format = "ics" # Default
                    if entities and hasattr(entities, 'format') and entities.format:
                        export_format = entities.format.lower()
                    
                    # --- Parse date range using dateparser --- 
                    range_start_utc: datetime = None
                    range_end_utc: datetime = None

                    # Get date strings from NLP result, default to relative terms
                    start_date_str = getattr(nlp_result.date_info, 'start_date', None) or "today"
                    end_date_str = getattr(nlp_result.date_info, 'end_date', None) or "next Sunday"
                    # TODO: Consider using time_description for more complex ranges?

                    try:
                        # Parse start date (assume start of day if no time)
                        start_dt = dateparser.parse(start_date_str, settings={'RETURN_AS_TIMEZONE_AWARE': True, 'TIMEZONE': 'UTC'})
                        if start_dt:
                             # Ensure it's start of the day in UTC
                             start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                             range_start_utc = date_utils.normalize_to_utc(start_dt)

                        # Parse end date (assume end of day if no time)
                        end_dt = dateparser.parse(end_date_str, settings={'RETURN_AS_TIMEZONE_AWARE': True, 'TIMEZONE': 'UTC'})
                        if end_dt:
                             # Ensure it's end of the day in UTC
                             end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                             range_end_utc = date_utils.normalize_to_utc(end_dt)
                        
                        # Handle cases where parsing might fail or return None
                        if range_start_utc is None:
                            logger.warning("Failed to parse start date for export, defaulting to today.", task_id=task_id, input=start_date_str)
                            range_start_utc = date_utils.normalize_to_utc(datetime.combine(datetime.now(timezone.utc).date(), time.min))
                        if range_end_utc is None:
                             logger.warning("Failed to parse end date for export, defaulting to 7 days from start.", task_id=task_id, input=end_date_str)
                             range_end_utc = range_start_utc + timedelta(days=7) # Default to 7 days from start
                        
                        # Ensure start is before end
                        if range_start_utc >= range_end_utc:
                             logger.warning("Parsed start date is after end date, adjusting end date.", task_id=task_id, start=range_start_utc, end=range_end_utc)
                             range_end_utc = range_start_utc + timedelta(days=7) # Default to 7 days

                    except Exception as parse_err:
                        logger.error("Error parsing date range for export, defaulting to next 7 days.", task_id=task_id, error=parse_err)
                        range_start_utc = date_utils.normalize_to_utc(datetime.combine(datetime.now(timezone.utc).date(), time.min))
                        range_end_utc = range_start_utc + timedelta(days=7)

                    logger.info("Determined export date range (UTC)", task_id=task_id, start=range_start_utc, end=range_end_utc)

                    # 2. Get the correct exporter
                    exporter = exporters.get(export_format)
                    if not exporter:
                        logger.error("Unsupported export format requested", task_id=task_id, format=export_format)
                        raise InvalidParamsError(f"Unsupported export format requested: {export_format}")

                    # 3. Query events from DB
                    logger.info("Querying events for export", task_id=task_id, start=range_start_utc, end=range_end_utc)
                    events_to_export = await db_service.get_events_in_range(start=range_start_utc, end=range_end_utc)
                    logger.info(f"Found {len(events_to_export)} events to export", task_id=task_id)

                    # 4. Export events
                    exported_data = exporter.export(events_to_export)
                    mime_type = exporter.get_mime_type()
                    file_name = f"calendar.{export_format}"

                    # 5. Base64 encode the result
                    encoded_bytes = base64.b64encode(exported_data.encode('utf-8')).decode('utf-8')

                    # 6. Create Artifact
                    file_content = FileContent(name=file_name, mimeType=mime_type, bytes=encoded_bytes)
                    artifact = Artifact(name=file_name, parts=[Part(type="file", file=file_content)]) # Corrected nesting

                    # 7. Complete task
                    result_message = f"Calendar exported successfully as {file_name}."
                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
                    final_task = await task_manager.complete_task(task_id, agent_message, artifact)
                    logger.info("Export task completed successfully", task_id=task_id)
                    return final_task

                except InvalidParamsError as ipe: # Re-raise specific errors
                    raise ipe
                except Exception as e:
                    logger.exception("Error during calendar export", task_id=task_id, exc_info=e)
                    await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text=f"An error occurred during export: {e}")]))
                    raise InternalError(message=f"Error during calendar export: {e}")

            # TODO: Add handlers for other intents (e.g., export_events, delete_event)
            # Explicitly check if the intent is known but not handled (excluding "unknown")
            elif intent and intent != "unknown": # Intent detected but not handled
                 logger.warning("Unhandled NLP intent", task_id=task_id, intent=intent)
                 unhandled_msg = f"I understood the intent '{intent}', but I cannot handle that yet."
                 agent_message = Message(role="agent", parts=[Part(type="text", text=unhandled_msg)])
                 final_task = await task_manager.fail_task(task_id, agent_message) # Fail for now
                 # Alternatively, could use require_input_task if clarification might help
                 # final_task = await task_manager.require_input_task(task_id, agent_message)
                 return final_task # Return failed/input_required task

            # Handles intent == "unknown" or intent is None
            else: # Intent not detected or unknown
                logger.warning("Could not determine intent from text or intent is unknown", task_id=task_id, text=input_text, intent=intent)
                clarification_msg = "I wasn't sure what you wanted to do. Could you please rephrase your request?"
                agent_message = Message(role="agent", parts=[Part(type="text", text=clarification_msg)])
                final_task = await task_manager.require_input_task(task_id, agent_message)
                return final_task # Return the INPUT_REQUIRED Task object

        # Catch specific NLPError to allow it to propagate to FastAPI handler
        except NLPError as nlp_err:
            logger.exception("NLPError caught in handle_tasks_send, re-raising for centralized handler.", task_id=task_id, exc_info=nlp_err)
            raise nlp_err # Re-raise to be caught by main.py handler
        # Catch specific A2A errors that should be handled by the main RPC handler
        except InvalidParamsError as ipe:
            logger.warning("InvalidParamsError caught in handle_tasks_send, re-raising for RPC handler.", task_id=task_id, error_message=ipe.message)
            raise ipe # Re-raise to be caught by handle_rpc_request
        # Catch other potential exceptions during NLP processing/handling
        except Exception as e:
            logger.exception("Unexpected error during NLP processing or handling", task_id=task_id, exc_info=e)
            # Fail the task before raising a generic error
            await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text=f"An error occurred processing your request: {e}")]))
            # Raise a generic InternalError, this will be caught by the main rpc handler
            raise InternalError(message=f"Error processing NLP request: {e}")

    else:
        logger.warning("Unsupported input type in tasks/send", task_id=task_id)
        await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text="Unsupported input type.")]))
        raise InvalidParamsError(message="Unsupported input type provided.")

# Placeholder for actual task getting logic (Phase 2, Task 4)
async def handle_tasks_get(
    params: TaskGetParams, # Expect parsed params
    task_manager: SchedulingTaskManager
) -> Task: # Changed return type hint to Task
    """Handles the logic for the tasks/get method."""
    logger.info("handle_tasks_get called", task_id=params.id)
    task = await task_manager.get_task(params.id)
    if task is None:
        logger.warning("Task not found during get request", task_id=params.id)
        raise TaskNotFoundError(f"Task with ID '{params.id}' not found.")
    return task

# Handler for tasks/cancel (Phase 4, Task 1)
async def handle_tasks_cancel(
    params: CancelTaskParams,
    task_manager: SchedulingTaskManager
) -> Task:
    """Handles the logic for the tasks/cancel method."""
    logger.info("handle_tasks_cancel called", task_id=params.id)
    # Exceptions (TaskNotFoundError, TaskNotCancelableError) raised by
    # task_manager.cancel_task will be caught by the main handler.
    canceled_task = await task_manager.cancel_task(task_id=params.id)
    return canceled_task

@router.post("/")
async def handle_rpc_request(
    request: JSONRPCRequest, # Pydantic model handles initial validation
    task_manager: SchedulingTaskManager = Depends(get_task_manager),
    db_service: DatabaseService = Depends(get_db_service), # Add db_service here too
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver), # Add conflict_resolver here too
    nlp_processor: NLPProcessor = Depends(get_nlp_processor), # Add nlp_processor here too
    exporters: Dict[str, CalendarExporter] = Depends(get_exporters), # Add exporters here too for consistency
    date_utils: DateUtils = Depends(get_date_utils) # Inject DateUtils
) -> JSONRPCResponse:
    """Main JSON-RPC handler, routes based on method."""
    logger.debug("Received RPC request", method=request.method, request_id=request.id)
    request_id = request.id # Store request id for response

    try:
        if request.method == "tasks/send":
            parsed_params = TaskSendParams.model_validate(request.params or {})
            # Call the handler, which now returns a Task object on success or raises A2A exceptions
            result_task = await handle_tasks_send(
                params=parsed_params,
                task_manager=task_manager,
                db_service=db_service,
                conflict_resolver=conflict_resolver,
                nlp_processor=nlp_processor, # Pass nlp_processor
                exporters=exporters, # Pass exporters
                date_utils=date_utils # Pass DateUtils
            )
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif request.method == "tasks/get":
            parsed_params = TaskGetParams.model_validate(request.params or {})
            # Call the handler, which returns a Task object or raises TaskNotFoundError
            result_task = await handle_tasks_get(params=parsed_params, task_manager=task_manager)
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif request.method == "tasks/cancel":
            parsed_params = CancelTaskParams.model_validate(request.params or {})
            # Call the handler, which returns a canceled Task or raises errors
            result_task = await handle_tasks_cancel(params=parsed_params, task_manager=task_manager)
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        else:
            logger.warning("Unsupported RPC method", method=request.method)
            raise MethodNotFoundError()

    # --- Exception Handling Moved Here --- #
    except (MethodNotFoundError, InvalidParamsError, TaskNotFoundError, TaskNotCancelableError) as e:
        # Catch specific A2A errors raised by handlers or param validation
        # Added TaskNotCancelableError
        logger.info("A2A Error occurred within RPC handler", error_type=type(e).__name__, code=e.code, message=e.message, request_id=request_id)
        return JSONRPCResponse(id=request_id, error=e.as_jsonrpc_error())

    except ValidationError as ve:
        # Handle Pydantic validation errors specifically during param parsing *within this handler*
        logger.error("Invalid parameters for method during handler validation", method=request.method, error=ve.errors(), request_id=request_id)
        err = InvalidParamsError(message="Invalid parameters provided.", data=ve.errors())
        return JSONRPCResponse(id=request_id, error=err.as_jsonrpc_error())

    # REMOVED broad NotImplementedError and Exception handlers here
    # Let them propagate to the centralized handlers in main.py
    # except NotImplementedError as e:
    #     logger.error("Method handler not implemented", method=request.method, error=str(e), request_id=request_id)
    #     internal_err = InternalError(message=f"Method '{request.method}' is not fully implemented.")
    #     return JSONRPCResponse(id=request_id, error=internal_err.as_jsonrpc_error())
    #
    # except Exception as e:
    #     logger.exception("Unexpected error handling RPC request within handler", method=request.method, exc_info=e, request_id=request_id)
    #     internal_err = InternalError(message=f"An unexpected error occurred handling method '{request.method}'.", data=str(e))
    #     return JSONRPCResponse(id=request_id, error=internal_err.as_jsonrpc_error()) 