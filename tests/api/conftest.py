import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typing import Dict, Any, Generator

from sa.api.__main__ import main as create_app
from sa.core.logging_config import get_logger
from sa.core.settings import Settings
from sa.db.dao import EventCreate
from sa.db.models import Event
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils
from sa.event.conflict_resolver import ConflictResolver

logger = get_logger(__name__)

# Define standard test API keys
TEST_API_KEY_VALID = "test_api_key_good"
TEST_API_KEY_INVALID = "test_api_key_bad"

@pytest.fixture(scope="session")
def settings() -> Settings:
    """
    Provides test-specific application settings.
    Crucially uses an in-memory SQLite DB and defines test API keys.
    Session-scoped as settings are generally static for a test run.
    """
    logger.info("Creating test settings...")
    test_settings = Settings()
    test_settings.database_url = "sqlite+aiosqlite:///:memory:?cache=shared"
    test_settings.api_keys = [TEST_API_KEY_VALID]
    test_settings.log_level = "WARNING"
    test_settings.llm_base_url = "http://mock-llm.test/v1"
    test_settings.llm_api_key = "mock-api-key" # THIS IS REQUIRED BY THE openai CLIENt, DO NOT REMOVE
    return test_settings

@pytest_asyncio.fixture(scope="function")
async def test_app(settings: Settings) -> FastAPI:
    """
    Creates a real FastAPI application instance configured for testing.
    Uses the actual app factory (sa.api.__main__.main) to ensure
    lifespan events, middleware, dependencies, and routes are set up correctly.
    Session-scoped because app setup can be reused across tests.
    """
    logger.info(f"Creating test app with DB: {settings.database_url}...")
    # Pass the test settings to the app factory
    app = create_app(settings=settings)
    logger.info("Test app created.")
    db_service : DatabaseService = app.state.db_service
    await db_service.initialize()
    return app

@pytest.fixture(scope="function")
def app_db_service(test_app: FastAPI) -> DatabaseService:
    return test_app.state.db_service

@pytest.fixture(scope="function")
def client(test_app: FastAPI) -> Generator[TestClient, None, None]: # Changed return type hint
    """
    Provides a FastAPI TestClient configured to talk to the test_app.
    Function-scoped for test isolation.
    Note: TestClient is synchronous, but can test async applications.
    """
    logger.info("Creating FastAPI TestClient...")
    # Do not change this to the async version, it will break the tests.
    # DO NOT CHANGE FROM TESTCLIENT TO HTTPX CLIENT
    # I REAPEAT DO NOT CHANGE THIS CLIENT OR THE TESTS WILL BREAK
    with TestClient(app=test_app, base_url="http://testserver") as test_client:
        logger.info("TestClient created, yielding...")
        yield test_client
        logger.info("TestClient cleanup.")

@pytest.fixture(scope="function")
def auth_headers_valid() -> Dict[str, str]:
    """Provides valid authentication headers."""
    return {"Authorization": f"Bearer {TEST_API_KEY_VALID}"}

@pytest.fixture(scope="function")
def auth_headers_invalid() -> Dict[str, str]:
    """Provides invalid authentication headers."""
    return {"Authorization": f"Bearer {TEST_API_KEY_INVALID}"}

# --- Helper for building RPC requests ---
def build_rpc_request(method: str, params: Dict | list | None = None, request_id: Any = "test-id") -> Dict:
    """Helper function to construct a JSON-RPC request dictionary."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params if params is not None else {},
        "id": request_id,
    }

# --- Test Setup Helper using Database Strategy ---
async def create_db_event(
    event_create: EventCreate,
    app_db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
) -> Event:
    get_events_in_range_strategy = GetEventsInRangeStrategy(
        db_service=app_db_service,
        date_utils=date_utils,
    )
    create_strategy = CreateEventStrategy(
        db_service=app_db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy,
    )
    """Helper to directly create an event in the DB using the strategy."""
    created_event = await create_strategy.execute(event_create)
    assert created_event is not None
    logger.info(f"Event created via strategy with ID: {created_event.id}")
    return created_event 