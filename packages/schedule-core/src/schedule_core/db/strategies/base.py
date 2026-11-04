# sa/db/strategies/base.py
# Copyright (C) 2025 Martin Bukowski

"""Base class for database execution strategies."""

import abc
import logging
from typing import Any, TypeVar, Generic

from sqlalchemy.ext.asyncio import AsyncSession

from schedule_core.core.exceptions import DatabaseError, TaskNotFoundError, ConflictError, CoreLogicError
from schedule_core.db.service import DatabaseService

logger = logging.getLogger(__name__)

InputType = TypeVar("InputType")
ReturnType = TypeVar("ReturnType")

class DatabaseExecutionStrategy(abc.ABC, Generic[InputType, ReturnType]):
    """
    Abstract Base Class for database execution strategies.
    Generic over InputType (data passed to execute) and ReturnType (return type).

    Handles session acquisition via DatabaseService, transaction commit/rollback,
    and error handling.
    Subclasses must implement the _run method with the specific database logic
    and may implement _rollback for custom error handling.
    """

    def __init__(self, db_service: DatabaseService):
        """Initialize the strategy with the database service."""
        if db_service is None:
            # Added check for db_service
            raise ValueError("DatabaseService cannot be None for DatabaseExecutionStrategy")
        self.db_service = db_service

    @abc.abstractmethod
    async def _run(self, session: AsyncSession, data: InputType) -> ReturnType:
        """
        Execute the specific database logic within a transaction.

        Args:
            session: The database session provided by the execute method.
            data: The input data for the specific strategy.

        Returns:
            The result of the database operation.

        Raises:
            Specific exceptions related to the operation's logic if needed,
            which will be caught and wrapped by the execute method.
        """
        pass

    @abc.abstractmethod
    async def _failure(self, session: AsyncSession, exception: Exception) -> None:
        """
        Execute custom failure logic. Called when an exception occurs in _run or commit.

        Args:
            session: The database session.
            exception: The exception that triggered the rollback.
        """
        pass

    async def execute(self, data: InputType) -> ReturnType:
        """
        Execute the strategy, managing the session and transaction.

        Args:
            data: The input data to pass to the _run method.

        Returns:
            The result returned by the _run method.

        Raises:
            TaskNotFoundError: If the underlying operation signals a task was not found.
            DatabaseError: If a database-related or unexpected exception occurs.
        """
        strategy_name = self.__class__.__name__
        session: AsyncSession | None = None
        try:
            async with self.db_service.get_session() as session:
                logger.debug(f"Executing strategy: {strategy_name} with data: {data}")
                result = await self._run(session=session, data=data)
                await session.commit()
                logger.debug(f"Strategy {strategy_name} committed successfully.")
                return result
        except TaskNotFoundError as e: # Catch specific known application errors
            logger.warning(
                f"Application error in strategy {strategy_name}: {e}"
            )
            raise e

        except ConflictError as ce:
             logger.warning(
                 f"ConflictError in strategy {strategy_name}: {ce}"
             )
             raise ce

        except Exception as e:
            logger.error(
                f"Error executing strategy {strategy_name}: {e}. Initiating rollback.",
                exc_info=True,
            )
            if session is not None:
                try:
                    await self._failure(session=session, exception=e)
                    logger.info(f"Custom _failure for {strategy_name} completed.")
                except Exception as failure_ex:
                    logger.exception(f"Error during custom _failure for {strategy_name}: {failure_ex}")

            raise DatabaseError(
                f"Strategy {strategy_name} failed due to unexpected error: {e}"
            ) from e
