from contextlib import asynccontextmanager
from datetime import datetime
import pytest
import pytest_asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from unittest.mock import AsyncMock, MagicMock
import uuid
from sqlmodel import SQLModel

from sa.core.logging_config import get_logger
from sa.core.settings import Settings
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.a2a.task_manager import SchedulingTaskManager
from sa.nlp.nlp_processor import NLPProcessor
from sa.nlp.llm_provider import LLMProvider
from sa.event.exporters.ical_exporter import ICalExporter
from sa.event.exporters.orgmode_exporter import OrgModeExporter
from sa.event.exporters.base import CalendarExporter
from sa.a2a.task_manager import Task, TaskStatus, TaskState
from sa.nlp.dao import LLMDateInfo, LLMFilters, LLMSchedulingParseResult

logger = get_logger(__name__)

@pytest.fixture
def test_db_url(tmp_path):
    """
    Provides a unique file-based SQLite DB URL for each test function.
    This improves isolation compared to shared in-memory databases.
    """
    db_file = tmp_path / f"test_api_{uuid.uuid4()}.db"
    # Use WAL mode for better concurrency with file-based SQLite
    db_url = f"sqlite+aiosqlite:///{db_file}?check_same_thread=False&journal_mode=WAL"
    logger.debug(f"Using temporary file-based DB: {db_url}")
    return db_url

@pytest.fixture
def conflict_resolver(date_utils: DateUtils):
    """
    Create a conflict resolver for testing.
    """
    return ConflictResolver(date_utils)

@pytest.fixture
def exporters(date_utils: DateUtils) -> dict[str, CalendarExporter]:
    """Provides a dictionary of initialized calendar exporters."""
    return {
        "ics": ICalExporter(date_utils=date_utils),
        "org": OrgModeExporter(date_utils=date_utils),
        # Add other exporters if needed
    }

@pytest.fixture
def date_utils():
    """
    Create a date utils for testing.
    """
    return DateUtils()

@pytest.fixture
def settings(test_db_url: str) -> Settings:
    """Provides a Settings instance for tests."""
    settings = Settings()
    settings.database_url = test_db_url
    return settings

@pytest_asyncio.fixture
async def db_service(
    settings: Settings,
) -> DatabaseService:
    """Provides a DatabaseService instance using the real settings."""
    # Instantiate CoreDatabaseService correctly using engine and factory
    service = DatabaseService.from_settings(settings=settings)
    await service.initialize()
    return service

@pytest_asyncio.fixture(scope="function")
async def task_manager(db_service: DatabaseService, date_utils: DateUtils,
                        conflict_resolver: ConflictResolver, settings: Settings,
                        exporters: dict[str, CalendarExporter]
                        ) -> SchedulingTaskManager:
    """Provides a TaskManager instance using the real db_service."""
    llm_provider = LLMProvider.from_settings(settings=settings)
    nlp_processor = NLPProcessor(llm_provider=llm_provider, date_utils=date_utils)
    return SchedulingTaskManager(
        nlp_processor=nlp_processor,
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        exporters=exporters,
    )

@pytest_asyncio.fixture(scope="function")
async def initialize_db(db_service: DatabaseService):
    """Fixture to create all tables in the test database."""
    async with db_service.engine.begin() as conn:
        # Drop all tables first to ensure clean state (important for function scope)
        await conn.run_sync(SQLModel.metadata.drop_all)
        # Create all tables
        await conn.run_sync(SQLModel.metadata.create_all)
    logger.debug("Test database tables dropped and created.")
    yield # Test runs here
    # Optional: could drop tables after test, but new in-memory DB handles cleanup

@pytest.fixture
def test_session():
    mock_session = AsyncMock(spec=AsyncSession)

    # --- Mock Result for session.execute() ---
    # Create a mock for the intermediate result object after execute()
    mock_execute_internal_result = MagicMock()

    # Configure scalar_one_or_none() on this intermediate mock
    # By default, return a basic mock Event. Tests can override this.
    from sa.db.models import Event # Import moved inside fixture
    default_mock_event = MagicMock(spec=Event)
    default_mock_event.id = uuid.uuid4()
    default_mock_event.rrule = None
    default_mock_event.time_zone = "UTC"
    default_mock_event.modified_instances = [] # Add this attribute
    mock_execute_internal_result.scalar_one_or_none = AsyncMock(return_value=default_mock_event)
    # Also mock scalar_one, returning the same default event
    mock_execute_internal_result.scalar_one = AsyncMock(return_value=default_mock_event)

    # Make session.execute an AsyncMock that returns the intermediate result mock
    mock_session.execute = AsyncMock(return_value=mock_execute_internal_result)

    # Mock other essential session methods
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()
    mock_session.close = AsyncMock() # Ensure close is awaitable if needed

    return mock_session


"""
old fixtures
@pytest.fixture
def mock_db_service(test_session: AsyncSession) -> DatabaseService:
    mock_service = AsyncMock(spec=DatabaseService)

    @asynccontextmanager
    async def mock_get_session_cm():
        session = test_session
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    mock_service.get_session.return_value = mock_get_session_cm()
    return mock_service

@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    resolver = AsyncMock(spec=ConflictResolver)
    resolver.detect_conflicts = AsyncMock(return_value=[]) # Default: no conflicts
    return resolver
"""

@pytest.fixture
def nlp_processor(settings: Settings, date_utils: DateUtils) -> NLPProcessor:
    """Provides an NLPProcessor instance, potentially with a mock LLM."""
    # For integration tests, we might want the real LLMProvider,
    # but mocking it can speed things up and isolate NLP logic.
    # Using real provider based on task_manager fixture logic
    llm_provider = LLMProvider.from_settings(settings=settings)
    return NLPProcessor(llm_provider=llm_provider, date_utils=date_utils)


@pytest.fixture
def mock_task_manager() -> MagicMock:
    mock = MagicMock(spec=SchedulingTaskManager)
    mock.create_task = AsyncMock()
    mock.complete_task = AsyncMock()
    mock.fail_task = AsyncMock()
    mock.require_input_task = AsyncMock()
    mock.add_task_artifact = AsyncMock()
    mock.get_task = AsyncMock()
    # Add cancel_task mock
    mock.cancel_task = AsyncMock()
    # Configure complete_task to return a basic completed task
    # Use AsyncMock for return values to ensure they are awaitable if needed by tests
    mock.complete_task.return_value = AsyncMock(
        return_value=Task(id="test_task", status=TaskStatus(state=TaskState.COMPLETED))
    )
    return mock


@pytest.fixture
def mock_db_service() -> DatabaseService:
    """Provides a mock DatabaseService that yields the test_session."""
    mock_service = AsyncMock(spec=DatabaseService)
    mock_session_instance = AsyncMock(spec=AsyncSession)

    # Create a mock async context manager
    mock_session_cm = AsyncMock()
    mock_session_cm.__aenter__.return_value = mock_session_instance
    # Make __aexit__ call rollback on the session instance if an exception type is passed
    async def mock_aexit(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.__aenter__.return_value.rollback()
        return None # Indicates exception was handled (or no exception)
    mock_session_cm.__aexit__ = mock_aexit

    # mock_get_session should return the context manager instance
    mock_service.get_session.return_value = mock_session_cm

    # Allow accessing the inner session mock for test assertions/configuration
    # This makes it slightly easier to configure the session methods in tests
    mock_service.mock_session = mock_session_instance

    return mock_service

@pytest.fixture
def mock_nlp_processor() -> AsyncMock:
    """Provides a mock NLPProcessor for unit tests."""
    mock = AsyncMock(spec=NLPProcessor)
    # Mock the 'process' method called by the handler
    mock.process = AsyncMock(
        return_value=LLMSchedulingParseResult(
            intent="unknown", 
            date_info=LLMDateInfo(), 
            filters=LLMFilters() # Add default filters
        )
    )
    mock.date_utils = MagicMock()
    return mock


# Fixture for exporters (needed for export intent tests)
@pytest.fixture
def mock_ical_exporter() -> MagicMock:
    mock = MagicMock()
    mock.export = MagicMock(return_value="BEGIN:VCALENDAR...")
    mock.get_mime_type = MagicMock(return_value="text/calendar")
    return mock


@pytest.fixture
def mock_org_exporter() -> MagicMock:
    mock = MagicMock()
    mock.export = MagicMock(return_value="* Event...")
    mock.get_mime_type = MagicMock(return_value="text/plain")
    return mock


@pytest.fixture
def mock_exporters(mock_ical_exporter, mock_org_exporter):
    return {
        "ics": mock_ical_exporter,
        "org": mock_org_exporter,
    }

@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    return AsyncMock(spec=ConflictResolver)

"""
@pytest.fixture
def mock_task_manager() -> AsyncMock:
    return AsyncMock(spec=SchedulingTaskManager)

@pytest.fixture
def mock_db_service() -> AsyncMock:
    mock = AsyncMock(spec=DatabaseService)
    # Mock the session context manager
    mock_session = AsyncMock()
    mock_async_context_manager = AsyncMock()
    mock_async_context_manager.__aenter__.return_value = mock_session
    mock.get_session.return_value = mock_async_context_manager
    # Add mocks for methods called by command handlers
    mock.delete_event = AsyncMock()
    mock.find_events = AsyncMock()
    # Keep existing update_event if present, or add if needed
    mock.update_event = AsyncMock()
    return mock

"""

@pytest.fixture
def sample_event_data() -> dict:
    """Provides generic sample data for creating an Event model."""
    return {
        "id": uuid.uuid4(),
        "title": "Sample Test Event",
        "start_time": datetime(2024, 8, 1, 10, 0, 0),
        "end_time": datetime(2024, 8, 1, 11, 0, 0),
        "time_zone": "UTC",
    } 