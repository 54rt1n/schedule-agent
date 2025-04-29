# tests/unit/a2a/command/test_contact_query.py
"""Unit tests for the structured contact query handler."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import uuid
import json
from datetime import datetime, UTC
from pydantic import ValidationError
from typing import Any
from sa.core.exceptions import DatabaseError

# Import necessary types and handlers
from sa.a2a.command.structured_contact_query import handle_structured_query_contact
from sa.a2a.dto import (
    Task,
    TaskState,
    TaskStatus,
    Message,
    Part,
)
from sa.db.models import Contact
from sa.db.dao import ContactQuery
# Import the strategy to be patched
from sa.db.strategies.contact_find import FindContactsStrategy
from sa.a2a.exceptions import InvalidParamsError, InternalError

# --- Fixtures --- #

@pytest.fixture
def mock_contact_list() -> list[Contact]:
    """Provides a list of mock Contact objects for strategy return."""
    return [
        Contact(
            id=uuid.uuid4(),
            name="Test Alice",
            email="alice@example.com",
            phone="111-222-3333",
            notes="Alice note",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        ),
        Contact(
            id=uuid.uuid4(),
            name="Test Bob",
            email="bob@example.com",
            phone="444-555-6666",
            notes="Bob note",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        ),
    ]

@pytest.fixture
def valid_query_payload() -> dict[str, str]:
    """Provides a valid dictionary payload for querying contacts."""
    # Example: query by partial name
    return {"name_contains": "Test"}

@pytest.fixture
def invalid_query_payload() -> dict[str, Any]:
    """Provides an invalid query payload (e.g., wrong type)."""
    # ContactQuery expects name_contains to be str, not int
    return {"name_contains": 123}

@pytest.fixture
def mock_found_contacts(valid_query_payload: dict) -> list[Contact]:
    return [
        Contact(
            id=uuid.uuid4(),
            name="Test Alice",
            email="alice@example.com",
            phone="111-222-3333",
            notes="Alice note",
            created_at=datetime.now(UTC).replace(tzinfo=None),
            updated_at=datetime.now(UTC).replace(tzinfo=None),
        ),
    ]

@pytest.fixture
def mock_completed_task() -> Task:
    # Mock return task from TaskManager
    return Task(
        id="mock_task_id_query_completed",
        status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
    )

# --- Test Cases --- #

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_query.FindContactsStrategy")
async def test_handle_structured_query_contact_success(
    mock_find_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_query_payload: dict,
    mock_contact_list: list[Contact],
):
    """Test the contact query handler successfully finds contacts."""
    task_id = "test_query_contact_success"

    # Mock Strategy response
    mock_find_strategy_instance = AsyncMock()
    mock_find_strategy_instance.execute.return_value = mock_contact_list
    mock_find_strategy_class.return_value = mock_find_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Expected DAO based on payload
    expected_query_dao = ContactQuery.model_validate(valid_query_payload)

    # Call the handler
    final_task = await handle_structured_query_contact(
        task_id=task_id,
        payload=valid_query_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_completed_task

    # Check Strategy call
    mock_find_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_find_strategy_instance.execute.assert_awaited_once_with(data=expected_query_dao)

    # Check TaskManager call
    expected_results_list = [
        {
            "id": str(contact.id),
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "notes": contact.notes,
            "created_at": contact.created_at.isoformat(),
            "updated_at": contact.updated_at.isoformat(),
        }
        for contact in mock_contact_list
    ]
    expected_result_json = json.dumps(expected_results_list)

    mock_task_manager.complete_task.assert_awaited_once_with(
        task_id,
        Message(role="agent", parts=[Part(type="text", text=expected_result_json)])
    )
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_query.FindContactsStrategy")
async def test_handle_structured_query_contact_no_results(
    mock_find_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_query_payload: dict, # Use valid payload, but mock returns empty
):
    """Test querying when the strategy returns no matching contacts."""
    task_id = "test_query_contact_no_results"

    # Mock Strategy response to return empty list
    mock_find_strategy_instance = AsyncMock()
    mock_find_strategy_instance.execute.return_value = [] # Empty list
    mock_find_strategy_class.return_value = mock_find_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Expected DAO based on payload
    expected_query_dao = ContactQuery.model_validate(valid_query_payload)

    # Call the handler
    final_task = await handle_structured_query_contact(
        task_id=task_id,
        payload=valid_query_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_completed_task

    # Check Strategy call
    mock_find_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_find_strategy_instance.execute.assert_awaited_once_with(data=expected_query_dao)

    # Check TaskManager call with empty JSON list
    expected_result_json = json.dumps([]) # Should be "[]"

    mock_task_manager.complete_task.assert_awaited_once_with(
        task_id,
        Message(role="agent", parts=[Part(type="text", text=expected_result_json)])
    )
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_query.FindContactsStrategy")
async def test_handle_structured_query_contact_empty_payload(
    mock_find_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    # mock_contact_list: list[Contact], # Mock strategy to return empty for simplicity
):
    """Test querying with an empty (None) payload, expecting empty ContactQuery."""
    task_id = "test_query_contact_empty_payload"

    # Mock Strategy response (returning empty list for this specific test)
    mock_find_strategy_instance = AsyncMock()
    mock_find_strategy_instance.execute.return_value = []
    mock_find_strategy_class.return_value = mock_find_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Expected DAO when payload is None/empty
    expected_query_dao = ContactQuery() # Empty DAO

    # Call the handler with payload=None
    final_task = await handle_structured_query_contact(
        task_id=task_id,
        payload=None,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
    )

    # Assertions
    assert final_task is mock_completed_task

    # Check Strategy call with empty DAO
    mock_find_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_find_strategy_instance.execute.assert_awaited_once_with(data=expected_query_dao)

    # Check TaskManager call (expecting empty list based on mock return)
    expected_result_json = json.dumps([])

    mock_task_manager.complete_task.assert_awaited_once_with(
        task_id,
        Message(role="agent", parts=[Part(type="text", text=expected_result_json)])
    )
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
async def test_handle_structured_query_contact_invalid_payload(
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Test query handler raises InvalidParamsError for bad payload."""
    task_id = "test_query_contact_invalid_payload"
    # Example invalid payload (e.g., wrong type for a field)
    invalid_payload = {"name_contains": 123}

    # Expect InvalidParamsError to be raised during DAO validation
    with pytest.raises(InvalidParamsError) as exc_info:
         await handle_structured_query_contact(
             task_id=task_id,
             payload=invalid_payload,
             task_manager=mock_task_manager,
             db_service=mock_db_service,
         )

    # Check the exception details
    assert "Invalid query parameters" in str(exc_info.value)
    # Check the error data contains info about the validation error
    assert "name_contains" in str(exc_info.value.data).lower()
    assert "string_type" in str(exc_info.value.data).lower()

    # Ensure these are indented correctly (same level as the assert lines above)
    mock_task_manager.fail_task.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_query.FindContactsStrategy")
async def test_handle_structured_query_contact_database_error(
    mock_find_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_query_payload: dict,
):
    """Test handler raising InternalError when strategy raises DatabaseError."""
    task_id = "test_query_contact_db_error"
    db_error = DatabaseError("Simulated DB connection failed")

    # Mock Strategy response to raise DatabaseError
    mock_find_strategy_instance = AsyncMock()
    mock_find_strategy_instance.execute.side_effect = db_error
    mock_find_strategy_class.return_value = mock_find_strategy_instance

    # Expected DAO based on payload
    expected_query_dao = ContactQuery.model_validate(valid_query_payload)

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_query_contact(
            task_id=task_id,
            payload=valid_query_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected error occurred during contact query processing" in str(exc_info.value)
    assert exc_info.value.__cause__ is db_error

    # Check Strategy was called
    mock_find_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_find_strategy_instance.execute.assert_awaited_once_with(data=expected_query_dao)

    # Ensure TaskManager methods were NOT called
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_contact_query.FindContactsStrategy")
async def test_handle_structured_query_contact_completion_error(
    mock_find_strategy_class: MagicMock,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    valid_query_payload: dict,
    mock_contact_list: list[Contact],
):
    """Test handler raising InternalError when TaskManager.complete_task fails."""
    task_id = "test_query_contact_completion_error"
    completion_error = Exception("Simulated TaskManager failure")

    # Mock Strategy response to succeed
    mock_find_strategy_instance = AsyncMock()
    mock_find_strategy_instance.execute.return_value = mock_contact_list
    mock_find_strategy_class.return_value = mock_find_strategy_instance

    # Mock TaskManager response to raise error
    mock_task_manager.complete_task.side_effect = completion_error

    # Expected DAO based on payload
    expected_query_dao = ContactQuery.model_validate(valid_query_payload)

    # Call the handler and expect InternalError
    with pytest.raises(InternalError) as exc_info:
        await handle_structured_query_contact(
            task_id=task_id,
            payload=valid_query_payload,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
        )

    # Assertions
    assert "An unexpected error occurred during contact query processing" in str(exc_info.value)
    assert exc_info.value.__cause__ is completion_error

    # Check Strategy was called
    mock_find_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_find_strategy_instance.execute.assert_awaited_once_with(data=expected_query_dao)

    # Check TaskManager complete_task was called (even though it failed)
    mock_task_manager.complete_task.assert_awaited_once()
    mock_task_manager.fail_task.assert_not_called()

# Add more tests: completion error 