# tests/integration/mcp/test_schedule_create_contact.py

import uuid
import pytest
from sqlalchemy.exc import IntegrityError # To check for specific exceptions if needed

# Function to test
from sa.mcp.handlers import schedule_create_contact

# Dependencies and DB access
from sa.db.service import DatabaseService
from sa.db.models import Contact
from sa.db.dao import ContactCreate # For potential direct strategy use

@pytest.mark.asyncio
async def test_schedule_create_contact_success(
    mcp_test_db_service: DatabaseService,
):
    """Test successful creation of a new contact via schedule_create_contact."""
    params = {
        "name": "MCP Test Contact",
        "email": "mcp.test@example.com",
        "phone": "123-456-7890",
        "notes": "Integration test via MCP handler for contact creation.",
    }

    result = await schedule_create_contact(
        **params,
        db_service=mcp_test_db_service,
    )

    # Assert Handler Response
    assert result is not None
    assert result.get("success") is True
    assert "contact_id" in result
    contact_id_str = result["contact_id"]
    assert isinstance(uuid.UUID(contact_id_str), uuid.UUID) # Validate UUID format
    assert "contact" in result
    created_contact_dict = result["contact"]
    assert created_contact_dict["name"] == params["name"]
    assert created_contact_dict["email"] == params["email"]
    assert created_contact_dict["phone"] == params["phone"]
    assert created_contact_dict["notes"] == params["notes"]

    # Assert Database State
    contact_id = uuid.UUID(contact_id_str)
    async with mcp_test_db_service.get_session() as session:
        db_contact = await session.get(Contact, contact_id)
        assert db_contact is not None
        assert db_contact.id == contact_id
        assert db_contact.name == params["name"]
        assert db_contact.email == params["email"]
        assert db_contact.phone == params["phone"]
        assert db_contact.notes == params["notes"]

@pytest.mark.asyncio
async def test_schedule_create_contact_duplicate_email(
    mcp_test_db_service: DatabaseService,
):
    """Test creating a contact with an email that already exists."""
    # First, create a contact
    initial_params = {
        "name": "Original MCP Contact",
        "email": "duplicate.mcp@example.com",
    }
    initial_result = await schedule_create_contact(
        **initial_params,
        db_service=mcp_test_db_service,
    )
    assert initial_result.get("success") is True, "Setup failed: could not create initial contact"

    # Attempt to create another contact with the same email
    duplicate_params = {
        "name": "Duplicate MCP Contact",
        "email": "duplicate.mcp@example.com", # Same email
    }
    result = await schedule_create_contact(
        **duplicate_params,
        db_service=mcp_test_db_service,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "email" in result["error"].lower()
    assert "exists" in result["error"].lower()

@pytest.mark.asyncio
async def test_schedule_create_contact_invalid_data(
    mcp_test_db_service: DatabaseService,
):
    """Test creating a contact with invalid data (e.g., missing name)."""
    # Pydantic validation happens within the handler now.
    # Pass invalid data structure (missing required name) to trigger handler's validation.
    invalid_params = {
        # "name": None, # Explicitly missing or None
        "email": "invalid.data@example.com",
    }

    # The handler catches the ValidationError and returns a failure dict
    # We need to use try-except here if the handler itself raises the ValidationError
    # But based on the handler code, it catches and returns a dict.
    result = await schedule_create_contact(
        name=invalid_params.get("name"), # Pass None for name
        email=invalid_params.get("email"),
        db_service=mcp_test_db_service,
    )

    # Assert Handler Response (failure expected)
    assert result is not None
    assert result.get("success") is False
    assert "error" in result
    assert "invalid input data" in result["error"].lower()
    # Check for Pydantic error details for the 'name' field
    assert "name" in result["error"] # Check if the field name is mentioned
    assert "string_type" in result["error"].lower() # Check for the type error Pydantic gives for None 