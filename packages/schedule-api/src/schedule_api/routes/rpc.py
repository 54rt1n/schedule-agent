# packages/schedule-api/src/schedule_api/routes/rpc.py
# Copyright (C) 2025 Martin Bukowski

"""Routes for JSON-RPC API."""

from fastapi import APIRouter, Depends
from pydantic import ValidationError

from schedule_a2a.command import (
    handle_nlp_request,
    handle_structured_create,
    handle_structured_delete,
    handle_structured_export,
    handle_structured_query,
    handle_structured_update,
    # Add contact handlers
    handle_structured_create_contact,
    handle_structured_query_contact,
    handle_structured_update_contact,
    handle_structured_delete_contact,
)
from schedule_a2a.dto import (
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
from schedule_api.dependencies import (
    get_date_utils,
    get_db_service,
    get_exporters,
    get_nlp_processor,
    get_task_manager,
    get_conflict_resolver,
)
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.core.logging_config import get_logger
from schedule_core.db.service import DatabaseService
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.exporters.base import CalendarExporter
from schedule_nlp.nlp_processor import NLPProcessor
from schedule_core.core.exceptions import (
    DatabaseError, NLPError, ConflictError, ExporterError,
    EventNotFoundError, TaskNotFoundError, CoreLogicError,
)
from schedule_a2a.exceptions import (
    BaseA2AError,
    MethodNotFoundError as A2AMethodNotFoundError,
    InvalidParamsError as A2AInvalidParamsError,
    InternalError as A2AInternalError,
    TaskNotFoundError as A2ATaskNotFoundError,
)

logger = get_logger(__name__)
router = APIRouter()

# --- Helper for Task Failure ---

async def _attempt_fail_task_on_error(
    task_manager: SchedulingTaskManager,
    task_id: str | None,
    error: Exception | None
):
    """Attempts to mark a task as FAILED, logging any secondary errors."""
    if not task_id or not error:
        # Cannot fail task if ID or error is missing
        return

    logger.info(f"Attempting to fail task {task_id} due to {error.__class__.__name__}")
    try:
        # Create an error message
        if isinstance(error, BaseA2AError) and hasattr(error, 'to_message'):
            # Use the specific error's message generation if available
            error_message = error.to_message()
        else:
            # Fallback to a generic message using the exception string
            error_message = Message(role="agent", parts=[Part(type="text", text=f"Task failed due to error: {error}")])

        await task_manager.fail_task(task_id, error_message)
        logger.info(f"Task {task_id} marked as FAILED.")
    except Exception as fail_exc:
        # Log if failing the task itself fails, but don't raise to avoid masking original error context
        logger.exception(f"Failed to mark task {task_id} as FAILED: {fail_exc}", exc_info=True)


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
        await task_manager.get_task(task_id)
        logger.debug(f"Task {task_id} already exists, proceeding.")
        await task_manager.append_message(task_id, message)
    except A2ATaskNotFoundError:
        logger.info(f"Task {task_id} not found, creating new task.")
        try:
            await task_manager.create_task(params)
        except Exception as e:
            logger.exception(f"Failed to create task {task_id} during handle_tasks_send", exc_info=e)
            raise A2AInternalError(f"Failed to initialize task state for task ID {task_id}") from e

    # --- Routing Logic --- #
    if not message or not message.parts:
        raise A2AInvalidParamsError("Message with parts is required for tasks/send.")

    first_part = message.parts[0]

    if first_part.type == "text":
        logger.info(f"Routing text input to NLP handler for task_id={task_id}")
        interpreted = await handle_nlp_request(
            task_id=task_id, message=message,
            task_manager=task_manager, db_service=db_service,
            nlp_processor=nlp_processor, exporters=exporters,
            date_utils=date_utils, conflict_resolver=conflict_resolver
        )
        if isinstance(interpreted, Task):
            return interpreted
        first_part = Part(type="data", data=interpreted.model_dump())

    if first_part.type == "data":
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
                    task_manager=task_manager, db_service=db_service,
                    date_utils=date_utils
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
            # --- Contact Intents ---
            elif intent == "create_contact":
                return await handle_structured_create_contact(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service
                )
            elif intent == "query_contacts":
                return await handle_structured_query_contact(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service
                )
            elif intent == "update_contact":
                return await handle_structured_update_contact(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service
                )
            elif intent == "delete_contact":
                return await handle_structured_delete_contact(
                    task_id=task_id, payload=payload,
                    task_manager=task_manager, db_service=db_service
                )
            # --- Fallback --- #
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
    logger.debug(f"Received RPC request: method={request.method}, request_id={request.id}")
    request_id = request.id
    task_successful = False
    exception_caught: Exception | None = None
    task_id_to_fail: str | None = None # Store potential task ID here

    try:
        # --- Method Routing ---
        if request.method == "tasks/send":
            parsed_params = TaskSendParams.model_validate(request.params or {})
            task_id_to_fail = parsed_params.id # Store task ID for potential failure
            result_task = await handle_tasks_send(
                params=parsed_params,
                task_manager=task_manager,
                db_service=db_service,
                nlp_processor=nlp_processor,
                exporters=exporters,
                date_utils=date_utils,
                conflict_resolver=conflict_resolver,
            )
            task_successful = True
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif request.method == "tasks/get":
            parsed_params = TaskGetParams.model_validate(request.params or {})
            # task_id_to_fail = parsed_params.id # tasks/get doesn't modify, maybe don't auto-fail?
            result_task = await handle_tasks_get(params=parsed_params, task_manager=task_manager)
            task_successful = True
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif request.method == "tasks/cancel":
            parsed_params = CancelTaskParams.model_validate(request.params or {})
            task_id_to_fail = parsed_params.id # Store task ID for potential failure
            result_task = await handle_tasks_cancel(params=parsed_params, task_manager=task_manager)
            task_successful = True
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        else:
            logger.warning(f"Unsupported RPC method requested: {request.method}")
            raise A2AMethodNotFoundError(f"Method '{request.method}' not found.")

    # --- Exception Handling ---
    # These are already A2A errors, let them propagate to the main handler
    except BaseA2AError as known_error:
        exception_caught = known_error
        raise known_error # Re-raise for main handler

    # Handle known core errors, translate to A2A errors before raising
    except ValidationError as validation_error:
        logger.warning(
            f"Caught Pydantic ValidationError within route: {validation_error}",
            errors=validation_error.errors(),
        )
        a2a_error = A2AInvalidParamsError(
            message=f"Invalid parameters for method execution: {validation_error.title}",
            data=validation_error.errors()
        )
        exception_caught = a2a_error
        raise a2a_error
    except EventNotFoundError as core_error:
        a2a_error = A2AInvalidParamsError(message=core_error.message, data=core_error.data)
        exception_caught = a2a_error
        raise a2a_error
    except TaskNotFoundError as core_error:
        a2a_error = A2ATaskNotFoundError(message=core_error.message, data=core_error.data)
        exception_caught = a2a_error
        raise a2a_error
    except DatabaseError as core_error:
        a2a_error = A2AInternalError(message=core_error.message, data=core_error.data)
        exception_caught = a2a_error
        raise a2a_error
    except NLPError as core_error:
        a2a_error = A2AInternalError(message=core_error.message, data=core_error.data)
        exception_caught = a2a_error
        raise a2a_error
    except ConflictError as core_error:
        error_data = {
            "details": "Scheduling conflict detected.",
            "conflicts": [c["event"].model_dump(mode="json") for c in core_error.data if c.get("event")]
        }
        a2a_error = A2AInvalidParamsError(
            message=core_error.message,
            data=error_data
        )
        exception_caught = a2a_error
        raise a2a_error
    except ExporterError as core_error:
        a2a_error = A2AInternalError(message=core_error.message, data=core_error.data)
        exception_caught = a2a_error
        raise a2a_error
    except CoreLogicError as core_error:
        a2a_error = A2AInternalError(message=core_error.message, data=core_error.data)
        exception_caught = a2a_error
        raise a2a_error

    # Catch any other truly unexpected exception
    except Exception as e:
        exception_caught = e # Capture unexpected exception
        logger.exception(
            f"Caught unexpected exception in RPC handler for method {request.method}: {e}",
            exc_info=True
        )
        # Raise a generic A2AInternalError that will be caught by the main handler
        raise A2AInternalError(
            message=f"Internal server error processing method {request.method}",
            data={"error_type": type(e).__name__}
        ) from e

    finally:
        # --- Attempt to Fail Task on Error ---
        # This runs regardless of whether an exception was handled or not.
        # It checks if the task was *not* marked successful AND an exception was caught.
        if not task_successful and exception_caught:
            # Try to determine task ID if not already set (e.g., for errors during parsing)
            if not task_id_to_fail and isinstance(request.params, dict):
                 if request.method in ("tasks/send", "tasks/cancel"): # Add other relevant methods if needed
                     task_id_to_fail = request.params.get("id")

            # Call the helper function to attempt failure
            await _attempt_fail_task_on_error(
                task_manager=task_manager,
                task_id=task_id_to_fail,
                error=exception_caught
            )
            # NOTE: The original exception is still propagating if it was raised in an except block.
            # This finally block just adds the side effect of trying to fail the task.

