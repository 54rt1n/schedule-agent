import pytest
from unittest.mock import AsyncMock, MagicMock

# Import actual classes for spec
from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.db.service import DatabaseService


@pytest.fixture
def mock_task_manager() -> AsyncMock:
    """Provides a function-scoped AsyncMock for SchedulingTaskManager."""
    return AsyncMock(spec=SchedulingTaskManager)

@pytest.fixture
def mock_db_service() -> AsyncMock:
    """Provides a function-scoped AsyncMock for DatabaseService."""
    return AsyncMock(spec=DatabaseService)

@pytest.fixture
def mock_nlp_processor() -> MagicMock:
    """Provides a function-scoped MagicMock for NLPProcessor."""
    # NLPProcessor itself isn't async, but its methods might be
    # Using MagicMock for the object, AsyncMock can be used for its methods if needed
    return MagicMock(spec=NLPProcessor)

@pytest.fixture
def mock_conflict_resolver() -> AsyncMock:
    """Provides a function-scoped AsyncMock for ConflictResolver."""
    return AsyncMock(spec=ConflictResolver)

@pytest.fixture
def mock_date_utils() -> MagicMock:
    """Provides a function-scoped MagicMock for DateUtils."""
    # DateUtils methods are synchronous
    mock = MagicMock(spec=DateUtils)
    # Configure specific methods if default behavior is needed across tests
    # e.g., mock.normalize_to_utc.side_effect = lambda dt, tz=None: ...
    return mock

@pytest.fixture
def mock_ical_exporter() -> MagicMock:
    """Provides a function-scoped MagicMock for ICalExporter."""
    return MagicMock(spec=ICalExporter)

@pytest.fixture
def mock_org_exporter() -> MagicMock:
    """Provides a function-scoped MagicMock for OrgModeExporter."""
    return MagicMock(spec=OrgModeExporter)

@pytest.fixture
def mock_exporters(mock_ical_exporter, mock_org_exporter) -> dict[str, MagicMock]:
    """Provides a dictionary of mock exporters, mirroring how they might be passed."""
    return {
        "ics": mock_ical_exporter,
        "org": mock_org_exporter,
    }
