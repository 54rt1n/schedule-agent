# packages/schedule-a2a/src/schedule_a2a/command/structured_contact_query.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the structured query_contact intent."""

import json
from pydantic import ValidationError
from typing import Any

from schedule_a2a.dto import Task, Message, Part
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.core.logging_config import get_logger
from schedule_core.db.dao import ContactQuery
from schedule_core.db.service import DatabaseService
# Import the strategy we will use later
from schedule_core.db.strategies.contact_find import FindContactsStrategy
from schedule_a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)


async def handle_structured_query_contact(
    task_id: str,
    payload: dict[str, Any] | None,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles structured queries for contacts based on the unwrapped payload (dict)."""
    logger.info(f"Handling structured query_contact for task {task_id}")

    if not payload:
        # If payload is empty, assume query for all contacts
        payload = {}

    query_params: ContactQuery | None = None
    result_message = ""

    try:
        try:
            query_params = ContactQuery.model_validate(payload)
            logger.debug(f"Validated query parameters for contacts: {query_params}")
        except ValidationError as e:
            logger.warning(f"Invalid ContactQuery payload for task {task_id}: {e}")
            raise InvalidParamsError(
                f"Invalid query parameters in payload: {e}", data=e.errors()
            )

        # --- Strategy Execution --- 
        find_strategy = FindContactsStrategy(db_service=db_service)
        logger.info(f"Calling FindContactsStrategy for task {task_id}")
        found_contacts = await find_strategy.execute(data=query_params)

        # --- Result Formatting --- 
        # Format results similar to ContactRead DAO but as plain dicts for JSON
        results_list = [
            {
                "id": str(contact.id),
                "name": contact.name,
                "email": contact.email,
                "phone": contact.phone,
                "notes": contact.notes,
                "created_at": contact.created_at.isoformat() if contact.created_at else None,
                "updated_at": contact.updated_at.isoformat() if contact.updated_at else None,
            }
            for contact in found_contacts
        ]
        result_message = json.dumps(results_list)
        logger.info(f"Found {len(found_contacts)} contacts for task {task_id}")

        # --- Task Completion --- 
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        return await task_manager.complete_task(task_id, agent_message)

        # Placeholder until implemented
        # logger.warning(f"Contact query logic not fully implemented for task {task_id}.")
        # placeholder_message = Message(role="agent", parts=[Part(type="text", text="Contact query not yet fully implemented.")])
        # return await task_manager.fail_task(task_id, placeholder_message)


    except InvalidParamsError as ipe:
        logger.warning(f"InvalidParamsError processing structured query_contact for task {task_id}: {ipe}")
        raise ipe # Re-raise to be handled upstream
    except Exception as e:
        logger.exception(f"Unexpected error processing structured query_contact for task {task_id}: {e}")
        raise InternalError(
            message=f"An unexpected error occurred during contact query processing: {e}"
        ) from e 