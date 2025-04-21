import pytest
import pytest_asyncio
from unittest.mock import AsyncMock

# Import necessary components for the fixture
from sqlalchemy.orm import sessionmaker
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.db.service import DatabaseService

# Make the fixture available to all tests in this directory
@pytest_asyncio.fixture
async def db_service(test_session_factory) -> DatabaseService:
    """Fixture to provide a DatabaseService instance with mocks."""
    # Create mock dependencies
    mock_conflict_resolver = AsyncMock(spec=ConflictResolver)
    mock_conflict_resolver.detect_conflicts.return_value = []  # Default: no conflicts

    service = DatabaseService(
        session_factory=test_session_factory,  # Pass the regular factory from root conftest
        date_utils=DateUtils(),  # Instantiate real DateUtils
        conflict_resolver=mock_conflict_resolver,  # Use placeholder mock
    )
    return service 