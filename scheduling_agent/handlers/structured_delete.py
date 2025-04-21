import logging
import json
from typing import Any
import uuid

from pydantic import ValidationError

from scheduling_agent.a2a.types import Task, Message, TaskState
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.db.schemas import EventDelete # For parsing payload


logger = logging.getLogger(__name__)


async def handle_structured_delete(
    task_id: str,
    message: Message,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles structured deletes for events based on EventDelete payload."""
    logger.info(f"Handling structured delete for task {task_id}")

    try:
        # 1. Extract and validate the payload
        if not message.parts or message.parts[0].type != "data":
            raise ValueError("Missing or invalid data part in message for structured delete.")

        payload_data = message.parts[0].data
        if not isinstance(payload_data, dict):
            raise ValueError("Data part payload is not a dictionary.")

        try:
            delete_params = EventDelete.model_validate(payload_data)
            event_id_to_delete = delete_params.id
            logger.debug(f"Validated delete parameters for event ID: {event_id_to_delete}")
        except ValidationError as e:
            logger.warning(f"Invalid EventDelete payload for task {task_id}: {e}")
            raise ValueError(f"Invalid delete parameters: {e}")

        # 2. Call the database service to delete
        logger.info(f"Attempting to delete event {event_id_to_delete} for task {task_id}")
        success = await db_service.delete_event(event_id=event_id_to_delete)

        # 3. Complete task based on success
        if success:
            result_message = f"Successfully deleted event {event_id_to_delete}."
            logger.info(result_message)
            return await task_manager.complete_task(task_id, result_message)
        else:
            result_message = f"Event with ID {event_id_to_delete} not found for deletion."
            logger.warning(f"Event not found for deletion task {task_id}: ID {event_id_to_delete}")
            # Treat 'not found' as failure in this context
            return await task_manager.fail_task(task_id, result_message)

    except ValueError as e:
        logger.error(f"Value error processing structured delete for task {task_id}: {e}")
        return await task_manager.fail_task(task_id, f"Error processing delete: {e}")
    except Exception as e:
        # Catch other potential errors (e.g., database connection)
        logger.exception(f"Unexpected error processing structured delete for task {task_id}: {e}")
        return await task_manager.fail_task(task_id, f"An unexpected error occurred: {e}") 