# tests/unit/a2a/command/test_contact_update.py
"""Unit tests for the structured contact update handler."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import uuid
from datetime import datetime, UTC, timedelta
from pydantic import ValidationError

# Import necessary types and handlers
from schedule_a2a.command.structured_contact_update import handle_structured_update_contact
from schedule_a2a.dto import (
    Task,
    TaskState,
    TaskStatus,
    Message,
    Part,
    Artifact,
)
from schedule_core.db.models import Contact
from schedule_core.db.dao import ContactUpdate
# Import the strategy to be patched
from schedule_core.db.strategies.contact_update import UpdateContactStrategy
from schedule_core.core.exceptions import ConflictError, DatabaseError, ContactNotFoundError
from sqlalchemy.exc import IntegrityError
from schedule_a2a.exceptions import InvalidParamsError, InternalError

# --- Fixtures --- #

@pytest.fixture
def contact_id() -> uuid.UUID:
    return uuid.uuid4()

@pytest.fixture
def valid_update_payload(contact_id: uuid.UUID) -> dict:
    """Provides a valid payload dictionary for updating a contact."""
    return {
        "id": str(contact_id),
        "name": "Updated Name",
        "phone": "555-1212",
        "notes": "Updated notes."
    }

@pytest.fixture
def mock_updated_contact(contact_id: uuid.UUID, valid_update_payload: dict) -> Contact:
    """Provides a mock Contact object representing the state after update."""
    # Start with update data, remove id as it's not a Contact field directly in __init__
    data = {k: v for k, v in valid_update_payload.items() if k != 'id'}
    # Add potentially unchanged fields (like email in this case)
    data["email"] = "original@example.com" # Assume email wasn't updated
    return Contact(
        id=contact_id,
        created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1), # Simulate existing contact
        updated_at=datetime.now(UTC).replace(tzinfo=None),
        **data
    )


# Mock return tasks from TaskManager
mock_completed_task = Task(
    id="mock_task_id_update_completed",
    status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
    artifacts=[Artifact(name="updated_contact_details", parts=[Part(type="data", data={})])]
)
mock_failed_task = Task(
    id="mock_task_id_update_failed",
    status=TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[])),
)

# --- Test Cases --- #

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_contact_update.UpdateContactStrategy")
async def test_handle_structured_update_contact_success(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_update_payload: dict,
    mock_updated_contact: Contact,
):
    """Test the contact update handler successfully updates a contact."""
    task_id = "test_update_contact_success"

    # Mock Strategy response
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.return_value = mock_updated_contact
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Prepare expected DAO (excluding the id)
    expected_update_dao = ContactUpdate.model_validate({k:v for k,v in valid_update_payload.items() if k != 'id'})

    # Call the handler
    final_task = await handle_structured_update_contact(
        task_id=task_id,
        payload=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_completed_task

    # Check Strategy call
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service)
    # Strategy expects a tuple: (contact_id, update_dao)
    mock_update_strategy_instance.execute.assert_awaited_once_with(data=(contact_id, expected_update_dao))

    # Check TaskManager call
    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert f"Contact '{mock_updated_contact.name}' (ID: {contact_id}) updated successfully." in complete_call_args[1].parts[0].text
    assert isinstance(complete_call_args[2], Artifact)
    assert complete_call_args[2].name == "updated_contact_details"
    assert complete_call_args[2].parts[0].type == "data"
    # Verify artifact data matches mock updated contact
    artifact_data = complete_call_args[2].parts[0].data
    assert artifact_data["id"] == str(mock_updated_contact.id)
    assert artifact_data["name"] == mock_updated_contact.name
    assert artifact_data["email"] == mock_updated_contact.email
    assert artifact_data["phone"] == mock_updated_contact.phone
    assert artifact_data["notes"] == mock_updated_contact.notes
    assert artifact_data["updated_at"] == mock_updated_contact.updated_at.isoformat()

    mock_task_manager.fail_task.assert_not_called()

# --- Payload Validation Tests --- #

@pytest.mark.asyncio
async def test_handle_structured_update_contact_missing_payload(
    mock_task_manager: AsyncMock, # Need mocks even if not called
    mock_db_service: AsyncMock,
):
    """Test update handler raises InvalidParamsError if payload is None."""
    task_id = "test_update_missing_payload"
    with pytest.raises(InvalidParamsError, match="Payload is required"):
        await handle_structured_update_contact(
            task_id=task_id,
            payload=None,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

@pytest.mark.asyncio
async def test_handle_structured_update_contact_missing_id(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Test update handler raises InvalidParamsError if 'id' is missing from payload."""
    task_id = "test_update_missing_id"
    payload_no_id = {"name": "Some Name"}
    with pytest.raises(InvalidParamsError, match="Contact 'id' is required"):
        await handle_structured_update_contact(
            task_id=task_id,
            payload=payload_no_id,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

@pytest.mark.asyncio
async def test_handle_structured_update_contact_invalid_id_format(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Test update handler raises InvalidParamsError if 'id' is not a valid UUID."""
    task_id = "test_update_invalid_id"
    payload_bad_id = {"id": "not-a-uuid", "name": "Some Name"}
    with pytest.raises(InvalidParamsError, match="Invalid UUID format"):
        await handle_structured_update_contact(
            task_id=task_id,
            payload=payload_bad_id,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

@pytest.mark.asyncio
async def test_handle_structured_update_contact_no_update_fields(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
):
    """Test update handler raises InvalidParamsError if only 'id' is provided."""
    task_id = "test_update_no_fields"
    payload_only_id = {"id": str(contact_id)}
    with pytest.raises(InvalidParamsError, match="No fields provided to update"):
        await handle_structured_update_contact(
            task_id=task_id,
            payload=payload_only_id,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

@pytest.mark.asyncio
async def test_handle_structured_update_contact_invalid_data(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
):
    """Test update handler raises InvalidParamsError for invalid update data (e.g., bad email)."""
    task_id = "test_update_invalid_data"
    payload_bad_data = {"id": str(contact_id), "email": "not-an-email"}
    with pytest.raises(InvalidParamsError, match="Invalid data provided for contact update") as exc_info:
        await handle_structured_update_contact(
            task_id=task_id,
            payload=payload_bad_data,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )
    assert isinstance(exc_info.value.__cause__, ValidationError)

# --- Strategy Outcome / TaskManager Interaction Tests --- #

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_contact_update.UpdateContactStrategy")
async def test_handle_structured_update_contact_not_found(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Test update handler fails task when strategy raises ContactNotFoundError."""
    task_id = "test_update_contact_not_found"
    not_found_error = ContactNotFoundError(contact_id=contact_id)

    # Mock Strategy response to raise ContactNotFoundError
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = not_found_error
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Mock TaskManager response
    mock_task_manager.fail_task.return_value = mock_failed_task

    # Prepare expected DAO (excluding the id)
    expected_update_dao = ContactUpdate.model_validate({k:v for k,v in valid_update_payload.items() if k != 'id'})

    # Call the handler
    final_task = await handle_structured_update_contact(
        task_id=task_id,
        payload=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_failed_task
    # Check Strategy call
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_update_strategy_instance.execute.assert_awaited_once_with(data=(contact_id, expected_update_dao))
    # Check TaskManager call
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id
    assert isinstance(fail_call_args[1], Message)
    # Check message matches exception string
    assert str(not_found_error) in fail_call_args[1].parts[0].text
    mock_task_manager.complete_task.assert_not_called()

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_contact_update.UpdateContactStrategy")
async def test_handle_structured_update_contact_conflict(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Test update handler fails task when strategy raises IntegrityError for email."""
    task_id = "test_update_contact_conflict"
    # Simulate the original DB error message containing relevant keywords
    mock_db_error = MagicMock(spec=IntegrityError)
    mock_db_error.orig = MagicMock()
    mock_db_error.orig.__str__.return_value = "duplicate key value violates unique constraint contact_email_key"
    # Create the actual IntegrityError instance to raise
    integrity_exception = IntegrityError(statement="mock stmt", params={}, orig=mock_db_error.orig)

    # Mock Strategy response to raise IntegrityError
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = integrity_exception
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Mock TaskManager response
    mock_task_manager.fail_task.return_value = mock_failed_task

    # Prepare expected DAO
    expected_update_dao = ContactUpdate.model_validate({k:v for k,v in valid_update_payload.items() if k != 'id'})

    # Call the handler
    final_task = await handle_structured_update_contact(
        task_id=task_id,
        payload=valid_update_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_failed_task
    # Check Strategy call
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_update_strategy_instance.execute.assert_awaited_once_with(data=(contact_id, expected_update_dao))
    # Check TaskManager call
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id
    assert isinstance(fail_call_args[1], Message)
    # Check specific message for duplicate email
    assert "Failed to update contact: Email already exists" in fail_call_args[1].parts[0].text
    mock_task_manager.complete_task.assert_not_called()

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_contact_update.UpdateContactStrategy")
async def test_handle_structured_update_contact_database_error(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_update_payload: dict,
):
    """Test update handler raises InternalError when strategy raises DatabaseError."""
    task_id = "test_update_contact_db_error"
    db_error = DatabaseError("Simulated DB connection failed during update")

    # Mock Strategy response to raise DatabaseError
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.side_effect = db_error
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Prepare expected DAO
    expected_update_dao = ContactUpdate.model_validate({k:v for k,v in valid_update_payload.items() if k != 'id'})

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_update_contact(
            task_id=task_id,
            payload=valid_update_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected database error occurred" in str(exc_info.value)
    assert exc_info.value.__cause__ is db_error
    # Check Strategy call
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_update_strategy_instance.execute.assert_awaited_once_with(data=(contact_id, expected_update_dao))
    # Ensure TaskManager methods were NOT called
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
@patch("schedule_a2a.command.structured_contact_update.UpdateContactStrategy")
async def test_handle_structured_update_contact_completion_error(
    mock_update_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_update_payload: dict,
    mock_updated_contact: Contact, # Strategy succeeds
):
    """Test update handler raises InternalError when TaskManager.complete_task fails."""
    task_id = "test_update_contact_completion_error"
    completion_error = Exception("Simulated TaskManager failure during completion")

    # Mock Strategy response to succeed
    mock_update_strategy_instance = AsyncMock()
    mock_update_strategy_instance.execute.return_value = mock_updated_contact
    mock_update_strategy_class.return_value = mock_update_strategy_instance

    # Mock TaskManager response to raise error
    mock_task_manager.complete_task.side_effect = completion_error

    # Prepare expected DAO
    expected_update_dao = ContactUpdate.model_validate({k:v for k,v in valid_update_payload.items() if k != 'id'})

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_update_contact(
            task_id=task_id,
            payload=valid_update_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected error occurred while updating contact" in str(exc_info.value)
    assert exc_info.value.__cause__ is completion_error
    # Check Strategy call
    mock_update_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_update_strategy_instance.execute.assert_awaited_once_with(data=(contact_id, expected_update_dao))
    # Ensure complete_task was called (even though it failed)
    mock_task_manager.complete_task.assert_awaited_once()
    mock_task_manager.fail_task.assert_not_called() 