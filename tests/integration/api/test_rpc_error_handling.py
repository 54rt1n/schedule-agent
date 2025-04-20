import pytest
import pytest_asyncio
import httpx
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

# Import fixtures from conftest
from .conftest import RPC_URL

# Import necessary types
from scheduling_agent.common.types import (
    JSONRPCRequest,
    InternalError,
    ParseError,
    InvalidRequestError,
    # Add specific task types if needed for triggering errors
    TaskSendParams,
    Message,
    Part,
    InvalidParamsError,
    MethodNotFoundError,
)
# Import specific exceptions to mock
from scheduling_agent.db.service import DatabaseService # To mock
from scheduling_agent.core.nlp_processor import NLPProcessor # To mock
from scheduling_agent.db.exceptions import DatabaseError # Import DatabaseError
from scheduling_agent.core.exceptions import NLPError, ExporterError # Import ExporterError (or Exception)
# Need LLMSchedulingParseResult for mocking NLP responses
from scheduling_agent.core.schemas import LLMSchedulingParseResult, LLMDateInfo, LLMFilters

# Tests for centralized error handling and invalid requests will go here
# (Phase 3, Task 2)

@pytest.mark.asyncio
async def test_rpc_internal_error_on_db_exception(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_conflict_resolver: AsyncMock,
    mock_nlp_processor: AsyncMock,
):
    """Test that a DatabaseError raised by db_service results in A2A InternalError."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "Find my meetings"
    error_message = "Simulated DB connection failed"

    # 1. Mock TaskManager to succeed on initial create_task
    mock_task_manager.create_task.return_value = True # Or whatever it returns on success

    # 2. Mock NLP Processor to return a 'query_events' intent
    mock_nlp_result = LLMSchedulingParseResult(intent="query_events")
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # 3. Mock DatabaseService.query_events to raise DatabaseError
    mock_db_service.query_events.side_effect = DatabaseError(error_message)

    # Prepare RPC request (tasks/send with text query)
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id
    )

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200 # JSON-RPC errors use 200 OK
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    error_response = response_data["error"] # Assign error dict for clarity
    assert error_response.get("code") == InternalError.DEFAULT_CODE # -32603
    # Check if the message reflects the internal error (might be generic)
    assert "internal database error occurred" in error_response.get("message", "").lower()
    # Check data field for taskId and details
    error_data = error_response.get("data")
    assert isinstance(error_data, dict)
    assert error_data.get("taskId") == task_id
    assert error_message in error_data.get("details", "")

    # Verify mocks were called as expected
    mock_task_manager.create_task.assert_awaited_once()
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once()
    mock_db_service.query_events.assert_awaited_once() # It should be called before raising

@pytest.mark.asyncio
async def test_rpc_internal_error_on_nlp_exception(
    async_client: httpx.AsyncClient,
    mock_nlp_processor: AsyncMock,
    # Add other mocks required by the specific path tested (e.g., tasks/send)
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock, # Required by handler signature
    mock_conflict_resolver: AsyncMock, # Required by handler signature
):
    """Test that an NLPError raised by nlp_processor results in A2A InternalError."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "Some complex query"
    error_message = "Simulated NLP parsing failure"

    # 1. Mock TaskManager to succeed on initial create_task
    mock_task_manager.create_task.return_value = True

    # 2. Mock NLP Processor to raise NLPError
    mock_nlp_processor.parse_natural_language_query.side_effect = NLPError(error_message)

    # Prepare RPC request
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id
    )

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    error_response = response_data["error"] # Assign error dict for clarity
    assert error_response.get("code") == InternalError.DEFAULT_CODE
    assert "internal nlp processing error occurred" in error_response.get("message", "").lower()
    # Check data field for taskId and details
    error_data = error_response.get("data")
    assert isinstance(error_data, dict)
    assert error_data.get("taskId") == task_id
    assert error_message in error_data.get("details", "")

    # Verify mocks
    mock_task_manager.create_task.assert_awaited_once()
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once()

@pytest.mark.asyncio
async def test_rpc_parse_error_invalid_json(
    async_client: httpx.AsyncClient
):
    """Test that sending invalid JSON results in A2A ParseError."""
    request_id = str(uuid.uuid4()) # Although ID might not be parsable
    invalid_json_string = "{\"jsonrpc\": \"2.0\", \"method\": \"tasks/send\" " # Missing closing brace

    # Action
    response = await async_client.post(
        RPC_URL,
        content=invalid_json_string,
        headers={'Content-Type': 'application/json'}
    )

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    # ID might be null if it couldn't be parsed from the invalid JSON
    assert response_data.get("id") is None
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    assert response_data["error"].get("code") == ParseError.DEFAULT_CODE # -32700
    assert "failed to parse json request" in response_data["error"].get("message", "").lower()

@pytest.mark.asyncio
async def test_rpc_invalid_request_error_missing_method(
    async_client: httpx.AsyncClient,
    # Add necessary mock fixtures even if not directly configured in this test
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    mock_nlp_processor: AsyncMock,
):
    """Test that a valid JSON lacking 'method' results in A2A InvalidRequestError."""
    request_id = str(uuid.uuid4())
    rpc_request_dict = {
        "jsonrpc": "2.0",
        # "method": "tasks/send", # Method is missing
        "params": {"sessionId": "abc", "message": {"role": "user", "parts": []}},
        "id": request_id
    }

    # Action
    response = await async_client.post(RPC_URL, json=rpc_request_dict)

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    assert response_data["error"].get("code") == InvalidRequestError.DEFAULT_CODE # -32600
    assert "invalid json-rpc request structure" in response_data["error"].get("message", "").lower()
    # Optionally check data for validation details
    assert isinstance(response_data["error"].get("data"), list)

    # Note: mock_conflict_resolver is not called in this test
    # mock_conflict_resolver.assert_not_called()
    # mock_nlp_processor.assert_not_called()
    # mock_task_manager.assert_not_called()
    # mock_task_manager.create_task.assert_not_called()
    # mock_task_manager.complete_task.assert_not_called()
    # mock_task_manager.require_input_task.assert_not_called()
    # mock_task_manager.fail_task.assert_awaited_once()
    # mock_task_manager.fail_task.assert_awaited_once()

@pytest.mark.asyncio
async def test_rpc_internal_error_on_exporter_exception(
    async_client: httpx.AsyncClient,
    mock_exporters: MagicMock, # Inject the mock exporters dict
    mock_nlp_processor: AsyncMock, # Need NLP for intent detection
    mock_db_service: AsyncMock, # Need DB for get_events_in_range
    mock_task_manager: MagicMock, # Need TM for initial create_task
    # conflict_resolver and date_utils might also be needed by handler signature
    mock_conflict_resolver: AsyncMock,
    mock_date_utils: MagicMock,
):
    """Test that an Exception raised by an exporter results in A2A InternalError."""
    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    query = "Export calendar as ics"
    error_message = "Simulated exporter failure"

    # 1. Mock TaskManager for initial create_task
    mock_task_manager.create_task.return_value = True

    # 2. Mock NLP Processor to return 'export_calendar' intent
    mock_filters = LLMFilters(format="ics") # Specify format
    mock_nlp_result = LLMSchedulingParseResult(intent="export_calendar", filters=mock_filters)
    mock_nlp_processor.parse_natural_language_query.return_value = mock_nlp_result

    # 3. Mock DB Service to return some events
    mock_db_service.get_events_in_range.return_value = [MagicMock()] # Return dummy event(s)

    # 4. Configure the mock exporter (obtained via mock_exporters.get) to raise an Exception
    # Assuming the mock_exporters fixture is set up as previously discussed
    mock_ical_exporter = mock_exporters.get('ics')
    if mock_ical_exporter:
        # Make the export method raise an error
        # Using generic Exception as ExporterError might not be defined
        mock_ical_exporter.export.side_effect = Exception(error_message)
    else:
        pytest.fail("Mock exporter for 'ics' not found in mock_exporters fixture setup.")

    # Prepare RPC request
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id
    )

    # Action & Assert: Expect the underlying Exception to be raised by the test client
    # Due to how Starlette/httpx handle exceptions in handlers in this context,
    # the test client sees the original exception, not the JSONResponse from the handler.
    with pytest.raises(Exception, match=error_message):
        await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Verify mocks were called up to the point of failure
    mock_task_manager.create_task.assert_awaited_once()
    mock_nlp_processor.parse_natural_language_query.assert_awaited_once()
    mock_db_service.get_events_in_range.assert_awaited_once()
    mock_ical_exporter.export.assert_called_once() # Exporter should still be called

# TODO: Add test for ConflictResolver error (Task 4.1)

@pytest.mark.asyncio
async def test_rpc_internal_error_on_conflict_resolver_exception(
    async_client: httpx.AsyncClient,
    mock_db_service: AsyncMock,
    mock_task_manager: MagicMock,
    mock_conflict_resolver: AsyncMock,
    # Add other necessary mocks if the code path requires them (likely not needed)
):
    """Test that an Exception raised during conflict resolution results in an error."""
    # Need imports at the top of the function
    from datetime import datetime
    from scheduling_agent.core.exceptions import ConflictError
    from scheduling_agent.db.models import Event
    from scheduling_agent.db.schemas import EventCreate

    request_id = str(uuid.uuid4())
    task_id = str(uuid.uuid4())
    error_message = "Simulated failure generating alternatives"

    # 1. Mock TaskManager for initial create_task
    mock_task_manager.create_task.return_value = True

    # 2. Mock DB Service create_event to raise ConflictError
    # Create a dummy conflict event for the error payload
    conflict_event = Event(
        id=uuid.uuid4(),
        title="Existing Conflicting Event",
        start_time=datetime(2024, 1, 1, 10, 0, 0),
        end_time=datetime(2024, 1, 1, 11, 0, 0),
        time_zone="UTC"
    )
    conflict_list = [{"type": "instance", "event": conflict_event}]
    mock_db_service.create_event.side_effect = ConflictError(conflicts=conflict_list)

    # 3. Mock ConflictResolver generate_alternative_times to raise Exception
    mock_conflict_resolver.generate_alternative_times.side_effect = Exception(error_message)

    # Prepare RPC request (tasks/send with structured data)
    event_data = EventCreate(
        title="New Event Causing Conflict",
        start_time=datetime(2024, 1, 1, 10, 30, 0), # Overlaps conflict_event
        end_time=datetime(2024, 1, 1, 11, 30, 0),
        time_zone="UTC"
    )
    params = TaskSendParams(
        id=task_id,
        # Use mode='json' to serialize datetime objects to ISO strings
        message=Message(role="user", parts=[Part(type="data", data=event_data.model_dump(mode='json'))])
    )
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method="tasks/send",
        params=params.model_dump(),
        id=request_id
    )

    # Action & Assert: Expect the raw Exception raised by the conflict resolver
    with pytest.raises(Exception, match=error_message):
        await async_client.post(RPC_URL, json=rpc_request.model_dump())

    # Verify mocks were called up to the point of failure
    mock_task_manager.create_task.assert_awaited_once()
    # create_event is called inside _handle_send_structured_data
    mock_db_service.create_event.assert_awaited_once()
    # generate_alternative_times is called in the except ConflictError block
    mock_conflict_resolver.generate_alternative_times.assert_awaited_once()

# --- Task 4.2: Input Validation Tests --- #

# Define common valid structures to modify for invalid cases
valid_message = Message(role="user", parts=[Part(type="text", text="hello")])
valid_task_id = str(uuid.uuid4())
valid_session_id = str(uuid.uuid4())

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, params, expected_error_code, expected_error_message",
    [
        # === tasks/send Invalid Params ===
        (
            "tasks/send",
            {"id": valid_task_id, "sessionId": valid_session_id}, # Missing 'message'
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/send",
            {"id": valid_task_id, "sessionId": valid_session_id, "message": {"parts": []}}, # Missing 'role' in message
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/send",
            {"id": valid_task_id, "sessionId": valid_session_id, "message": {"role": "user"}}, # Missing 'parts' in message
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/send",
            {"id": valid_task_id, "sessionId": valid_session_id, "message": {"role": "user", "parts": [{}]}}, # Missing 'type' in part
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/send",
            {"id": valid_task_id, "sessionId": valid_session_id, "message": {"role": "user", "parts": [{"type": "text"}]}}, # Missing 'text' in text part
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/send",
            {"id": valid_task_id, "sessionId": valid_session_id, "message": {"role": "user", "parts": [{"type": "data"}]}}, # Missing 'data' in data part
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),

        # === tasks/get Invalid Params ===
        (
            "tasks/get",
            {"id": 12345}, # 'id' is int instead of string
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/get",
            {"id": valid_task_id, "historyLength": -1}, # negative historyLength
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/get",
            {}, # missing 'id'
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),

        # === tasks/cancel Invalid Params ===
        (
            "tasks/cancel",
            {}, # missing 'id'
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
        (
            "tasks/cancel",
            {"id": 9876}, # 'id' is int instead of string
            InvalidParamsError.DEFAULT_CODE,
            "Invalid parameters"
        ),
    ],
)
async def test_rpc_invalid_params_integration(
    async_client: httpx.AsyncClient,
    method: str,
    params: dict,
    expected_error_code: int,
    expected_error_message: str,
    # Include mocks needed for *any* of the tested methods, even if not used by all
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
    mock_conflict_resolver: AsyncMock,
    mock_nlp_processor: AsyncMock,
):
    """Test various invalid parameter structures for RPC methods."""
    request_id = str(uuid.uuid4())
    rpc_request = JSONRPCRequest(
        jsonrpc="2.0",
        method=method,
        params=params,
        id=request_id
    )

    # Use model_dump(mode='json') to ensure any nested models (like datetime if used)
    # are properly serialized, although these tests focus on structure.
    response = await async_client.post(RPC_URL, json=rpc_request.model_dump(mode='json'))

    # Assertions
    assert response.status_code == 200
    response_data = response.json()
    assert response_data.get("id") == request_id
    assert "result" not in response_data or response_data["result"] is None
    assert "error" in response_data
    error_response = response_data["error"]
    assert error_response.get("code") == expected_error_code
    assert expected_error_message in error_response.get("message", "")
    # Optionally check for presence of 'data' field with validation details
    assert "data" in error_response
    assert isinstance(error_response["data"], list)
    assert len(error_response["data"]) > 0 # Ensure there's at least one validation error detail 