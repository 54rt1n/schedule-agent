# tests/api/rpc/test_tasks_send_structured_contact_query.py
"""Integration tests for the tasks/send RPC method with structured query_contacts intent."""

import pytest
import uuid
from fastapi.testclient import TestClient
import json

from sa.a2a.dto import TaskSendParams, Message, Part, TaskState
from sa.db.dao import ContactCreate, ContactGetById
from sa.db.service import DatabaseService
from sa.db.strategies.contact_create import CreateContactStrategy
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


@pytest.mark.asyncio
async def test_rpc_tasks_send_structured_contact_query_success_by_name(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService,
):
    """Test tasks/send with structured data for query_contacts intent by name."""
    # 1. Create a contact to query
    contact_name = f"QueryTarget {uuid.uuid4().hex[:8]}"
    contact_email = f"query.{uuid.uuid4().hex[:8]}@example.com"
    contact_to_create = ContactCreate(
        name=contact_name,
        email=contact_email
    )
    get_dao = await _create_db_contact(
        contact_create=contact_to_create,
        app_db_service=app_db_service
    )
    contact_id = get_dao.id

    # 2. Prepare Query Request (using name)
    task_id = str(uuid.uuid4())
    session_id = str(uuid.uuid4())
    query_data = {
        "name": contact_name
        # Add other query params like email, phone if needed by handler/DAO
    }

    # Wrap the structured data
    structured_data_wrapper = {
        "intent": "query_contacts",
        "payload": query_data
    }

    send_params = TaskSendParams(
        id=task_id,
        sessionId=session_id,
        message=Message(role="user", parts=[
            Part(type="data", data=structured_data_wrapper)
        ])
    )
    rpc_query_payload = build_rpc_request("tasks/send", send_params.model_dump())

    # 3. Send Query Request
    response_query = client.post("/api/rpc", json=rpc_query_payload, headers=auth_headers_valid)

    # 4. Assertions
    assert response_query.status_code == 200
    response_query_data = response_query.json()
    assert "result" in response_query_data, f"RPC Error: {response_query_data.get('error')}"
    assert response_query_data.get("error") is None

    result_payload = response_query_data["result"]
    assert result_payload["id"] == task_id
    # Expect COMPLETED state if query is successful
    assert result_payload["status"]["state"] == TaskState.COMPLETED.value

    # Check the status message for query results (JSON string)
    assert result_payload["status"]["message"]["role"] == "agent"
    query_message_part = result_payload["status"]["message"]["parts"][0]
    assert query_message_part["type"] == "text"

    # Parse the JSON string in the message
    query_results = json.loads(query_message_part["text"])
    assert isinstance(query_results, list)
    assert len(query_results) >= 1 # Should find at least the one we created

    # Check that our created contact is in the parsed results
    found_contact = False
    for contact_data in query_results:
        # Compare ID as string and name
        if contact_data.get("id") == str(contact_id) and contact_data.get("name") == contact_name:
             assert contact_data.get("email") == contact_email # Verify other fields too
             found_contact = True
             break
    assert found_contact, f"Created contact {contact_id} with name '{contact_name}' not found in query results"

# TODO: Add tests for querying by email, phone, non-existent, validation errors etc. 