import base64
from datetime import datetime
import pytest
from pydantic import BaseModel
from unittest.mock import AsyncMock, patch
from typing import Any
import uuid

from schedule_api.routes.rpc import (
    handle_rpc_request,
    handle_tasks_get,
    handle_tasks_send,
)
from schedule_a2a.dto import (
    Artifact,
    CancelTaskParams,
    FileContent,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Part,
    StructuredPayloadWrapper,
    Task,
    TaskGetParams,
    TaskSendParams,
    TaskState,
    TaskStatus,
)
from schedule_a2a.exceptions import (
    MethodNotFoundError as A2AMethodNotFoundError,
    InvalidParamsError as A2AInvalidParamsError,
    TaskNotFoundError as A2ATaskNotFoundError,
    TaskNotCancelableError as A2ATaskNotCancelableError,
)
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_api.routes.rpc import handle_tasks_cancel
from schedule_core.db.models import Event
from schedule_core.db.service import DatabaseService
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.exporters.base import CalendarExporter
from schedule_nlp.nlp_processor import NLPProcessor


# --- Unit Tests for handle_tasks_send ---

@pytest.mark.asyncio
async def test_handle_tasks_send_create_success(
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_exporters: dict[str, CalendarExporter],
    mock_conflict_resolver: ConflictResolver,
):
    """Unit test handle_tasks_send: Create event success path using structured data. Patches handler."""
    task_id = str(uuid.uuid4())
    event_title = "Board Meeting"
    # Prepare the actual payload for the create operation
    event_payload = {
        "title": event_title,
        "start_time": "2024-06-15T14:00:00Z",
        "end_time": "2024-06-15T15:30:00Z",
    }
    # Wrap the payload
    wrapped_data = StructuredPayloadWrapper(
        intent="create_event",
        payload=event_payload
    ).model_dump() # Convert wrapper to dict for Part.data

    # Prepare TaskSendParams using the wrapped data
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=wrapped_data)])
        # No intent field here anymore
    )

    # Mock the event that the downstream handler (handle_structured_create) would create
    mock_created_event = Event(
        id=uuid.uuid4(),
        title=event_title,
        start_time=datetime(2024, 6, 15, 14, 0, 0),
        end_time=datetime(2024, 6, 15, 15, 30, 0),
    )
    # Mock the final task state that the *patched handler* should return
    expected_final_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED),
        artifacts=[
            Artifact(name="created_event", parts=[Part(type="data", data=mock_created_event.model_dump(mode='json'))])
        ]
    )

    # Patch the specific command handler that handle_tasks_send will route to
    with patch("schedule_api.routes.rpc.handle_structured_create", new_callable=AsyncMock) as mock_create_handler:
        mock_create_handler.return_value = expected_final_task

        # Call the main router
        returned_task = await handle_tasks_send(
            params=params,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            nlp_processor=mock_nlp_processor,
            date_utils=date_utils,
            exporters=mock_exporters,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions
    # 1. Check the correct handler was called with the *unwrapped* payload
    mock_create_handler.assert_awaited_once()
    call_args, call_kwargs = mock_create_handler.await_args
    assert call_kwargs.get('task_id') == task_id
    assert call_kwargs.get('payload') == event_payload # Check unwrapped payload
    assert call_kwargs.get('task_manager') is mock_task_manager
    assert call_kwargs.get('db_service') is mock_db_service
    assert 'conflict_resolver' in call_kwargs
    assert 'date_utils' in call_kwargs

    # 2. Check the returned Task object matches handler's return
    assert isinstance(returned_task, Task)
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == expected_final_task.model_dump()


@pytest.mark.asyncio
async def test_handle_tasks_send_create_conflict(
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_exporters: dict[str, CalendarExporter],
    mock_conflict_resolver: ConflictResolver,
):
    """Unit test handle_tasks_send: Create event conflict path. Patches handler."""
    task_id = str(uuid.uuid4())
    # Prepare the actual payload
    event_payload = {
        "title": "Conflict Event",
        "start_time": "2024-07-01T09:00:00Z",
        "end_time": "2024-07-01T10:00:00Z",
    }
    # Wrap the payload
    wrapped_data = StructuredPayloadWrapper(
        intent="create_event",
        payload=event_payload
    ).model_dump()

    # Prepare TaskSendParams
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=wrapped_data)])
    )

    # Define the expected final task state returned by the *patched handler*
    expected_final_input_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.INPUT_REQUIRED),
        artifacts=[]
    )

    # Patch the specific command handler
    with patch("schedule_api.routes.rpc.handle_structured_create", new_callable=AsyncMock) as mock_create_handler:
        mock_create_handler.return_value = expected_final_input_task

        # Call the main router
        returned_task = await handle_tasks_send(
            params=params,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            nlp_processor=mock_nlp_processor,
            date_utils=date_utils,
            exporters=mock_exporters,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions
    # 1. Check the correct handler was called with the *unwrapped* payload
    mock_create_handler.assert_awaited_once()
    call_args, call_kwargs = mock_create_handler.await_args
    assert call_kwargs.get('task_id') == task_id
    assert call_kwargs.get('payload') == event_payload # Check unwrapped payload
    assert call_kwargs.get('task_manager') is mock_task_manager
    assert call_kwargs.get('db_service') is mock_db_service
    assert 'conflict_resolver' in call_kwargs
    assert 'date_utils' in call_kwargs

    # 2. Check the returned Task object matches handler's return
    assert isinstance(returned_task, Task)
    assert returned_task.status.state == TaskState.INPUT_REQUIRED
    assert returned_task.model_dump() == expected_final_input_task.model_dump()


@pytest.mark.asyncio
async def test_handle_tasks_send_query_success(
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_exporters: dict[str, CalendarExporter],
    mock_conflict_resolver: ConflictResolver,
):
    """Unit test handle_tasks_send: Query schedule success path. Patches NLP dispatcher."""
    task_id = str(uuid.uuid4())
    query = "What is on my calendar for the rest of today?"

    # Prepare input params
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)]),
        intent="query_events" # Assume intent is provided or NLP detects it
    )

    # Mock expected final task state that the *patched handler* returns
    expected_artifact = Artifact(name="queried_events", parts=[Part(type="data", data=[{"id": "mock_id"}])]) # Dummy artifact
    expected_final_task = Task(
        id=task_id, status=TaskStatus(state=TaskState.COMPLETED), artifacts=[expected_artifact]
    )

    # --- Action: Patch the actual NLP handler --- #
    # Corrected patch target
    with patch("schedule_api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_nlp_handler:
        # Configure the patched handler to return the expected task
        mock_nlp_handler.return_value = expected_final_task

        # Call the handler function directly
        returned_task = await handle_tasks_send(
            params=params,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            nlp_processor=mock_nlp_processor,
            date_utils=date_utils,
            exporters=mock_exporters,
            conflict_resolver=mock_conflict_resolver,
        )

        # Assertions
        # 1. Check the NLP handler was called with correct keyword args
        mock_nlp_handler.assert_awaited_once_with(
             task_id=task_id,
             message=params.message, # Pass the whole message object
             task_manager=mock_task_manager,
             db_service=mock_db_service,
             nlp_processor=mock_nlp_processor,
             exporters=mock_exporters,
             date_utils=date_utils,
             conflict_resolver=mock_conflict_resolver
        )

    # 2. Check DB/TaskManager not called directly by handle_tasks_send
    mock_task_manager.complete_task.assert_not_called()

    # 3. Check returned task
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == expected_final_task.model_dump()


# --- Unit Tests for handle_tasks_get ---


@pytest.mark.asyncio
async def test_handle_tasks_get_success(mock_task_manager: SchedulingTaskManager):
    """Unit test handle_tasks_get: Success path."""
    task_id = str(uuid.uuid4())
    # Ensure TaskGetParams is imported if not globally available
    params = TaskGetParams(id=task_id)

    # Mock TaskManager response
    mock_found_task = Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.COMPLETED,
            message=Message(role="agent", parts=[Part(type="text", text="Done")]),
        ),
    )
    mock_task_manager.get_task.return_value = mock_found_task

    # Action
    returned_task = await handle_tasks_get(params=params, task_manager=mock_task_manager)

    # Assertions
    mock_task_manager.get_task.assert_awaited_once_with(task_id=task_id)
    assert returned_task == mock_found_task


@pytest.mark.asyncio
async def test_handle_tasks_get_not_found(mock_task_manager: SchedulingTaskManager):
    """Unit test handle_tasks_get: Task not found path."""
    task_id = str(uuid.uuid4())
    # Ensure TaskGetParams is imported if not globally available
    params = TaskGetParams(id=task_id)

    # Mock TaskManager to raise A2ATaskNotFoundError directly
    mock_task_manager.get_task.side_effect = A2ATaskNotFoundError(f"Task {task_id} not found")

    # Action & Assertion
    # The handler should propagate the A2ATaskNotFoundError raised by get_task
    with pytest.raises(A2ATaskNotFoundError, match=f"Task {task_id} not found"):
        await handle_tasks_get(params=params, task_manager=mock_task_manager)

    # Assertions
    mock_task_manager.get_task.assert_awaited_once_with(task_id=task_id)


# --- Unit Tests for handle_tasks_cancel ---

# Import the (yet to be created) handle_tasks_cancel function
# This is now handled by the conditional import at the top


@pytest.mark.asyncio
async def test_handle_tasks_cancel_success(mock_task_manager: SchedulingTaskManager):
    """Unit test handle_tasks_cancel: Success path."""
    task_id = str(uuid.uuid4())
    params = CancelTaskParams(id=task_id)

    # Mock TaskManager response for successful cancel
    mock_canceled_task = Task(id=task_id, status=TaskStatus(state=TaskState.CANCELED))
    mock_task_manager.cancel_task.return_value = mock_canceled_task

    # Action
    returned_task = await handle_tasks_cancel(params=params, task_manager=mock_task_manager)

    # Assertions
    mock_task_manager.cancel_task.assert_awaited_once_with(task_id=task_id)
    assert returned_task == mock_canceled_task


@pytest.mark.asyncio
async def test_handle_tasks_cancel_not_found(mock_task_manager: SchedulingTaskManager):
    """Unit test handle_tasks_cancel: Task not found path."""
    task_id = str(uuid.uuid4())
    params = CancelTaskParams(id=task_id)

    # Mock TaskManager to raise TaskNotFoundError
    mock_task_manager.cancel_task.side_effect = A2ATaskNotFoundError(f"Task {task_id} not found")

    # Action & Assertion
    with pytest.raises(A2ATaskNotFoundError):
        await handle_tasks_cancel(params=params, task_manager=mock_task_manager)

    mock_task_manager.cancel_task.assert_awaited_once_with(task_id=task_id)


@pytest.mark.asyncio
async def test_handle_tasks_cancel_not_cancelable(mock_task_manager: SchedulingTaskManager):
    """Unit test handle_tasks_cancel: Task not cancelable path."""
    task_id = str(uuid.uuid4())
    params = CancelTaskParams(id=task_id)

    # Mock TaskManager to raise TaskNotCancelableError
    mock_task_manager.cancel_task.side_effect = A2ATaskNotCancelableError(
        f"Task {task_id} not cancelable"
    )

    # Action & Assertion
    with pytest.raises(A2ATaskNotCancelableError):
        await handle_tasks_cancel(params=params, task_manager=mock_task_manager)

    mock_task_manager.cancel_task.assert_awaited_once_with(task_id=task_id)


# --- Unit Tests for export_calendar intent in handle_tasks_send ---


@pytest.mark.asyncio
async def test_handle_tasks_send_export_success(
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    mock_exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    mock_conflict_resolver: ConflictResolver,
):
    """Unit test handle_tasks_send: Export calendar successfully. Patches dispatcher."""
    task_id = str(uuid.uuid4())
    query = "Export my calendar as ics"

    # Prepare input params
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)]),
        intent="export_calendar" # Explicit intent
    )

    # Mock TaskManager completion (returned by patched dispatcher)
    exported_content_bytes = b"BEGIN:VCALENDAR..." # Example bytes
    expected_base64_bytes = base64.b64encode(exported_content_bytes).decode("utf-8")
    expected_artifact = Artifact(
        name="calendar.ics",
        parts=[
            Part(
                type="file",
                file=FileContent(
                    name="calendar.ics", mimeType="text/calendar", bytes=expected_base64_bytes
                ),
            )
        ],
    )
    final_task = Task(
        id=task_id, status=TaskStatus(state=TaskState.COMPLETED), artifacts=[expected_artifact]
    )
    # mock_task_manager_unit.complete_task.return_value = final_task # Dispatcher returns this

    # --- Action: Patch the actual NLP handler --- #
    # Corrected patch target
    with patch("schedule_api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_nlp_handler:
        mock_nlp_handler.return_value = final_task # Configure mock return

        # Pass the mock_exporters_unit fixture to the exporters argument
        returned_task = await handle_tasks_send(
            params=params,
            task_manager=mock_task_manager,
            db_service=mock_db_service,
            nlp_processor=mock_nlp_processor,
            exporters=mock_exporters,
            date_utils=date_utils,
            conflict_resolver=mock_conflict_resolver,
        )

    # Assertions
    # 1. Check NLP handler called correctly with keyword args
    mock_nlp_handler.assert_awaited_once_with(
             task_id=task_id,
             message=params.message,
             task_manager=mock_task_manager,
             db_service=mock_db_service,
             nlp_processor=mock_nlp_processor,
             exporters=mock_exporters,
             date_utils=date_utils,
             conflict_resolver=mock_conflict_resolver
        )

    # 2. Verify mocks not called directly by handle_tasks_send
    mock_task_manager.complete_task.assert_not_called()

    # 3. Check returned task
    assert returned_task is not None
    assert returned_task.id == task_id
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == final_task.model_dump() # Compare dumps


@pytest.mark.asyncio
async def test_handle_tasks_send_export_unsupported_format(
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    mock_exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    mock_conflict_resolver: ConflictResolver,
):
    """Unit test handle_tasks_send: Export calendar with unsupported format. Patches dispatcher."""
    task_id = str(uuid.uuid4())
    query = "Export my calendar as pdf"  # Requesting unsupported format

    # Prepare input params
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="text", text=query)]),
        intent="export_calendar" # Explicit intent
    )

    # --- Action: Patch the actual NLP handler --- #
    # Expect the *handler* (or the code it calls) to raise the error
    # Corrected patch target
    with patch("schedule_api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_nlp_handler:
        # Configure the patched handler to raise the expected error
        mock_nlp_handler.side_effect = A2AInvalidParamsError("Unsupported export format requested: pdf")

        # Action & Assert: Expect InvalidParamsError from the *patched handler*
        with pytest.raises(A2AInvalidParamsError, match="Unsupported export format requested: pdf"):
            await handle_tasks_send(
                params=params,
                task_manager=mock_task_manager,
                db_service=mock_db_service,
                nlp_processor=mock_nlp_processor,
                exporters=mock_exporters,
                date_utils=date_utils,
                conflict_resolver=mock_conflict_resolver,
            )

    # Assertions
    # 1. Check the NLP handler was called
    mock_nlp_handler.assert_awaited_once_with(
             task_id=task_id,
             message=params.message,
             task_manager=mock_task_manager,
             db_service=mock_db_service,
             nlp_processor=mock_nlp_processor,
             exporters=mock_exporters,
             date_utils=date_utils,
             conflict_resolver=mock_conflict_resolver
        )

    # 2. Verify other mocks not called directly
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.fail_task.assert_not_called()


# --- Unit Tests for handle_rpc_request (Parameter Validation) ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "params", "expected_error_code", "expected_error_message"),
    [
        # Method not found
        ("unknown/method", {}, A2AMethodNotFoundError.DEFAULT_CODE, "Method 'unknown/method' not found."),
        # tasks/send invalid params - expecting specific message format
        (
            "tasks/send",
            {"sessionId": 123},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            {"message": {"role": "user", "parts": []}},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            {"task": {"status": {"state": "QUEUED"}, "message": {"role": "user", "parts": []}}},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            None,
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            {},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            {"sessionId": "abc", "message": "not a message object"},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            {"sessionId": "abc", "task": {"status": {"state": "invalid_state"}}},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        (
            "tasks/send",
            {
                "sessionId": "abc",
                "message": {"role": "invalid", "parts": [{"type": "text", "text": "hi"}]},
            },
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskSendParams",
        ),
        # tasks/get invalid params - expecting specific message format
        (
            "tasks/get",
            {"taskId": "abc"},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskGetParams",
        ),
        (
            "tasks/get",
            {"id": 123},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskGetParams",
        ),
        (
            "tasks/get",
            None,
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskGetParams",
        ),
        (
            "tasks/get",
            {},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskGetParams",
        ),
        (
            "tasks/get",
            {"id": "abc", "historyLength": -1},
            A2AInvalidParamsError.DEFAULT_CODE,
            "Invalid parameters for method execution: TaskGetParams",
        ),
    ],
)
async def test_handle_rpc_request_invalid_params(
    method: str,
    params: dict[str, Any] | list[Any] | BaseModel | None,
    expected_error_code: str,
    expected_error_message: str,
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    mock_exporters: dict[str, CalendarExporter],
    mock_conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
):
    """Tests handle_rpc_request with various invalid parameters or methods."""
    # Mock the actual handlers to isolate handle_rpc_request validation/routing
    # We don't want the actual logic of handle_tasks_send/get to run here.
    with (
        patch(
            "schedule_api.routes.rpc.handle_tasks_send", new_callable=AsyncMock
        ) as mock_send,
        patch(
            "schedule_api.routes.rpc.handle_tasks_get", new_callable=AsyncMock
        ) as mock_get,
    ):
        request_id = str(uuid.uuid4())
        rpc_request = JSONRPCRequest(jsonrpc="2.0", method=method, params=params, id=request_id)

        # Action: Call the main RPC handler
        # Simulate FastAPI exception handling: catch expected A2A errors and create response
        response = None
        try:
            # Normally, FastAPI would run this and catch the A2A error
            # We mock the handlers, so this call should primarily test routing/validation
            await handle_rpc_request(
                request=rpc_request,
                task_manager=mock_task_manager,
                db_service=mock_db_service,
                nlp_processor=mock_nlp_processor,
                exporters=mock_exporters,
                date_utils=date_utils,
                conflict_resolver=mock_conflict_resolver,
            )
            # If no exception was raised, but the test expects one, fail explicitly
            if expected_error_code:
                 pytest.fail(f"Expected an A2AError ({expected_error_message}) but none was raised.")

        except (A2AMethodNotFoundError, A2AInvalidParamsError) as e:
            # Construct the JSONRPCResponse manually as the FastAPI handler would
            response = JSONRPCResponse(
                id=request_id,
                error=e.as_jsonrpc_error()
            )
        except Exception as e:
            pytest.fail(f"Caught unexpected exception type {type(e).__name__}: {e}")


        # Assertions on the constructed response (or lack thereof if no error expected)
        assert isinstance(response, JSONRPCResponse)
        assert response.id == request_id
        assert response.result is None
        assert response.error is not None
        assert response.error.code == expected_error_code
        # Check the primary message. Details might be in response.error.data for validation errors.
        assert response.error.message == expected_error_message

        # Ensure the specific handlers were NOT called when params were invalid
        if method == "tasks/send":
            mock_send.assert_not_called()
        elif method == "tasks/get":
            mock_get.assert_not_called()


# --- Unit Tests for handle_tasks_send (Structured Query/Delete using Wrapper) --- #

@pytest.mark.asyncio
@patch("schedule_api.routes.rpc.handle_structured_query", new_callable=AsyncMock) # Patch the handler
async def test_handle_tasks_send_structured_query_wrapper(
    mock_query_handler: AsyncMock,
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_conflict_resolver: ConflictResolver,
    mock_exporters: dict[str, CalendarExporter],
):
    """Test handle_tasks_send routes correctly for structured query using the payload wrapper."""
    task_id = str(uuid.uuid4())

    # Prepare the actual query payload
    query_payload = {"title_contains": "Project Meeting"}
    # Wrap it
    wrapped_data = StructuredPayloadWrapper(
        intent="query_events",
        payload=query_payload
    ).model_dump()

    # Prepare TaskSendParams
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=wrapped_data)])
    )

    # Mock the final task state returned by the *patched query handler*
    expected_final_task = Task(id=task_id, status=TaskStatus(state=TaskState.COMPLETED))
    mock_query_handler.return_value = expected_final_task

    # Action: Call the main send handler
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        nlp_processor=mock_nlp_processor,
        date_utils=date_utils,
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters,
    )

    # Assertions
    # 1. Check the query handler was called correctly
    mock_query_handler.assert_awaited_once()
    call_args, call_kwargs = mock_query_handler.await_args
    assert call_kwargs.get('task_id') == task_id
    # Check the unwrapped payload passed to the specific handler
    assert call_kwargs.get('payload') == query_payload # Verify unwrapped payload
    assert call_kwargs.get('task_manager') is mock_task_manager
    assert call_kwargs.get('db_service') is mock_db_service
    # ... check other dependencies ...

    # 2. Check the returned task matches the handler's return
    assert returned_task is not None
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == expected_final_task.model_dump()

@pytest.mark.asyncio
@patch("schedule_api.routes.rpc.handle_structured_delete", new_callable=AsyncMock) # Patch the handler
async def test_handle_tasks_send_structured_delete_wrapper(
    mock_delete_handler: AsyncMock,
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_conflict_resolver: ConflictResolver,
    mock_exporters: dict[str, CalendarExporter],
):
    """Test handle_tasks_send routes correctly for structured delete using the payload wrapper."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()

    # Prepare the actual delete payload
    delete_payload = {"id": str(event_id_to_delete)} # DAO expects UUID, but payload might be str
    # Wrap it
    wrapped_data = StructuredPayloadWrapper(
        intent="delete_event",
        payload=delete_payload
    ).model_dump()

    # Prepare TaskSendParams
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=wrapped_data)])
    )

    # Mock the final task state returned by the *patched delete handler*
    expected_final_task = Task(id=task_id, status=TaskStatus(state=TaskState.COMPLETED))
    mock_delete_handler.return_value = expected_final_task

    # Action: Call the main send handler
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        nlp_processor=mock_nlp_processor,
        date_utils=date_utils,
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters,
    )

    # Assertions
    # 1. Check the delete handler was called correctly
    mock_delete_handler.assert_awaited_once()
    call_args, call_kwargs = mock_delete_handler.await_args
    assert call_kwargs.get('task_id') == task_id
    # Check the unwrapped payload passed to the specific handler
    assert call_kwargs.get('payload') == delete_payload # Verify unwrapped payload
    assert call_kwargs.get('task_manager') is mock_task_manager
    assert call_kwargs.get('db_service') is mock_db_service
    # ... check other dependencies ...

    # 2. Check the returned task matches the handler's return
    assert returned_task is not None
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == expected_final_task.model_dump()

