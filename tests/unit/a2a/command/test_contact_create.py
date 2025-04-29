import pytest
from unittest.mock import AsyncMock, patch, ANY, MagicMock
import uuid
from datetime import datetime, UTC

from sa.a2a.dto import (
    Artifact,
    Message,
    Part,
    Task,
    TaskState,
    TaskStatus,
)
from sa.a2a.exceptions import InternalError, InvalidParamsError
from sa.a2a.command.structured_contact_create import handle_structured_create_contact
from sa.core.exceptions import DatabaseError
from sa.db.dao import ContactCreate
from sa.db.models import Contact
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def valid_create_contact_input() -> dict:
    """Provides valid input data for creating a contact."""
    return {
        "name": "Test User",
        "email": f"test.user.{uuid.uuid4()}@example.com", # Ensure unique email
        "phone": "123-456-7890",
        "notes": "A test note."
    }

@pytest.fixture
def created_db_contact(valid_create_contact_input: dict) -> Contact:
    """Provides a sample Contact object as returned by CreateContactStrategy.execute."""
    return Contact(
        id=uuid.uuid4(),
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
        **valid_create_contact_input
    )

@pytest.fixture
def invalid_contact_input() -> dict:
    """Provides input data with missing required field (name)."""
    return {
        "email": "missing.name@example.com",
        "phone": "987-654-3210",
    }


# Mock return task from TaskManager for success
mock_completed_task = Task(
    id="mock_task_id_completed",
    status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
    artifacts=[Artifact(name="created_contact", parts=[Part(type="data", data={})])]
)
# Mock return task from TaskManager for failure
mock_failed_task = Task(
    id="mock_task_id_failed",
    status=TaskStatus(state=TaskState.FAILED, message=Message(role="agent", parts=[])),
)

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_create.CreateContactStrategy") # Patch the strategy within the module where the handler *will* be
async def test_handle_structured_create_contact_success(
    mock_strategy_class: AsyncMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_create_contact_input: dict,
    created_db_contact: Contact,
):
    """Tests successful contact creation via structured input."""
    task_id = "test_contact_create_success"

    # Mock the strategy instance and its execute method
    mock_strategy_instance = AsyncMock()
    mock_strategy_instance.execute.return_value = created_db_contact
    mock_strategy_class.return_value = mock_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Call the handler
    result_task = await handle_structured_create_contact(
        task_id=task_id,
        payload=valid_create_contact_input,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assert Strategy call
    mock_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_strategy_instance.execute.assert_awaited_once()
    call_args, call_kwargs = mock_strategy_instance.execute.await_args
    passed_dao = call_kwargs['data']
    assert isinstance(passed_dao, ContactCreate)
    assert passed_dao.name == valid_create_contact_input['name']
    assert passed_dao.email == valid_create_contact_input['email']

    # Assert TaskManager call
    mock_task_manager.complete_task.assert_awaited_once()
    complete_call_args, _ = mock_task_manager.complete_task.await_args
    assert complete_call_args[0] == task_id # task_id
    assert isinstance(complete_call_args[1], Message) # agent_message
    assert f"Contact '{created_db_contact.name}' created successfully" in complete_call_args[1].parts[0].text
    assert isinstance(complete_call_args[2], Artifact) # artifact
    assert complete_call_args[2].name == "created_contact"
    assert complete_call_args[2].parts[0].type == "data"
    assert complete_call_args[2].parts[0].data["id"] == str(created_db_contact.id)
    assert complete_call_args[2].parts[0].data["name"] == created_db_contact.name

    # Assert overall result
    assert result_task is mock_completed_task
    # Ensure fail_task was NOT called in success case
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()


@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_create.CreateContactStrategy") # Patch strategy
async def test_handle_structured_create_contact_validation_error(
    mock_strategy_class: AsyncMock, # Include mock even if not called to satisfy patch
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    invalid_contact_input: dict,
):
    """Tests handler raising InvalidParamsError for invalid payload."""
    task_id = "test_contact_validation_error"

    # No need to mock strategy execute as validation fails first
    mock_strategy_instance = AsyncMock()
    mock_strategy_class.return_value = mock_strategy_instance

    # Call the handler and expect InvalidParamsError
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_create_contact(
            task_id=task_id,
            payload=invalid_contact_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "Invalid data provided for contact creation" in str(exc_info.value)
    assert isinstance(exc_info.value.data, list) # Pydantic validation errors
    assert isinstance(exc_info.value.__cause__, ValidationError)
    # Ensure strategy was NOT called
    mock_strategy_instance.execute.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()


@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_create.CreateContactStrategy")
async def test_handle_structured_create_contact_missing_payload(
    mock_strategy_class: AsyncMock, # Patch to check it's not called
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Tests handler raising InvalidParamsError when payload is None."""
    task_id = "test_contact_missing_payload"

    # Call the handler and expect InvalidParamsError because payload is None
    with pytest.raises(InvalidParamsError) as exc_info:
        await handle_structured_create_contact(
            task_id=task_id,
            payload=None, # Explicitly pass None
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "Payload is required for structured contact creation." in str(exc_info.value)
    # Ensure strategy was NOT called
    mock_strategy_class.assert_not_called()
    # Ensure TaskManager methods were NOT called
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()


@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_create.CreateContactStrategy")
async def test_handle_structured_create_contact_conflict_error(
    mock_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_create_contact_input: dict,
):
    """Tests handler failing the task when CreateContactStrategy raises IntegrityError for email."""
    task_id = "test_contact_conflict_error"
    conflict_email = valid_create_contact_input['email']
    # Simulate the original DB error message containing relevant keywords
    # Need to mock the .orig attribute structure if the handler checks it
    mock_db_error = MagicMock(spec=IntegrityError)
    mock_db_error.orig = MagicMock()
    mock_db_error.orig.__str__.return_value = "unique constraint failed: contact.email"
    # Create the actual IntegrityError instance to raise
    integrity_exception = IntegrityError(statement="mock stmt", params={}, orig=mock_db_error.orig)


    # Mock the strategy instance and its execute method to raise IntegrityError
    mock_strategy_instance = AsyncMock()
    mock_strategy_instance.execute.side_effect = integrity_exception
    mock_strategy_class.return_value = mock_strategy_instance

    # Mock TaskManager fail_task response
    mock_task_manager.fail_task.return_value = mock_failed_task

    # Call the handler
    result_task = await handle_structured_create_contact(
        task_id=task_id,
        payload=valid_create_contact_input,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assert Strategy call
    mock_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_strategy_instance.execute.assert_awaited_once() # Strategy execute *was* called

    # Assert TaskManager fail_task call
    mock_task_manager.fail_task.assert_awaited_once()
    fail_call_args, _ = mock_task_manager.fail_task.await_args
    assert fail_call_args[0] == task_id # task_id
    assert isinstance(fail_call_args[1], Message) # agent_message
    # Check the specific failure message for duplicate email
    assert f"Failed to create contact: Email '{conflict_email}' already exists." in fail_call_args[1].parts[0].text

    # Assert overall result
    assert result_task is mock_failed_task
    mock_task_manager.complete_task.assert_not_called()
    # Check if require_input_task exists and was not called (if applicable)
    if hasattr(mock_task_manager, 'require_input_task'):
        mock_task_manager.require_input_task.assert_not_called()


@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_create.CreateContactStrategy")
async def test_handle_structured_create_contact_database_error(
    mock_strategy_class: AsyncMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_create_contact_input: dict,
):
    """Tests handler raising InternalError when CreateContactStrategy raises DatabaseError."""
    task_id = "test_contact_db_error"
    db_exception = DatabaseError(message="Simulated DB connection lost")

    # Mock the strategy instance to raise DatabaseError
    mock_strategy_instance = AsyncMock()
    mock_strategy_instance.execute.side_effect = db_exception
    mock_strategy_class.return_value = mock_strategy_instance

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_create_contact(
            task_id=task_id,
            payload=valid_create_contact_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected error occurred during contact creation." in str(exc_info.value)
    assert exc_info.value.__cause__ is db_exception
    # Ensure strategy was called
    mock_strategy_instance.execute.assert_awaited_once()
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.require_input_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_create.CreateContactStrategy")
async def test_handle_structured_create_contact_unexpected_error_in_completion(
    mock_strategy_class: AsyncMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_create_contact_input: dict,
    created_db_contact: Contact,
):
    """Tests handler raising InternalError when TaskManager.complete_task fails."""
    task_id = "test_contact_completion_error"
    completion_exception = Exception("Failed to update task status in manager")

    # Mock the strategy instance to succeed
    mock_strategy_instance = AsyncMock()
    mock_strategy_instance.execute.return_value = created_db_contact
    mock_strategy_class.return_value = mock_strategy_instance

    # Mock TaskManager complete_task to raise an error
    mock_task_manager.complete_task.side_effect = completion_exception

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_create_contact(
            task_id=task_id,
            payload=valid_create_contact_input,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected error occurred after successfully creating the contact." in str(exc_info.value)
    assert exc_info.value.__cause__ is completion_exception
    # Ensure strategy was called
    mock_strategy_instance.execute.assert_awaited_once()
    # Ensure complete_task was called (even though it failed)
    mock_task_manager.complete_task.assert_awaited_once()
    # Ensure fail_task *was* called as a fallback
    mock_task_manager.fail_task.assert_awaited_once()
    mock_task_manager.require_input_task.assert_not_called() 