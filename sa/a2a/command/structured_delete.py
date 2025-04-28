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
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils

logger = get_logger(__name__)

async def handle_structured_delete(
    task_id: str,
    payload: dict[str, Any],
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles structured deletes for events based on the unwrapped payload (dict)."""
    logger.info(f"Handling structured delete for task {task_id}")

    event_id_to_delete: uuid.UUID
    try:
        try:
            delete_params = EventDelete.model_validate(payload)
            event_id_to_delete = delete_params.id
            logger.debug(f"Validated delete parameters for event ID: {event_id_to_delete}")
        except ValidationError as e:
            logger.warning(f"Invalid EventDelete payload for task {task_id}: {e}")
            raise InvalidParamsError(f"Invalid delete parameters in payload: {e.errors()}", data=e.errors())
        
        # 2. Execute delete strategy
        delete_event_strategy = DeleteEventStrategy(db_service=db_service)
        logger.info(f"Attempting to delete event {delete_params.id} for task {task_id}")
        success = await delete_event_strategy.execute(data=delete_params)

        # 3. Complete or Fail task based on strategy result
        if success:
            result_text = f"Successfully deleted event {delete_params.id}."
            logger.info(result_text)
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_text)])
            return await task_manager.complete_task(task_id, agent_message)
        else:
            # Event not found by strategy
            error_text = f"Event with ID {delete_params.id} not found for deletion."
            logger.warning(f"Event not found for deletion task {task_id}: ID {delete_params.id}")
            agent_message = Message(role="agent", parts=[Part(type="text", text=error_text)])
            return await task_manager.fail_task(task_id, agent_message)

    except InvalidParamsError as ipe:
        logger.warning(f"Invalid parameters processing delete for task {task_id}: {ipe}")
        raise ipe
    except ValidationError as ve:
        logger.warning(f"ValidationError during delete processing: {ve}")
        if not isinstance(ve, InvalidParamsError):
             raise InvalidParamsError(message="Invalid delete parameters", data=ve.errors()) from ve
        else:
             raise ve
    except Exception as e:
        logger.exception(f"Unexpected error processing structured delete for task {task_id}: {e}")
        raise InternalError(message=f"An unexpected error occurred during delete processing: {e}") from e
