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
from scheduling_agent.a2a.types import (
    Artifact,
    CancelTaskParams,  # Add CancelTaskParams
    FileContent,  # Add FileContent
    InternalError,
    InvalidParamsError,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    MethodNotFoundError,
    ParseError, # Add ParseError
    Part,
    Task,
    TaskGetParams,
    TaskNotCancelableError,  # Add TaskNotCancelableError
    TaskNotFoundError,  # Added TaskNotFoundError
    TaskSendParams,
    StructuredUpdatePayload,
)
from scheduling_agent.core.conflict_resolver import ConflictResolver  # Import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils  # Import DateUtils
from scheduling_agent.core.exceptions import (
    ConflictError,
    NLPError,  # Added NLPError
    DatabaseError # Added DatabaseError
)
from scheduling_agent.core.exporters.base import (
    CalendarExporter,
)  # Import base exporter for type hinting
from scheduling_agent.core.nlp_processor import NLPProcessor  # Corrected case to NLPProcessor
from scheduling_agent.core.schemas import LLMSchedulingParseResult

# Import the DB table model Event from models and the DTO EventCreate from schemas
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate, EventUpdate, EventQuery

# Import db models/services for logic
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger
from scheduling_agent.a2a.task_manager import SchedulingTaskManager

# Consolidated handler import
from scheduling_agent import handlers

# Import StructuredUpdatePayload (or define it here if not moved)
# Define the structured payload here for now, can move later
import uuid

# Add ConflictError import
from scheduling_agent.core.exceptions import ConflictError, DatabaseError

logger = get_logger(__name__)
router = APIRouter()

# --- Helper functions removed, logic moved to handlers --- 

# --- Main NLP Dispatcher ---
async def _dispatch_nlp_request(
    task_id: str,
    input_text: str,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
) -> Task:
    """Processes NLP input, determines intent, and dispatches to the appropriate handler."""
    logger.info(
        "Processing NLP input", task_id=task_id, input_text=input_text[:100] # Log truncated text
    )
    try:
        # 1. Process text with NLP Processor
        nlp_result = await nlp_processor.process(input_text)
        logger.info(
            "NLP processing result",
            task_id=task_id,
            intent=nlp_result.intent,
            filters=nlp_result.filters,
            date_info=nlp_result.date_info,
        )

        # 2. Route based on intent
        intent = nlp_result.intent

        if intent == "create_event":
            return await handlers.handle_nlp_create(
                task_id, nlp_result, task_manager
            )

        elif intent == "query_events":
            return await handlers.handle_nlp_query(
                task_id, nlp_result, task_manager, db_service
            )

        elif intent == "export_calendar":
            return await handlers.handle_nlp_export(
                task_id, nlp_result, task_manager, db_service, exporters, date_utils
            )

        elif intent == "update_event":
            return await handlers.handle_nlp_update(
                task_id, nlp_result, task_manager, db_service, date_utils, conflict_resolver
            )

        elif intent == "delete_event":
            return await handlers.handle_nlp_delete(
                 task_id, nlp_result, task_manager
            )

        else:  # Includes "unknown" or other intents
            logger.warning("Unhandled NLP intent", task_id=task_id, intent=intent)
            agent_message = Message(
                role="agent",
                parts=[
                    Part(
                        type="text",
                        text="I wasn't sure what you wanted to do. Could you please rephrase your request?",
                    )
                ],
            )
            return await task_manager.require_input_task(task_id, agent_message)

    except NLPError as nlp_err:
        logger.error("NLP processing failed", task_id=task_id, error=str(nlp_err))
        agent_message = Message(
            role="agent",
            parts=[
                Part(
                    type="text",
                    text=f"I had trouble understanding your request: {nlp_err}",
                )
            ],
        )
        # Return INPUT_REQUIRED state according to spec/tests
        return await task_manager.require_input_task(task_id, agent_message)

    except (InternalError, InvalidParamsError, TaskNotFoundError, TaskNotCancelableError, DatabaseError) as a2a_err:
        # Re-raise known A2A/DB errors directly if they bubble up from intent handlers
        logger.warning(f"Propagating known error from NLP handler: {type(a2a_err).__name__}", task_id=task_id)
        raise a2a_err
    except Exception as e:
        # Catch any other unexpected exceptions from intent handlers or NLP processing
        logger.exception("Unexpected error dispatching NLP request", task_id=task_id, error=str(e))
        # Wrap in a generic InternalError
        raise InternalError(
            message="An unexpected error occurred processing your NLP request."
        ) from e


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
    """Handles the 'tasks/send' JSON-RPC method.
    Routes based on intent and data type (structured vs. NLP)."""
    task_id = params.id
    message = params.message
    # --- Get Intent and Structure Hint ---
    intent = getattr(params, 'intent', None) # Get intent if provided
    is_structured = getattr(params, 'is_structured', False) # Get hint if provided

    logger.info(
        "Received tasks/send request",
        task_id=task_id,
        intent=intent,
        is_structured=is_structured,
        message_role=message.role,
        num_parts=len(message.parts)
    )

    if not message or not message.parts:
        raise InvalidParamsError(message="Message with parts is required for tasks/send.")

    first_part = message.parts[0] # Access first part for type checking

    # --- Explicit check: Intent is required for structured data (type="data") ---
    if first_part.type == "data" and intent is None:
        logger.warning("Received structured data without an explicit intent", task_id=task_id)
        raise InvalidParamsError("An explicit 'intent' is required when sending structured data (type='data').")

    # --- Intent-Based Routing ---
    if intent == "update_event":
        logger.debug("Routing to update_event handler", task_id=task_id)
        # 1. Ensure task exists for update
        existing_task = await task_manager.get_task(task_id)
        if not existing_task:
            # If the task doesn't exist, we cannot update it. Fail immediately.
            # This prevents the creation logic from running for an update intent.
            logger.warning("Task not found for update_event intent", task_id=task_id)
            # Raising TaskNotFoundError will be caught by the main handler
            raise TaskNotFoundError(f"Task {task_id} not found, cannot process update.")

        # 2. Determine if structured or NLP update
        # Use explicit flag first, then check part type as fallback
        if is_structured or (first_part.type == "data"):
            if first_part.type != "data" or not first_part.data:
                 raise InvalidParamsError("Structured update requires data in the first message part.")
            # Call structured update helper
            return await handlers.handle_structured_update(
                task_id, first_part.data, task_manager, db_service, conflict_resolver
            )
        elif first_part.type == "text":
             # Route to NLP handler (which needs update_event logic added later)
             logger.info("Routing update_event to NLP handler", task_id=task_id)
             return await _dispatch_nlp_request(
                 task_id, first_part.text, task_manager, db_service, nlp_processor, exporters, date_utils, conflict_resolver
             )
        else:
             raise InvalidParamsError("Unsupported part type for update_event intent.")

    elif intent == "create_event": 
        logger.debug("Routing to create_event handler", task_id=task_id)
        # --- Task Creation/Retrieval --- 
        task = await task_manager.get_task(task_id)
        if not task:
            logger.info("Task not found, creating new task state", task_id=task_id)
            task = await task_manager.create_task(params)
            # If task creation itself fails, it raises an exception

        # --- Process based on type (Intent is known to be create_event here) ---
        if first_part.type == "data": # Structured Create
            if not first_part.data or not isinstance(first_part.data, dict):
                raise InvalidParamsError("Structured create_event requires non-empty 'data' part as a JSON object (dict).")
            # We know intent is create_event, directly call handler
            return await handlers.handle_structured_create(
                task_id, first_part.data, task_manager, db_service, conflict_resolver
            )
        elif first_part.type == "text": # NLP Create
            if not first_part.text:
                 raise InvalidParamsError("Text part cannot be empty for create_event intent.")
            # NLP dispatcher handles create_event intent from text
            return await _dispatch_nlp_request(
                 task_id, first_part.text, task_manager, db_service, nlp_processor, exporters, date_utils, conflict_resolver
            )
        else:
            raise InvalidParamsError(f"Unsupported first part type for create_event intent: {first_part.type}")

    # --- Handle other explicit intents for structured or text data --- 
    elif intent == "query_events":
        logger.debug("Routing to query_events handler", task_id=task_id)
        task = await task_manager.get_task(task_id) # Ensure task exists or create if needed? (Decide based on requirements)
        if not task:
            # Decide: should query create a task if not found, or fail? Let's assume fail for now.
            logger.warning("Task not found for query_events intent", task_id=task_id)
            raise TaskNotFoundError(f"Task {task_id} not found, cannot process query.")

        if first_part.type == "data": # Structured Query
             if not first_part.data or not isinstance(first_part.data, dict):
                 raise InvalidParamsError("Structured query_events requires non-empty 'data' part as a JSON object (dict).")
             try:
                 # Assume handlers.handle_structured_query exists
                 return await handlers.handle_structured_query(
                     task_id, message, task_manager, db_service
                 )
             except AttributeError:
                 logger.error("handle_structured_query handler not found in handlers", task_id=task_id)
                 raise InternalError("Structured query handler is not configured.")
        elif first_part.type == "text": # NLP Query
            if not first_part.text:
                 raise InvalidParamsError("Text part cannot be empty for query_events intent.")
            return await _dispatch_nlp_request(
                 task_id, first_part.text, task_manager, db_service, nlp_processor, exporters, date_utils, conflict_resolver
            )
        else:
            raise InvalidParamsError(f"Unsupported first part type for query_events intent: {first_part.type}")

    elif intent == "delete_event":
        logger.debug("Routing to delete_event handler", task_id=task_id)
        task = await task_manager.get_task(task_id) # Ensure task exists
        if not task:
            logger.warning("Task not found for delete_event intent", task_id=task_id)
            raise TaskNotFoundError(f"Task {task_id} not found, cannot process delete.")

        if first_part.type == "data": # Structured Delete
             if not first_part.data or not isinstance(first_part.data, dict):
                 raise InvalidParamsError("Structured delete_event requires non-empty 'data' part as a JSON object (dict).")
             try:
                 # Assume handlers.handle_structured_delete exists
                 return await handlers.handle_structured_delete(
                     task_id, message, task_manager, db_service
                 )
             except AttributeError:
                 logger.error("handle_structured_delete handler not found in handlers", task_id=task_id)
                 raise InternalError("Structured delete handler is not configured.")
        elif first_part.type == "text": # NLP Delete
            if not first_part.text:
                 raise InvalidParamsError("Text part cannot be empty for delete_event intent.")
            return await _dispatch_nlp_request(
                 task_id, first_part.text, task_manager, db_service, nlp_processor, exporters, date_utils, conflict_resolver
            )
        else:
            raise InvalidParamsError(f"Unsupported first part type for delete_event intent: {first_part.type}")

    # --- Handle NLP-only intents (or intents where structured data is not applicable/handled) --- 
    elif intent == "export_calendar":
         logger.debug("Routing export_calendar intent to NLP handler", task_id=task_id)
         if first_part.type != "text" or not first_part.text:
              raise InvalidParamsError("export_calendar intent currently only supports text input.")
         return await _dispatch_nlp_request(
              task_id, first_part.text, task_manager, db_service, nlp_processor, exporters, date_utils, conflict_resolver
         )
         
    # --- Handle case where intent is None BUT input type is text (route to NLP) --- 
    elif intent is None and first_part.type == "text":
        logger.debug("Routing text input with no intent to NLP handler", task_id=task_id)
        if not first_part.text:
            raise InvalidParamsError("Text part cannot be empty.")
        # Allow NLP to try and determine intent from text when none is provided
        task = await task_manager.get_task(task_id)
        if not task:
            logger.info("Task not found, creating new task state for NLP processing", task_id=task_id)
            task = await task_manager.create_task(params)

        return await _dispatch_nlp_request(
             task_id, first_part.text, task_manager, db_service, nlp_processor, exporters, date_utils, conflict_resolver
        )

    # --- Fallback for unhandled explicit intents --- 
    else:
        # This handles cases where intent is not None, but not one of the handled elif conditions above
        logger.warning("Received unhandled explicit intent", task_id=task_id, intent=intent)
        raise MethodNotFoundError(f"Intent '{intent}' is not supported by tasks/send.")


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
