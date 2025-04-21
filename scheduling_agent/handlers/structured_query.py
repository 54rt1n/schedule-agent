import logging
import json
from typing import Any

from pydantic import ValidationError

from scheduling_agent.a2a.types import (
    Task,
    Message,
    TaskState,
)
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.schemas import EventQuery
from scheduling_agent.a2a.task_manager import SchedulingTaskManager


logger = logging.getLogger(__name__)


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
            raise ValueError("Missing or invalid data part in message for structured query.")

        payload_data = message.parts[0].data
        if not isinstance(payload_data, dict):
            raise ValueError("Data part payload is not a dictionary.")

        try:
            query_params = EventQuery.model_validate(payload_data)
            logger.debug(f"Validated query parameters: {query_params}")
        except ValidationError as e:
            logger.warning(f"Invalid EventQuery payload for task {task_id}: {e}")
            raise ValueError(f"Invalid query parameters: {e}")

        # 2. Call the database service
        query_dump = query_params.model_dump(exclude_unset=True)
        logger.info(f"Calling find_events for task {task_id} with query: {query_dump}")
        found_events = await db_service.find_events(query=query_params)

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
        return await task_manager.complete_task(task_id, result_message)

    except ValueError as e:
        logger.error(f"Value error processing structured query for task {task_id}: {e}")
        return await task_manager.fail_task(task_id, f"Error processing query: {e}")
    except Exception as e:
        # Catch other potential errors (e.g., database connection)
        logger.exception(f"Unexpected error processing structured query for task {task_id}: {e}")
        return await task_manager.fail_task(task_id, f"An unexpected error occurred: {e}") 