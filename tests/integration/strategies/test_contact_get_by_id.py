# tests/integration/strategies/test_contact_get_by_id.py

import pytest
import uuid
import pytest_asyncio

from sa.db.models import Contact
from sa.db.dao import ContactCreate, ContactGetById
from sa.db.strategies.contact_create import CreateContactStrategy
from sa.db.strategies.contact_get_by_id import GetContactByIdStrategy
from sa.db.service import DatabaseService

# Use pytest-asyncio for async tests
pytestmark = pytest.mark.asyncio

# Use pytest_asyncio.fixture decorator
@pytest_asyncio.fixture
async def existing_contact(create_contact_strategy: CreateContactStrategy) -> Contact:
    """Fixture to create a contact before tests run."""
    dao = ContactCreate(name="GetById Tester", email=f"getme.{uuid.uuid4()}@example.com")
    contact = await create_contact_strategy.execute(data=dao)
    assert contact is not None
    return contact

async def test_get_contact_success(existing_contact: Contact,
                                  get_contact_by_id_strategy: GetContactByIdStrategy):
    """Test successfully getting an existing contact by ID."""
    get_dao = ContactGetById(id=existing_contact.id)

    # --- Act --- 
    fetched_contact = await get_contact_by_id_strategy.execute(data=get_dao)

    # --- Assert --- 
    assert fetched_contact is not None
    assert isinstance(fetched_contact, Contact)
    assert fetched_contact.id == existing_contact.id
    assert fetched_contact.name == existing_contact.name
    assert fetched_contact.email == existing_contact.email
    assert fetched_contact.created_at == existing_contact.created_at

async def test_get_contact_not_found(get_contact_by_id_strategy: GetContactByIdStrategy):
    """Test getting a contact with a non-existent ID."""
    non_existent_id = uuid.uuid4()
    get_dao = ContactGetById(id=non_existent_id)

    # --- Act --- 
    fetched_contact = await get_contact_by_id_strategy.execute(data=get_dao)

    # --- Assert ---
    assert fetched_contact is None
