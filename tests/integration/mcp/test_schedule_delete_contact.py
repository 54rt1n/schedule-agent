# tests/integration/mcp/test_schedule_delete_contact.py

import uuid
import pytest
import pytest_asyncio

# Function to test and setup helper
from sa.mcp.handlers import schedule_delete_contact, schedule_create_contact

# Dependencies and DB access
from sa.db.service import DatabaseService
from sa.db.models import Contact
from sa.core.exceptions import ContactNotFoundError # For checking exceptions if needed

# --- Fixtures ---

@pytest_asyncio.fixture(scope="function")
async def contact_to_delete(mcp_test_db_service: DatabaseService) -> Contact:
    """Fixture to create a contact to be deleted in tests."""
    params = {
        "name": "Delete Test Contact",
        "email": "delete.test@example.com",
    }
    result = await schedule_create_contact(**params, db_service=mcp_test_db_service)
    assert result.get("success") is True, "Fixture failed to create contact for deletion"
    contact_id_str = result["contact_id"]
    contact_id_uuid = uuid.UUID(contact_id_str) # Convert to UUID
    async with mcp_test_db_service.get_session() as session:
        contact = await session.get(Contact, contact_id_uuid) # Use UUID
        assert contact is not None
        return contact

# --- Test Cases ---

@pytest.mark.asyncio
async def test_schedule_delete_contact_success(
    mcp_test_db_service: DatabaseService,
    contact_to_delete: Contact,
):
    """Test successful deletion of an existing contact."""
    contact_id_str = str(contact_to_delete.id)
    contact_id_uuid = contact_to_delete.id # Keep UUID form for DB check

    # Pre-check: Ensure contact exists
    async with mcp_test_db_service.get_session() as session:
        pre_check_contact = await session.get(Contact, contact_id_uuid) # Use UUID
        assert pre_check_contact is not None

    # Action
    result = await schedule_delete_contact(
        db_service=mcp_test_db_service,
        contact_id=contact_id_str,
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("contact_id") == contact_id_str # Handler returns string

    # Assert Database State
    async with mcp_test_db_service.get_session() as session:
        post_check_contact = await session.get(Contact, contact_id_uuid) # Use UUID for DB check
        assert post_check_contact is None

@pytest.mark.asyncio
async def test_schedule_delete_contact_not_found(
    mcp_test_db_service: DatabaseService,
):
    """Test attempting to delete a non-existent contact."""
    non_existent_id = str(uuid.uuid4())

    # Action
    result = await schedule_delete_contact(
        db_service=mcp_test_db_service,
        contact_id=non_existent_id,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "not found" in result["error"].lower()
    assert non_existent_id in result["error"]

@pytest.mark.asyncio
async def test_schedule_delete_contact_invalid_id_format(
    mcp_test_db_service: DatabaseService,
):
    """Test deleting with an invalid UUID format for contact_id."""
    invalid_id = "not-a-uuid"

    result = await schedule_delete_contact(
        db_service=mcp_test_db_service,
        contact_id=invalid_id,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "invalid contact_id format" in result["error"].lower() 