# sa/a2a/command/structured_update.py
"""Handles the structured update_event intent."""

from pydantic import ValidationError
import uuid

from sa.a2a.dto import Artifact, Message, Part, StructuredUpdatePayload, Task
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.exceptions import ConflictError
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.db.strategies.update_event import UpdateEventStrategy
from sa.event.date_utils import DateUtils
from sa.a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)


async def handle_structured_update(
    task_id: str,
    message: Message,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
) -> Task:
    """Handles the logic path for structured update_event intent."""
    logger.info(f"Handling structured update_event for task {task_id}")
    event_id: uuid.UUID | None = None # Initialize event_id
    structured_payload: StructuredUpdatePayload | None = None # Initialize payload
    try:
        if not message.parts or message.parts[0].data is None:
            raise InvalidParamsError("Missing data payload in message parts.")
        input_data = message.parts[0].data

        # 1. Validate the structured payload
        structured_payload = StructuredUpdatePayload.model_validate(input_data)
        event_id = structured_payload.event_id
        update_data = structured_payload.update_data

        logger.debug("Parsed structured update payload", task_id=task_id, event_id=event_id, update_data=update_data.model_dump(exclude_unset=True))

        # 2. Call UpdateEventStrategy
        update_strategy = UpdateEventStrategy(
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils
        )
        # Strategy expects a tuple (event_id, EventUpdate object)
        updated_event_or_instance = await update_strategy.execute(data=(event_id, update_data))

        # 3. Handle outcome
        if updated_event_or_instance:
            # Success
            result_message = f"Event '{event_id}' updated successfully."
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
            # Create artifact with updated event/instance details
            artifact_data = updated_event_or_instance.model_dump(mode="json")
            artifact = Artifact(name="updated_event_details", parts=[Part(type="data", data=artifact_data)])
            final_task = await task_manager.complete_task(task_id, agent_message, artifact)
            return final_task
        else:
            # Event not found by strategy (returns None)
            logger.warning(f"Event not found during update attempt, task_id={task_id}, event_id={event_id}")
            fail_message = Message(role="agent", parts=[Part(type="text", text=f"Could not find event with ID '{event_id}' to update.")])
            final_task = await task_manager.fail_task(task_id, fail_message)
            return final_task

    except ConflictError as ce:
        # Handle potential conflicts if update_event starts checking them
        logger.warning("Conflict detected during event update", task_id=task_id, event_id=event_id, conflicts=ce.data)
        conflict_message = Message(role="agent", parts=[Part(type="text", text="Update for event failed due to scheduling conflicts. Please provide alternatives or cancel.")])
        conflict_artifact = Artifact(name="scheduling_conflicts", parts=[Part(type="data", data=ce.data)])
        final_task = await task_manager.require_input_task(task_id, conflict_message, conflict_artifact)
        return final_task
    except ValidationError as ve:
        # Handle Pydantic validation error for StructuredUpdatePayload or EventUpdate
        logger.warning("Invalid structured update payload received", task_id=task_id, validation_error=ve.errors())
        raise InvalidParamsError(message="Invalid data provided for event update.", data=ve.errors()) from ve
    except NotImplementedError as nie:
        # Handle unsupported updates (e.g., series time change)
        logger.exception("Unsupported update operation attempted", task_id=task_id, event_id=event_id, error=str(nie))
        fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update event '{event_id}': {nie!s}")])
        final_task = await task_manager.fail_task(task_id, fail_message)
        return final_task
    except ValueError as ve:
        # Handle value errors from strategy (e.g., invalid instance time)
        logger.exception("Value error during event update", task_id=task_id, event_id=event_id, error=str(ve))
        fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update event '{event_id}': {ve!s}")])
        final_task = await task_manager.fail_task(task_id, fail_message)
        return final_task
    except InvalidParamsError as ipe:
        # Catch InvalidParamsError raised internally (e.g., missing data part)
        logger.warning(f"Invalid parameters processing update for task {task_id}: {ipe}")
        # Re-raise to be caught by the caller or main error handler
        raise ipe
    except Exception as e:
        # Catch unexpected errors during update
        # Attempt to get event_id from the potentially assigned structured_payload
        event_id_str = str(getattr(structured_payload, "event_id", "unknown")) # Use payload var
        logger.exception("Unexpected error during structured event update", task_id=task_id, event_id=event_id_str, error=str(e))
        raise InternalError(message="An unexpected error occurred while updating event.") from e
