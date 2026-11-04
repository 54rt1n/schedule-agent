import uuid
from datetime import datetime, timezone, UTC # Correct import for UTC

import pytest
from pydantic import ValidationError
from schedule_core.db.models.contact import Contact # Import the actual model


def test_import_contact_model():
    """Tests if the Contact model can be imported."""
    # Test passes if import above worked and this function is reached
    assert True


def test_contact_model_creation_minimal():
    """Tests creating a Contact instance with minimal required fields."""
    # Use naive UTC timestamps for comparison, matching the planned implementation
    # Let the model handle defaults
    before_creation = datetime.now(UTC).replace(tzinfo=None)
    contact = Contact(name="Test User")
    after_creation = datetime.now(UTC).replace(tzinfo=None)

    # Validate UUID generation (assuming default_factory works)
    assert isinstance(contact.id, uuid.UUID)
    assert contact.name == "Test User"
    assert contact.email is None
    assert contact.phone is None
    assert contact.notes is None
    assert isinstance(contact.created_at, datetime)
    assert isinstance(contact.updated_at, datetime)
    # Check if default factory was used (approximate check, against naive time)
    assert contact.created_at.tzinfo is None # Should be naive
    assert contact.updated_at.tzinfo is None # Should be naive
    assert before_creation <= contact.created_at <= after_creation
    assert before_creation <= contact.updated_at <= after_creation


def test_contact_model_creation_all_fields():
    """Tests creating a Contact instance with all fields populated."""
    now_naive = datetime.now(UTC).replace(tzinfo=None)
    contact_id = uuid.uuid4()
    # Manually set timestamps for direct comparison
    contact = Contact(
        id=contact_id,
        name="Test User Full",
        email="test@example.com",
        phone="123-456-7890",
        notes="Some notes here.",
        created_at=now_naive,
        updated_at=now_naive
    )
    assert contact.id == contact_id
    assert contact.name == "Test User Full"
    assert contact.email == "test@example.com"
    assert contact.phone == "123-456-7890"
    assert contact.notes == "Some notes here."
    assert contact.created_at == now_naive
    assert contact.updated_at == now_naive


def test_contact_model_missing_required_fields():
    """Tests that validating data without required fields raises an error."""
    with pytest.raises(ValidationError):
        # Use model_validate with data missing 'name'
        Contact.model_validate({"email": "test@example.com"})



def test_contact_model_timestamps_defaults():
    """Tests the default factories for timestamp fields."""
    # Use naive UTC timestamps for comparison, matching the planned implementation
    before_creation = datetime.now(UTC).replace(tzinfo=None)
    # Create contact without specifying timestamps to test defaults
    contact = Contact(name="Timestamp Test")
    after_creation = datetime.now(UTC).replace(tzinfo=None)

    # These assertions depend on the actual Contact model being available
    assert isinstance(contact.created_at, datetime)
    assert isinstance(contact.updated_at, datetime)
    # Timestamps should be naive UTC as per plan
    assert contact.created_at.tzinfo is None
    assert contact.updated_at.tzinfo is None

    # Check if timestamps are within the expected range
    # Allow a small buffer for execution time
    assert before_creation <= contact.created_at <= after_creation
    assert before_creation <= contact.updated_at <= after_creation
    # For a new object, created_at and updated_at might be very close
    assert abs((contact.updated_at - contact.created_at).total_seconds()) < 2 # Increased buffer slightly

# Note: Testing Field properties like index=True, unique=True, and onupdate
# directly in unit tests is difficult with SQLModel alone.
# These are primarily database-level concerns verified via:
# 1. Alembic migration script review.
# 2. Integration tests that interact with the actual database. 