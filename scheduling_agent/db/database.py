import os
from typing import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event
from sqlmodel import SQLModel # Though not used directly here, needed for metadata later
from dotenv import load_dotenv

# Corrected import path for logging_config
from scheduling_agent.logging_config import get_logger

# Load environment variables from .env file
load_dotenv()

logger = get_logger(__name__)

# --- Database Configuration ---
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "sqlite+aiosqlite:///./schedule_agent.db"
)
# Use connect_args for SQLite specific settings
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}


# --- Engine Creation ---
try:
    async_engine = create_async_engine(
        DATABASE_URL,
        echo=os.environ.get("DB_ECHO", "False").lower() == "true",
        future=True, # Enables SQLAlchemy 2.0 features
        connect_args=connect_args,
        pool_pre_ping=True, # Check connections before use
        pool_recycle=3600, # Recycle connections after 1 hour
    )
    logger.info("Async database engine created successfully", db_url=DATABASE_URL)
except Exception as e:
    logger.error("Failed to create database engine", error=str(e), db_url=DATABASE_URL)
    raise

# --- SQLite WAL Mode Optimization ---
# Only applicable for SQLite
if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(async_engine.sync_engine, "connect")
    def _enable_wal(dbapi_con, connection_record):
        """Enable WAL mode for SQLite connections."""
        cursor = dbapi_con.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL;")
            logger.debug("SQLite WAL mode enabled")
        except Exception as e:
            logger.warning("Failed to enable WAL mode for SQLite", error=str(e))
        finally:
            cursor.close()


# --- Session Management ---
AsyncSessionFactory = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False, # Recommended for async use
    autoflush=False,
    autocommit=False,
)

@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional scope around a series of operations."""
    async with AsyncSessionFactory() as session:
        try:
            yield session
            # Optionally commit here if you want auto-commit behavior
            # await session.commit()
        except Exception as e:
            logger.error("Session rollback initiated due to error", error=str(e))
            await session.rollback()
            raise
        finally:
            await session.close()
    logger.debug("Async session closed")


async def create_db_and_tables():
    """Create database tables based on SQLModel metadata (call during app startup)."""
    async with async_engine.begin() as conn:
        # TODO: Integrate with Alembic later instead of creating tables directly
        # await conn.run_sync(SQLModel.metadata.create_all)
        logger.warning("Skipping direct table creation; use Alembic migrations.")
    logger.info("Database tables check/creation process finished (using Alembic recommended).")


# Example usage (typically in your service layer or API endpoints):
# async def some_database_operation():
#     async with get_async_session() as session:
#         # Perform database operations using 'session'
#         result = await session.execute(select(MyModel).where(MyModel.id == 1))
#         item = result.scalars().first()
#         return item 