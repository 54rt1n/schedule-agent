# sa/a2a/command/structured_delete.py
"""Handles the logic path for structured data input (delete event ONLY)."""

from pydantic import ValidationError
from typing import Any
import uuid

from sa.a2a.dto import Task, Message, Part
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.logging_config import get_logger
from sa.db.dao import EventDelete
from sa.db.service import DatabaseService
from sa.db.strategies.delete_event import DeleteEventStrategy

logger = get_logger(__name__)


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

        event_id_to_delete : uuid.UUID | None = None

        try:
            delete_params = EventDelete.model_validate(payload_data)
            event_id_to_delete = delete_params.id
            logger.debug(f"Validated delete parameters for event ID: {event_id_to_delete}")
        except ValidationError as e:
            logger.warning(f"Invalid EventDelete payload for task {task_id}: {e}")
            raise ValueError(f"Invalid delete parameters: {e}")

        delete_event_strategy = DeleteEventStrategy(
            db_service=db_service,
        )

        # 2. Call the database service to delete
        logger.info(f"Attempting to delete event {event_id_to_delete} for task {task_id}")
        success = await delete_event_strategy.execute(data=event_id_to_delete)

        # 3. Complete task based on success
        if success:
            result_text = f"Successfully deleted event {event_id_to_delete}."
            logger.info(result_text)
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_text)])
            return await task_manager.complete_task(task_id, agent_message)
        else:
            error_text = f"Event with ID {event_id_to_delete} not found for deletion."
            logger.warning(f"Event not found for deletion task {task_id}: ID {event_id_to_delete}")
            agent_message = Message(role="agent", parts=[Part(type="text", text=error_text)])
            # Treat 'not found' as failure in this context
            return await task_manager.fail_task(task_id, agent_message)

    except ValueError as e:
        error_text = f"Error processing delete: {e}"
        logger.error(f"Value error processing structured delete for task {task_id}: {e}")
        agent_message = Message(role="agent", parts=[Part(type="text", text=error_text)])
        return await task_manager.fail_task(task_id, agent_message)
    except Exception as e:
        error_text = f"An unexpected error occurred: {e}"
        # Catch other potential errors (e.g., database connection)
        logger.exception(f"Unexpected error processing structured delete for task {task_id}: {e}")
        agent_message = Message(role="agent", parts=[Part(type="text", text=error_text)])
        return await task_manager.fail_task(task_id, agent_message) 