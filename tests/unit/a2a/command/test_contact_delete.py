# tests/unit/a2a/command/test_contact_delete.py
"""Unit tests for the structured contact delete handler."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import uuid
from pydantic import ValidationError

# Import necessary types and handlers
from sa.a2a.command.structured_contact_delete import handle_structured_delete_contact
from sa.a2a.dto import (
    Task,
    TaskState,
    TaskStatus,
    Message,
    Part,
)
from sa.db.dao import ContactDelete
# Import the strategy to be patched
from sa.db.strategies.contact_delete import DeleteContactStrategy
# Import ContactNotFoundError
from sa.core.exceptions import DatabaseError, ContactNotFoundError
from sa.a2a.exceptions import InvalidParamsError, InternalError

# --- Fixtures --- #

@pytest.fixture
def contact_id() -> uuid.UUID:
    return uuid.uuid4()

@pytest.fixture
def valid_delete_payload(contact_id: uuid.UUID) -> dict:
    """Provides a valid payload for deleting a contact."""
    return {"id": str(contact_id)}

# Mock return tasks from TaskManager
mock_completed_task = Task(
    id="mock_task_id_delete_completed",
    status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
)
mock_failed_task = Task(
    id="mock_task_id_delete_failed",
    status=TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[])),
)

# --- Test Cases --- #

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_delete.DeleteContactStrategy")
async def test_handle_structured_delete_contact_success(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_delete_payload: dict,
):
    """Test the contact delete handler successfully deletes a contact."""
    task_id = "test_delete_contact_success"

    # Mock Strategy response
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = True # Simulate successful deletion
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Expected DAO based on payload
    expected_delete_dao = ContactDelete.model_validate(valid_delete_payload)

    # Call the handler
    final_task = await handle_structured_delete_contact(
        task_id=task_id,
        payload=valid_delete_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_completed_task
    # Check Strategy call
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=expected_delete_dao)
    # Check TaskManager call
    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id
    assert isinstance(complete_call_args[1], Message)
    assert f"Successfully deleted contact {contact_id}." in complete_call_args[1].parts[0].text
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_delete.DeleteContactStrategy")
async def test_handle_structured_delete_contact_not_found(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_delete_payload: dict,
):
    """Test the delete handler fails task when strategy raises ContactNotFoundError."""
    task_id = "test_delete_contact_not_found"
    not_found_error = ContactNotFoundError(contact_id=contact_id)

    # Mock Strategy response to raise ContactNotFoundError
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.side_effect = not_found_error
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response
    mock_task_manager.fail_task.return_value = mock_failed_task

    # Expected DAO based on payload
    expected_delete_dao = ContactDelete.model_validate(valid_delete_payload)

    # Call the handler
    final_task = await handle_structured_delete_contact(
        task_id=task_id,
        payload=valid_delete_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_failed_task
    # Check Strategy call
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=expected_delete_dao)
    # Check TaskManager call
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id
    assert isinstance(fail_call_args[1], Message)
    # Check the failure message uses the exception string
    assert str(not_found_error) in fail_call_args[1].parts[0].text
    mock_task_manager.complete_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_structured_delete_contact_invalid_payload(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Tests delete handler raises InvalidParamsError for bad payload."""
    task_id = "test_delete_invalid_payload"
    invalid_payload = {"name": "some name"} # Missing id

    # Expect InvalidParamsError to be raised
    with pytest.raises(InvalidParamsError) as exc_info:
         await handle_structured_delete_contact(
             task_id=task_id,
             payload=invalid_payload,
             task_manager=mock_task_manager,
             db_service=mock_db_service,
         )

    # Check the exception details
    assert "Invalid delete parameters" in str(exc_info.value)
    assert "id" in str(exc_info.value.data).lower() # Check data indicates 'id' was the issue
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()

# --- Payload Validation Tests --- #

@pytest.mark.asyncio
async def test_handle_structured_delete_contact_missing_payload(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Test delete handler raises InvalidParamsError if payload is None."""
    task_id = "test_delete_missing_payload"
    with pytest.raises(InvalidParamsError, match="Payload is required"):
        await handle_structured_delete_contact(
            task_id=task_id,
            payload=None,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

# --- Error Handling Tests --- #

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_delete.DeleteContactStrategy")
async def test_handle_structured_delete_contact_database_error(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_delete_payload: dict,
):
    """Test delete handler raises InternalError when strategy raises DatabaseError."""
    task_id = "test_delete_contact_db_error"
    db_error = DatabaseError("Simulated DB connection failed during delete")

    # Mock Strategy response to raise DatabaseError
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.side_effect = db_error
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Expected DAO
    expected_delete_dao = ContactDelete.model_validate(valid_delete_payload)

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_delete_contact(
            task_id=task_id,
            payload=valid_delete_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected database error occurred" in str(exc_info.value)
    assert exc_info.value.__cause__ is db_error
    # Check Strategy call
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=expected_delete_dao)
    # Ensure TaskManager methods were NOT called
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_delete.DeleteContactStrategy")
async def test_handle_structured_delete_contact_completion_error(
    mock_delete_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    contact_id: uuid.UUID,
    valid_delete_payload: dict,
):
    """Test delete handler raises InternalError when TaskManager.complete_task fails."""
    task_id = "test_delete_contact_completion_error"
    completion_error = Exception("Simulated TaskManager failure during completion")

    # Mock Strategy response to succeed
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = True
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance

    # Mock TaskManager response to raise error
    mock_task_manager.complete_task.side_effect = completion_error

    # Expected DAO
    expected_delete_dao = ContactDelete.model_validate(valid_delete_payload)

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_delete_contact(
            task_id=task_id,
            payload=valid_delete_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected error occurred while deleting contact" in str(exc_info.value)
    assert exc_info.value.__cause__ is completion_error
    # Check Strategy call
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=expected_delete_dao)
    # Check TaskManager complete_task was called (even though it failed)
    mock_task_manager.complete_task.assert_awaited_once()
    mock_task_manager.fail_task.assert_not_called() 