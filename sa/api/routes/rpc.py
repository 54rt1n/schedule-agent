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
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Part,
    StructuredPayloadWrapper,
    Task,
    TaskGetParams,
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
from sa.core.exceptions import (
    DatabaseError, NLPError, ConflictError, ExporterError,
    EventNotFoundError, TaskNotFoundError, CoreLogicError,
)
from sa.a2a.exceptions import (
    BaseA2AError,
    MethodNotFoundError as A2AMethodNotFoundError,
    InvalidParamsError as A2AInvalidParamsError,
    InternalError as A2AInternalError,
    TaskNotFoundError as A2ATaskNotFoundError,
    TaskNotCancelableError as A2ATaskNotCancelableError,
    InvalidTaskStateError as A2AInvalidTaskStateError,
    AgentCapabilityError as A2AAgentCapabilityError,
)

logger = get_logger(__name__)
router = APIRouter()

# --- Main Handler ---
async def handle_tasks_send(
    params: TaskSendParams,
    task_manager: SchedulingTaskManager = Depends(get_task_manager),
    db_service: DatabaseService = Depends(get_db_service),
    nlp_processor: NLPProcessor = Depends(get_nlp_processor),
    exporters: dict[str, CalendarExporter] = Depends(get_exporters),
    date_utils: DateUtils = Depends(get_date_utils),
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver),
) -> Task:
    """Handles the 'tasks/send' JSON-RPC method. Routes based on message part type and wrapped intent."""
    task_id = params.id
    message = params.message

    # --- Ensure Task Exists or Create It --- #
    try:
        task = await task_manager.get_task(task_id)
        logger.debug(f"Task {task_id} already exists, proceeding.")
    except A2ATaskNotFoundError:
        logger.info(f"Task {task_id} not found, creating new task.")
        try:
            task = await task_manager.create_task(params)
        except Exception as e:
            logger.exception(f"Failed to create task {task_id} during handle_tasks_send", exc_info=e)
            raise A2AInternalError(f"Failed to initialize task state for task ID {task_id}") from e

    # --- Routing Logic --- #
    if not message or not message.parts:
        raise A2AInvalidParamsError("Message with parts is required for tasks/send.")

    first_part = message.parts[0]

    if first_part.type == "text":
        logger.info(f"Routing text input to NLP handler for task_id={task_id}")
        return await handle_nlp_request(
            task_id=task_id, message=message,
            task_manager=task_manager, db_service=db_service,
            nlp_processor=nlp_processor, exporters=exporters,
            date_utils=date_utils, conflict_resolver=conflict_resolver
        )

    elif first_part.type == "data":
        logger.info(f"Processing structured data input for task_id={task_id}")
        try:
            wrapped_payload = StructuredPayloadWrapper.model_validate(first_part.data)
            intent = wrapped_payload.intent
            payload = wrapped_payload.payload # Extract payload

            # --- Validate payload is a dict before routing --- #
            if not isinstance(payload, dict):
                logger.warning(f"Structured data payload is not a dictionary for task {task_id}")
                raise A2AInvalidParamsError("Structured data payload must be a JSON object (dictionary).")

            logger.debug(f"Structured intent '{intent}' received for task {task_id}")

            # Route based on the intent
            if intent == "create_event":
                return await handle_structured_create(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service,
                    conflict_resolver=conflict_resolver, date_utils=date_utils
                )
            elif intent == "query_events":
                return await handle_structured_query(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service
                )
            elif intent == "delete_event":
                return await handle_structured_delete(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service
                )
            elif intent == "update_event":
                return await handle_structured_update(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service,
                    conflict_resolver=conflict_resolver, date_utils=date_utils
                )
            elif intent == "export_calendar":
                return await handle_structured_export(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service,
                    exporters=exporters, date_utils=date_utils
                )
            else:
                logger.warning(f"Received unsupported intent '{intent}' in structured data for task {task_id}")
                raise A2AInvalidParamsError(f"Unsupported intent '{intent}' provided in structured data payload.")

        except ValidationError as e:
            logger.warning(f"Failed to parse structured data payload wrapper for task {task_id}: {e.errors()}")
            raise A2AInvalidParamsError("Invalid format for structured data payload. Expected {'intent': ..., 'payload': ...}.", data=e.errors()) from e
        except BaseA2AError as e:
            raise e
        except Exception as e:
            # Catch-all for unexpected errors during data parsing/routing
            logger.exception(f"Unexpected error processing structured data for task {task_id}: {e}", exc_info=True)
            raise A2AInternalError("Internal error processing structured data.") from e

    else:
        # Handle other part types if necessary, or raise error
        logger.warning(f"Received unsupported message part type '{first_part.type}' for task {task_id}")
        raise A2AInvalidParamsError(f"Unsupported message part type: {first_part.type}")


# Placeholder for actual task getting logic (Phase 2, Task 4)
async def handle_tasks_get(
    params: TaskGetParams,  # Expect parsed params
    task_manager: SchedulingTaskManager,
) -> Task:  # Changed return type hint to Task
    """Handles the logic for the tasks/get method."""
    logger.info(f"handle_tasks_get called for task_id={params.id}")
    # task_manager.get_task now raises A2ATaskNotFoundError directly if not found.
    # This exception will be caught by handle_rpc_request.
    task = await task_manager.get_task(task_id=params.id)
    # Removed the redundant 'if task is None:' check.
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
    request: JSONRPCRequest,
    task_manager: SchedulingTaskManager = Depends(get_task_manager),
    db_service: DatabaseService = Depends(get_db_service),
    nlp_processor: NLPProcessor = Depends(get_nlp_processor),
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver),
    exporters: dict[str, CalendarExporter] = Depends(get_exporters),
    date_utils: DateUtils = Depends(get_date_utils),
) -> JSONRPCResponse:
    """Main JSON-RPC handler, routes based on method."""
    # Access settings via app state
    logger.debug(f"Received RPC request: method={request.method}, request_id={request.id}")
    request_id = request.id  # Store request id for response

    try:
        # --- Method Routing ---
        if request.method == "tasks/send":
            parsed_params = TaskSendParams.model_validate(request.params or {})
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

        elif request.method == "tasks/get":
            parsed_params = TaskGetParams.model_validate(request.params or {})
            result_task = await handle_tasks_get(params=parsed_params, task_manager=task_manager)
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif request.method == "tasks/cancel":
            parsed_params = CancelTaskParams.model_validate(request.params or {})
            result_task = await handle_tasks_cancel(params=parsed_params, task_manager=task_manager)
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        else:
            logger.warning(f"Unsupported RPC method requested: {request.method}")
            # Raise the *actual* exception now, not the DTO
            raise A2AMethodNotFoundError(f"Method '{request.method}' not found.")
    # These are already A2A errors, so we can raise them directly
    except BaseA2AError as known_error:
        raise known_error
    # Handle known errors here, translate to A2A errors before raising
    except ValidationError as validation_error:
        logger.warning(
            f"Caught Pydantic ValidationError within route: {validation_error}",
            errors=validation_error.errors(),
        )

        raise A2AInvalidParamsError(
            message=f"Invalid parameters for method execution: {validation_error.title}",
            data=validation_error.errors()
        )
    except EventNotFoundError as known_error:
        raise A2AInvalidParamsError(message=known_error.message, data=known_error.data)
    except TaskNotFoundError as known_error:
        raise A2ATaskNotFoundError(message=known_error.message, data=known_error.data)
    except DatabaseError as known_error:
        raise A2AInternalError(message=known_error.message, data=known_error.data)
    except NLPError as known_error:
        raise A2AInternalError(message=known_error.message, data=known_error.data)
    except ConflictError as known_error:
        error_data = {
            "details": "Scheduling conflict detected.",
            "conflicts": [c["event"].model_dump(mode="json") for c in known_error.data if c.get("event")]
        }
        raise A2AInvalidParamsError(
            message=known_error.message,
            data=error_data
        )
    except ExporterError as known_error:
        raise A2AInternalError(message=known_error.message, data=known_error.data)
    except CoreLogicError as known_error:
        raise A2AInternalError(message=known_error.message, data=known_error.data)
    # Catch any other truly unexpected exception originating *within* this handler
    except Exception as e:
        # Log the unexpected error
        logger.exception(
            f"Caught unexpected exception in RPC handler for method {request.method}: {e}",
            exc_info=True
        )
        # Raise the A2AInternalError *exception*.
        # This will be caught by the BaseA2AError handler in __main__.py
        raise A2AInternalError(
            message=f"Internal server error processing method {request.method}",
            data={"error_type": type(e).__name__}
        ) from e

