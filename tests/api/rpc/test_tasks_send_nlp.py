import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, AsyncMock, MagicMock
import uuid
from typing import List
from datetime import datetime, timedelta

from sa.a2a.dto import TaskState
# Corrected imports based on sa/nlp/dao.py and sa/db/models/event.py
from sa.nlp.dao import LLMSchedulingParseResult, LLMDateInfo, LLMFilters
from sa.db.models.event import Event
from sa.db.dao import EventCreate # Added import
from sa.event.date_utils import DateUtils # Added import
from sa.event.conflict_resolver import ConflictResolver # Added import
from sa.db.service import DatabaseService # Added import

# Assuming build_rpc_request and create_db_event are available from parent conftest
from ..conftest import build_rpc_request, create_db_event

@pytest.mark.skip(reason="NLP query execution flow under development")
@pytest.mark.asyncio
async def test_tasks_send_nlp_request_query_success(
    client: TestClient,
    auth_headers_valid: dict,
    app_db_service: DatabaseService,
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
):
    '''Test NLP query_schedule intent routes and executes successfully.'''
    task_id = str(uuid.uuid4())
    user_query = "What do I have scheduled for tomorrow morning?"

    # 1. Setup: Create an event that the query should find
    event_payload = {
        "title": "Morning Standup",
        "start_time": datetime.fromisoformat("2025-08-01T09:00:00"),
        "end_time": datetime.fromisoformat("2025-08-01T09:30:00"),
        "user_id": "test_user_nlp_query"
    }
    event_create_dto = EventCreate(**event_payload)

    # Call the helper function, passing required fixtures
    db_event: Event = await create_db_event(
        event_create=event_create_dto,
        app_db_service=app_db_service,
        date_utils=date_utils,
        conflict_resolver=conflict_resolver,
    )
    # create_db_event only returns the event, not the task
    assert db_event is not None
    expected_event_artifact = db_event.model_dump(mode='json')

    # Define what the mocked NLP processor should return using actual DAO models
    nlp_date_info = LLMDateInfo(
        start_date="2025-08-01",
        end_date="2025-08-01",
        time_description="tomorrow morning"
    )
    nlp_filters = LLMFilters()

    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_query}],
        },
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # 2. Mock ONLY the NLPProcessor class where it's defined
    mock_nlp_result = LLMSchedulingParseResult(
        intent="query_schedule",
        date_info=nlp_date_info,
        filters=nlp_filters,
        confidence=0.95,
        method="llm",
        original_query=user_query
    )
    # Create a mock NLPProcessor *instance*
    mock_processor_instance = MagicMock()
    # Set its parse_natural_language_query method to an AsyncMock returning the result
    mock_processor_instance.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)

    # Patch the dependency *getter function* where it is used in Depends()
    dependency_getter_path = "sa.api.routes.rpc.get_nlp_processor"

    # The patched function will return our mock instance directly
    with patch(dependency_getter_path, return_value=mock_processor_instance) as mock_get_nlp:

        # 3. Call API
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    # 4. Assert response
    assert response.status_code == 200
    response_data = response.json()
    assert "result" in response_data, f"RPC call failed: {response_data.get('error')}"
    assert response_data.get("error") is None
    result = response_data["result"]

    assert result["taskId"] == task_id
    assert result["state"] == TaskState.COMPLETED.value
    assert isinstance(result["artifact"], list)
    assert len(result["artifact"]) >= 1
    found = False
    for item in result["artifact"]:
        if item["id"] == expected_event_artifact["id"]:
            # Compare relevant fields, dump might have subtle differences
            assert item["summary"] == expected_event_artifact["summary"]
            assert item["start"] == expected_event_artifact["start"]
            assert item["end"] == expected_event_artifact["end"]
            found = True
            break
    assert found, "Expected event not found in query results"

    # 5. Verify the method on our mock instance was called
    mock_processor_instance.parse_natural_language_query.assert_awaited_once_with(user_query)
    # Optional: Verify the dependency getter itself was called if needed
    # mock_get_nlp.assert_called_once()

# Skip this test for now as the exact implementation might change
@pytest.mark.skip(reason="NLP clarify flow under development")
@pytest.mark.asyncio
async def test_tasks_send_nlp_requests_clarification(
    client: TestClient,
    auth_headers_valid: dict,
    date_utils: DateUtils, # Needed for NLP Processor mock potentially
):
    """Test NLP processing results in a clarification question."""
    task_id = str(uuid.uuid4())
    user_query = "Book a meeting."

    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_query}],
        },
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Mock the NLP Processor to return a clarification request
    clarification_text = "Okay, I can help with that. Who should attend the meeting, and when should it be?"
    mock_nlp_result = LLMSchedulingParseResult(
        intent="clarify", # Or maybe create_event with low confidence/missing fields
        structured_data=None,
        response_text=clarification_text,
        confidence=0.5,
        method="llm",
        original_query=user_query
    )
    mock_processor_instance = MagicMock()
    mock_processor_instance.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)

    dependency_getter_path = "sa.api.routes.rpc.get_nlp_processor"
    with patch(dependency_getter_path, return_value=mock_processor_instance):
        # Call API
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)

    # Assert response
    assert response.status_code == 200
    response_data = response.json()
    assert "result" in response_data, f"RPC call failed: {response_data.get('error')}"
    assert response_data.get("error") is None
    result = response_data["result"]

    # Assert task state and clarification message
    assert result["id"] == task_id
    assert result["status"]["state"] == TaskState.INPUT_REQUIRED.value
    assert clarification_text in result["status"]["message"]["parts"][0]["text"]

    # Assert no structured data artifact
    assert len(result["artifacts"]) == 0

    # Verify mock was called
    mock_processor_instance.parse_natural_language_query.assert_awaited_once_with(user_query)

# === Placeholder Tests for Other NLP Intents ===

@pytest.mark.skip(reason="NLP create routing not fully implemented/tested")
@pytest.mark.asyncio
async def test_tasks_send_nlp_request_create_event(
    client: TestClient,
    auth_headers_valid: dict,
):
    """Placeholder test for NLP leading to a create_event intent."""
    task_id = str(uuid.uuid4())
    user_query = "Schedule lunch with Bob next Tuesday at noon."
    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_query}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Mock NLP result for create
    mock_nlp_result = LLMSchedulingParseResult(intent="create_event", structured_data={"title": "Lunch with Bob"}, response_text="OK.")
    mock_processor_instance = MagicMock()
    mock_processor_instance.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)
    dependency_getter_path = "sa.api.routes.rpc.get_nlp_processor"
    with patch(dependency_getter_path, return_value=mock_processor_instance):
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)
    # Basic assertion: Expect success, further checks depend on handler implementation
    assert response.status_code == 200
    # TODO: Add assertions based on actual routing logic in handle_nlp_request

@pytest.mark.skip(reason="NLP delete routing not fully implemented/tested")
@pytest.mark.asyncio
async def test_tasks_send_nlp_request_delete_event(
    client: TestClient,
    auth_headers_valid: dict,
):
    """Placeholder test for NLP leading to a delete_event intent."""
    task_id = str(uuid.uuid4())
    user_query = "Cancel my 3 PM meeting tomorrow."
    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_query}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Mock NLP result for delete (assuming it extracts an ID or criteria)
    mock_nlp_result = LLMSchedulingParseResult(intent="delete_event", structured_data={"event_id": "some-event-id"}, response_text="OK.")
    mock_processor_instance = MagicMock()
    mock_processor_instance.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)
    dependency_getter_path = "sa.api.routes.rpc.get_nlp_processor"
    with patch(dependency_getter_path, return_value=mock_processor_instance):
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)
    assert response.status_code == 200
    # TODO: Add assertions based on actual routing logic in handle_nlp_request

@pytest.mark.skip(reason="NLP update routing not fully implemented/tested")
@pytest.mark.asyncio
async def test_tasks_send_nlp_request_update_event(
    client: TestClient,
    auth_headers_valid: dict,
):
    """Placeholder test for NLP leading to an update_event intent."""
    task_id = str(uuid.uuid4())
    user_query = "Move my 10 AM standup to 10:15 AM."
    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_query}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Mock NLP result for update
    mock_nlp_result = LLMSchedulingParseResult(intent="update_event", structured_data={"event_id": "standup-id", "update_data": {"start_time": "..."}}, response_text="OK.")
    mock_processor_instance = MagicMock()
    mock_processor_instance.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)
    dependency_getter_path = "sa.api.routes.rpc.get_nlp_processor"
    with patch(dependency_getter_path, return_value=mock_processor_instance):
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)
    assert response.status_code == 200
    # TODO: Add assertions based on actual routing logic in handle_nlp_request

@pytest.mark.skip(reason="NLP export routing not fully implemented/tested")
@pytest.mark.asyncio
async def test_tasks_send_nlp_request_export_calendar(
    client: TestClient,
    auth_headers_valid: dict,
):
    """Placeholder test for NLP leading to an export_calendar intent."""
    task_id = str(uuid.uuid4())
    user_query = "Export my calendar for next week as ics."
    send_params = {
        "id": task_id,
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": user_query}]
        }
    }
    rpc_payload = build_rpc_request("tasks/send", send_params)

    # Mock NLP result for export
    mock_nlp_result = LLMSchedulingParseResult(intent="export_calendar", date_info=LLMDateInfo(start_date="..."), filters=LLMFilters(format="ics"), response_text="OK.")
    mock_processor_instance = MagicMock()
    mock_processor_instance.parse_natural_language_query = AsyncMock(return_value=mock_nlp_result)
    dependency_getter_path = "sa.api.routes.rpc.get_nlp_processor"
    with patch(dependency_getter_path, return_value=mock_processor_instance):
        response = client.post("/api/rpc", json=rpc_payload, headers=auth_headers_valid)
    assert response.status_code == 200
    # TODO: Add assertions based on actual routing logic in handle_nlp_request 