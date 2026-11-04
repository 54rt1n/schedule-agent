# tests/unit/core/test_database.py
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from collections.abc import AsyncGenerator
from asyncstdlib import anext # Import anext
import pytest_asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import Field, SQLModel, select

from schedule_core.core.logging_config import get_logger
from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService

logger = get_logger(__name__)

# --- Test Model Definition ---
class DbTestItem(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    value: float | None = None

# --- Fixtures ---

@pytest_asyncio.fixture
def mock_db_settings() -> MagicMock:
    """Provides mocked Settings for database tests (in-memory SQLite)."""
    settings = MagicMock(spec=Settings)
    settings.database_url = "sqlite+aiosqlite:///:memory:?cache=shared" # In-memory SQLite
    settings.log_level = "DEBUG" # To test engine echo flag
    return settings

@pytest_asyncio.fixture
async def db_service(mock_db_settings) -> AsyncGenerator[DatabaseService, None]:
    """Provides an initialized DatabaseService instance and handles cleanup."""
    service = DatabaseService.from_settings(mock_db_settings)
    # Initialize the database (create tables)
    await service.initialize()

    yield service # Provide the service to the test

    # Teardown: close the engine connections
    await service.close()

# --- Test Cases ---

def test_from_settings_initialization(mock_db_settings):
    """Test that DatabaseService initializes engine and factory from settings."""
    service = DatabaseService.from_settings(mock_db_settings)
    assert service is not None
    assert isinstance(service.engine, AsyncEngine)
    assert isinstance(service.session_factory, sessionmaker)
    # Check if echo flag was set based on log_level
    assert service.engine.echo is True # Because log_level was DEBUG
    assert str(service.engine.url) == mock_db_settings.database_url


@pytest.mark.asyncio
async def test_initialize_creates_tables(db_service: DatabaseService):
    """Test that the initialize method creates the defined SQLModel tables."""
    # Arrange: db_service fixture already called initialize()
    # Act: Get the service instance from the fixture and access the engine
    # No need to await anext here as pytest-asyncio handles generator fixtures
    async with db_service.engine.connect() as connection:
        # Reflect table names
        result = await connection.run_sync(
            lambda sync_conn: sync_conn.dialect.has_table(sync_conn, DbTestItem.__tablename__)
        )
        # Assert: Check if the 'dbtestitem' table exists (SQLAlchemy uses lowercase)
        assert result is True


@pytest.mark.asyncio
async def test_get_session_context_manager(db_service: DatabaseService):
    """Test the get_session async context manager provides a valid session."""
    # Get the service instance from the fixture
    async with db_service.get_session() as session:
        assert isinstance(session, AsyncSession)
        assert session.is_active

    # After exiting context, session should be closed (though checking internal state is tricky)
    # We'll rely on testing actual operations


@pytest.mark.asyncio
async def test_session_operations(db_service: DatabaseService):
    """Test basic database operations (add, commit, select) using the service session."""
    # Arrange: Create a test item using the renamed model
    item_name = "Test Item 1"
    item_value = 123.45
    test_item = DbTestItem(name=item_name, value=item_value)

    # Act: Get service instance and add/commit
    async with db_service.get_session() as session:
        session.add(test_item)
        await session.commit()
        await session.refresh(test_item) # Refresh to get ID, etc.

    # Assert: Verify the item was saved by retrieving it in a new session
    assert test_item.id is not None
    retrieved_item = None
    async with db_service.get_session() as session:
        # Use renamed model in select
        statement = select(DbTestItem).where(DbTestItem.id == test_item.id)
        # Use session.execute and fetch results
        result = await session.execute(statement)
        retrieved_item = result.scalars().one_or_none()

    assert retrieved_item is not None
    assert retrieved_item.id == test_item.id
    assert retrieved_item.name == item_name
    assert retrieved_item.value == item_value


@pytest.mark.asyncio
async def test_session_rollback_on_error(db_service: DatabaseService):
    """Test that the session automatically rolls back on exception within the context."""
    # Arrange: Add an initial item successfully using renamed model
    initial_item = DbTestItem(name="Initial", value=1.0)
    async with db_service.get_session() as session:
        session.add(initial_item)
        await session.commit()
        initial_id = initial_item.id

    # Act: Try to add another item, but raise an exception before commit
    error_raised = False
    try:
        async with db_service.get_session() as session:
            # Use renamed model
            item_to_fail = DbTestItem(name="Will Fail", value=2.0)
            session.add(item_to_fail)
            # Simulate an error before commit
            raise ValueError("Simulated operation failure")
            # await session.commit() # This line won't be reached
    except ValueError as e:
        assert str(e) == "Simulated operation failure"
        error_raised = True

    # Assert
    assert error_raised, "Exception was not propagated correctly"

    # Verify the second item was NOT committed due to rollback
    async with db_service.get_session() as session:
        # Use renamed model
        statement_fail = select(DbTestItem).where(DbTestItem.name == "Will Fail")
        # Use session.execute and fetch results
        result_fail = await session.execute(statement_fail)
        failed_item = result_fail.scalars().one_or_none()
        assert failed_item is None

        # Verify the initial item still exists
        # Use renamed model
        statement_initial = select(DbTestItem).where(DbTestItem.id == initial_id)
        # Use session.execute and fetch results
        result_initial = await session.execute(statement_initial)
        retrieved_initial = result_initial.scalars().one_or_none()
        assert retrieved_initial is not None
        assert retrieved_initial.name == "Initial"


@pytest.mark.asyncio
async def test_close_disposes_engine(mock_db_settings):
    """Test that the close method calls engine.dispose()."""
    # Arrange: Create service, but mock the engine's dispose method
    # Use patch on the class method that creates the engine
    with patch.object(DatabaseService, '_create_engine') as mock_create_engine:
        mock_engine = MagicMock(spec=AsyncEngine)
        # Mock dispose as an async function
        mock_engine.dispose = AsyncMock()
        mock_create_engine.return_value = mock_engine

        # Use patch on the session factory creator to avoid dependency
        with patch.object(DatabaseService, '_create_session_factory') as mock_create_factory:
            mock_create_factory.return_value = MagicMock(spec=sessionmaker)

            service = DatabaseService.from_settings(mock_db_settings)
            # Ensure the mocked engine was used
            assert service.engine is mock_engine

            # Act: Call the close method
            await service.close()

            # Assert: Verify dispose was awaited
            mock_engine.dispose.assert_awaited_once() 