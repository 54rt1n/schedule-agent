# tests/integration/strategies/test_contact_delete.py

import pytest
import uuid
import sqlalchemy as sa
import pytest_asyncio

# Assuming necessary imports - adjust as needed
from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactCreate, ContactDelete, ContactGetById
from schedule_core.db.strategies.contact_create import CreateContactStrategy
from schedule_core.db.strategies.contact_delete import DeleteContactStrategy
from schedule_core.db.strategies.contact_get_by_id import GetContactByIdStrategy # For verification
from schedule_core.db.service import DatabaseService
# Import necessary exceptions
from schedule_core.core.exceptions import DatabaseError, ContactNotFoundError

# Use pytest-asyncio for async tests
pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture
async def contact_to_delete(create_contact_strategy: CreateContactStrategy) -> Contact:
    """Fixture to create a contact specifically for deletion tests."""
    dao = ContactCreate(name="Delete Me Soon", email=f"delete.me.{uuid.uuid4()}@example.com")
    contact = await create_contact_strategy.execute(data=dao)
    assert contact is not None
    return contact

async def test_delete_contact_success(contact_to_delete: Contact, delete_contact_strategy: DeleteContactStrategy,
                                     get_contact_by_id_strategy: GetContactByIdStrategy):
    """Test successfully deleting an existing contact."""
    delete_dao = ContactDelete(id=contact_to_delete.id)
    contact_id = contact_to_delete.id

    # --- Act ---
    # Strategy returns None on success now
    result = await delete_contact_strategy.execute(data=delete_dao)

    # --- Assert ---
    # Assert result is None
    assert result is None

    # Verify in DB
    fetched = await get_contact_by_id_strategy.execute(data=ContactGetById(id=contact_id))
    assert fetched is None # Should not be found

async def test_delete_contact_not_found(delete_contact_strategy: DeleteContactStrategy):
    """Test deleting a contact that does not exist raises DatabaseError(ContactNotFoundError)."""
    non_existent_id = uuid.uuid4()
    delete_dao = ContactDelete(id=non_existent_id)

    # --- Act & Assert ---
    # Expect DatabaseError wrapping ContactNotFoundError
    with pytest.raises(DatabaseError) as exc_info:
        await delete_contact_strategy.execute(data=delete_dao)

    # Check the cause
    assert isinstance(exc_info.value.__cause__, ContactNotFoundError)
    # Check the message on the original error
    assert str(non_existent_id) in str(exc_info.value.__cause__)
