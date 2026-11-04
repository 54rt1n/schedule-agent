# tests/integration/mcp/test_schedule_delete_contact.py

import uuid
import pytest
import pytest_asyncio

# Function to test and setup helper

# Dependencies and DB access
from schedule_core.db.service import DatabaseService
from schedule_core.db.models import Contact
from schedule_core.core.exceptions import ContactNotFoundError # For checking exceptions if needed

# --- Fixtures ---

@pytest_asyncio.fixture(scope="function")
async def contact_to_delete(mcp_call, mcp_test_db_service: DatabaseService) -> Contact:
    """Fixture to create a contact to be deleted in tests."""
    params = {
        "name": "Delete Test Contact",
        "email": "delete.test@example.com",
    }
    result = await mcp_call('create_contact', {**params})
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
    mcp_call, mcp_test_db_service: DatabaseService,
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
    result = await mcp_call('delete_contact', {'id': contact_id_str})

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
    mcp_call, mcp_test_db_service: DatabaseService,
):
    """Test attempting to delete a non-existent contact."""
    non_existent_id = str(uuid.uuid4())

    # Action
    result = await mcp_call('delete_contact', {'id': non_existent_id})

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "not found" in result["error"].lower()
    assert non_existent_id in result["error"]

@pytest.mark.asyncio
async def test_schedule_delete_contact_invalid_id_format(
    mcp_call, mcp_test_db_service: DatabaseService,
):
    """Test deleting with an invalid UUID format for contact_id."""
    invalid_id = "not-a-uuid"

    result = await mcp_call('delete_contact', {'id': invalid_id})

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert result["problem"]["code"] == "validation"
    assert result["problem"]["issues"][0]["path"] == "id"
