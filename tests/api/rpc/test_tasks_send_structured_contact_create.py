# tests/api/rpc/test_tasks_send_structured_contact_create.py
"""Integration tests for the tasks/send RPC method with structured create_contact intent."""

import pytest
import uuid
from fastapi.testclient import TestClient

from schedule_a2a.dto import TaskSendParams, Message, Part, TaskState
from schedule_core.db.dao import ContactGetById
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.contact_get_by_id import GetContactByIdStrategy
from ..conftest import build_rpc_request

# Mark all tests in this module as async
pytestmark = pytest.mark.asyncio

async def test_rpc_tasks_send_structured_contact_create_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService, # Get the app's DB service
):
    """Test tasks/send with structured data for create_contact intent successfully creates contact."""
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    contact_name = f"Integration Test User {task_id[:8]}"
    contact_email = f"integration.test.{task_id[:8]}@example.com"

    # Structured data payload matching ContactCreate schema
    contact_create_payload = {
        "name": contact_name,
        "email": contact_email,
        "phone": "123-555-4321",
        "notes": "Created via structured RPC integration test."
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "create_contact",
        "payload": contact_create_payload
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_send_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # --- Send API Request --- #
    response_send = client.post("/api/rpc", json=rpc_send_payload, headers=auth_headers_valid)

    # --- Assertions --- #
    assert response_send.status_code == 200
    response_data = response_send.json()
    assert "result" in response_data, f"RPC Error: {response_data.get('error')}"
    assert response_data.get("error") is None

    # Validate the returned task state
    result_payload = response_data["result"]
    assert result_payload["id"] == task_id
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value
    # Check for key parts of the success message
    success_message_text = result_payload["status"]["message"]["parts"][0]["text"]
    assert "created successfully" in success_message_text
    assert contact_name in success_message_text

    # Check artifact for created contact details
    assert len(result_payload["artifacts"]) > 0
    created_artifact = next((a for a in result_payload["artifacts"] if a["name"] == "created_contact"), None)
    assert created_artifact is not None
    assert created_artifact["parts"][0]["type"] == "data"
    artifact_data = created_artifact["parts"][0]["data"]
    assert artifact_data["name"] == contact_name
    assert artifact_data["email"] == contact_email
    assert "id" in artifact_data
    created_contact_id_str = artifact_data["id"]

    # --- Verify in Database --- #
    created_contact_id = uuid.UUID(created_contact_id_str)
    get_dao = ContactGetById(id=created_contact_id)
    # Use app_db_service to create the verification strategy instance
    verify_strategy = GetContactByIdStrategy(db_service=app_db_service)
    db_contact = await verify_strategy.execute(data=get_dao)

    assert db_contact is not None
    assert db_contact.id == created_contact_id
    assert db_contact.name == contact_name
    assert db_contact.email == contact_email
    assert db_contact.phone == "123-555-4321"
    assert db_contact.notes == "Created via structured RPC integration test."

# TODO: Add tests for conflict, validation error 