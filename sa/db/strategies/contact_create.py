# sa/db/strategies/contact_create.py
# Copyright (C) 2025 Martin Bukowski

"""Strategies for creating contacts."""

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sa.db.models import Contact
from sa.db.dao import ContactCreate
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.core.exceptions import DatabaseError
from sa.core.logging_config import get_logger

logger = get_logger(__name__)


class CreateContactStrategy(DatabaseExecutionStrategy[ContactCreate, Contact]):
    """Strategy to create a new contact in the database."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service)

    async def _run(self, session: AsyncSession, data: ContactCreate) -> Contact:
        """Creates a new contact instance."""
        logger.debug(f"Attempting to create contact: {data.name} <{data.email}>")

        # Create the Contact ORM model instance from the DAO
        # SQLModel handles the mapping if fields match
        # We use model_validate to ensure default_factory is called for timestamps etc.
        new_contact = Contact.model_validate(data.model_dump(exclude_unset=True))

        session.add(new_contact)
        try:
            # Flush to potentially trigger IntegrityError before commit
            await session.flush()
            # Refresh to get DB-generated defaults like ID, timestamps
            await session.refresh(new_contact)
            logger.info(f"Successfully created contact ID: {new_contact.id}")
            return new_contact
        except Exception as e:
             # IntegrityError will now be caught here
             logger.exception(f"Unexpected error creating contact {data.email}: {e}")
             # Let the base class handle unexpected errors and wrap in DatabaseError
             raise e


    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handle failures during the _run execution."""
        # Specific failure handling can be added here if needed beyond
        # what's caught in _run or handled by the base execute method's rollback.
        # For now, just log that the failure handler was called.
        logger.warning(f"Executing _failure for CreateContactStrategy due to: {type(exception).__name__}")
        # No explicit actions needed here for now, rollback is handled by base class
        pass 