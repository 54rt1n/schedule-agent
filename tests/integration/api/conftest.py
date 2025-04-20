import uuid
from collections.abc import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from scheduling_agent.api.dependencies import (
    get_conflict_resolver,
    get_date_utils,
    get_db_service,
    get_exporters,
    get_nlp_processor,
    get_task_manager,
)

# Import the app instance and dependency getters
from scheduling_agent.api.main import app as main_app

# Import middleware module to patch its settings
from scheduling_agent.a2a.types import Task, TaskState, TaskStatus
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exporters.base import CalendarExporter
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.core.schemas import LLMDateInfo, LLMSchedulingParseResult

# Import types and services for mock specs
from scheduling_agent.db.service import DatabaseService

# Import settings to patch
from scheduling_agent.settings import settings
from scheduling_agent.a2a.task_manager import SchedulingTaskManager

# Import test_db_url fixture from root conftest

# Constants used by tests
BASE_URL = "http://test"
RPC_URL = "/api/rpc/"  # Ensure consistent path

# --- NEW Fixture for App with Patched Settings ---


@pytest.fixture
def test_app(test_db_url: str) -> Generator[FastAPI, None, None]:
    """
    Provides the FastAPI app instance with the database URL patched
    to use the in-memory test database for the duration of a test function.
    """
    with patch.object(settings, "database_url", test_db_url):
        yield main_app


# --- Core Fixtures ---


@pytest_asyncio.fixture(scope="function")  # Function scope for client to ensure clean state
async def async_client(test_app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provides a standard, unauthenticated httpx client configured for the FastAPI app."""
    transport = httpx.ASGITransport(app=test_app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client


# --- Mock Service Fixtures ---


@pytest.fixture
def mock_db_service() -> AsyncMock:
    """Provides a function-scoped AsyncMock for DatabaseService."""
    mock = AsyncMock(spec=DatabaseService)
    mock.create_event = AsyncMock()
    mock.query_events = AsyncMock()
    mock.get_events_in_range = AsyncMock(return_value=[])
    mock.date_utils = MagicMock(spec=DateUtils)
    mock.conflict_resolver = MagicMock(spec=ConflictResolver)
    return mock


@pytest.fixture
def mock_task_manager() -> MagicMock:
    """Provides a function-scoped MagicMock for SchedulingTaskManager."""
    mock = MagicMock(spec=SchedulingTaskManager)
    mock.create_task = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING))
    )
    mock.get_task = AsyncMock(return_value=None)
    mock.update_task_status = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING))
    )
    mock.add_task_artifact = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING))
    )
    mock.complete_task = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.COMPLETED))
    )
    mock.fail_task = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.FAILED))
    )
    mock.require_input_task = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.INPUT_REQUIRED))
    )
    mock.cancel_task = AsyncMock(
        return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.CANCELED))
    )
    return mock


@pytest.fixture
def mock_nlp_processor() -> AsyncMock:
    """Provides a function-scoped AsyncMock for NLPProcessor."""
    mock = AsyncMock(spec=NLPProcessor)
    mock.parse_natural_language_query = AsyncMock(
        return_value=LLMSchedulingParseResult(intent="unknown", date_info=LLMDateInfo())
    )
    return mock


@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    """Provides a function-scoped AsyncMock for ConflictResolver."""
    mock = AsyncMock(spec=ConflictResolver)
    mock.generate_alternative_times = AsyncMock(return_value=[])
    return mock


@pytest.fixture
def mock_date_utils() -> MagicMock:
    """Provides a function-scoped MagicMock for DateUtils."""
    return MagicMock(spec=DateUtils)


# --- Mock Exporter Fixture (Configured) ---
@pytest.fixture
def mock_exporters() -> MagicMock:
    """Provides a function-scoped MagicMock for the exporters dictionary,
    configured to return specific exporter mocks via .get()."""
    # Create specific mock instances for each exporter type needed
    mock_ical = MagicMock(spec=CalendarExporter)
    # Explicitly create the mock methods before setting return_value
    mock_ical.export = MagicMock(return_value="BEGIN:VCALENDAR...END:VCALENDAR")
    mock_ical.get_mime_type = MagicMock(return_value="text/calendar")

    mock_org = MagicMock(spec=CalendarExporter)
    mock_org.export = MagicMock(return_value="* Event 1\n* Event 2")
    mock_org.get_mime_type = MagicMock(return_value="text/plain")

    # Create the main dictionary mock
    exporters_dict_mock = MagicMock(spec=dict)

    # Configure the .get() method
    def get_side_effect(key, default=None):
        if key == "ics":
            return mock_ical
        if key == "org":
            return mock_org
        return default

    exporters_dict_mock.get.side_effect = get_side_effect

    return exporters_dict_mock


# --- Auto-use Fixture for Dependency Overrides ---


@pytest.fixture(autouse=True)
def apply_mock_dependencies(
    test_app: FastAPI,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_nlp_processor: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    mock_date_utils: MagicMock,
    mock_exporters: MagicMock,
):
    """Automatically overrides FastAPI dependencies for each test function
    using the app instance provided by the test_app fixture.
    Auth key patching is now handled specifically in test_auth.py.
    """
    original_overrides = test_app.dependency_overrides.copy()
    test_app.dependency_overrides[get_db_service] = lambda: mock_db_service
    test_app.dependency_overrides[get_task_manager] = lambda: mock_task_manager
    test_app.dependency_overrides[get_nlp_processor] = lambda: mock_nlp_processor
    test_app.dependency_overrides[get_conflict_resolver] = lambda: mock_conflict_resolver
    test_app.dependency_overrides[get_date_utils] = lambda: mock_date_utils
    test_app.dependency_overrides[get_exporters] = lambda: mock_exporters

    yield

    test_app.dependency_overrides = original_overrides
