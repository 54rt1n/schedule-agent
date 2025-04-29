import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid
import json
from datetime import datetime, timezone, timedelta

# Import necessary types and handlers
from sa.a2a.command.structured_query import handle_structured_query
from sa.a2a.dto import (
    Task,
    TaskState,
    TaskStatus,
    Message,
    Part,
)
from sa.a2a.exceptions import InvalidParamsError
from sa.db.models import Event
from sa.db.dao import TimeRange
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.date_utils import DateUtils

# Mock return task from TaskManager
mock_completed_task = Task(
    id="mock_task_id",
    status=TaskStatus(state=TaskState.COMPLETED, message=Message(role="agent", parts=[])),
)

@pytest.mark.asyncio
@patch("sa.a2a.command.structured_query.GetEventsInRangeStrategy")
async def test_handle_structured_query(
    mock_get_strategy_class: MagicMock,
    mock_task_manager: MagicMock,
    mock_db_service: AsyncMock,
):
    """Test the handle_structured_query handler with valid unwrapped payload."""
    task_id = str(uuid.uuid4())
    date_utils = DateUtils()

    # Prepare a valid query payload dictionary with dates the real DateUtils can parse
    query_payload = {
        "start_time": "2024-05-20T10:00:00Z",
        "end_time": "2024-05-20T18:00:00Z",
        "title_contains": "Meeting"
    }

    # Mock Event and GetEventsInRangeStrategy response
    # Use fixed, predictable timestamps for mock event data
    mock_start_utc = date_utils.parse_datetime(query_payload["start_time"])
    mock_end_utc = date_utils.parse_datetime(query_payload["end_time"])
    mock_event_start = mock_start_utc + timedelta(hours=1)
    mock_event_end = mock_start_utc + timedelta(hours=2)
    fixed_creation_time = datetime(2024, 5, 20, 12, 0, 0, tzinfo=timezone.utc)

    mock_event = Event(
        id=uuid.uuid4(),
        title="Team Meeting",
        start_time=mock_event_start,
        end_time=mock_event_end,
        location="Office",
        created_at=fixed_creation_time,
        updated_at=fixed_creation_time
    )
    mock_get_strategy_instance = AsyncMock()
    mock_get_strategy_instance.execute = AsyncMock(return_value=[mock_event])
    mock_get_strategy_class.return_value = mock_get_strategy_instance

    # Mock TaskManager response
    mock_task_manager.complete_task.return_value = mock_completed_task

    # Call the handler, passing the payload dict and REAL date_utils
    final_task = await handle_structured_query(
        task_id=task_id,
        payload=query_payload,
        task_manager=mock_task_manager,
        db_service=mock_db_service,
        date_utils=date_utils,
    )

    # Assertions
    assert final_task is not None
    assert final_task.status.state == TaskState.COMPLETED

    # Check Strategy instantiation and call
    # Expect the REAL date_utils instance to be passed
    mock_get_strategy_class.assert_called_once_with(
        db_service=mock_db_service,
        date_utils=date_utils
    )
    # Verify execute was called with the TimeRange DAO created from parsed dates
    expected_time_range_dao = TimeRange(start_time=mock_start_utc, end_time=mock_end_utc)
    # Use the dates produced by the SAME method the handler uses
    actual_start_utc, actual_end_utc = date_utils.parse_nlp_date_range(
        start_date_str=query_payload["start_time"],
        end_date_str=query_payload["end_time"]
    )
    expected_time_range_dao_from_nlp = TimeRange(start_time=actual_start_utc, end_time=actual_end_utc)

    mock_get_strategy_instance.execute.assert_awaited_once()
    args, kwargs = mock_get_strategy_instance.execute.await_args
    assert kwargs.get('data') == expected_time_range_dao_from_nlp

    # Check TaskManager call - using robust data comparison
    expected_result_list = [mock_event.model_dump(mode="json")]
    
    mock_task_manager.complete_task.assert_awaited_once()
    args, kwargs = mock_task_manager.complete_task.await_args
    called_task_id = args[0]
    called_message = args[1]
    
    assert called_task_id == task_id
    assert isinstance(called_message, Message)
    assert called_message.role == "agent"
    assert len(called_message.parts) == 1
    called_part = called_message.parts[0]
    assert isinstance(called_part, Part)
    assert called_part.type == "text"
    assert called_part.text is not None
    
    # Compare the parsed JSON data
    actual_result_data = json.loads(called_part.text)
    assert actual_result_data == expected_result_list 