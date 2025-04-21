from pydantic import ValidationError

from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.a2a.types import (
    Artifact,
    InternalError,
    InvalidParamsError,
    Message,
    Part,
    Task,
)
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exceptions import ConflictError
from scheduling_agent.db.models import Event
from scheduling_agent.db.schemas import EventCreate
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

async def handle_structured_create(
    task_id: str,
    input_data: dict,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
) -> Task:
    """Handles the logic path for structured data input (create event ONLY)."""
    # Logic for handling structured data (Create Event Path)
    # Basic check if it looks like a potential create request
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
                name="created_event", parts=[Part(type="data", data=created_event.model_dump(mode="json"))] # Ensure JSON serializable
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
                        c["event"].model_dump(mode="json") # Ensure JSON serializable
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
                "Invalid event data provided for creation", task_id=task_id, validation_error=ve.errors()
            )
            raise InvalidParamsError(
                message="Invalid data provided for event creation.", data=ve.errors()
            ) from ve

    else:
        # Structured data didn't match create event criteria
        logger.warning(
            "Could not determine create action from structured data", task_id=task_id, data=input_data
        )
        # Fail task directly? Or raise error?
        # Let's raise InvalidParamsError for consistency. The task might be failed by the caller if needed.
        raise InvalidParamsError(message="Could not determine create action from structured data provided.")
