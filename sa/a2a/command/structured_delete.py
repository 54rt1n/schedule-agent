# sa/a2a/command/structured_delete.py
"""Handles the logic path for structured data input (delete event ONLY)."""

from pydantic import ValidationError
from typing import Any
import uuid

from sa.a2a.dto import Task, Message, Part
from sa.a2a.exceptions import InvalidParamsError, InternalError
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

    event_id_to_delete: uuid.UUID
    try:
        # 1. Extract and validate the payload structure
        if not message.parts or message.parts[0].type != "data":
            # Raise InvalidParamsError directly if message structure is wrong
            raise InvalidParamsError("Missing or invalid data part in message for structured delete.")

        payload_data = message.parts[0].data
        if not isinstance(payload_data, dict):
             # Raise InvalidParamsError directly if payload is not a dict
            raise InvalidParamsError("Data part payload is not a dictionary.")

        # 2. Validate the payload content against EventDelete DAO
        delete_params = EventDelete.model_validate(payload_data)
        event_id_to_delete = delete_params.id
        logger.debug(f"Validated delete parameters for event ID: {event_id_to_delete}")

        # 3. Execute delete strategy
        delete_event_strategy = DeleteEventStrategy(db_service=db_service)
        logger.info(f"Attempting to delete event {event_id_to_delete} for task {task_id}")
        success = await delete_event_strategy.execute(data=event_id_to_delete)

        # 4. Complete or Fail task based on strategy result
        if success:
            result_text = f"Successfully deleted event {event_id_to_delete}."
            logger.info(result_text)
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_text)])
            return await task_manager.complete_task(task_id, agent_message)
        else:
            # Event not found by strategy
            error_text = f"Event with ID {event_id_to_delete} not found for deletion."
            logger.warning(f"Event not found for deletion task {task_id}: ID {event_id_to_delete}")
            agent_message = Message(role="agent", parts=[Part(type="text", text=error_text)])
            return await task_manager.fail_task(task_id, agent_message)

    except InvalidParamsError as ipe:
        # If we raised InvalidParamsError explicitly, let it propagate
        logger.warning(f"Invalid parameters processing delete for task {task_id}: {ipe}")
        raise ipe
    except ValidationError as ve:
        # Catch ValidationError specifically to let it propagate clearly
        logger.warning(f"ValidationError during delete: {ve}")
        raise InvalidParamsError(message="Invalid delete parameters", data=ve.errors()) from ve
    except Exception as e:
        # Catch unexpected errors during processing
        error_text = f"An unexpected error occurred during delete processing: {e}"
        logger.exception(f"Unexpected error processing structured delete for task {task_id}: {e}")
        # Raise InternalError for unexpected issues
        raise InternalError(message=error_text) from e
