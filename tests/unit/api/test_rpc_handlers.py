import base64
from datetime import datetime
from fastapi import Depends
import pytest
from pydantic import BaseModel
from unittest.mock import AsyncMock, MagicMock, patch, ANY
from typing import Any
import uuid

from sa.api.routes.rpc import (
    handle_rpc_request,
    handle_tasks_get,
    handle_tasks_send,
)
from sa.a2a.dto import (
    Artifact,
    CancelTaskParams,
    FileContent,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Part,
    Task,
    TaskGetParams,
    TaskSendParams,
    TaskState,
    TaskStatus,
)
from sa.a2a.exceptions import (
    BaseA2AError,
    MethodNotFoundError as A2AMethodNotFoundError,
    InvalidParamsError as A2AInvalidParamsError,
    InternalError as A2AInternalError,
    TaskNotFoundError as A2ATaskNotFoundError,
    TaskNotCancelableError as A2ATaskNotCancelableError,
    InvalidTaskStateError as A2AInvalidTaskStateError,
    AgentCapabilityError as A2AAgentCapabilityError,
)
from sa.db.dao import EventDelete, EventQuery
from sa.a2a.task_manager import SchedulingTaskManager
from sa.api.routes.rpc import handle_tasks_cancel
from sa.db.models import Event
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.nlp.dao import LLMDateInfo, LLMFilters, LLMSchedulingParseResult
from sa.nlp.nlp_processor import NLPProcessor


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
    created_event_id = str(uuid.uuid4())

    # Mock event object that the *handler* would return
    mock_created_event = Event(
        id=uuid.UUID(created_event_id),
        title=event_title,
        start_time=datetime(2024, 6, 15, 14, 0, 0),
        end_time=datetime(2024, 6, 15, 15, 30, 0),
    )

    # Prepare input params (This is correct)
    event_data = {
        "title": event_title,
        "start_time": "2024-06-15T14:00:00Z",
        "end_time": "2024-06-15T15:30:00Z",
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)]),
        intent="create_event"
    )

    # Define the expected final task state that the *patched handler* should return
    expected_final_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.COMPLETED),
        artifacts=[ # Handler is responsible for creating the artifact
            Artifact(name="created_event", parts=[Part(type="data", data=mock_created_event.model_dump(mode='json'))])
        ]
    )

    # --- Action: Patch the command handler --- #
    with patch("sa.api.routes.rpc.handle_structured_create", new_callable=AsyncMock) as mock_create_handler:
        # Configure the patched handler to return the expected task
        mock_create_handler.return_value = expected_final_task

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
    # 1. Check the patched handler was called with correct args
    mock_create_handler.assert_awaited_once()
    call_args, call_kwargs = mock_create_handler.await_args
    assert call_kwargs.get('task_id') == task_id
    assert call_kwargs.get('input_data') == event_data
    assert call_kwargs.get('task_manager') is mock_task_manager
    assert call_kwargs.get('db_service') is mock_db_service
    assert 'conflict_resolver' in call_kwargs
    assert 'date_utils' in call_kwargs

    # 2. Check DB/TaskManager calls are NOT made directly by handle_tasks_send
    mock_task_manager.complete_task.assert_not_called()
    mock_task_manager.create_task.assert_not_awaited()

    # 3. Check the returned Task object
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

    # Prepare input params
    event_data = {
        "title": "Conflict Event",
        "start_time": "2024-07-01T09:00:00Z",
        "end_time": "2024-07-01T10:00:00Z",
    }
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=event_data)]),
        intent="create_event"
    )

    # Define the expected final task state that the *patched handler* should return
    expected_final_input_task = Task(
        id=task_id,
        status=TaskStatus(state=TaskState.INPUT_REQUIRED), # Handler sets this
        artifacts=[] # Let handler manage artifacts
    )

    # --- Action: Patch the command handler --- #
    with patch("sa.api.routes.rpc.handle_structured_create", new_callable=AsyncMock) as mock_create_handler:
        # Configure the *patched handler* to return the expected task state
        mock_create_handler.return_value = expected_final_input_task

        # Call the main tasks_send function
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
    # 1. Check the patched handler was called
    mock_create_handler.assert_awaited_once()
    call_args, call_kwargs = mock_create_handler.await_args
    assert call_kwargs.get('task_id') == task_id
    assert call_kwargs.get('input_data') == event_data
    assert call_kwargs.get('task_manager') is mock_task_manager
    assert call_kwargs.get('db_service') is mock_db_service
    assert 'conflict_resolver' in call_kwargs
    assert 'date_utils' in call_kwargs

    # 2. Check DB / Task Manager were NOT called directly by handle_tasks_send
    mock_task_manager.require_input_task.assert_not_called()
    mock_task_manager.complete_task.assert_not_called()

    # 3. Check the returned Task object matches what the patched handler returned
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

    # Mock NLP result (needed for _dispatch_nlp_request routing)
    mock_nlp_result = LLMSchedulingParseResult(
        intent="query_events", # This is important for routing inside _dispatch_nlp_request
        date_info=LLMDateInfo(start_date="today", end_date="today"),
        entities=None,
        filters=LLMFilters(),
    )
    mock_nlp_processor.process.return_value = mock_nlp_result

    # Mock expected final task state that the *patched handler* returns
    expected_artifact = Artifact(name="queried_events", parts=[Part(type="data", data=[{"id": "mock_id"}])]) # Dummy artifact
    expected_final_task = Task(
        id=task_id, status=TaskStatus(state=TaskState.COMPLETED), artifacts=[expected_artifact]
    )

    # --- Action: Patch the actual NLP handler --- #
    # Corrected patch target
    with patch("sa.api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_nlp_handler:
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

    # Mock TaskManager response
    mock_task_manager.get_task.return_value = None

    # Action & Assertion
    with pytest.raises(A2ATaskNotFoundError, match=f"Task with ID '{task_id}' not found."):
        await handle_tasks_get(params=params, task_manager=mock_task_manager)

    # Assertions
    mock_task_manager.get_task.assert_awaited_once_with(task_id=task_id)


# --- Unit Tests for handle_tasks_cancel ---

# Import the (yet to be created) handle_tasks_cancel function
# This is now handled by the conditional import at the top


@pytest.mark.asyncio
async def test_handle_tasks_cancel_success(mock_task_manager: SchedulingTaskManager):
    """Unit test handle_tasks_cancel: Success path."""
    if not handle_tasks_cancel:
        pytest.skip("handle_tasks_cancel not implemented yet.")

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
    if not handle_tasks_cancel:
        pytest.skip("handle_tasks_cancel not implemented yet.")

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
    if not handle_tasks_cancel:
        pytest.skip("handle_tasks_cancel not implemented yet.")

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


# --- Unit Tests for export_calendar intent in handle_tasks_send --- #


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

    # Mock NLP result (needed for dispatcher routing)
    mock_nlp_result = LLMSchedulingParseResult(
        intent="export_calendar",
        date_info=LLMDateInfo(),
        filters=LLMFilters(format="ics"),
        entities=None,
    )
    # mock_nlp_processor_unit.process.return_value = mock_nlp_result # Dispatcher calls process

    # Mock exporter behavior (not needed if patching dispatcher)
    # exported_content = "BEGIN:VCALENDAR..."
    # mock_ical_exporter_unit.export.return_value = exported_content
    # mock_ical_exporter_unit.get_mime_type.return_value = "text/calendar"

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
    with patch("sa.api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_nlp_handler:
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

    # Mock NLP result (still needed for dispatcher routing logic if it checks format)
    # mock_filters = LLMFilters(format="pdf")
    # mock_nlp_result = LLMSchedulingParseResult(
    #     intent="export_calendar",
    #     date_info=LLMDateInfo(),
    #     filters=mock_filters,
    #     entities=None,
    # )
    # mock_nlp_processor_unit.process.return_value = mock_nlp_result # Dispatcher calls process

    # --- Action: Patch the actual NLP handler --- #
    # Expect the *handler* (or the code it calls) to raise the error
    # Corrected patch target
    with patch("sa.api.routes.rpc.handle_nlp_request", new_callable=AsyncMock) as mock_nlp_handler:
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
            "sa.api.routes.rpc.handle_tasks_send", new_callable=AsyncMock
        ) as mock_send,
        patch(
            "sa.api.routes.rpc.handle_tasks_get", new_callable=AsyncMock
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


# --- Unit Tests for handle_tasks_send (Structured Query/Delete) ---

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_query.FindEventsStrategy", new_callable=MagicMock)
async def test_handle_tasks_send_structured_query(
    mock_find_strategy_class: MagicMock, # Add the patched class as an arg
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_conflict_resolver: ConflictResolver,
    mock_exporters: dict[str, CalendarExporter],
):
    """Test handle_tasks_send routes correctly for structured query. Patches Strategy."""
    task_id = str(uuid.uuid4())

    # Prepare input params with EventQuery data
    query = EventQuery(title_contains="Project Meeting")
    query_data = query.model_dump(exclude_unset=True)
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=query_data)]),
        intent="query_events"
    )

    # Mock the FindEventsStrategy instance and its execute method
    mock_find_strategy_instance = AsyncMock()
    mock_found_events = [Event(id=uuid.uuid4(), title="Found Event")] # Dummy event
    mock_find_strategy_instance.execute.return_value = mock_found_events
    mock_find_strategy_class.return_value = mock_find_strategy_instance # Mock class to return instance

    # Mock the final task state returned by TaskManager (called by the handler)
    expected_final_task = Task(id=task_id, status=TaskStatus(state=TaskState.COMPLETED))
    # The handler will call complete_task, so mock that on the task_manager fixture
    mock_task_manager.complete_task = AsyncMock(return_value=expected_final_task)

    # --- Action: Call the main send handler --- #
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager,
        db_service=mock_db_service, # db_service is still needed to instantiate the strategy
        nlp_processor=mock_nlp_processor,
        date_utils=date_utils,
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters,
    )

    # Assertions
    # 1. Check the FindEventsStrategy was instantiated and executed correctly within the handler
    mock_find_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_find_strategy_instance.execute.assert_awaited_once_with(data=query) # Strategy expects 'data'

    # 2. Check TaskManager was called correctly by the handler
    mock_task_manager.complete_task.assert_awaited_once()
    # Check the message content passed to complete_task
    complete_args, _ = mock_task_manager.complete_task.await_args
    assert complete_args[0] == task_id
    completion_message: Message = complete_args[1]
    assert isinstance(completion_message, Message)
    assert len(completion_message.parts) == 1
    assert completion_message.parts[0].type == "text"
    # Check if the JSON representation of the found event is in the message text
    assert '"title": "Found Event"' in completion_message.parts[0].text


    # 3. Check the returned Task object matches the one from complete_task mock
    assert returned_task is not None
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == expected_final_task.model_dump()

@pytest.mark.asyncio
# Patch the DeleteEventStrategy where it's USED (inside the structured_delete handler)
@patch("sa.a2a.command.structured_delete.DeleteEventStrategy", new_callable=MagicMock)
async def test_handle_tasks_send_structured_delete(
    mock_delete_strategy_class: MagicMock, # Add the patched class as an arg
    mock_task_manager: SchedulingTaskManager,
    mock_db_service: DatabaseService,
    mock_nlp_processor: NLPProcessor,
    date_utils: DateUtils,
    mock_conflict_resolver: ConflictResolver,
    mock_exporters: dict[str, CalendarExporter],
):
    """Test handle_tasks_send routes correctly for structured delete. Patches Strategy."""
    task_id = str(uuid.uuid4())
    event_id_to_delete = uuid.uuid4()

    # Prepare input params with EventDelete data
    delete_payload = EventDelete(id=event_id_to_delete)
    delete_data = delete_payload.model_dump()
    params = TaskSendParams(
        id=task_id,
        message=Message(role="user", parts=[Part(type="data", data=delete_data)]),
        intent="delete_event"
    )

    # Mock the DeleteEventStrategy instance and its execute method
    mock_delete_strategy_instance = AsyncMock()
    mock_delete_strategy_instance.execute.return_value = True # Simulate successful deletion
    mock_delete_strategy_class.return_value = mock_delete_strategy_instance # Mock class to return instance

    # Mock the final task state returned by TaskManager (called by the handler)
    expected_final_task = Task(id=task_id, status=TaskStatus(state=TaskState.COMPLETED))
    # The handler will call complete_task, so mock that on the task_manager fixture
    mock_task_manager.complete_task = AsyncMock(return_value=expected_final_task)

    # --- Action: Call the main send handler --- #
    returned_task = await handle_tasks_send(
        params=params,
        task_manager=mock_task_manager,
        db_service=mock_db_service, # db_service is still needed to instantiate the strategy
        nlp_processor=mock_nlp_processor,
        date_utils=date_utils,
        conflict_resolver=mock_conflict_resolver,
        exporters=mock_exporters,
    )

    # Assertions
    # 1. Check the DeleteEventStrategy was instantiated and executed correctly
    mock_delete_strategy_class.assert_called_once_with(db_service=mock_db_service)
    mock_delete_strategy_instance.execute.assert_awaited_once_with(data=event_id_to_delete) # Strategy expects 'data'

    # 2. Task manager was called to complete the task by the handler
    mock_task_manager.complete_task.assert_awaited_once()
    # Check args if needed, e.g., the success message
    complete_args, _ = mock_task_manager.complete_task.await_args
    assert complete_args[0] == task_id
    completion_message: Message = complete_args[1]
    assert isinstance(completion_message, Message)
    assert len(completion_message.parts) == 1
    assert completion_message.parts[0].type == "text"
    assert f"Successfully deleted event {event_id_to_delete}" in completion_message.parts[0].text

    # 3. Check the returned Task object matches the one from complete_task mock
    assert returned_task is not None
    assert returned_task.status.state == TaskState.COMPLETED
    assert returned_task.model_dump() == expected_final_task.model_dump()

