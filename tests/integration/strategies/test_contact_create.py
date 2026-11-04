# tests/integration/strategies/test_contact_create.py

import pytest
import uuid
from sqlalchemy.exc import IntegrityError # Though we expect ConflictError
import sqlalchemy as sa # For verification query

# Assuming necessary imports - adjust as needed
from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactCreate
from schedule_core.db.strategies.contact_create import CreateContactStrategy
from schedule_core.db.service import DatabaseService
from schedule_core.core.exceptions import DatabaseError

# Use pytest-asyncio for async tests
pytestmark = pytest.mark.asyncio


async def test_create_contact_success(db_service: DatabaseService, create_contact_strategy: CreateContactStrategy):
    """Test successfully creating a unique contact."""
    create_dao = ContactCreate(
        name="Alice Smith",
        email="alice.smith.unique@example.com", # Ensure unique for test runs
        phone="111-222-3333",
        notes="Test note for Alice."
    )

    # --- Act --- 
    created_contact = await create_contact_strategy.execute(data=create_dao)

    # --- Assert --- 
    assert created_contact is not None
    assert isinstance(created_contact, Contact)
    assert created_contact.id is not None
    assert created_contact.name == create_dao.name
    assert created_contact.email == create_dao.email
    assert created_contact.phone == create_dao.phone
    assert created_contact.notes == create_dao.notes
    assert created_contact.created_at is not None
    assert created_contact.updated_at is not None

    # Verify persistence (optional but good)
    async with db_service.get_session() as session:
        fetched = await session.get(Contact, created_contact.id)
        assert fetched is not None
        assert fetched.name == "Alice Smith"
        assert fetched.email == "alice.smith.unique@example.com"


async def test_create_contact_duplicate_email(db_service: DatabaseService, create_contact_strategy: CreateContactStrategy):
    """Test creating a contact with an email that already exists."""
    unique_email = f"bob.j.{uuid.uuid4()}@example.com" # Ensure unique email for test setup
    # --- Arrange: Create the first contact ---
    first_dao = ContactCreate(name="Bob Johnson", email=unique_email)
    first_contact = await create_contact_strategy.execute(data=first_dao)
    assert first_contact is not None
    assert first_contact.email == unique_email

    # --- Act & Assert: Expect DatabaseError caused by IntegrityError ---
    duplicate_dao = ContactCreate(name="Bobby Johnson", email=unique_email)

    # Expect DatabaseError from the strategy's base execute method
    with pytest.raises(DatabaseError) as exc_info:
        await create_contact_strategy.execute(data=duplicate_dao)

    # Check that the underlying cause is IntegrityError
    assert isinstance(exc_info.value.__cause__, IntegrityError)
    # Optional: Check the original error message for email constraint details
    assert "contact.email" in str(exc_info.value.__cause__.orig).lower()
    assert "unique constraint" in str(exc_info.value.__cause__.orig).lower()

    # Verify only the first contact exists
    async with db_service.get_session() as session:
        contacts = (
            await session.execute(
                sa.select(Contact).where(Contact.email == unique_email)
            )
        ).scalars().all()
        assert len(contacts) == 1
        assert contacts[0].id == first_contact.id
        assert contacts[0].name == "Bob Johnson"