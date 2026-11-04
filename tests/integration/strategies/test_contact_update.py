# tests/integration/strategies/test_contact_update.py

import pytest
import uuid
from datetime import datetime, timezone, timedelta
import sqlalchemy as sa
import pytest_asyncio
import asyncio # For sleep

# Assuming necessary imports - adjust as needed
from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactCreate, ContactUpdate, ContactGetById
from schedule_core.db.strategies.contact_create import CreateContactStrategy
from schedule_core.db.strategies.contact_update import UpdateContactStrategy
from schedule_core.db.strategies.contact_get_by_id import GetContactByIdStrategy
from schedule_core.core.exceptions import ConflictError, DatabaseError, ContactNotFoundError
from sqlalchemy.exc import IntegrityError

# Use pytest-asyncio for async tests
pytestmark = pytest.mark.asyncio

@pytest_asyncio.fixture
async def contacts_for_update(create_contact_strategy: CreateContactStrategy):
    """Fixture to create contacts for update tests."""
    contact1_email = f"update.me.{uuid.uuid4()}@example.com"
    contact2_email = f"existing.other.{uuid.uuid4()}@example.com"
    c1 = await create_contact_strategy.execute(data=ContactCreate(name="Update Me", email=contact1_email, phone="111-1111"))
    c2 = await create_contact_strategy.execute(data=ContactCreate(name="Existing Other", email=contact2_email, phone="222-2222"))
    assert c1 is not None
    assert c2 is not None
    return c1, c2 # Return as a tuple

async def test_update_contact_success(contacts_for_update, update_contact_strategy: UpdateContactStrategy,
                                     get_contact_by_id_strategy: GetContactByIdStrategy):
    """Test successfully updating a contact's name and phone."""
    contact_to_update, _ = contacts_for_update
    update_dao = ContactUpdate(name="Updated Name", phone="999-9999")
    contact_id = contact_to_update.id

    original_updated_at = contact_to_update.updated_at
    await asyncio.sleep(0.01) # Ensure timestamp changes

    # --- Act ---
    updated_contact = await update_contact_strategy.execute(data=(contact_id, update_dao))

    # --- Assert ---
    assert updated_contact is not None
    assert updated_contact.id == contact_id
    assert updated_contact.name == "Updated Name"
    assert updated_contact.email == contact_to_update.email # Email should not change
    assert updated_contact.phone == "999-9999"
    assert updated_contact.notes == contact_to_update.notes # Notes should not change
    assert updated_contact.updated_at > original_updated_at # Timestamp should update

    # Verify in DB
    fetched = await get_contact_by_id_strategy.execute(data=ContactGetById(id=contact_id))
    assert fetched is not None
    assert fetched.name == "Updated Name"
    assert fetched.phone == "999-9999"
    assert fetched.updated_at == updated_contact.updated_at

async def test_update_contact_not_found(update_contact_strategy: UpdateContactStrategy):
    """Test updating a contact that does not exist raises DatabaseError(ContactNotFoundError)."""
    non_existent_id = uuid.uuid4()
    update_dao = ContactUpdate(name="Doesn't Matter")

    # --- Act & Assert ---
    # Expect DatabaseError wrapping ContactNotFoundError
    with pytest.raises(DatabaseError) as exc_info:
        await update_contact_strategy.execute(data=(non_existent_id, update_dao))

    # Check the cause
    assert isinstance(exc_info.value.__cause__, ContactNotFoundError)
    assert str(non_existent_id) in str(exc_info.value.__cause__)

async def test_update_contact_duplicate_email(contacts_for_update, update_contact_strategy: UpdateContactStrategy,
                                              get_contact_by_id_strategy: GetContactByIdStrategy):
    """Test updating a contact's email to one that already exists raises DatabaseError(IntegrityError)."""
    contact_to_update, existing_other = contacts_for_update

    # Try to update contact1's email to contact2's email
    update_dao = ContactUpdate(email=existing_other.email)
    contact_id = contact_to_update.id

    # --- Act & Assert ---
    # Expect DatabaseError wrapping IntegrityError
    with pytest.raises(DatabaseError) as exc_info:
        await update_contact_strategy.execute(data=(contact_id, update_dao))

    # Check the cause
    assert isinstance(exc_info.value.__cause__, IntegrityError)
    # Check the original error message
    assert "email" in str(exc_info.value.__cause__.orig).lower()
    assert "unique constraint" in str(exc_info.value.__cause__.orig).lower()

    # Verify original contact email didn't change
    fetched = await get_contact_by_id_strategy.execute(data=ContactGetById(id=contact_id))
    assert fetched is not None
    assert fetched.email == contact_to_update.email # Should be original email
