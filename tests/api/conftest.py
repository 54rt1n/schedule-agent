import pytest
import pytest_asyncio
# Remove httpx imports if no longer directly used, keep if needed for types
# from httpx import AsyncClient, ASGITransport 
from fastapi import FastAPI
from fastapi.testclient import TestClient # Import TestClient
from typing import AsyncGenerator, Dict, Any, Generator # Add Generator
from unittest.mock import patch, MagicMock # Import patch and MagicMock

from sa.core.settings import Settings
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.api.__main__ import main as create_app
from sa.nlp.llm_provider import LLMProvider # Import LLMProvider

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
    test_settings.log_level = "INFO"
    test_settings.llm_base_url = "http://mock-llm.test/v1"
    test_settings.llm_api_key = "mock-api-key"
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
def client(test_app: FastAPI) -> Generator[TestClient, None, None]: # Changed return type hint
    """
    Provides a FastAPI TestClient configured to talk to the test_app.
    Function-scoped for test isolation.
    Note: TestClient is synchronous, but can test async applications.
    """
    logger.info("Creating FastAPI TestClient...")
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

# Optional: Helper for building RPC requests
def build_rpc_request(method: str, params: Dict | list | None = None, request_id: Any = "test-id") -> Dict:
    """Helper function to construct a JSON-RPC request dictionary."""
    return {
        "jsonrpc": "2.0",
        "method": method,
        "params": params if params is not None else {},
        "id": request_id,
    } 