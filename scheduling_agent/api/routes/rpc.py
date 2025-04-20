import base64  # Add base64 import
from datetime import (
    UTC,
    datetime,
    time,
    timedelta,
)  # Added datetime, timedelta, timezone and time

import dateparser  # Import dateparser
from fastapi import APIRouter, Depends
from pydantic import ValidationError  # Import ValidationError

from scheduling_agent.api.dependencies import (
    get_conflict_resolver,
    get_date_utils,
    get_db_service,
    get_exporters,
    get_nlp_processor,
    get_task_manager,
)  # Added get_exporters and get_date_utils
from scheduling_agent.common.types import (
    Artifact,
    CancelTaskParams,  # Add CancelTaskParams
    EventQuery,
    FileContent,  # Add FileContent
    InternalError,
    InvalidParamsError,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    MethodNotFoundError,
    Part,
    Task,
    TaskGetParams,
    TaskNotCancelableError,  # Add TaskNotCancelableError
    TaskNotFoundError,  # Added TaskNotFoundError
    TaskSendParams,
)
from scheduling_agent.core.conflict_resolver import ConflictResolver  # Import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils  # Import DateUtils
from scheduling_agent.core.exceptions import (
    ConflictError,
    NLPError,  # Added NLPError
)
from scheduling_agent.core.exporters.base import (
    CalendarExporter,
)  # Import base exporter for type hinting
from scheduling_agent.core.nlp_processor import NLPProcessor  # Corrected case to NLPProcessor
from scheduling_agent.core.schemas import LLMSchedulingParseResult

# Import the DB table model Event from models and the DTO EventCreate from schemas
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate

# Import db models/services for logic
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger
from scheduling_agent.task_manager import SchedulingTaskManager

logger = get_logger(__name__)
router = APIRouter()

# --- Helper Functions for handle_tasks_send Logic ---


async def _handle_send_structured_data(
    task_id: str,
    input_data: dict,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    # Removed unused date_utils dependency here
) -> Task:
    """Handles the logic path for structured data input (create event)."""
    # Logic for handling structured data (Create Event Path)
    if "title" in input_data and ("start_time" in input_data or "startTime" in input_data):
        try:
            # Validate input data against EventCreate model
            event_create_data = EventCreate.model_validate(input_data)
            # Call DB service to create event
            created_event = await db_service.create_event(event_data=event_create_data)

            # Prepare success message and artifact
            result_message = f"Event '{created_event.title}' created successfully."
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
            artifact = Artifact(
                name="created_event", parts=[Part(type="data", data=created_event.model_dump())]
            )

            # Update task state via TaskManager
            final_task = await task_manager.complete_task(task_id, agent_message, artifact)
            return final_task

        except ConflictError as ce:
            # --- Conflict Handling Path --- #
            logger.warning(
                "Conflict detected during event creation", task_id=task_id, conflicts=ce.conflicts
            )
            try:
                # Validate input data again (needed for generate_alternative_times)
                event_create_data_for_conflict = EventCreate.model_validate(input_data)
                alternatives = await conflict_resolver.generate_alternative_times(
                    event_create_data_for_conflict, ce.conflicts
                )
                conflict_info = {
                    "message": str(ce),
                    "conflicts": [
                        c["event"].model_dump()
                        for c in ce.conflicts
                        if isinstance(c.get("event"), Event)
                    ],
                    "alternatives": alternatives,
                }
                artifact = Artifact(
                    name="conflict_info", parts=[Part(type="data", data=conflict_info)]
                )
                result_message = "Event creation failed due to conflicts. Please provide an alternative time or confirm override."
                agent_message = Message(
                    role="agent", parts=[Part(type="text", text=result_message)]
                )
                # Update task state via TaskManager
                final_task = await task_manager.require_input_task(task_id, agent_message, artifact)
                return final_task
            except Exception as alt_err:
                logger.exception(
                    "Failed to generate alternatives after conflict", task_id=task_id, error=alt_err
                )
                result_message = f"Event creation failed due to conflicts, and generating alternatives also failed: {alt_err}"
                # Let the main handler catch and convert to InternalError
                raise InternalError(message=result_message) from alt_err

        except ValidationError as ve:
            # Re-raise as InvalidParamsError for the main handler
            logger.exception(
                "Invalid event data provided", task_id=task_id, validation_error=ve.errors()
            )
            raise InvalidParamsError(
                message="Invalid data provided for event creation.", data=ve.errors()
            ) from ve

    else:
        # Structured data didn't match create event criteria
        logger.warning(
            "Could not determine action from structured data", task_id=task_id, data=input_data
        )
        # Fail task directly? Or raise error?
        # Let's raise InvalidParamsError for consistency
        await task_manager.fail_task(
            task_id,
            Message(
                role="agent",
                parts=[Part(type="text", text="Could not determine action from structured data.")],
            ),
        )  # Keep fail for now
        raise InvalidParamsError(message="Could not determine action from structured data.")


async def _handle_nlp_intent_query(
    task_id: str,
    nlp_result: LLMSchedulingParseResult,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    # Removed unused dependencies (conflict_resolver, exporters, date_utils)
) -> Task:
    """Handles the query_events intent from NLP result."""
    entities = nlp_result.filters  # Assuming filters contain query params
    try:
        # Map NLP entities to EventQuery fields
        query_data = {
            "start_time": entities.start_time if hasattr(entities, "start_time") else None,
            "end_time": entities.end_time if hasattr(entities, "end_time") else None,
            "title_contains": entities.title if hasattr(entities, "title") else None,
        }
        query_data = {k: v for k, v in query_data.items() if v is not None}
        event_query = EventQuery.model_validate(query_data)

        queried_events = await db_service.query_events(query=event_query)

        if queried_events:
            result_message = f"Found {len(queried_events)} event(s) matching your query."
            events_data = [event.model_dump() for event in queried_events]
            artifact = Artifact(name="queried_events", parts=[Part(type="data", data=events_data)])
        else:
            result_message = "No events found matching your query."
            artifact = None

        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        final_task = await task_manager.complete_task(task_id, agent_message, artifact)
        return final_task

    except ValidationError as ve:
        logger.exception(
            "Invalid query parameters extracted from NLP",
            task_id=task_id,
            validation_error=ve.errors(),
            extracted_entities=entities,
        )
        clarification_msg = f"I understood you wanted to query events, but I couldn't understand the details. Could you please rephrase your query? (Details: {ve.errors()})"
        agent_message = Message(role="agent", parts=[Part(type="text", text=clarification_msg)])
        final_task = await task_manager.require_input_task(task_id, agent_message)
        return final_task


async def _handle_nlp_intent_export(
    task_id: str,
    nlp_result: LLMSchedulingParseResult,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    # Removed unused dependency conflict_resolver
) -> Task:
    """Handles the export_calendar intent from NLP result."""
    entities = nlp_result.filters  # Assuming filters contain export params
    logger.info("Handling export_calendar intent", task_id=task_id, entities=entities)
    try:
        # 1. Parse format and date range
        export_format = "ics"  # Default
        if entities and hasattr(entities, "format") and entities.format:
            export_format = entities.format.lower()

        range_start_utc: datetime = None
        range_end_utc: datetime = None
        start_date_str = getattr(nlp_result.date_info, "start_date", None) or "today"
        end_date_str = getattr(nlp_result.date_info, "end_date", None) or "next Sunday"

        try:
            start_dt = dateparser.parse(
                start_date_str, settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC"}
            )
            if start_dt:
                start_dt = start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
                range_start_utc = date_utils.normalize_to_utc(start_dt)
            end_dt = dateparser.parse(
                end_date_str, settings={"RETURN_AS_TIMEZONE_AWARE": True, "TIMEZONE": "UTC"}
            )
            if end_dt:
                end_dt = end_dt.replace(hour=23, minute=59, second=59, microsecond=999999)
                range_end_utc = date_utils.normalize_to_utc(end_dt)

            if range_start_utc is None:
                logger.warning(
                    "Failed to parse start date for export, defaulting to today.",
                    task_id=task_id,
                    input=start_date_str,
                )
                range_start_utc = date_utils.normalize_to_utc(
                    datetime.combine(datetime.now(UTC).date(), time.min)
                )
            if range_end_utc is None:
                logger.warning(
                    "Failed to parse end date for export, defaulting to 7 days from start.",
                    task_id=task_id,
                    input=end_date_str,
                )
                range_end_utc = range_start_utc + timedelta(days=7)
            if range_start_utc >= range_end_utc:
                logger.warning(
                    "Parsed start date is after end date, adjusting end date.",
                    task_id=task_id,
                    start=range_start_utc,
                    end=range_end_utc,
                )
                range_end_utc = range_start_utc + timedelta(days=7)
        except Exception as parse_err:
            logger.exception(
                "Error parsing date range for export, defaulting to next 7 days.",
                task_id=task_id,
                error=parse_err,
            )
            range_start_utc = date_utils.normalize_to_utc(
                datetime.combine(datetime.now(UTC).date(), time.min)
            )
            range_end_utc = range_start_utc + timedelta(days=7)

        logger.info(
            "Determined export date range (UTC)",
            task_id=task_id,
            start=range_start_utc,
            end=range_end_utc,
        )

        # 2. Get the correct exporter
        exporter = exporters.get(export_format)
        if not exporter:
            # This is an invalid parameter from the user/NLP, handle it here
            raise InvalidParamsError(f"Unsupported export format requested: {export_format}")

        # 3. Query events from DB
        # Let DatabaseError propagate if it occurs here
        events_to_export = await db_service.get_events_in_range(
            start=range_start_utc, end=range_end_utc
        )

        # 4. Export events
        # Let ExporterError or other Exceptions propagate if they occur here
        exported_data = exporter.export(events_to_export)
        mime_type = exporter.get_mime_type()
        file_name = f"calendar.{export_format}"

        # 5. Base64 encode the result
        encoded_bytes = base64.b64encode(exported_data.encode("utf-8")).decode("utf-8")

        # 6. Create Artifact
        file_content = FileContent(name=file_name, mimeType=mime_type, bytes=encoded_bytes)
        artifact = Artifact(name=file_name, parts=[Part(type="file", file=file_content)])

        # 7. Complete task
        result_message = f"Calendar exported successfully as {file_name}."
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        final_task = await task_manager.complete_task(task_id, agent_message, artifact)
        logger.info("Export task completed successfully", task_id=task_id)
        return final_task

    except InvalidParamsError as ipe:
        # Catch InvalidParamsError specifically to let it propagate clearly
        logger.warning(f"InvalidParamsError during export: {ipe.message}", task_id=task_id)
        raise


async def _handle_send_nlp_input(
    task_id: str,
    input_text: str,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    # Removed unused dependency conflict_resolver
) -> Task:
    """Handles the logic path for NLP text input."""
    logger.info("Processing text input via NLP", task_id=task_id, text=input_text)
    try:
        # Let NLPError propagate from here
        nlp_result = await nlp_processor.parse_natural_language_query(input_text)
        intent = nlp_result.intent
        logger.info(
            "NLP processing result",
            task_id=task_id,
            intent=intent,
            nlp_result=nlp_result.model_dump(),
        )

        if intent == "query_events":
            # Let DatabaseError propagate from here
            return await _handle_nlp_intent_query(
                task_id=task_id,
                nlp_result=nlp_result,
                task_manager=task_manager,
                db_service=db_service,
            )

        if intent == "export_calendar":
            # Let DatabaseError, ExporterError, Exception propagate from here
            # Also lets InvalidParamsError propagate if format is wrong
            return await _handle_nlp_intent_export(
                task_id=task_id,
                nlp_result=nlp_result,
                task_manager=task_manager,
                db_service=db_service,
                exporters=exporters,
                date_utils=date_utils,
            )

        # TODO: Add handlers for other intents (e.g., create_event, update_event, delete_event)
        if intent and intent != "unknown":
            logger.warning("Unhandled NLP intent", task_id=task_id, intent=intent)
            unhandled_msg = f"I understood the intent '{intent}', but I cannot handle that yet."
            agent_message = Message(role="agent", parts=[Part(type="text", text=unhandled_msg)])
            return await task_manager.fail_task(task_id, agent_message)

        # Intent not detected or unknown
        logger.warning(
            "Could not determine intent from text or intent is unknown",
            task_id=task_id,
            text=input_text,
            intent=intent,
        )
        clarification_msg = (
            "I wasn't sure what you wanted to do. Could you please rephrase your request?"
        )
        agent_message = Message(role="agent", parts=[Part(type="text", text=clarification_msg)])
        return await task_manager.require_input_task(task_id, agent_message)

    except InvalidParamsError as ipe:
        # Still catch InvalidParamsError specifically if needed for direct handling/logging
        logger.warning(
            "InvalidParamsError caught in NLP handler, re-raising.",
            task_id=task_id,
            error_message=ipe.message,
        )
        raise


# --- Main Handler ---


async def handle_tasks_send(
    params: TaskSendParams,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService = Depends(get_db_service),
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver),
    nlp_processor: NLPProcessor = Depends(get_nlp_processor),
    exporters: dict[str, CalendarExporter] = Depends(get_exporters),
    date_utils: DateUtils = Depends(get_date_utils),
) -> Task:
    """Handles the core logic for the tasks/send method by delegating to helpers."""
    logger.info("handle_tasks_send called", task_id=params.id)
    task_id = params.id
    initial_message = params.message

    # 1. Ensure initial task entry is created
    try:
        await task_manager.create_task(params=params)
        logger.debug("Initial task entry created successfully", task_id=task_id)
    except Exception as e:
        logger.exception("Failed to create initial task entry", task_id=task_id, error=e)
        # Re-raise as InternalError for the main RPC handler to catch
        raise InternalError(message=f"Failed to initialize task: {e}") from e

    # 2. Determine input type and delegate to helpers
    input_text = next(
        (part.text for part in initial_message.parts if part.type == "text" and part.text), None
    )
    input_data = next((part.data for part in initial_message.parts if part.type == "data"), None)

    try:
        if input_data and isinstance(input_data, dict):
            logger.debug("Handling structured data input", task_id=task_id)
            return await _handle_send_structured_data(
                task_id=task_id,
                input_data=input_data,
                task_manager=task_manager,
                db_service=db_service,
                conflict_resolver=conflict_resolver,
            )
        if input_text:
            logger.debug("Handling text input via NLP", task_id=task_id)
            return await _handle_send_nlp_input(
                task_id=task_id,
                input_text=input_text,
                task_manager=task_manager,
                db_service=db_service,
                nlp_processor=nlp_processor,
                exporters=exporters,
                date_utils=date_utils,
            )
        logger.warning("Unsupported input type in tasks/send", task_id=task_id)
        await task_manager.fail_task(
            task_id,
            Message(role="agent", parts=[Part(type="text", text="Unsupported input type.")]),
        )
        # Raise InvalidParamsError to be caught by the main handler
        raise InvalidParamsError(
            message="Unsupported input type provided. Must include text or data part."
        )

    # Catch A2A errors raised by helpers and let them propagate to the main handler
    except (InvalidParamsError, InternalError, NLPError) as e:
        logger.info(
            f"A2A Exception caught in handle_tasks_send, propagating: {type(e).__name__}",
            task_id=task_id,
        )
        raise


# Placeholder for actual task getting logic (Phase 2, Task 4)
async def handle_tasks_get(
    params: TaskGetParams,  # Expect parsed params
    task_manager: SchedulingTaskManager,
) -> Task:  # Changed return type hint to Task
    """Handles the logic for the tasks/get method."""
    logger.info("handle_tasks_get called", task_id=params.id)
    task = await task_manager.get_task(params.id)
    if task is None:
        logger.warning("Task not found during get request", task_id=params.id)
        raise TaskNotFoundError(f"Task with ID '{params.id}' not found.")
    return task


# Handler for tasks/cancel (Phase 4, Task 1)
async def handle_tasks_cancel(
    params: CancelTaskParams, task_manager: SchedulingTaskManager
) -> Task:
    """Handles the logic for the tasks/cancel method."""
    logger.info("handle_tasks_cancel called", task_id=params.id)
    # Exceptions (TaskNotFoundError, TaskNotCancelableError) raised by
    # task_manager.cancel_task will be caught by the main handler.
    canceled_task = await task_manager.cancel_task(task_id=params.id)
    return canceled_task


@router.post("/")
async def handle_rpc_request(
    request: JSONRPCRequest,  # Pydantic model handles initial validation
    task_manager: SchedulingTaskManager = Depends(get_task_manager),
    db_service: DatabaseService = Depends(get_db_service),  # Add db_service here too
    conflict_resolver: ConflictResolver = Depends(
        get_conflict_resolver
    ),  # Add conflict_resolver here too
    nlp_processor: NLPProcessor = Depends(get_nlp_processor),  # Add nlp_processor here too
    exporters: dict[str, CalendarExporter] = Depends(
        get_exporters
    ),  # Add exporters here too for consistency
    date_utils: DateUtils = Depends(get_date_utils),  # Inject DateUtils
) -> JSONRPCResponse:
    """Main JSON-RPC handler, routes based on method."""
    logger.debug("Received RPC request", method=request.method, request_id=request.id)
    request_id = request.id  # Store request id for response

    try:
        if request.method == "tasks/send":
            parsed_params = TaskSendParams.model_validate(request.params or {})
            # Call the handler, which now returns a Task object on success or raises A2A exceptions
            result_task = await handle_tasks_send(
                params=parsed_params,
                task_manager=task_manager,
                db_service=db_service,
                conflict_resolver=conflict_resolver,
                nlp_processor=nlp_processor,  # Pass nlp_processor
                exporters=exporters,  # Pass exporters
                date_utils=date_utils,  # Pass DateUtils
            )
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        if request.method == "tasks/get":
            parsed_params = TaskGetParams.model_validate(request.params or {})
            # Call the handler, which returns a Task object or raises TaskNotFoundError
            result_task = await handle_tasks_get(params=parsed_params, task_manager=task_manager)
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        if request.method == "tasks/cancel":
            parsed_params = CancelTaskParams.model_validate(request.params or {})
            # Call the handler, which returns a canceled Task or raises errors
            result_task = await handle_tasks_cancel(params=parsed_params, task_manager=task_manager)
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        logger.warning("Unsupported RPC method", method=request.method)
        raise MethodNotFoundError()

    # --- Exception Handling Moved Here --- #
    except (
        MethodNotFoundError,
        InvalidParamsError,
        TaskNotFoundError,
        TaskNotCancelableError,
    ) as e:
        # Catch specific A2A errors raised by handlers or param validation
        # Added TaskNotCancelableError
        logger.info(
            "A2A Error occurred within RPC handler",
            error_type=type(e).__name__,
            code=e.code,
            message=e.message,
            request_id=request_id,
        )
        return JSONRPCResponse(id=request_id, error=e.as_jsonrpc_error())

    except ValidationError as ve:
        # Handle Pydantic validation errors specifically during param parsing *within this handler*
        logger.exception(
            "Invalid parameters for method during handler validation",
            method=request.method,
            error=ve.errors(),
            request_id=request_id,
        )
        # Sanitize validation error details before creating the A2A error
        sanitized_errors = []
        for error in ve.errors():
            sanitized_error = error.copy()
            # Convert non-serializable context items to strings
            if "ctx" in sanitized_error and isinstance(sanitized_error["ctx"], dict):
                sanitized_error["ctx"] = {k: str(v) for k, v in sanitized_error["ctx"].items()}
            sanitized_errors.append(sanitized_error)

        err = InvalidParamsError(message="Invalid parameters provided.", data=sanitized_errors)
        return JSONRPCResponse(id=request_id, error=err.as_jsonrpc_error())

    # REMOVED broad NotImplementedError and Exception handlers here
    # Let them propagate to the centralized handlers in main.py
