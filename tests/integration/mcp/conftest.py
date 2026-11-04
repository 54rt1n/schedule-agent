# tests/integration/mcp/conftest.py

import pytest
import pytest_asyncio

from schedule_core.core.settings import Settings
from schedule_core.db.service import DatabaseService
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils

# --- Fixtures for Real Services connected to Test DB ---


@pytest.fixture(scope="function")
def mcp_test_date_utils() -> DateUtils:
    """Provides a real DateUtils instance for tests."""
    return DateUtils()


@pytest.fixture(scope="function")
def mcp_test_settings() -> Settings:
    """Provides a real Settings instance for tests."""
    settings = Settings()
    # Use in-memory SQLite for testing
    settings.database_url = "sqlite+aiosqlite:///:memory:"
    return settings


@pytest_asyncio.fixture(scope="function")
async def mcp_test_db_service(mcp_test_settings: Settings) -> DatabaseService:
    """Provides a real DatabaseService connected to the test DB"""
    db_service = DatabaseService.from_settings(mcp_test_settings)
    await db_service.initialize()
    try:
        yield db_service
    finally:
        await db_service.close()


@pytest.fixture(scope="function")
def mcp_test_conflict_resolver(mcp_test_date_utils: DateUtils) -> ConflictResolver:
    """Provides a real ConflictResolver"""
    resolver = ConflictResolver(date_utils=mcp_test_date_utils)
    return resolver


@pytest.fixture
def mcp_call(mcp_test_db_service, mcp_test_settings):
    """Invoke the registered MCP handler with its public request contract."""
    from mcp.server.fastmcp import FastMCP
    from schedule_mcp.handlers import register_handlers

    server = FastMCP("handler-tests")
    register_handlers(server, mcp_test_db_service, mcp_test_settings)

    async def call(name, arguments):
        tool = server._tool_manager.get_tool(name)
        result = await tool.fn(**arguments)
        return result.model_dump(mode="json")

    return call
