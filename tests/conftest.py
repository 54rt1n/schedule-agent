import uuid

import pytest

# Import pytest-asyncio
import pytest_asyncio

# Import text for raw SQL execution
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel


@pytest.fixture
def test_db_url():
    """
    Create a unique named in-memory database for SQLite.
    Using a named memory connection with a URI allows connection sharing.
    """
    db_id = str(uuid.uuid4())
    # Using the URI format to enable shared cache
    return f"sqlite+aiosqlite:///file:{db_id}?mode=memory&cache=shared&uri=true"


@pytest_asyncio.fixture(scope="function")
async def test_engine(test_db_url):
    """Create a test engine with the provided URL."""
    engine = create_async_engine(
        test_db_url,
        echo=False,  # Set to True for debugging SQL
        future=True,
        # SQLite optimizations
        pool_pre_ping=True,
        pool_recycle=3600,
    )

    # Create tables before yielding
    async with engine.begin() as conn:
        # Explicitly import models here to ensure metadata is populated before create_all
        # This is a workaround for potential import order issues in tests
        from scheduling_agent.db import models  # noqa: F401

        await conn.run_sync(SQLModel.metadata.create_all)

    yield engine

    # Clean up after test - dispose engine releases connections
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def test_session_factory(test_engine):
    """Create a session factory for the test engine."""
    factory = sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,  # Keep autoflush=False for tests
    )
    return factory


@pytest_asyncio.fixture(scope="function")
async def test_session(test_session_factory):
    """Provide a test session that automatically rolls back."""
    async with test_session_factory() as session:
        try:
            yield session
        finally:
            # Always rollback after the test to ensure isolation
            await session.rollback()
            # Session is closed automatically by the async context manager


# Add the SQLite optimization fixture from the plan
@pytest_asyncio.fixture(autouse=True)
async def enable_sqlite_optimizations(test_engine):
    """Enable WAL mode and other optimizations for SQLite."""
    # WAL mode is generally beneficial but might behave unexpectedly with :memory:
    # Let's try enabling it. If issues arise, we can remove it.
    # Foreign keys should be ON by default with modern SQLite via SQLAlchemy, but explicit is fine.
    async with test_engine.connect() as conn:  # Use connect() not begin() for PRAGMA
        await conn.execute(text("PRAGMA journal_mode=WAL;"))
        await conn.execute(text("PRAGMA synchronous=NORMAL;"))
        await conn.execute(text("PRAGMA foreign_keys=ON;"))
    # No finally block needed here, 'async with' handles closing.
