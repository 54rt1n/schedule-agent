# sa/a2a/command/structured_contact_update.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the structured update_contact intent."""

import uuid
from pydantic import ValidationError
from typing import Any
from sqlalchemy.exc import IntegrityError

from sa.a2a.dto import Artifact, Task, Message, Part
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.exceptions import DatabaseError, ContactNotFoundError
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.db.strategies.contact_update import UpdateContactStrategy
from sa.db.dao import ContactUpdate
from sa.a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)


async def handle_structured_update_contact(
    task_id: str,
    payload: dict[str, Any] | None,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles the logic path for structured update_contact intent based on unwrapped payload (dict)."""
    logger.info(f"Handling structured update_contact for task {task_id}")

    if not payload:
        raise InvalidParamsError("Payload is required for structured contact update.")

    contact_id_str = payload.get("id")
    if not contact_id_str:
        raise InvalidParamsError("Contact 'id' is required in the payload for update.")

    try:
        contact_id = uuid.UUID(contact_id_str)
    except ValueError:
        raise InvalidParamsError(f"Invalid UUID format for contact 'id': {contact_id_str}")

    # Separate the ID from the actual update data
    update_data_dict = {k: v for k, v in payload.items() if k != "id"}
    if not update_data_dict:
        raise InvalidParamsError("No fields provided to update.")

    update_dao: ContactUpdate | None = None
    try:
        # Validate the update fields against the ContactUpdate DAO
        update_dao = ContactUpdate.model_validate(update_data_dict)
        logger.debug(
            "Parsed contact update DAO from payload",
            task_id=task_id,
            contact_id=contact_id,
            update_data=update_dao.model_dump(exclude_unset=True)
        )
    except ValidationError as ve:
        logger.warning("Invalid structured update payload received", task_id=task_id, validation_error=ve.errors())
        raise InvalidParamsError(message="Invalid data provided for contact update.", data=ve.errors()) from ve

    try:
        # --- Strategy Execution ---
        update_strategy = UpdateContactStrategy(db_service=db_service)
        logger.info(f"Calling UpdateContactStrategy for task {task_id}, contact_id={contact_id}")
        # Strategy now returns Contact or raises specific errors
        updated_contact = await update_strategy.execute(data=(contact_id, update_dao))

        # --- Handle Success Outcome ---
        # If execute succeeds, contact was updated
        logger.info(f"Successfully updated contact {contact_id} for task {task_id}")
        result_message = Message(
            role="agent",
            parts=[Part(type="text", text=f"Contact '{updated_contact.name}' (ID: {contact_id}) updated successfully.")]
        )
        # Create artifact with updated details
        artifact_data = {
             "id": str(updated_contact.id),
             "name": updated_contact.name,
             "email": updated_contact.email,
             "phone": updated_contact.phone,
             "notes": updated_contact.notes,
             "updated_at": updated_contact.updated_at.isoformat() if updated_contact.updated_at else None,
        }
        artifact = Artifact(name="updated_contact_details", parts=[Part(type="data", data=artifact_data)])
        return await task_manager.complete_task(task_id, result_message, artifact)

    except ContactNotFoundError as nfe:
        # Handle specific not found error
        logger.warning(f"Contact {nfe.contact_id} not found for update, task {task_id}")
        fail_message = Message(role="agent", parts=[Part(type="text", text=str(nfe))])
        return await task_manager.fail_task(task_id, fail_message)

    except IntegrityError as ie:
        # Handle potential unique email constraint violation
        logger.warning("IntegrityError during contact update", task_id=task_id, contact_id=contact_id, error=str(ie))
        # Check if it's the email constraint (basic check, refine if needed based on DB)
        error_str = str(ie.orig).lower() if ie.orig else str(ie).lower()
        if "contact" in error_str and "email" in error_str and ("unique" in error_str or "duplicate" in error_str):
             fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to update contact: Email already exists for another contact.")])
        else:
             # Unhandled integrity error - treat as internal
             logger.exception(f"Unhandled IntegrityError during contact update for task {task_id}: {ie}")
             raise InternalError("An unexpected database constraint occurred during contact update.") from ie
        return await task_manager.fail_task(task_id, fail_message)

    except DatabaseError as dbe:
        # Catch other database errors from the strategy (or base class)
        logger.exception(f"Database error during contact update for task {task_id}: {dbe}")
        raise InternalError("An unexpected database error occurred during contact update.") from dbe

    except Exception as e:
        # Catch unexpected errors during strategy execution or task completion
        logger.exception(f"Unexpected error during structured contact update for task {task_id}: {e}")
        raise InternalError("An unexpected error occurred while updating contact.") from e 