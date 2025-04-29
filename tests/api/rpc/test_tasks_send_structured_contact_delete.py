# tests/api/rpc/test_tasks_send_structured_contact_delete.py
"""Integration tests for the tasks/send RPC method with structured delete_contact intent."""

import pytest
import uuid
from fastapi.testclient import TestClient

from sa.a2a.dto import TaskSendParams, Message, Part, TaskState
from sa.db.dao import ContactCreate, ContactGetById, ContactDelete # Import needed DAOs
from sa.db.service import DatabaseService
from sa.db.strategies.contact_create import CreateContactStrategy # Import create strategy
from sa.db.strategies.contact_get_by_id import GetContactByIdStrategy # Import get strategy
from ..conftest import build_rpc_request

async def _create_db_contact(
    contact_create: ContactCreate,
    app_db_service: DatabaseService
) -> ContactGetById:
    """Helper function to create a contact directly in the database."""
    create_strategy = CreateContactStrategy(db_service=app_db_service)
    created_contact = await create_strategy.execute(data=contact_create)
    # Return a DTO suitable for getting the contact, containing the ID
    return ContactGetById(id=created_contact.id)

@pytest.mark.asyncio
async def test_rpc_tasks_send_structured_contact_delete_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService,
):
    """Test tasks/send with structured data for delete_contact intent."""
    # 1. Create a contact to delete using the helper
    contact_to_create = ContactCreate(
        name=f"ContactToDelete {uuid.uuid4().hex[:8]}",
        email=f"delete.{uuid.uuid4().hex[:8]}@example.com"
    )
    get_dao = await _create_db_contact(
        contact_create=contact_to_create,
        app_db_service=app_db_service
    )
    contact_id = get_dao.id

    # 2. Prepare Delete Request
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    delete_data = {
        "id": str(contact_id) # Match ContactDelete DAO
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "delete_contact",
        "payload": delete_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_delete_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Delete Request
    response_delete = client.post("/api/rpc", json=rpc_delete_payload, headers=auth_headers_valid)

    # 4. Assertions on API Response
    assert response_delete.status_code == 200
    response_delete_data = response_delete.json()
    assert "result" in response_delete_data, f"RPC Error: {response_delete_data.get('error')}"
    assert response_delete_data.get("error") is None

    result_payload = response_delete_data["result"]
    assert result_payload["id"] == task_id
    # Expect COMPLETED state if delete is successful
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    assert f"Successfully deleted contact {contact_id}" in result_payload["status"]["message"]["parts"][0]["text"]

    # 5. Verify Deletion in Database
    verify_strategy = GetContactByIdStrategy(db_service=app_db_service)
    # Verify by fetching the contact and asserting it's None
    deleted_contact = await verify_strategy.execute(data=get_dao)
    assert deleted_contact is None

# Test handling when the underlying command handler raises ContactNotFoundError
# This test is synchronous because it makes a direct API call and checks the result
def test_rpc_tasks_send_structured_contact_delete_not_found(
    client: TestClient,
    auth_headers_valid: dict,
):
    """Test tasks/send with contact_delete fails correctly when contact not found."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    non_existent_contact_id = str(uuid.uuid4())

    # Prepare Delete Request for non-existent contact
    delete_data = {
        "id": non_existent_contact_id
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "delete_contact",
        "payload": delete_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_delete_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # Send Delete Request
    response_delete = client.post("/api/rpc", json=rpc_delete_payload, headers=auth_headers_valid)

    # Assertions: Expect JSON-RPC error because ContactNotFoundError becomes InternalError
    assert response_delete.status_code == 200
    response_delete_data = response_delete.json()
    # Check for JSON-RPC error field, not result field
    assert "error" in response_delete_data
    assert response_delete_data.get("result") is None

    error_payload = response_delete_data["error"]
    # Check for internal error code
    assert error_payload["code"] == -32603 # INTERNAL_ERROR
    # Check that the message indicates an internal error related to deletion
    assert "error occurred during contact deletion" in error_payload["message"]

    # Verify request ID is echoed
    assert response_delete_data["id"] == rpc_delete_payload["id"]


# Test handling when the input payload fails validation within the command handler
# This test is synchronous because it makes a direct API call and checks the result
def test_rpc_tasks_send_structured_contact_delete_validation_error(
    client: TestClient,
    auth_headers_valid: dict,
):
    """Test tasks/send with contact_delete fails correctly on payload validation error."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    invalid_contact_id = "not-a-valid-uuid"

    # Prepare Delete Request with invalid ID format
    delete_data = {
        "id": invalid_contact_id
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "delete_contact",
        "payload": delete_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_delete_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # Send Delete Request
    response_delete = client.post("/api/rpc", json=rpc_delete_payload, headers=auth_headers_valid)

    # Assertions
    assert response_delete.status_code == 200 # Expect JSON-RPC error wrapped in 200 OK
    response_delete_data = response_delete.json()
    # Expect InvalidParamsError because the handler should raise it due to UUID validation
    assert "error" in response_delete_data
    assert response_delete_data.get("result") is None

    error_payload = response_delete_data["error"]
    assert error_payload["code"] == -32602 # INVALID_PARAMS
    # Check that the core message indicates invalid parameters, details may vary
    assert "Invalid delete parameters" in error_payload["message"] or "Validation error" in error_payload["message"]

    # Check that the data field contains details about the validation failure
    error_data = error_payload.get("data")
    assert error_data is not None
    assert isinstance(error_data, list)
    assert len(error_data) > 0
    # Check the specifics of the first error detail for UUID parsing failure
    first_error = error_data[0]
    assert first_error.get("type") == "uuid_parsing"
    assert first_error.get("loc") == ["id"] # Pydantic errors() uses list for loc

    # Verify request ID is echoed
    assert response_delete_data["id"] == rpc_delete_payload["id"] 