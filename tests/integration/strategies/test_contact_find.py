# tests/integration/strategies/test_contact_find.py

import pytest
import uuid
import sqlalchemy as sa
from sqlalchemy import func # For lower()
import pytest_asyncio # Import for async fixture

# Assuming necessary imports - adjust as needed
from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactCreate, ContactQuery
from schedule_core.db.strategies.contact_create import CreateContactStrategy # Needed for setup
from schedule_core.db.strategies.contact_find import FindContactsStrategy
from schedule_core.db.service import DatabaseService

# Use pytest-asyncio for async tests
pytestmark = pytest.mark.asyncio

# Use default function scope
@pytest_asyncio.fixture
async def setup_contacts(db_service: DatabaseService):
    """Fixture to create a set of contacts for query tests."""
    creator = CreateContactStrategy(db_service=db_service)
    contacts_data = [
        ContactCreate(name="Alice Wonderland", email=f"alice.w.{uuid.uuid4()}@example.com"),
        ContactCreate(name="Bob The Builder", email=f"bob.b.{uuid.uuid4()}@example.com", phone="123-456-7890"),
        ContactCreate(name="Charlie Chaplin", email=f"charlie.c.{uuid.uuid4()}@example.com"),
        ContactCreate(name="Alicia Keys", email=f"alicia.k.{uuid.uuid4()}@example.com"), # Another Alice
    ]
    created_contacts = []
    for data in contacts_data:
        c = await creator.execute(data=data)
        created_contacts.append(c)

    yield created_contacts # Yield the created contacts

    # Optional: Teardown - delete created contacts if needed, though test DB is usually wiped
    # async with db_service.get_session() as session:
    #     for contact in created_contacts:
    #         await session.delete(contact)
    #     await session.commit()

async def test_find_contact_by_name_contains(db_service: DatabaseService, setup_contacts):
    """Test finding contacts by partial name (case-insensitive)."""
    strategy = FindContactsStrategy(db_service=db_service)
    query_dao = ContactQuery(name_contains="alic")

    # --- Act ---
    results = await strategy.execute(data=query_dao)

    # --- Assert ---
    assert results is not None
    assert len(results) == 2
    names = {c.name for c in results}
    assert "Alice Wonderland" in names
    assert "Alicia Keys" in names

async def test_find_contact_by_email(db_service: DatabaseService, setup_contacts):
    """Test finding a contact by exact email address."""
    strategy = FindContactsStrategy(db_service=db_service)
    target_email = setup_contacts[1].email # Bob The Builder's email
    query_dao = ContactQuery(email=target_email)

    # --- Act ---
    results = await strategy.execute(data=query_dao)

    # --- Assert ---
    assert results is not None
    assert len(results) == 1
    assert results[0].id == setup_contacts[1].id
    assert results[0].name == "Bob The Builder"
    assert results[0].email == target_email

async def test_find_contact_no_match(db_service: DatabaseService, setup_contacts):
    """Test finding contacts with criteria that match none."""
    strategy = FindContactsStrategy(db_service=db_service)
    query_dao = ContactQuery(name_contains="Zaphod", email="beeblebrox@example.com")

    # --- Act ---
    results = await strategy.execute(data=query_dao)

    # --- Assert ---
    assert results is not None
    assert len(results) == 0

async def test_find_contact_no_criteria(db_service: DatabaseService, setup_contacts):
    """Test finding contacts with no criteria (should return all)."""
    strategy = FindContactsStrategy(db_service=db_service)
    query_dao = ContactQuery() # Empty query

    # --- Act ---
    results = await strategy.execute(data=query_dao)

    # --- Assert ---
    assert results is not None
    # Should match the number created in the fixture
    assert len(results) == len(setup_contacts)

async def test_strategy_import():
    """Placeholder to ensure the test file structure is okay before implementation."""
    from schedule_core.db.strategies.contact_find import FindContactsStrategy # noqa
    assert True 