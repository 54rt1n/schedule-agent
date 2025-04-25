# tests/integration/mcp/conftest.py

import pytest
import pytest_asyncio

from sa.core.settings import Settings
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils

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
async def mcp_test_db_service(
    mcp_test_settings: Settings
) -> DatabaseService:
    """Provides a real DatabaseService connected to the test DB"""
    db_service = DatabaseService.from_settings(mcp_test_settings)
    await db_service.initialize()
    return db_service

@pytest.fixture(scope="function")
def mcp_test_conflict_resolver(
    mcp_test_date_utils: DateUtils
) -> ConflictResolver:
    """Provides a real ConflictResolver"""
    resolver = ConflictResolver(
        date_utils=mcp_test_date_utils
    )
    return resolver
