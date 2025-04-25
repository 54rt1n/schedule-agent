# sa/a2a/command/structured_query.py
"""Handles the structured query_events intent."""

import json
from pydantic import ValidationError
from typing import Any

from sa.a2a.dto import Task, Message, Part
from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.logging_config import get_logger
from sa.db.dao import EventQuery
from sa.db.service import DatabaseService
from sa.db.strategies.find_events import FindEventsStrategy
from sa.a2a.exceptions import InvalidParamsError, InternalError

logger = get_logger(__name__)


async def handle_structured_query(
    task_id: str,
    message: Message,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles structured queries for events based on EventQuery payload."""
    logger.info(f"Handling structured query for task {task_id}")

    query_params: EventQuery | None = None
    result_message = ""

    try:
        # 1. Extract and validate the payload
        if not message.parts or message.parts[0].type != "data":
            raise InvalidParamsError("Missing or invalid data part in message for structured query.")

        payload_data = message.parts[0].data
        if not isinstance(payload_data, dict):
            raise InvalidParamsError("Data part payload is not a dictionary.")

        try:
            query_params = EventQuery.model_validate(payload_data)
            logger.debug(f"Validated query parameters: {query_params}")
        except ValidationError as e:
            logger.warning(f"Invalid EventQuery payload for task {task_id}: {e}")
            raise InvalidParamsError(f"Invalid query parameters: {e}", data=e.errors())

        # 2. Call the find_events strategy
        find_strategy = FindEventsStrategy(db_service=db_service) # Pass dependencies
        logger.info(f"Calling FindEventsStrategy for task {task_id}")
        # Strategy expects EventQuery object as data
        found_events = await find_strategy.execute(data=query_params)

        # 3. Format the results
        # Convert found events (SQLModel objects) to dicts/JSON
        # Using EventRead schema might be better long-term, but basic dict for now
        results_list = [
            {
                "id": str(event.id),
                "title": event.title,
                "start_time": event.start_time.isoformat() if event.start_time else None,
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "location": event.location,
                # Add other relevant fields as needed
            }
            for event in found_events
        ]
        result_message = json.dumps(results_list)
        logger.info(f"Found {len(found_events)} events for task {task_id}")

        # 4. Complete the task
        # Result should be a Message object
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        return await task_manager.complete_task(task_id, agent_message)

    except InvalidParamsError as ipe:
        error_text = f"Error processing query: {ipe}"
        logger.error(f"InvalidParamsError processing structured query for task {task_id}: {ipe}")
        raise ipe
    except Exception as e:
        error_text = f"An unexpected error occurred: {e}"
        # Catch other potential errors (e.g., database connection)
        logger.exception(f"Unexpected error processing structured query for task {task_id}: {e}")
        raise InternalError(message=error_text) from e