# tests/integration/mcp/test_schedule_update_contact.py

import uuid
import pytest
import pytest_asyncio

# Function to test and setup helper
from sa.mcp.handlers import schedule_update_contact, schedule_create_contact

# Dependencies and DB access
from sa.db.service import DatabaseService
from sa.db.models import Contact
from sa.core.exceptions import ContactNotFoundError # For checking exceptions if needed

# --- Fixtures ---

@pytest_asyncio.fixture(scope="function")
async def contact_for_update(mcp_test_db_service: DatabaseService) -> Contact:
    """Fixture to create a contact to be updated in tests."""
    params = {
        "name": "Update Test Contact Original",
        "email": "update.test.original@example.com",
        "phone": "555-0001",
        "notes": "Original Notes",
    }
    result = await schedule_create_contact(**params, db_service=mcp_test_db_service)
    assert result.get("success") is True, "Fixture failed to create contact for update"
    contact_id_str = result["contact_id"]
    contact_id_uuid = uuid.UUID(contact_id_str) # Convert to UUID
    async with mcp_test_db_service.get_session() as session:
        contact = await session.get(Contact, contact_id_uuid) # Use UUID
        assert contact is not None
        return contact

@pytest_asyncio.fixture(scope="function")
async def another_contact(mcp_test_db_service: DatabaseService) -> Contact:
    """Fixture to create a second contact to test email conflicts."""
    params = {
        "name": "Another Contact",
        "email": "another.contact@example.com",
    }
    result = await schedule_create_contact(**params, db_service=mcp_test_db_service)
    assert result.get("success") is True, "Fixture failed to create another contact"
    contact_id_str = result["contact_id"]
    contact_id_uuid = uuid.UUID(contact_id_str) # Convert to UUID
    async with mcp_test_db_service.get_session() as session:
        contact = await session.get(Contact, contact_id_uuid) # Use UUID
        assert contact is not None
        return contact

# --- Test Cases ---

@pytest.mark.asyncio
async def test_schedule_update_contact_success(
    mcp_test_db_service: DatabaseService,
    contact_for_update: Contact,
):
    """Test successful update of an existing contact."""
    contact_id_str = str(contact_for_update.id)
    contact_id_uuid = contact_for_update.id # Keep UUID form for DB check
    update_data = {
        "name": "Updated Contact Name",
        "phone": "555-9999",
        # Intentionally omitting email and notes to ensure they are not overwritten
    }

    result = await schedule_update_contact(
        db_service=mcp_test_db_service,
        contact_id=contact_id_str,
        update_data=update_data,
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert result.get("contact_id") == contact_id_str
    assert "contact" in result
    updated_contact_dict = result["contact"]
    assert updated_contact_dict["id"] == contact_id_str
    assert updated_contact_dict["name"] == update_data["name"]
    assert updated_contact_dict["phone"] == update_data["phone"]
    # Check that unchanged fields remain the same
    assert updated_contact_dict["email"] == contact_for_update.email
    assert updated_contact_dict["notes"] == contact_for_update.notes

    # Assert Database State
    async with mcp_test_db_service.get_session() as session:
        db_contact = await session.get(Contact, contact_id_uuid) # Use UUID for DB check
        assert db_contact is not None
        assert db_contact.name == update_data["name"]
        assert db_contact.phone == update_data["phone"]
        assert db_contact.email == contact_for_update.email # Verify unchanged in DB
        assert db_contact.notes == contact_for_update.notes # Verify unchanged in DB

@pytest.mark.asyncio
async def test_schedule_update_contact_not_found(
    mcp_test_db_service: DatabaseService,
):
    """Test attempting to update a non-existent contact."""
    non_existent_id = str(uuid.uuid4())
    update_data = {"name": "Doesn't Matter"}

    result = await schedule_update_contact(
        db_service=mcp_test_db_service,
        contact_id=non_existent_id,
        update_data=update_data,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "not found" in result["error"].lower()
    assert non_existent_id in result["error"]

@pytest.mark.asyncio
async def test_schedule_update_contact_invalid_data(
    mcp_test_db_service: DatabaseService,
    contact_for_update: Contact,
):
    """Test updating a contact with invalid data (e.g., invalid email format)."""
    contact_id_str = str(contact_for_update.id)
    update_data = {
        "email": "this is not an email",
    }

    # Pydantic validation happens within the handler
    result = await schedule_update_contact(
        db_service=mcp_test_db_service,
        contact_id=contact_id_str,
        update_data=update_data,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "invalid input data" in result["error"].lower()

@pytest.mark.asyncio
async def test_schedule_update_contact_duplicate_email(
    mcp_test_db_service: DatabaseService,
    contact_for_update: Contact,
    another_contact: Contact, # Fixture with a known email
):
    """Test updating a contact's email to one that already exists."""
    contact_id_str = str(contact_for_update.id)
    update_data = {
        "email": another_contact.email, # Use the email from the other contact
    }

    result = await schedule_update_contact(
        db_service=mcp_test_db_service,
        contact_id=contact_id_str,
        update_data=update_data,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "email" in result["error"].lower()
    assert "exists" in result["error"].lower()

@pytest.mark.asyncio
async def test_schedule_update_contact_invalid_id_format(
    mcp_test_db_service: DatabaseService,
):
    """Test updating with an invalid UUID format for contact_id."""
    invalid_id = "not-a-uuid"
    update_data = {"name": "Anything"}

    result = await schedule_update_contact(
        db_service=mcp_test_db_service,
        contact_id=invalid_id,
        update_data=update_data,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "invalid contact_id format" in result["error"].lower() 