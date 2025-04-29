# sa/db/strategies/contact_get_by_id.py
# Copyright (C) 2025 Martin Bukowski

"""Strategies for retrieving contacts by ID."""

import logging
import uuid
from sqlalchemy.ext.asyncio import AsyncSession

from sa.db.models import Contact
from sa.db.dao import ContactGetById
from sa.db.service import DatabaseService
from sa.db.strategies.base import DatabaseExecutionStrategy
from sa.core.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class GetContactByIdStrategy(DatabaseExecutionStrategy[ContactGetById, Contact | None]):
    """Strategy to retrieve a contact by its primary key (ID)."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service)

    async def _run(self, session: AsyncSession, data: ContactGetById) -> Contact | None:
        """Fetches a contact by its ID."""
        contact_id = data.id
        logger.debug(f"Attempting to get contact by ID: {contact_id}")
        try:
            contact = await session.get(Contact, contact_id)
            if contact:
                logger.info(f"Successfully retrieved contact ID: {contact_id}")
            else:
                logger.info(f"Contact ID not found: {contact_id}")
            return contact
        except Exception as e:
            logger.exception(f"Unexpected error getting contact {contact_id}: {e}")
            # Let the base class handle wrapping this in a DatabaseError
            raise

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handle failures during the _run execution."""
        logger.warning(f"Executing _failure for GetContactByIdStrategy due to: {type(exception).__name__}")
        # No explicit actions needed here for now, rollback is handled by base class
        pass 