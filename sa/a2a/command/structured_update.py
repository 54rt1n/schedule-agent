# sa/a2a/command/structured_update.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the structured update_event intent."""

from pydantic import ValidationError
from typing import Any
import uuid

from sa.a2a.dto import Artifact, Task, StructuredPayloadWrapper, Message, Part
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.exceptions import ConflictError
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.db.strategies.update_event import UpdateEventStrategy
from sa.db.dao import EventUpdate
from sa.event.date_utils import DateUtils
from sa.a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)


async def handle_structured_update(
    task_id: str,
    payload: dict[str, Any],
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
) -> Task:
    """Handles the logic path for structured update_event intent based on unwrapped payload (dict)."""
    logger.info(f"Handling structured update_event for task {task_id}")
    update_dao: EventUpdate | None = None
    try:
        try:
            update_dao = EventUpdate.model_validate(payload)
            event_id = update_dao.id
            logger.debug("Parsed event update DAO from payload", task_id=task_id, event_id=event_id, update_data=update_dao.model_dump(exclude_unset=True))
        except ValidationError as ve:
             # Handle Pydantic validation error for EventUpdate DAO
             logger.warning("Invalid structured update payload received", task_id=task_id, validation_error=ve.errors())
             raise InvalidParamsError(message="Invalid data provided for event update.", data=ve.errors()) from ve

        # 2. Call UpdateEventStrategy
        update_strategy = UpdateEventStrategy(
            db_service=db_service,
            conflict_resolver=conflict_resolver,
            date_utils=date_utils
        )
        updated_event_or_instance = await update_strategy.execute(data=update_dao)

        # 3. Handle outcome
        if updated_event_or_instance:
            result_message = f"Event '{event_id}' updated successfully."
            # Still need Message/Part for completion
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
            artifact_data = updated_event_or_instance.model_dump(mode="json")
            artifact = Artifact(name="updated_event_details", parts=[Part(type="data", data=artifact_data)])
            final_task = await task_manager.complete_task(task_id, agent_message, artifact)
            return final_task
        else:
            logger.warning(f"Event not found during update attempt, task_id={task_id}, event_id={event_id}")
            fail_message = Message(role="agent", parts=[Part(type="text", text=f"Could not find event with ID '{event_id}' to update.")])
            final_task = await task_manager.fail_task(task_id, fail_message)
            return final_task

    except ConflictError as ce:
        logger.warning("Conflict detected during event update", task_id=task_id, event_id=getattr(update_dao, 'id', 'unknown'), conflicts=ce.data)
        # Still need Message/Part for reporting conflict
        conflict_message = Message(role="agent", parts=[Part(type="text", text="Update for event failed due to scheduling conflicts. Please provide alternatives or cancel.")])
        conflict_artifact = Artifact(name="scheduling_conflicts", parts=[Part(type="data", data=ce.data)])
        final_task = await task_manager.require_input_task(task_id, conflict_message, conflict_artifact)
        return final_task
    except NotImplementedError as nie:
        logger.exception("Unsupported update operation attempted", task_id=task_id, event_id=getattr(update_dao, 'id', 'unknown'), error=str(nie))
        fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update event '{getattr(update_dao, 'id', 'unknown')}': {nie!s}")])
        final_task = await task_manager.fail_task(task_id, fail_message)
        return final_task
    except ValueError as ve_val:
        logger.exception("Value error during event update", task_id=task_id, event_id=getattr(update_dao, 'id', 'unknown'), error=str(ve_val))
        fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update event '{getattr(update_dao, 'id', 'unknown')}': {ve_val!s}")])
        final_task = await task_manager.fail_task(task_id, fail_message)
        return final_task
    except InvalidParamsError as ipe:
        logger.warning(f"Invalid parameters processing update for task {task_id}: {ipe}")
        raise ipe
    except Exception as e:
        event_id_str = str(getattr(update_dao, "id", "unknown"))
        logger.exception("Unexpected error during structured event update", task_id=task_id, event_id=event_id_str, error=str(e))
        raise InternalError(message="An unexpected error occurred while updating event.") from e
