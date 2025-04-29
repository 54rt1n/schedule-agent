# sa/a2a/command/structured_contact_create.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the logic path for structured contact creation.

This handler takes a validated payload dictionary, uses the CreateContactStrategy
to attempt creating the contact, and updates the task status via the TaskManager.
"""

import asyncio
from logging import getLogger
from typing import Any
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from sa.a2a.dto import Artifact, Message, Part, Task
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.exceptions import DatabaseError
from sa.core.logging_config import get_logger
from sa.db.dao import ContactCreate
from sa.db.service import DatabaseService
from sa.db.strategies.contact_create import CreateContactStrategy
from sa.a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)

async def handle_structured_create_contact(
    task_id: str,
    payload: dict | None,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """
    Handles the creation of a contact based on structured input data provided
    in the payload.

    It validates the input, uses the CreateContactStrategy to persist the
    contact, and then completes the task via the TaskManager, providing the
    created contact details as an artifact.

    Args:
        task_id: The unique identifier of the task.
        payload: A dictionary containing the structured data for contact creation.
                 Expected keys match the ContactCreate DAO fields (e.g., name, email).
        task_manager: An instance of TaskManager to update task status.
        db_service: An instance of DBService to interact with the database.

    Returns:
        The completed Task object from the TaskManager.

    Raises:
        InvalidParamsError: If the payload is missing or validation fails.
        InternalError: If an unexpected database error occurs during creation
                       or task completion.
    """
    logger.info(f"Handling structured create_contact for task {task_id}")

    if not payload:
        raise InvalidParamsError("Payload is required for structured contact creation.")

    contact_data: ContactCreate | None = None # Initialize for exception scope
    try:
        contact_data = ContactCreate(**payload)
    except ValidationError as e:
        logger.warning(f"Invalid contact data for task {task_id}: {e.errors()}")
        raise InvalidParamsError(
            "Invalid data provided for contact creation.", data=e.errors()
        ) from e

    try:
        strategy = CreateContactStrategy(db_service=db_service)
        created_contact = await strategy.execute(data=contact_data)

        # Prepare success message and artifact
        success_message = Message(
            role="agent",
            parts=[
                Part(
                    type="text",
                    text=f"Contact '{created_contact.name}' created successfully with ID {created_contact.id}.",
                )
            ],
        )
        contact_artifact = Artifact(
            name="created_contact",
            parts=[
                Part(
                    type="data",
                    data={
                        "id": str(created_contact.id),
                        "name": created_contact.name,
                        "email": created_contact.email, # Include email as well for completeness
                    },
                )
            ],
        )

        # Complete the task
        completed_task = await task_manager.complete_task(
            task_id, success_message, contact_artifact
        )
        logger.info(f"Successfully created contact for task {task_id}, contact_id={created_contact.id}")
        return completed_task

    except IntegrityError as ie:
        # Handle potential unique email constraint violation from strategy
        logger.warning("IntegrityError during contact creation", task_id=task_id, error=str(ie))
        # Check if it's the email constraint (basic check, refine if needed based on DB)
        error_str = str(ie.orig).lower() if ie.orig else str(ie).lower()
        # Use the validated contact_data DAO from the outer try block
        email_provided = contact_data.email if contact_data else "<unknown>"
        if "contact" in error_str and "email" in error_str and ("unique" in error_str or "duplicate" in error_str):
             fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to create contact: Email '{email_provided}' already exists.")])
        else:
             # Unhandled integrity error - treat as internal
             logger.exception(f"Unhandled IntegrityError during contact creation for task {task_id}: {ie}")
             raise InternalError("An unexpected database constraint occurred during contact creation.") from ie
        return await task_manager.fail_task(task_id, fail_message)

    except DatabaseError as e:
        # Handle unexpected database errors during strategy execution
        logger.exception(f"Database error during contact creation for task {task_id}: {e}")
        raise InternalError(
            "An unexpected error occurred during contact creation."
        ) from e

    except Exception as e:
        # Catch potential errors during task completion (e.g., TaskManager issues)
        logger.exception(f"Unexpected error completing task {task_id} after contact creation: {e}")
        # Attempt to fail the task if completion failed
        try:
            fail_message = Message(role="agent", parts=[Part(type="text", text=f"Failed to complete task after creating contact: {e}")])
            await task_manager.fail_task(task_id, fail_message)
        except Exception as fail_e:
            logger.exception(f"Further error attempting to fail task {task_id}: {fail_e}")
        raise InternalError(
            "An unexpected error occurred after successfully creating the contact."
        ) from e

    # The following line is now unreachable due to the added logic,
    # but kept temporarily to show the original structure.
    # raise NotImplementedError("Handler logic not yet implemented.") 