# sa/a2a/command/structured_create.py
"""Handles the logic path for structured data input (create event ONLY)."""

from pydantic import ValidationError

from sa.a2a.dto import Artifact, Message, Part, Task
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.exceptions import ConflictError, DatabaseError
from sa.core.logging_config import get_logger
from sa.db.dao import EventCreate
from sa.db.service import DatabaseService
from sa.db.strategies.create_event import CreateEventStrategy
from sa.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)

async def handle_structured_create(
    task_id: str,
    input_data: dict,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    conflict_resolver: ConflictResolver,
    date_utils: DateUtils,
) -> Task:
    """Handles the logic path for structured data input (create event ONLY)."""
    # Logic for handling structured data (Create Event Path)
    # Basic check if it looks like a potential create request
    if "title" in input_data and ("start_time" in input_data or "startTime" in input_data):
        # Validate input data against EventCreate model
        event_create_data = EventCreate.model_validate(input_data)

        get_events_in_range_strategy = GetEventsInRangeStrategy(
            db_service=db_service,
            date_utils=date_utils,
        )

        # Instantiate the strategy here, passing real dependencies
        create_strategy = CreateEventStrategy(
            db_service=db_service,
            date_utils=date_utils,
            conflict_resolver=conflict_resolver,
            get_events_in_range_strategy=get_events_in_range_strategy
        )

        # Call the CreateEventStrategy
        created_event = await create_strategy.execute(event_create_data)

        # Prepare success message and artifact
        result_message = f"Event '{created_event.title}' created successfully."
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        artifact = Artifact(
            name="created_event", parts=[Part(type="data", data=created_event.model_dump(mode="json"))]
        )

        # Update task state via TaskManager
        final_task = await task_manager.complete_task(task_id, agent_message, artifact)
        return final_task
    else:
        # Structured data didn't match create event criteria
        logger.warning(
            "Could not determine create action from structured data", task_id=task_id, data=input_data
        )
        # Fail task directly? Or raise error?
        # Let's raise InvalidParamsError for consistency. The task might be failed by the caller if needed.
        raise InvalidParamsError(message="Could not determine create action from structured data provided.")
