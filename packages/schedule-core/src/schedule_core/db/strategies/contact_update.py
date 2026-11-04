# sa/db/strategies/contact_update.py
# Copyright (C) 2025 Martin Bukowski

"""Strategies for updating contacts."""

import logging
import uuid
from datetime import datetime, UTC
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactUpdate, ContactGetById # DAO needed for getting existing
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.core.exceptions import ContactNotFoundError

logger = logging.getLogger(__name__)

# Define the type for the input data tuple
UpdateInput = tuple[uuid.UUID, ContactUpdate]

class UpdateContactStrategy(DatabaseExecutionStrategy[UpdateInput, Contact]):
    """Strategy to update an existing contact in the database."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service)

    async def _run(self, session: AsyncSession, data: UpdateInput) -> Contact:
        """Updates an existing contact instance."""
        contact_id, update_data = data
        logger.debug(f"Attempting to update contact ID: {contact_id} with data: {update_data}")

        # Fetch the existing contact
        contact = await session.get(Contact, contact_id)

        if not contact:
            logger.warning(f"Contact ID not found for update: {contact_id}")
            raise ContactNotFoundError(contact_id=contact_id)

        # Apply updates from the DAO
        update_dict = update_data.model_dump(exclude_unset=True)
        updated = False
        for key, value in update_dict.items():
            if hasattr(contact, key):
                setattr(contact, key, value)
                updated = True

        if not updated:
            logger.info(f"No fields provided to update for contact ID: {contact_id}")
            # Optionally return the existing contact if no updates were made?
            # Or handle as needed. Returning contact as is for now.
            return contact

        # Manually update the updated_at timestamp
        contact.updated_at = datetime.now(UTC).replace(tzinfo=None)

        # Add and flush to potentially catch errors
        session.add(contact) # Add the modified instance back to the session
        try:
            await session.flush()
            # Refresh to get any DB-level updates (though less likely for update)
            await session.refresh(contact)
            logger.info(f"Successfully updated contact ID: {contact_id}")
            return contact

        except Exception as e:
             logger.exception(f"Unexpected error updating contact {contact_id}: {e}")
             # Let the base class handle unexpected errors
             raise

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handle failures during the _run execution."""
        logger.warning(f"Executing _failure for UpdateContactStrategy due to: {type(exception).__name__}")
        # No explicit actions needed here for now, rollback is handled by base class
        pass 