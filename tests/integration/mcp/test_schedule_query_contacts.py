# tests/integration/mcp/test_schedule_query_contacts.py

import pytest
import pytest_asyncio
import uuid

# Function to test
from sa.mcp.handlers import schedule_query_contacts, schedule_create_contact

# Dependencies
from sa.db.service import DatabaseService
from sa.db.models import Contact # For type hinting fixture

# --- Fixtures ---

@pytest_asyncio.fixture(scope="function")
async def contact_for_query(mcp_test_db_service: DatabaseService) -> Contact:
    """Fixture to create a contact to be used in query tests."""
    params = {
        "name": "Query Test Contact",
        "email": "query.test@example.com",
        "phone": "555-1234",
    }
    result = await schedule_create_contact(**params, db_service=mcp_test_db_service)
    assert result.get("success") is True, "Fixture failed to create contact"
    contact_id_str = result["contact_id"]
    contact_id_uuid = uuid.UUID(contact_id_str) # Convert to UUID for DB interaction
    async with mcp_test_db_service.get_session() as session:
        contact = await session.get(Contact, contact_id_uuid) # Use UUID here
        assert contact is not None
        return contact

# --- Test Cases ---

@pytest.mark.asyncio
async def test_schedule_query_contacts_success_no_results(
    mcp_test_db_service: DatabaseService,
):
    """Test querying contacts when no contacts exist or match filters."""
    params = {
        "name_contains": "NonExistentName",
        "email_contains": None,
    }
    result = await schedule_query_contacts(**params, db_service=mcp_test_db_service)

    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 0
    assert result.get("contacts") == []

@pytest.mark.asyncio
async def test_schedule_query_contacts_by_name(
    mcp_test_db_service: DatabaseService,
    contact_for_query: Contact,
):
    """Test querying contacts filtering by name."""
    params = {
        "name_contains": "Query Test", # Partial match
        "email_contains": None,
    }
    result = await schedule_query_contacts(**params, db_service=mcp_test_db_service)

    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 1
    assert len(result.get("contacts", [])) == 1
    returned_contact = result["contacts"][0]
    assert returned_contact["id"] == str(contact_for_query.id)
    assert returned_contact["name"] == contact_for_query.name

@pytest.mark.asyncio
async def test_schedule_query_contacts_by_email(
    mcp_test_db_service: DatabaseService,
    contact_for_query: Contact,
):
    """Test querying contacts filtering by email."""
    params = {
        "name_contains": None,
        "email_contains": "query.test@example", # Partial match
    }
    result = await schedule_query_contacts(**params, db_service=mcp_test_db_service)

    assert result is not None
    assert result.get("success") is True
    assert result.get("count") == 1
    assert len(result.get("contacts", [])) == 1
    returned_contact = result["contacts"][0]
    assert returned_contact["id"] == str(contact_for_query.id)
    assert returned_contact["email"] == contact_for_query.email

@pytest.mark.asyncio
async def test_schedule_query_contacts_no_filters(
    mcp_test_db_service: DatabaseService,
    contact_for_query: Contact, # Ensure at least one contact exists
):
    """Test querying contacts with no filters (should return all)."""
    params = {
        "name_contains": None,
        "email_contains": None,
    }
    result = await schedule_query_contacts(**params, db_service=mcp_test_db_service)

    assert result is not None
    assert result.get("success") is True
    assert result.get("count") >= 1 # Should find at least the fixture contact
    assert len(result.get("contacts", [])) >= 1
    # Check if the fixture contact is in the results
    assert any(c["id"] == str(contact_for_query.id) for c in result["contacts"])

# Potential TODO: Test case sensitivity if applicable based on DB/strategy implementation
# Potential TODO: Test with multiple contacts existing 