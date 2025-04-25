# sa/api/routes/rpc.py
"""Routes for JSON-RPC API."""

from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError

from sa.a2a.command import (
    handle_nlp_request,
    handle_structured_create,
    handle_structured_delete,
    handle_structured_export,
    handle_structured_query,
    handle_structured_update,
)
from sa.a2a.dto import (
    CancelTaskParams,
    InternalError,
    InvalidParamsError,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    MethodNotFoundError,
    Part,
    Task,
    TaskGetParams,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskSendParams,
)
from sa.api.dependencies import (
    get_date_utils,
    get_db_service,
    get_exporters,
    get_nlp_processor,
    get_task_manager,
    get_conflict_resolver,
)
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.nlp.nlp_processor import NLPProcessor

logger = get_logger(__name__)
router = APIRouter()

# --- Main Handler ---
async def handle_tasks_send(
    params: TaskSendParams,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService = Depends(get_db_service),
    nlp_processor: NLPProcessor = Depends(get_nlp_processor),
    exporters: dict[str, CalendarExporter] = Depends(get_exporters),
    date_utils: DateUtils = Depends(get_date_utils),
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver),
) -> Task:
    """Handles the 'tasks/send' JSON-RPC method.
    Routes based on intent and data type (structured vs. NLP)."""
    task_id = params.id
    message = params.message

    # --- Ensure Task Exists in DB --- #
    # Attempt to get the task first
    task = await task_manager.get_task(task_id)
    if not task:
        logger.info(f"Task {task_id} not found, creating new task.")
        try:
            # create_task should save and return the Pydantic Task
            task = await task_manager.create_task(params)
        except Exception as e:
            # If task creation fails, we cannot proceed. Log and raise an internal error.
            logger.exception(f"Failed to create task {task_id} in handle_tasks_send", exc_info=e)
            raise InternalError(f"Failed to initialize task state for task ID {task_id}")
    else:
        logger.debug(f"Task {task_id} already exists, proceeding with handling.")

    # --- Get Intent and Structure Hint --- #
    # Note: params might be different from task.params if task already existed
    # Use the params from the current request for routing logic.
    intent = getattr(params, 'intent', None) # Get intent if provided
    is_structured = getattr(params, 'is_structured', False) # Get hint if provided

    logger.info(
        f"Received tasks/send request for task_id={task_id}: "
        f"intent={intent}, is_structured={is_structured}, "
        f"message_role={message.role}, num_parts={len(message.parts)}"
    )

    if not message or not message.parts:
        raise InvalidParamsError(message="Message with parts is required for tasks/send.")

    first_part = message.parts[0] # Access first part for type checking

    # --- Explicit check: Intent is required for structured data (type="data") ---
    if first_part.type == "data" and intent is None:
        logger.warning(f"Received structured data without an explicit intent for task_id={task_id}")
        raise InvalidParamsError("An explicit 'intent' is required when sending structured data (type='data').")
    elif first_part.type == "text":
        logger.info(f"Routing text input to NLP handler for task_id={task_id}")
        return await handle_nlp_request(
            task_id=task_id, message=message,
            task_manager=task_manager, db_service=db_service,
            nlp_processor=nlp_processor, exporters=exporters,
            date_utils=date_utils, conflict_resolver=conflict_resolver
        )
    elif intent == "update_event":
        logger.debug(f"Routing to update_event handler for task_id={task_id}")
        # 1. Ensure task exists for update
        existing_task = await task_manager.get_task(task_id)
        if not existing_task:
            # If the task doesn't exist, we cannot update it. Fail immediately.
            # This prevents the creation logic from running for an update intent.
            logger.warning(f"Task not found for update_event intent: task_id={task_id}")
            # Raising TaskNotFoundError will be caught by the main handler
            raise TaskNotFoundError(f"Task {task_id} not found, cannot process update.")

        # 2. Determine if structured or NLP update
        # Use explicit flag first, then check part type as fallback
        return await handle_structured_update(
            task_id=task_id, input_data=first_part.data,
            task_manager=task_manager, db_service=db_service,
            conflict_resolver=conflict_resolver
        )

    elif intent == "create_event": 
        logger.debug(f"Routing to create_event handler for task_id={task_id}")
        # --- Task Creation/Retrieval --- 
        task = await task_manager.get_task(task_id)
        if not task:
            logger.info(f"Task not found, creating new task state for task_id={task_id}")
            task = await task_manager.create_task(params)
            # If task creation itself fails, it raises an exception

        return await handle_structured_create(
            task_id=task_id, input_data=first_part.data,
            task_manager=task_manager, db_service=db_service,
            conflict_resolver=conflict_resolver, date_utils=date_utils
        )

    # --- Handle other explicit intents for structured or text data --- 
    elif intent == "query_events":
        logger.debug(f"Routing to query_events handler for task_id={task_id}")
        task = await task_manager.get_task(task_id) # Ensure task exists or create if needed? (Decide based on requirements)
        if not task:
            # Decide: should query create a task if not found, or fail? Let's assume fail for now.
            logger.warning(f"Task not found for query_events intent: task_id={task_id}")
            raise TaskNotFoundError(f"Task {task_id} not found, cannot process query.")

        return await handle_structured_query(
            task_id=task_id, message=message,
            task_manager=task_manager, db_service=db_service
        )

    elif intent == "delete_event":
        logger.debug(f"Routing to delete_event handler for task_id={task_id}")
        task = await task_manager.get_task(task_id) # Ensure task exists
        if not task:
            logger.warning(f"Task not found for delete_event intent: task_id={task_id}")
            raise TaskNotFoundError(f"Task {task_id} not found, cannot process delete.")

        return await handle_structured_delete(
            task_id=task_id, message=message, task_manager=task_manager, db_service=db_service
        )

    # --- Add route for structured export_calendar --- 
    elif intent == "export_calendar":
        # This handles explicit structured requests like: { "type": "data", "data": {"format": "ics", "start_date": ...} }
        if first_part.type != "data":
            # If intent=export_calendar but type=text, it should have been handled by handle_nlp_request.
            # If type is something else, it's an invalid request structure for this intent.
            logger.warning(f"Received export_calendar intent with non-data part type for task_id={task_id}")
            raise InvalidParamsError("export_calendar intent requires structured data (type='data'). Text queries should not specify this intent.")

        logger.debug(f"Routing structured export_calendar request to handler for task_id={task_id}")
        return await handle_structured_export(
             task_id=task_id,
             message=message, # Pass the original message containing the structured data
             task_manager=task_manager,
             db_service=db_service,
             exporters=exporters,
             date_utils=date_utils,
        )

    # --- Fallback for unhandled explicit intents --- 
    else:
        # This handles cases where intent is not None, but not one of the handled elif conditions above
        logger.warning(f"Received unhandled explicit intent for task_id={task_id}: intent={intent}")
        raise MethodNotFoundError(f"Intent '{intent}' is not supported by tasks/send.")


# Placeholder for actual task getting logic (Phase 2, Task 4)
async def handle_tasks_get(
    params: TaskGetParams,  # Expect parsed params
    task_manager: SchedulingTaskManager,
) -> Task:  # Changed return type hint to Task
    """Handles the logic for the tasks/get method."""
    logger.info(f"handle_tasks_get called for task_id={params.id}")
    task = await task_manager.get_task(task_id=params.id)
    if task is None:
        logger.warning(f"Task not found during get request: task_id={params.id}")
        raise TaskNotFoundError(f"Task with ID '{params.id}' not found.")
    return task


# Handler for tasks/cancel (Phase 4, Task 1)
async def handle_tasks_cancel(
    params: CancelTaskParams, task_manager: SchedulingTaskManager
) -> Task:
    """Handles the logic for the tasks/cancel method."""
    logger.info(f"handle_tasks_cancel called for task_id={params.id}")
    # Exceptions (TaskNotFoundError, TaskNotCancelableError) raised by
    # task_manager.cancel_task will be caught by the main handler.
    canceled_task = await task_manager.cancel_task(task_id=params.id)
    return canceled_task


@router.post("/")
async def handle_rpc_request(
    fastapi_request: Request, # Inject FastAPI Request object
    rpc_request: JSONRPCRequest,  # Keep Pydantic model for body validation
    task_manager: SchedulingTaskManager = Depends(get_task_manager),
    db_service: DatabaseService = Depends(get_db_service),
    nlp_processor: NLPProcessor = Depends(get_nlp_processor),
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver),
    exporters: dict[str, CalendarExporter] = Depends(get_exporters),
    date_utils: DateUtils = Depends(get_date_utils),
) -> JSONRPCResponse:
    """Main JSON-RPC handler, routes based on method."""
    # Access settings via app state
    settings = fastapi_request.app.state.settings

    logger.debug(f"Received RPC request: method={rpc_request.method}, request_id={rpc_request.id}")
    request_id = rpc_request.id  # Store request id for response

    try:
        if rpc_request.method == "tasks/send":
            parsed_params = TaskSendParams.model_validate(rpc_request.params or {})
            result_task = await handle_tasks_send(
                params=parsed_params,
                task_manager=task_manager,
                db_service=db_service,
                nlp_processor=nlp_processor,
                exporters=exporters,
                date_utils=date_utils,
                conflict_resolver=conflict_resolver,
            )
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif rpc_request.method == "tasks/get":
            parsed_params = TaskGetParams.model_validate(rpc_request.params or {})
            result_task = await handle_tasks_get(params=parsed_params, task_manager=task_manager)
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif rpc_request.method == "tasks/cancel":
            parsed_params = CancelTaskParams.model_validate(rpc_request.params or {})
            result_task = await handle_tasks_cancel(params=parsed_params, task_manager=task_manager)
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        else:
            # If method doesn't match any known method, raise MethodNotFoundError
            logger.warning(f"Unsupported RPC method requested: {rpc_request.method}")
            raise MethodNotFoundError(f"Method '{rpc_request.method}' not found.")

    # --- Specific A2A Error Handling --- # 
    except MethodNotFoundError as e:
        # Explicitly handle MethodNotFoundError first
        logger.info(f"Method not found: '{e.message}', request_id={request_id}")
        return JSONRPCResponse(id=request_id, error=e.as_jsonrpc_error())
    
    except (InvalidParamsError, TaskNotFoundError, TaskNotCancelableError) as e:
        # Handle other known A2A application errors
        logger.info(
            f"A2A Application Error: error_type={type(e).__name__}, "
            f"code={e.code}, message='{e.message}', request_id={request_id}"
        )
        return JSONRPCResponse(id=request_id, error=e.as_jsonrpc_error())

    except ValidationError as ve:
        # Handle Pydantic validation errors during param parsing *within this handler*
        logger.warning(
            f"Invalid parameters during handler validation: "
            f"method={rpc_request.method}, error_summary={ve.errors(include_context=False)}, request_id={request_id}"
        )
        
        error_data = None # Default to no data
        if settings.include_validation_error_detail:
            try:
                # Safely create data payload ONLY if setting is True
                error_data = {"details": ve.errors(include_context=False)}
            except Exception as sanitization_error:
                logger.exception(f"Failed to sanitize ValidationError details: {sanitization_error}", exc_info=sanitization_error)
                error_data = {"details": "Failed to serialize validation error details."}
        
        err = InvalidParamsError(
            message="Invalid parameters provided for method.", 
            data=error_data # Pass potentially None data
        )
        return JSONRPCResponse(id=request_id, error=err.as_jsonrpc_error())
    
    except Exception as e:
        # Catch-all for unexpected errors within the RPC handlers
        logger.exception(
            f"Unexpected internal error processing RPC request: method={rpc_request.method}, "
            f"error_type={type(e).__name__}, error_message='{str(e)}', request_id={request_id}",
            exc_info=e
        )
        internal_err = InternalError(
            message="An unexpected internal server error occurred.",
            data={"method": rpc_request.method, "error_type": type(e).__name__}
        )
        return JSONRPCResponse(id=request_id, error=internal_err.as_jsonrpc_error())
