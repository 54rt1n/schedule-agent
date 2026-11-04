# packages/schedule-a2a/src/schedule_a2a/command/structured_contact_delete.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the structured delete_contact intent."""

import uuid
from pydantic import ValidationError
from typing import Any

from schedule_a2a.dto import Task, Message, Part
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.core.exceptions import DatabaseError, ContactNotFoundError
from schedule_core.core.logging_config import get_logger
from schedule_core.db.service import DatabaseService
# Import the strategy and relevant DAO
from schedule_core.db.strategies.contact_delete import DeleteContactStrategy
from schedule_core.db.dao import ContactDelete
from schedule_a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)


async def handle_structured_delete_contact(
    task_id: str,
    payload: dict[str, Any] | None,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles structured deletes for contacts based on the unwrapped payload (dict)."""
    logger.info(f"Handling structured delete_contact for task {task_id}")

    if not payload:
        raise InvalidParamsError("Payload is required for structured contact deletion.")

    delete_dao: ContactDelete | None = None
    try:
        # Validate the payload against the ContactDelete DAO
        delete_dao = ContactDelete.model_validate(payload)
        logger.debug(f"Validated delete parameters for contact ID: {delete_dao.id}")
    except ValidationError as e:
        logger.warning(f"Invalid ContactDelete payload for task {task_id}: {e}")
        raise InvalidParamsError(f"Invalid delete parameters in payload: {e.errors()}", data=e.errors())

    try:
        # --- Strategy Execution ---
        delete_strategy = DeleteContactStrategy(db_service=db_service)
        logger.info(f"Attempting to delete contact {delete_dao.id} for task {task_id}")
        await delete_strategy.execute(data=delete_dao)

        # --- Handle Success Outcome ---
        result_text = f"Successfully deleted contact {delete_dao.id}."
        logger.info(f"Successfully deleted contact {delete_dao.id} for task {task_id}")
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_text)])
        return await task_manager.complete_task(task_id, agent_message)

    except ContactNotFoundError as nfe:
        logger.warning(f"Contact {nfe.contact_id} not found for deletion, task {task_id}")
        fail_message = Message(role="agent", parts=[Part(type="text", text=str(nfe))])
        return await task_manager.fail_task(task_id, fail_message)

    except DatabaseError as dbe:
        logger.exception(f"Database error during contact deletion for task {task_id}: {dbe}")
        raise InternalError("An unexpected database error occurred during contact deletion.") from dbe

    except Exception as e:
        logger.exception(f"Unexpected error during structured contact deletion for task {task_id}: {e}")
        raise InternalError("An unexpected error occurred while deleting contact.") from e 