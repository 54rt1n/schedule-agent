# sa/db/service.py
# Copyright (C) 2025 Martin Bukowski

"""Database service for the scheduling agent."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

from sa.core.settings import Settings
from sa.core.logging_config import get_logger

logger = get_logger(__name__)


class DatabaseService:
    """Manages database engine, session factory, and sessions."""

    def __init__(self, engine: AsyncEngine, session_factory: sessionmaker):
        """Initialize with an existing engine and session factory."""
        if not engine:
            raise ValueError("DatabaseService requires an AsyncEngine.")
        if not session_factory:
            raise ValueError("DatabaseService requires a sessionmaker factory.")
        self.engine = engine
        self.session_factory = session_factory
        logger.info("DatabaseService initialized.")

    @classmethod
    def from_settings(cls, settings: Settings) -> 'DatabaseService':
        """Factory method to create a DatabaseService instance from Settings."""
        if not settings.database_url:
            raise ValueError("Database URL not found in settings.")

        engine = cls._create_engine(
            db_url=settings.database_url,
            log_level=settings.log_level
        )
        session_factory = cls._create_session_factory(engine)
        logger.info(f"DatabaseService created from settings for DB: {settings.database_url}")
        return cls(engine=engine, session_factory=session_factory)

    @staticmethod
    def _create_engine(db_url: str, log_level: str) -> AsyncEngine:
        """Creates the SQLAlchemy async engine."""
        connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
        echo = log_level.upper() == "DEBUG"
        try:
            engine = create_async_engine(
                db_url,
                echo=echo,
                future=True,
                connect_args=connect_args,
                pool_pre_ping=True,
                pool_recycle=3600, # Consider making pool settings configurable via Settings
            )
            logger.info("Async database engine configured", db_url=db_url, echo=echo)

            # --- SQLite WAL Mode Optimization ---
            if db_url.startswith("sqlite"):
                @event.listens_for(engine.sync_engine, "connect")
                def _enable_wal(dbapi_con, connection_record):
                    cursor = dbapi_con.cursor()
                    try:
                        cursor.execute("PRAGMA journal_mode=WAL;")
                        logger.debug("SQLite WAL mode enabled")
                    except Exception as e:
                        logger.warning("Failed to enable WAL mode for SQLite", error=str(e))
                    finally:
                        cursor.close()

            return engine
        except Exception as e:
            logger.exception("Failed to create database engine", error=str(e), db_url=db_url)
            raise RuntimeError(f"Failed to create database engine for {db_url}: {e}") from e

    @staticmethod
    def _create_session_factory(engine: AsyncEngine) -> sessionmaker:
        """Creates the SQLAlchemy session factory."""
        logger.debug("Creating session factory")
        return sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Provide a transactional scope around a series of operations."""
        if self.session_factory is None:
            # This case should ideally not happen if constructed correctly
            logger.error("Session factory not available in DatabaseService")
            raise RuntimeError("Session factory not initialized.")

        session: AsyncSession = self.session_factory()
        logger.debug(f"Session {id(session)} acquired from factory.")
        try:
            yield session
            # Commit is expected to be called by the user of the session
            # logger.debug(f"Session {id(session)} commit successful (if called).")
        except Exception as e:
            logger.exception(f"Session {id(session)} rollback initiated due to error", error=str(e))
            await session.rollback()
            raise # Re-raise the exception after rollback
        finally:
            await session.close()
            logger.debug(f"Session {id(session)} closed.")

    async def initialize(self):
        """Initialize the database by creating all tables based on SQLModel metadata."""
        logger.info("Initializing database tables (SQLModel.metadata.create_all)")
        async with self.engine.begin() as conn:
            try:
                # In a real app, consider Alembic migrations for production
                await conn.run_sync(SQLModel.metadata.create_all)
                logger.info("Database tables created/verified successfully.")
            except Exception as e:
                logger.exception("Failed during database table initialization", error=str(e))
                raise RuntimeError(f"Failed to initialize database tables: {e}") from e

    async def close(self):
        """Dispose of the database engine connections."""
        if self.engine:
            logger.info("Closing database connections (disposing engine)")
            await self.engine.dispose()
            logger.info("Database engine disposed.")
