import pytest
import pytest_asyncio
import httpx
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
from typing import AsyncGenerator

# Import the app instance and dependency getters
from scheduling_agent.api.main import app
from scheduling_agent.api.dependencies import (
    get_db_service,
    get_task_manager,
    get_conflict_resolver,
    get_nlp_processor
)
# Import middleware module to patch its settings
from scheduling_agent.api.middleware import auth as auth_middleware

# Import types and services for mock specs
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.common.types import Task, TaskStatus, TaskState
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMDateInfo


# Constants used by tests
BASE_URL = "http://test"
RPC_URL = "/api/rpc/" # Ensure consistent path

# --- Core Fixtures ---

@pytest_asyncio.fixture(scope="function") # Function scope for client to ensure clean state
async def async_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Provides a standard, unauthenticated httpx client configured for the FastAPI app."""
    # Transport needs to be created within the async context
    transport = httpx.ASGITransport(app=app)
    # NO default headers
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        yield client

# --- Mock Service Fixtures ---

@pytest.fixture(scope="function")
def mock_db_service() -> AsyncMock:
    """Provides a function-scoped AsyncMock for DatabaseService."""
    mock = AsyncMock(spec=DatabaseService)
    mock.create_event = AsyncMock()
    mock.query_events = AsyncMock()
    mock.get_events_in_range = AsyncMock(return_value=[]) # Default for exporter tests later
    return mock

@pytest.fixture(scope="function")
def mock_task_manager() -> MagicMock: # TaskManager methods used by handlers are async now
    """Provides a function-scoped MagicMock for SchedulingTaskManager."""
    mock = MagicMock(spec=SchedulingTaskManager)
    # Configure necessary async methods primarily used by handlers
    mock.create_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING)))
    mock.get_task = AsyncMock(return_value=None) # Default to not found
    mock.update_task_status = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING))) # Placeholder
    mock.add_task_artifact = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.WORKING))) # Placeholder
    mock.complete_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.COMPLETED)))
    mock.fail_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.FAILED)))
    mock.require_input_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.INPUT_REQUIRED)))
    # Add cancel_task mock for Phase 4
    mock.cancel_task = AsyncMock(return_value=Task(id=str(uuid.uuid4()), status=TaskStatus(state=TaskState.CANCELED)))
    return mock

@pytest.fixture(scope="function")
def mock_nlp_processor() -> AsyncMock:
    """Provides a function-scoped AsyncMock for NLPProcessor."""
    mock = AsyncMock(spec=NLPProcessor)
    # Default parse result for basic tests
    mock.parse_natural_language_query = AsyncMock(
        return_value=LLMSchedulingParseResult(
            intent="unknown",
            date_info=LLMDateInfo()
        )
    )
    return mock

@pytest.fixture(scope="function")
def mock_conflict_resolver() -> AsyncMock:
    """Provides a function-scoped AsyncMock for ConflictResolver."""
    mock = AsyncMock(spec=ConflictResolver)
    mock.generate_alternative_times = AsyncMock(return_value=[]) # Default empty list
    return mock


# --- Auto-use Fixture for Dependency Overrides ---

@pytest.fixture(scope="function", autouse=True)
def apply_mock_dependencies(
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock, # Keep as MagicMock if spec uses sync methods internally
    mock_nlp_processor: AsyncMock,
    mock_conflict_resolver: AsyncMock,
):
    """Automatically overrides FastAPI dependencies for each test function.
    Auth key patching is now handled specifically in test_auth.py.
    """
    # --- DO NOT Patch Auth Keys Globally Here --- 
    # test_api_keys = {"test-key-1", "test-key-2"}
    # with patch.object(auth_middleware, 'VALID_API_KEYS', test_api_keys):
    
    # --- Override FastAPI Dependencies ---
    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides[get_db_service] = lambda: mock_db_service
    app.dependency_overrides[get_task_manager] = lambda: mock_task_manager
    app.dependency_overrides[get_nlp_processor] = lambda: mock_nlp_processor
    app.dependency_overrides[get_conflict_resolver] = lambda: mock_conflict_resolver
    
    yield # Run the test

    # --- Restore Overrides ---
    app.dependency_overrides = original_overrides 