import os
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event
from sqlmodel import SQLModel # Though not used directly here, needed for metadata later
from dotenv import load_dotenv

# Import settings object
from scheduling_agent.settings import settings
# Corrected import path for logging_config
from scheduling_agent.logging_config import get_logger

# Load environment variables from .env file (keep for other potential vars)
load_dotenv()

logger = get_logger(__name__)

# --- Database Configuration Function (Removed) ---
# (Now using settings.database_url directly)
# def get_database_url() -> str:
#     """Gets the database URL from environment variables."""
#     return os.environ.get(
#         "DATABASE_URL", "sqlite+aiosqlite:///./schedule_agent.db"
#     )

# --- Engine Creation Function ---
def create_db_engine(db_url: str) -> AsyncEngine:
    """Creates the SQLAlchemy async engine."""
    connect_args = {"check_same_thread": False} if db_url.startswith("sqlite") else {}
    try:
        engine = create_async_engine(
            db_url,
            # Use settings.log_level to control echo
            echo=settings.log_level.upper() == "DEBUG",
            future=True,
            connect_args=connect_args,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        logger.info("Async database engine configured", db_url=db_url)

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
        logger.error("Failed to create database engine", error=str(e), db_url=db_url)
        raise

# --- Session Factory Creation Function ---
def create_session_factory(engine: AsyncEngine) -> sessionmaker:
    """Creates the SQLAlchemy session factory."""
    return sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )

# --- Session Context Manager --- #
@asynccontextmanager
async def get_async_session(session_factory: sessionmaker) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional scope around a series of operations using the provided factory."""
    if session_factory is None:
        raise RuntimeError("Session factory not provided to get_async_session")

    async with session_factory() as session:
        try:
            yield session
        except Exception as e:
            logger.error("Session rollback initiated due to error", error=str(e))
            await session.rollback()
            raise
        finally:
            await session.close()
    logger.debug("Async session closed")


# --- Database Initialization Function (Placeholder for App Startup) --- #
async def initialize_database(engine: AsyncEngine):
    """Initialize the database by creating all tables."""
    # Placeholder: Implement table creation logic (e.g., using SQLModel or Alembic)
    # logger.info("Database initialization placeholder.")
    async with engine.begin() as conn:
        # In a real app, consider Alembic migrations
        # For simple cases or testing, SQLModel create_all works
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.info("Database tables created successfully (if not already present). Using SQLModel.metadata.create_all.")

# Example usage (typically in your service layer or API endpoints):
# async def some_database_operation():
#     async with get_async_session() as session:
#         # Perform database operations using 'session'
#         result = await session.execute(select(MyModel).where(MyModel.id == 1))
#         item = result.scalars().first()
#         return item 

# --- Create engine and session factory instances for import ---
# Use settings.database_url
db_url = settings.database_url
engine = create_db_engine(db_url)
async_session_factory = create_session_factory(engine) 