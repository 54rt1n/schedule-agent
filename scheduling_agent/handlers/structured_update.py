from pydantic import ValidationError

from scheduling_agent.a2a.types import (
    Artifact,
    InternalError,
    InvalidParamsError,
    Message,
    Part,
    StructuredUpdatePayload,
    Task,
)
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

async def handle_structured_update(
    task_id: str,
    input_data: dict, # This will be the parsed StructuredUpdatePayload.data
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
) -> Task:
    """Handles the logic path for structured update_event intent."""
    logger.info("Handling structured update_event", task_id=task_id)
    try:
        # 1. Validate the structured payload
        structured_payload = StructuredUpdatePayload.model_validate(input_data)
        event_id = structured_payload.event_id
        update_data = structured_payload.update_data

        logger.debug("Parsed structured update payload", task_id=task_id, event_id=event_id, update_data=update_data.model_dump(exclude_unset=True))

        # 2. Call DB service to update the event
        # Note: db_service.update_event handles EventNotFound internally (returns None)
        # and raises NotImplementedError for unsupported updates.
        updated_event_or_instance = await db_service.update_event(
            event_id=event_id, update_data=update_data
        )

        # 3. Handle outcome
        if updated_event_or_instance:
            # Success
            result_message = f"Event '{event_id}' updated successfully."
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
            # Create artifact with updated event/instance details
            artifact_data = updated_event_or_instance.model_dump(mode='json')
            artifact = Artifact(name="updated_event_details", parts=[Part(type="data", data=artifact_data)])
            final_task = await task_manager.complete_task(task_id, agent_message, artifact)
            return final_task
        else:
            # Event not found by db_service.update_event
            logger.warning("Event not found during update attempt", task_id=task_id, event_id=event_id)
            agent_message = Message(role="agent", parts=[Part(type="text", text=f"Could not find event with ID '{event_id}' to update.")])
            # Fail the A2A task
            final_task = await task_manager.fail_task(task_id, agent_message)
            return final_task

    except ConflictError as ce:
        # Handle potential conflicts if update_event starts checking them
        logger.warning("Conflict detected during event update", task_id=task_id, event_id=event_id, conflicts=ce.conflicts)
        # Request input instead of failing
        agent_message = Message(role="agent", parts=[Part(type="text", text=f"Update for event '{event_id}' failed due to scheduling conflicts. Please provide alternatives or cancel.")])
        # Create artifact with conflict details
        # Ensure conflict data is serializable (dicts are fine)
        conflict_artifact = Artifact(name="scheduling_conflicts", parts=[Part(type="data", data=ce.conflicts)])
        final_task = await task_manager.require_input_task(task_id, agent_message, conflict_artifact)
        return final_task
    except NotImplementedError as nie:
        # Handle unsupported updates (e.g., series time change)
        logger.error("Unsupported update operation attempted", task_id=task_id, event_id=event_id, error=str(nie))
        agent_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update event '{event_id}': {str(nie)}")])
        final_task = await task_manager.fail_task(task_id, agent_message)
        return final_task
    except ValueError as ve:
        # Handle validation errors within update logic (e.g., missing instance times)
        logger.error("Value error during event update", task_id=task_id, event_id=event_id, error=str(ve))
        agent_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update event '{event_id}': {str(ve)}")])
        final_task = await task_manager.fail_task(task_id, agent_message)
        return final_task
    except ValidationError as ve:
        # Handle Pydantic validation error for StructuredUpdatePayload
        logger.warning("Invalid structured update payload received", task_id=task_id, validation_error=ve.errors())
        raise InvalidParamsError(message="Invalid data provided for event update.", data=ve.errors()) from ve
    except Exception as e:
        # Catch unexpected errors during update
        logger.exception("Unexpected error during structured event update", task_id=task_id, event_id=getattr(structured_payload, 'event_id', 'unknown'), error=str(e))
        raise InternalError(message=f"An unexpected error occurred while updating event.") from e 