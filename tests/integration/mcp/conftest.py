# tests/integration/mcp/conftest.py

import pytest
import pytest_asyncio
from unittest.mock import patch
from sqlalchemy.orm import sessionmaker
import logging
from logging import getLogger

# Services and DB for creating *real* instances connected to test DB
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.db.service import DatabaseService

# --- Fixtures for Real Services connected to Test DB ---

@pytest.fixture(scope="function")
def mcp_test_date_utils() -> DateUtils:
    """Provides a real DateUtils instance for tests."""
    return DateUtils()

@pytest.fixture(scope="function")
def mcp_test_db_service(
    test_session_factory: sessionmaker, # From root conftest
    mcp_test_date_utils: DateUtils
) -> DatabaseService:
    """Provides a real DatabaseService connected to the test DB.
       Conflict resolver is NOT injected here yet.
    """
    service = DatabaseService(
        session_factory=test_session_factory,
        date_utils=mcp_test_date_utils
    )
    return service

@pytest.fixture(scope="function")
def mcp_test_conflict_resolver(
    mcp_test_db_service: DatabaseService, # Needs DB service
    mcp_test_date_utils: DateUtils
) -> ConflictResolver:
    """Provides a real ConflictResolver using the test DB service
       and injects itself back into the db_service instance.
    """
    resolver = ConflictResolver(
        db_service=mcp_test_db_service,
        date_utils=mcp_test_date_utils
    )
    # Inject the resolver back into the service instance used by the resolver
    mcp_test_db_service.conflict_resolver = resolver
    return resolver

# Removing unused logger fixture
# @pytest.fixture(scope="function")
# def mcp_test_logger():
#     """Provides a logger instance for MCP tests."""
#     # Reuse existing logging setup but get a specific logger
#     logger = getLogger("test.mcp")
#     logger.setLevel(logging.DEBUG) # Ensure detailed logging for tests
#     return logger

# --- Auto-use Fixture to Patch Global Services in mcp.py ---

# Removing the fixture below as it's not the correct approach
# @pytest_asyncio.fixture(scope="function", autouse=True)
# async def apply_mcp_patches(mcp_test_db_service, mcp_test_conflict_resolver, mcp_test_logger):
#     """Auto-applied fixture to patch dependencies within the mcp module for tests."""
#     target_module = "scheduling_agent.mcp.handlers" # Adjust if needed
#
#     with patch(f"{target_module}.db_service", mcp_test_db_service, create=True), \\
#          patch(f"{target_module}.conflict_resolver", mcp_test_conflict_resolver, create=True), \\
#          patch(f"{target_module}.logger", mcp_test_logger, create=True):
#         yield