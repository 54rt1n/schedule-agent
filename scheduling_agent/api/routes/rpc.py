from fastapi import APIRouter, Depends, Request
from pydantic import ValidationError # Import ValidationError
from scheduling_agent.common.types import (
    JSONRPCRequest, JSONRPCResponse, MethodNotFoundError, TaskSendParams, TaskGetParams, InternalError,
    TaskState, Message, Part, Artifact, InvalidParamsError, Task, EventQuery,
    TaskNotFoundError # Added TaskNotFoundError
)
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.api.dependencies import get_task_manager, get_db_service, get_conflict_resolver, get_nlp_processor # Added get_nlp_processor
from scheduling_agent.logging_config import get_logger
# Import db models/services for logic
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.models import Event, EventCreate # Removed EventQuery
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.core.conflict_resolver import ConflictResolver # Import ConflictResolver
from scheduling_agent.core.nlp_processor import NLPProcessor # Corrected case to NLPProcessor
from datetime import datetime, timedelta # Added datetime, timedelta

logger = get_logger(__name__)
router = APIRouter()

# Placeholder for actual task sending logic (Phase 2, Task 3)
async def handle_tasks_send(
    params: TaskSendParams, # Expect parsed params
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService = Depends(get_db_service), # Add db_service dependency
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver), # Add conflict_resolver dependency
    nlp_processor: NLPProcessor = Depends(get_nlp_processor) # Add nlp_processor dependency
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
                    await task_manager.fail_task(task_id, agent_message)
                    raise InternalError(message=result_message) # Raise internal error if alternative generation fails

            except ValidationError as ve:
                logger.error("Invalid event data provided", task_id=task_id, validation_error=ve.errors())
                raise InvalidParamsError(message="Invalid data provided for event creation.", data=ve.errors())
            except Exception as e:
                logger.exception("Error creating event from data", task_id=task_id, exc_info=e)
                await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text=f"Failed to create event from data: {e}")]))
                raise InternalError(message=f"Failed to create event from data: {e}")

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
            # entities = nlp_result.get("entities", {})

            logger.info("NLP processing result", task_id=task_id, intent=intent, entities=entities)

            if intent == "query_events":
                try:
                    # Validate extracted entities against EventQuery model
                    # Map NLP entities to EventQuery fields (adjust mapping as needed)
                    query_data = {
                        "start_time": entities.get("start_time"),
                        "end_time": entities.get("end_time"),
                        "title_contains": entities.get("keyword") or entities.get("title_contains"),
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
                    logger.error("Invalid query parameters extracted from NLP", task_id=task_id, validation_error=ve.errors(), extracted_entities=entities)
                    # Ask for clarification if validation fails
                    clarification_msg = f"I understood you wanted to query events, but I couldn't understand the details. Could you please rephrase your query? (Details: {ve.errors()})"
                    agent_message = Message(role="agent", parts=[Part(type="text", text=clarification_msg)])
                    final_task = await task_manager.require_input_task(task_id, agent_message)
                    return final_task

                except Exception as e:
                    logger.exception("Error querying events based on NLP input", task_id=task_id, exc_info=e)
                    await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text=f"Failed to query events: {e}")]))
                    raise InternalError(message=f"Failed to query events: {e}")

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

        except Exception as e:
            logger.exception("Error during NLP processing or handling", task_id=task_id, exc_info=e)
            await task_manager.fail_task(task_id, Message(role="agent", parts=[Part(type="text", text=f"An error occurred processing your request: {e}")]))
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
    """Handles the core logic for the tasks/get method.

    Returns:
        Task: The requested task object.
    Raises:
        TaskNotFoundError: If the task with the given ID is not found.
    """
    task_id = params.id
    logger.info("handle_tasks_get called", task_id=task_id)
    task = await task_manager.get_task(task_id)
    if not task:
        logger.warning("Task not found during get request", task_id=task_id)
        raise TaskNotFoundError(f"Task with ID '{task_id}' not found.")

    # TODO: Apply historyLength parameter if provided in params
    # if params.historyLength is not None:
    #     task.history = task.history[-params.historyLength:]

    logger.info("Task found", task_id=task_id, task_state=task.status.state)
    return task


@router.post("/")
async def handle_rpc_request(
    request: JSONRPCRequest, # Pydantic model handles initial validation
    task_manager: SchedulingTaskManager = Depends(get_task_manager),
    db_service: DatabaseService = Depends(get_db_service), # Add db_service here too
    conflict_resolver: ConflictResolver = Depends(get_conflict_resolver), # Add conflict_resolver here too
    nlp_processor: NLPProcessor = Depends(get_nlp_processor) # Add nlp_processor here too
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
                nlp_processor=nlp_processor # Pass nlp_processor
            )
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        elif request.method == "tasks/get":
            parsed_params = TaskGetParams.model_validate(request.params or {})
            # Call the handler, which returns a Task object or raises TaskNotFoundError
            result_task = await handle_tasks_get(params=parsed_params, task_manager=task_manager)
            # Construct success response
            return JSONRPCResponse(id=request_id, result=result_task.model_dump())

        else:
            logger.warning("Unsupported RPC method", method=request.method)
            raise MethodNotFoundError()

    # --- Exception Handling Moved Here --- #
    except (MethodNotFoundError, InvalidParamsError, InternalError, TaskNotFoundError) as e:
        # Catch specific A2A errors raised by handlers
        logger.info("A2A Error occurred", error_type=type(e).__name__, code=e.code, message=e.message, request_id=request_id)
        return JSONRPCResponse(id=request_id, error=e.as_jsonrpc_error())

    except ValidationError as ve:
        # Handle Pydantic validation errors during param parsing
        logger.error("Invalid parameters for method", method=request.method, error=ve.errors(), request_id=request_id)
        err = InvalidParamsError(message="Invalid parameters provided.", data=ve.errors())
        return JSONRPCResponse(id=request_id, error=err.as_jsonrpc_error())

    except NotImplementedError as e:
        logger.error("Method handler not implemented", method=request.method, error=str(e), request_id=request_id)
        internal_err = InternalError(message=f"Method '{request.method}' is not fully implemented.")
        return JSONRPCResponse(id=request_id, error=internal_err.as_jsonrpc_error())

    except Exception as e:
        logger.exception("Unexpected error handling RPC request", method=request.method, exc_info=e, request_id=request_id)
        internal_err = InternalError(message=f"An unexpected error occurred handling method '{request.method}'.", data=str(e))
        return JSONRPCResponse(id=request_id, error=internal_err.as_jsonrpc_error()) 