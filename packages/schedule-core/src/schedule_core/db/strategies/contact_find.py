# sa/db/strategies/contact_find.py
# Copyright (C) 2025 Martin Bukowski

"""Strategies for finding contacts."""

import logging
import sqlalchemy as sa
from sqlalchemy import func # For lower()
from sqlalchemy.ext.asyncio import AsyncSession

from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactQuery
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.base import DatabaseExecutionStrategy
from schedule_core.core.exceptions import DatabaseError

logger = logging.getLogger(__name__)


class FindContactsStrategy(DatabaseExecutionStrategy[ContactQuery, list[Contact]]):
    """Strategy to find contacts based on query criteria."""

    def __init__(self, db_service: DatabaseService):
        super().__init__(db_service)

    async def _run(self, session: AsyncSession, data: ContactQuery) -> list[Contact]:
        """Builds and executes a query to find contacts."""
        logger.debug(f"Attempting to find contacts with query: {data}")

        statement = sa.select(Contact)

        # Apply filters based on DAO fields
        if data.name_contains:
            # Case-insensitive search
            statement = statement.where(func.lower(Contact.name).contains(data.name_contains.lower()))

        if data.email_contains:
            statement = statement.where(func.lower(Contact.email).contains(data.email_contains.lower()))

        if data.email:
            # Exact match for email (already unique)
            statement = statement.where(Contact.email == data.email)

        # Add ordering for consistent results, e.g., by name
        statement = statement.order_by(Contact.name)

        try:
            result = await session.execute(statement)
            contacts = result.scalars().all()
            logger.info(f"Found {len(contacts)} contacts matching query: {data}")
            return list(contacts) # Ensure list return type
        except Exception as e:
            logger.exception(f"Unexpected error finding contacts with query {data}: {e}")
            # Let the base class handle wrapping this in a DatabaseError
            raise

    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """Handle failures during the _run execution."""
        logger.warning(f"Executing _failure for FindContactsStrategy due to: {type(exception).__name__}")
        # No explicit actions needed here for now, rollback is handled by base class
        pass 