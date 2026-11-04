# sa/db/strategies/contact_delete.py
# Copyright (C) 2025 Martin Bukowski

"""Strategies for deleting contacts."""

import logging
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactDelete
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.core.exceptions import ContactNotFoundError

logger = logging.getLogger(__name__)


class DeleteContactStrategy(DatabaseExecutionStrategy[ContactDelete, None]):
    """Strategy to delete a contact by its primary key (ID)."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service)

    async def _run(self, session: AsyncSession, data: ContactDelete) -> None:
        """Executes the delete operation for the given contact ID."""
        contact_id = data.id
        logger.debug(f"Attempting to delete contact ID: {contact_id}")

        statement = sa.delete(Contact).where(Contact.id == contact_id)

        try:
            result = await session.execute(statement)
            # Check if any rows were actually deleted
            deleted_count = result.rowcount
            if deleted_count > 0:
                logger.info(f"Successfully deleted contact ID: {contact_id}")
                return None
            else:
                logger.warning(f"Contact ID not found for deletion: {contact_id}")
                raise ContactNotFoundError(contact_id=contact_id)
        except Exception as e:
            logger.exception(f"Unexpected error deleting contact {contact_id}: {e}")
            # Let the base class handle wrapping this in a DatabaseError
            raise

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handle failures during the _run execution."""
        logger.warning(f"Executing _failure for DeleteContactStrategy due to: {type(exception).__name__}")
        # No explicit actions needed here for now, rollback is handled by base class
        pass 