import uuid
from unittest.mock import AsyncMock, patch
from datetime import datetime, UTC, timedelta

import pytest
from httpx import AsyncClient

from scheduling_agent.a2a import types as a2a_types
from scheduling_agent.a2a.types import StructuredUpdatePayload
from scheduling_agent.db.schemas import EventUpdate, EventQuery
from scheduling_agent.db.models import Event, ModifiedInstance # For mock return types
from scheduling_agent.core.exceptions import ConflictError, TaskNotFoundError
from scheduling_agent.db.service import DatabaseService # For mocking spec
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMFilters, LLMDateInfo # Add LLMSchedulingParseResult import

@pytest.mark.asyncio
async def test_send_task_update_structured_success(
    async_client: AsyncClient,
    test_event: Event,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
):
    """Test successful structured update_event intent via tasks/send."""
    # --- Arrange ---
    # 1. DatabaseService is now mocked via the apply_mock_dependencies fixture
    # using the mock_db_service fixture injected into this test.

    # 2. Mock the behavior of the injected mock_db_service.update_event
    # Simulate successful update, returning the updated Event object
    updated_event_data = test_event.model_copy(update={"title": "Updated Title via RPC"})
    mock_db_service.update_event.return_value = updated_event_data

    # 3. Mock TaskManager behavior
    task_id = str(uuid.uuid4())
    # Mock get_task for the initial check in handle_tasks_send
    dummy_existing_task = a2a_types.Task(id=task_id, status=a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING))
    mock_task_manager.get_task.return_value = dummy_existing_task
    # Mock the final task object returned by complete_task
    final_task_state = a2a_types.Task(
        id=task_id,
        status=a2a_types.TaskStatus(state=a2a_types.TaskState.COMPLETED),
    )
    mock_task_manager.complete_task.return_value = final_task_state

    # 4. Prepare the request payload
    event_id_to_update = test_event.id
    update_details = EventUpdate(title="Updated Title via RPC", location="New Room")
    structured_payload = StructuredUpdatePayload(
        event_id=event_id_to_update,
        update_data=update_details
    )

    request_payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [
                    {
                        "type": "data",
                        # Embed the structured payload within the 'data' field
                        "data": structured_payload.model_dump(mode='json'), # Ensure JSON serializable
                        # Add intent here? Or outside? Spec is flexible. Let's try here.
                        # Maybe better as top-level param in TaskSendParams if design allows?
                        # For now, assume intent is derived or passed differently.
                        # Let's assume intent is derived from the *presence* of this data structure
                        # OR passed in the top-level TaskSendParams `params` dict if we modify the route handler
                    }
                ],
            },
            # Add intent at the top level (more standard A2A)
            "intent": "update_event",
            "is_structured": True # Add a flag to help routing in handler
        },
        "id": "req-update-123",
    }

    # --- Act ---
    response = await async_client.post("/api/rpc/", json=request_payload)

    # --- Assert ---
    # 1. Check HTTP response
    assert response.status_code == 200
    response_data = response.json()

    # 2. Check JSON-RPC response structure
    assert response_data["jsonrpc"] == "2.0"
    assert response_data["id"] == "req-update-123"
    assert "result" in response_data
    assert response_data["error"] is None

    # 3. Check the result (final task state)
    # Assuming the handler returns the result from task_manager.complete_task
    # This depends on the implementation of the route handler.
    # For now, let's assume it returns a simple success confirmation or the final task object
    # If it returns the final task:
    # result_task = a2a_types.Task.model_validate(response_data["result"])
    # assert result_task.id == task_id
    # assert result_task.status.state == a2a_types.TaskState.COMPLETED
    # For now, just assert result is not None
    assert response_data["result"] is not None

    # 4. Verify DB service call (using the injected mock_db_service)
    mock_db_service.update_event.assert_awaited_once()
    call_args, call_kwargs = mock_db_service.update_event.await_args
    assert call_kwargs.get("event_id") == event_id_to_update
    assert call_kwargs.get("update_data") == update_details

    # 5. Verify Task Manager calls (using injected mock_task_manager)
    mock_task_manager.get_task.assert_awaited_once_with(task_id) # Verify get_task call
    mock_task_manager.complete_task.assert_awaited_once() # Verify complete_task call
    # Add assertion for arguments passed to complete_task
    call_args_tm, call_kwargs_tm = mock_task_manager.complete_task.await_args
    assert call_args_tm[0] == task_id
    # Assert message content if possible/needed
    # Assert artifact content if possible/needed


@pytest.mark.asyncio
async def test_send_task_update_structured_not_found(
    async_client: AsyncClient,
    test_event: Event, # Needed for event_id, though it won't be found
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock, 
):
    """Test structured update_event fails correctly if event ID is not found."""
    # --- Arrange ---
    # 1. Mock DatabaseService.update_event to return None (simulating not found)
    mock_db_service.update_event.return_value = None

    # 2. Mock TaskManager behavior
    task_id = str(uuid.uuid4())
    # Mock get_task for the initial check in handle_tasks_send
    dummy_existing_task = a2a_types.Task(id=task_id, status=a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING))
    mock_task_manager.get_task.return_value = dummy_existing_task
    # Mock the final task object returned by fail_task
    fail_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text=f"Could not find event with ID 'non-existent-id' to update.")]) # Message set by handler
    final_task_state = a2a_types.Task(
        id=task_id,
        status=a2a_types.TaskStatus(state=a2a_types.TaskState.FAILED, message=fail_message),
    )
    # Note: We configure the mock_task_manager fixture's fail_task method directly
    # in conftest.py using a side_effect to handle the message correctly.
    # No need to set return_value here if side_effect is used.

    # 3. Prepare the request payload
    non_existent_event_id = uuid.uuid4()
    update_details = EventUpdate(title="This Should Not Update")
    structured_payload = StructuredUpdatePayload(
        event_id=non_existent_event_id,
        update_data=update_details
    )

    request_payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [
                    {
                        "type": "data",
                        "data": structured_payload.model_dump(mode='json'),
                    }
                ],
            },
            "intent": "update_event",
            "is_structured": True
        },
        "id": "req-update-nf-456",
    }

    # --- Act ---
    response = await async_client.post("/api/rpc/", json=request_payload)

    # --- Assert ---
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["id"] == "req-update-nf-456"
    assert "result" in response_data
    assert response_data["error" ] is None

    # Assert the task result indicates FAILED state
    result_task = a2a_types.Task.model_validate(response_data["result"])
    assert result_task.id == task_id
    assert result_task.status.state == a2a_types.TaskState.FAILED
    assert result_task.status.message is not None
    assert result_task.status.message.role == "agent"
    assert "Could not find event with ID" in result_task.status.message.parts[0].text
    assert str(non_existent_event_id) in result_task.status.message.parts[0].text

    # Verify DB service call
    mock_db_service.update_event.assert_awaited_once_with(
        event_id=non_existent_event_id, update_data=update_details
    )

    # Verify Task Manager calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id)
    mock_task_manager.fail_task.assert_awaited_once()
    call_args_tm, _ = mock_task_manager.fail_task.await_args
    assert call_args_tm[0] == task_id
    assert isinstance(call_args_tm[1], a2a_types.Message)
    assert "Could not find event with ID" in call_args_tm[1].parts[0].text
    mock_task_manager.complete_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_task_update_structured_conflict(
    async_client: AsyncClient,
    test_event: Event, # Needed for event_id
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock, # Needed for conflict details
):
    """Test structured update_event results in INPUT_REQUIRED on ConflictError."""
    # --- Arrange ---
    # 1. Mock DatabaseService.update_event to raise ConflictError
    conflict_event_data = Event(
        id=uuid.uuid4(),
        title="Existing Conflicting Event",
        start_time=datetime.now(UTC),
        end_time=datetime.now(UTC) + timedelta(hours=1),
        time_zone="UTC"
    ).model_dump(mode='json') # Use dict for serialization in error
    conflict_list = [{"type": "instance", "event": conflict_event_data}]
    mock_db_service.update_event.side_effect = ConflictError(conflicts=conflict_list)

    # 2. Mock TaskManager behavior
    task_id = str(uuid.uuid4())
    # Mock get_task for the initial check
    dummy_existing_task = a2a_types.Task(id=task_id, status=a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING))
    mock_task_manager.get_task.return_value = dummy_existing_task
    # Mock the final task object returned by require_input_task
    input_req_message = a2a_types.Message(role="agent", parts=[a2a_types.Part(type="text", text="Update failed due to conflicts. Please provide alternatives or cancel.")])
    conflict_artifact = a2a_types.Artifact(name="scheduling_conflicts", parts=[a2a_types.Part(type="data", data=conflict_list)])
    final_task_state = a2a_types.Task(
        id=task_id,
        status=a2a_types.TaskStatus(state=a2a_types.TaskState.INPUT_REQUIRED, message=input_req_message),
        artifacts=[conflict_artifact]
    )
    mock_task_manager.require_input_task.return_value = final_task_state

    # 3. Prepare the request payload (attempting a time update)
    event_id_to_update = test_event.id
    update_details = EventUpdate(
        title="Conflicting Update", 
        start_time=datetime.now(UTC), # Add time fields to trigger check
        end_time=datetime.now(UTC) + timedelta(hours=2),
        time_zone="UTC"
    )
    structured_payload = StructuredUpdatePayload(
        event_id=event_id_to_update,
        update_data=update_details
    )

    request_payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [
                    {
                        "type": "data",
                        "data": structured_payload.model_dump(mode='json'),
                    }
                ],
            },
            "intent": "update_event",
            "is_structured": True
        },
        "id": "req-update-cf-789",
    }

    # --- Act ---
    response = await async_client.post("/api/rpc/", json=request_payload)

    # --- Assert ---
    assert response.status_code == 200
    response_data = response.json()
    assert response_data["id"] == "req-update-cf-789"
    assert "result" in response_data
    assert response_data["error"] is None

    # Assert the task result indicates INPUT_REQUIRED state
    result_task = a2a_types.Task.model_validate(response_data["result"])
    assert result_task.id == task_id
    assert result_task.status.state == a2a_types.TaskState.INPUT_REQUIRED
    assert result_task.status.message is not None
    assert "Update failed due to conflicts" in result_task.status.message.parts[0].text
    assert len(result_task.artifacts) == 1
    assert result_task.artifacts[0].name == "scheduling_conflicts"
    assert result_task.artifacts[0].parts[0].data == conflict_list

    # Verify DB service call
    mock_db_service.update_event.assert_awaited_once_with(
        event_id=event_id_to_update, update_data=update_details
    )

    # Verify Task Manager calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id)
    mock_task_manager.require_input_task.assert_awaited_once()
    call_args_tm, call_kwargs_tm = mock_task_manager.require_input_task.await_args
    assert call_args_tm[0] == task_id # Check task_id
    assert isinstance(call_args_tm[1], a2a_types.Message) # Check message
    assert isinstance(call_args_tm[2], a2a_types.Artifact) # Check artifact
    assert call_args_tm[2].name == "scheduling_conflicts"
    mock_task_manager.complete_task.assert_not_awaited()
    mock_task_manager.fail_task.assert_not_awaited()


# TODO: Add tests for conflict, not found, invalid params
# TODO: Add fixture for test_event if not already present in conftest 

@pytest.mark.asyncio
async def test_send_task_update_nlp_success(
    async_client: AsyncClient,
    test_event: Event,
    mock_task_manager: AsyncMock,
    mock_db_service: AsyncMock,
    mock_nlp_processor: AsyncMock, # Add NLP processor mock
    # Add date_utils fixture if needed for time parsing/comparison
):
    """Test successful NLP update_event intent via tasks/send."""
    # --- Arrange ---
    task_id = str(uuid.uuid4())
    user_text = f"change the meeting '{test_event.title}' to tomorrow at 3pm"
    new_title_from_nlp = test_event.title # Assume NLP doesn't change title in this example
    # Define expected update time based on NLP (need date_utils potentially)
    # For simplicity, let's assume NLP provides specific datetime objects
    new_start_time_from_nlp = datetime.now(UTC).replace(hour=15, minute=0, second=0, microsecond=0) + timedelta(days=1)
    new_end_time_from_nlp = new_start_time_from_nlp + (test_event.end_time - test_event.start_time) # Keep original duration

    # 1. Mock NLP Processor - Use correct LLMSchedulingParseResult structure
    nlp_result = LLMSchedulingParseResult(
        intent="update_event",
        # Populate filters/date_info based on the NLP interpretation
        # For querying the event, we might use the title from filters
        filters=LLMFilters(
            title=test_event.title, # Assume NLP identified the event by title for query
        ),
        # For the update data, we use date_info
        date_info=LLMDateInfo(
            start_date=new_start_time_from_nlp.strftime("%Y-%m-%d"),
            specific_time=new_start_time_from_nlp.strftime("%H:%M:%S"),
            end_date=new_end_time_from_nlp.strftime("%Y-%m-%d"),
            end_time=new_end_time_from_nlp.strftime("%H:%M:%S"),
            timeZone="UTC" # Use hardcoded "UTC" as the datetime uses datetime.UTC
        ),
        confidence=0.9,
        original_query=user_text # Set original_query field
    )
    mock_nlp_processor.process.return_value = nlp_result

    # 2. Mock DB Service - Find Event
    # Assume _handle_send_nlp_input will use find_events based on NLP query data
    mock_db_service.find_events.return_value = [test_event] # Return the event to be updated

    # 3. Mock DB Service - Update Event
    updated_event_data = test_event.model_copy(
        update={
            "start_time": new_start_time_from_nlp,
            "end_time": new_end_time_from_nlp,
        }
    )
    mock_db_service.update_event.return_value = updated_event_data

    # 4. Mock Task Manager
    dummy_existing_task = a2a_types.Task(id=task_id, status=a2a_types.TaskStatus(state=a2a_types.TaskState.WORKING))
    mock_task_manager.get_task.return_value = dummy_existing_task
    final_task_state = a2a_types.Task(
        id=task_id,
        status=a2a_types.TaskStatus(state=a2a_types.TaskState.COMPLETED),
        # Add expected artifact if handler creates one
        artifacts=[a2a_types.Artifact(name="updated_event_details", parts=[a2a_types.Part(type="data", data=updated_event_data.model_dump(mode='json'))])]
    )
    mock_task_manager.complete_task.return_value = final_task_state

    # 5. Prepare Request Payload
    request_payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "params": {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": user_text}],
            },
            "intent": "update_event", # Explicit intent helps routing
            # "is_structured": False, # Can be omitted or set to False
        },
        "id": "req-nlp-update-789",
    }

    # --- Act ---
    response = await async_client.post("/api/rpc/", json=request_payload)

    # --- Assert ---
    # 1. Check HTTP response
    assert response.status_code == 200
    response_data = response.json()

    # 2. Check JSON-RPC response structure
    assert response_data["jsonrpc"] == "2.0"
    assert response_data["id"] == "req-nlp-update-789"
    assert "result" in response_data
    assert response_data["error"] is None

    # 3. Check the result (final task state)
    result_task = a2a_types.Task.model_validate(response_data["result"])
    assert result_task.id == task_id
    assert result_task.status.state == a2a_types.TaskState.COMPLETED
    assert result_task.artifacts is not None
    assert len(result_task.artifacts) == 1
    assert result_task.artifacts[0].name == "updated_event_details"

    # 4. Verify NLP Processor call
    mock_nlp_processor.process.assert_awaited_once_with(user_text)

    # 5. Verify DB Service find call
    # This depends on how the handler uses the NLP result to query
    # Assuming it uses the EventQuery part of the NLP result
    mock_db_service.find_events.assert_awaited_once()
    call_args_find, call_kwargs_find = mock_db_service.find_events.await_args
    assert isinstance(call_kwargs_find.get("query"), EventQuery)
    assert call_kwargs_find.get("query").title_contains == test_event.title

    # 6. Verify DB Service update call
    mock_db_service.update_event.assert_awaited_once()
    call_args_update, call_kwargs_update = mock_db_service.update_event.await_args
    assert call_kwargs_update.get("event_id") == test_event.id
    actual_update_data = call_kwargs_update.get("update_data")
    assert isinstance(actual_update_data, EventUpdate)

    # Compare fields individually for debugging
    assert actual_update_data.start_time == new_start_time_from_nlp, "Start time mismatch"
    assert actual_update_data.end_time == new_end_time_from_nlp, "End time mismatch"
    assert actual_update_data.time_zone == "UTC", "Timezone mismatch"
    # Assert other fields are None/unset as expected
    assert actual_update_data.title is None, "Title should be None"
    assert actual_update_data.location is None, "Location should be None"
    assert actual_update_data.notes is None, "Notes should be None"
    assert actual_update_data.rrule is None, "RRule should be None"
    assert actual_update_data.original_start_time is None, "Original start time should be None"

    # 7. Verify Task Manager calls
    mock_task_manager.get_task.assert_awaited_once_with(task_id)
    mock_task_manager.complete_task.assert_awaited_once()
    call_args_tm, call_kwargs_tm = mock_task_manager.complete_task.await_args
    assert call_args_tm[0] == task_id # task_id
    assert isinstance(call_args_tm[1], a2a_types.Message) # agent_message
    assert isinstance(call_args_tm[2], a2a_types.Artifact) # artifact
    assert call_args_tm[2].name == "updated_event_details"

    # 8. Verify other mocks not called
    mock_task_manager.require_input_task.assert_not_awaited()
    mock_task_manager.fail_task.assert_not_awaited() 