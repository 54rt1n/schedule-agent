# tests/api/rpc/test_tasks_send_structured_contact_update.py
"""Integration tests for the tasks/send RPC method with structured update_contact intent."""

import pytest
import uuid
from fastapi.testclient import TestClient

from schedule_a2a.dto import TaskSendParams, Message, Part, TaskState
from schedule_core.db.dao import ContactCreate, ContactGetById # Import needed DAOs
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.contact_create import CreateContactStrategy # Import create strategy
from schedule_core.db.strategies.contact_get_by_id import GetContactByIdStrategy # Import get strategy
from ..conftest import build_rpc_request

# Mark all tests in this module as async
pytestmark = pytest.mark.asyncio

async def _create_db_contact(
    contact_create: ContactCreate,
    app_db_service: DatabaseService
) -> ContactGetById:
    """Helper function to create a contact directly in the database."""
    create_strategy = CreateContactStrategy(db_service=app_db_service)
    created_contact = await create_strategy.execute(data=contact_create)
    # Return a DTO suitable for getting the contact, containing the ID
    return ContactGetById(id=created_contact.id)


async def test_rpc_tasks_send_structured_contact_update_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService, # Get the app's DB service
):
    """Test tasks/send with structured data for update_contact intent successfully updates contact."""
    # 1. Create a contact to update first using the DB helper
    initial_name = f"Initial Contact {uuid.uuid4().hex[:8]}"
    initial_email = f"initial.{uuid.uuid4().hex[:8]}@example.com"
    contact_to_create = ContactCreate(
        name=initial_name,
        email=initial_email,
        phone="111-555-1111",
        notes="Initial contact for update test."
    )
    get_dao = await _create_db_contact(
        contact_create=contact_to_create,
        app_db_service=app_db_service
    )
    contact_id = get_dao.id # Get the ID from the returned DAO

    # 2. Prepare Update Request
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    new_name = f"Updated Contact {uuid.uuid4().hex[:8]}"
    new_phone = "222-555-2222"

    # Prepare the nested structure expected by StructuredUpdatePayload
    contact_update_details = {
        "id": str(contact_id),
        "name": new_name,
        "phone": new_phone
        # email and notes are omitted, they shouldn't change
    }
    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "update_contact",
        "payload": contact_update_details
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_update_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Update Request
    response_update = client.post("/api/rpc", json=rpc_update_payload, headers=auth_headers_valid)

    # 4. Assertions on API Response
    assert response_update.status_code == 200
    response_update_data = response_update.json()
    assert "result" in response_update_data, f"RPC Error: {response_update_data.get('error')}"
    assert response_update_data.get("error") is None

    result_payload = response_update_data["result"]
    assert result_payload["id"] == task_id
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    # Check artifact for updated contact details
    assert len(result_payload["artifacts"]) > 0
    updated_artifact = next((a for a in result_payload["artifacts"] if a["name"] == "updated_contact_details"), None)
    assert updated_artifact is not None
    artifact_data = updated_artifact["parts"][0]["data"]
    assert artifact_data["id"] == str(contact_id)
    assert artifact_data["name"] == new_name
    assert artifact_data["phone"] == new_phone
    assert artifact_data["email"] == initial_email # Verify email didn't change

    # 5. Verify in Database
    verify_strategy = GetContactByIdStrategy(db_service=app_db_service)
    db_contact = await verify_strategy.execute(data=get_dao)

    assert db_contact is not None
    assert db_contact.id == contact_id
    assert db_contact.name == new_name
    assert db_contact.email == initial_email # Verify unchanged field
    assert db_contact.phone == new_phone # Verify changed field
    assert db_contact.notes == "Initial contact for update test." # Verify unchanged field

# TODO: Add tests for not found, validation error 