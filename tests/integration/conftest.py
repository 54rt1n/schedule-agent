from datetime import datetime
import uuid

import pytest

from schedule_core.db.dao import EventCreate
from schedule_core.db.service import DatabaseService
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_core.db.strategies.create_event import CreateEventStrategy
from schedule_core.db.strategies.get_event_by_id import GetEventByIdStrategy
from schedule_core.db.strategies.save_a2a_task import SaveA2ATaskStrategy
from schedule_core.db.strategies.get_a2a_task_by_id import GetA2ATaskByIdStrategy
from schedule_core.db.strategies.update_a2a_task_status import UpdateA2ATaskStatusStrategy
from schedule_core.db.strategies.contact_create import CreateContactStrategy
from schedule_core.db.strategies.contact_find import FindContactsStrategy
from schedule_core.db.strategies.contact_get_by_id import GetContactByIdStrategy
from schedule_core.db.strategies.contact_update import UpdateContactStrategy
from schedule_core.db.strategies.contact_delete import DeleteContactStrategy

# Generic Sample Event Data (adjust if tests need more specific versions)
@pytest.fixture
def sample_event_data() -> dict:
    """Provides generic sample data for creating an Event model."""
    return {
        "id": uuid.uuid4(),
        "title": "Sample Test Event",
        "start_time": datetime(2024, 8, 1, 10, 0, 0), # Use naive UTC for direct model creation
        "end_time": datetime(2024, 8, 1, 11, 0, 0),
        "time_zone": "UTC",
    } 

@pytest.fixture
def create_event_strategy(db_service: DatabaseService, date_utils: DateUtils,
                          conflict_resolver: ConflictResolver,
                          get_events_in_range_strategy: GetEventsInRangeStrategy) -> CreateEventStrategy:
    """Provides data for creating an Event model."""
    create_event_strategy = CreateEventStrategy(
        db_service=db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
        get_events_in_range_strategy=get_events_in_range_strategy,
    )
    return create_event_strategy

@pytest.fixture
def get_event_by_id_strategy(db_service: DatabaseService) -> GetEventByIdStrategy:
    """Provides a strategy for getting an event by ID."""
    return GetEventByIdStrategy(db_service=db_service)

@pytest.fixture
def save_a2a_task_strategy(db_service: DatabaseService) -> SaveA2ATaskStrategy:
    """Provides a strategy for saving an A2A task."""
    return SaveA2ATaskStrategy(db_service=db_service)

@pytest.fixture
def get_a2a_task_by_id_strategy(db_service: DatabaseService) -> GetA2ATaskByIdStrategy:
    """Provides a strategy for getting an A2A task by ID."""
    return GetA2ATaskByIdStrategy(db_service=db_service)

@pytest.fixture
def update_a2a_task_status_strategy(db_service: DatabaseService) -> UpdateA2ATaskStatusStrategy:
    """Provides a strategy for updating an A2A task status."""
    return UpdateA2ATaskStatusStrategy(db_service=db_service)

@pytest.fixture
def get_events_in_range_strategy(db_service: DatabaseService, date_utils: DateUtils) -> GetEventsInRangeStrategy:
    """Provides an instance of the GetEventsInRangeStrategy."""
    return GetEventsInRangeStrategy(db_service=db_service, date_utils=date_utils) 

# Contact Strategy Fixtures
@pytest.fixture
def create_contact_strategy(db_service: DatabaseService) -> CreateContactStrategy:
    """Provides an instance of CreateContactStrategy."""
    return CreateContactStrategy(db_service=db_service)

@pytest.fixture
def find_contacts_strategy(db_service: DatabaseService) -> FindContactsStrategy:
    """Provides an instance of FindContactsStrategy."""
    return FindContactsStrategy(db_service=db_service)

@pytest.fixture
def get_contact_by_id_strategy(db_service: DatabaseService) -> GetContactByIdStrategy:
    """Provides an instance of GetContactByIdStrategy."""
    return GetContactByIdStrategy(db_service=db_service)

@pytest.fixture
def update_contact_strategy(db_service: DatabaseService) -> UpdateContactStrategy:
    """Provides an instance of UpdateContactStrategy."""
    return UpdateContactStrategy(db_service=db_service)

@pytest.fixture
def delete_contact_strategy(db_service: DatabaseService) -> DeleteContactStrategy:
    """Provides an instance of DeleteContactStrategy."""
    return DeleteContactStrategy(db_service=db_service) 