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
    payload: dict[str, Any],
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles structured queries for events based on the unwrapped payload (dict)."""
    logger.info(f"Handling structured query for task {task_id}")

    query_params: EventQuery | None = None
    result_message = ""

    try:
        try:
            query_params = EventQuery.model_validate(payload)
            logger.debug(f"Validated query parameters from payload: {query_params}")
        except ValidationError as e:
            logger.warning(f"Invalid EventQuery payload for task {task_id}: {e}")
            raise InvalidParamsError(f"Invalid query parameters in payload: {e}", data=e.errors())

        # 2. Call the find_events strategy
        find_strategy = FindEventsStrategy(db_service=db_service)
        logger.info(f"Calling FindEventsStrategy for task {task_id}")
        found_events = await find_strategy.execute(data=query_params)

        # 3. Format the results
        results_list = [
            {
                "id": str(event.id),
                "title": event.title,
                "start_time": event.start_time.isoformat() if event.start_time else None,
                "end_time": event.end_time.isoformat() if event.end_time else None,
                "location": event.location,
            }
            for event in found_events
        ]
        result_message = json.dumps(results_list)
        logger.info(f"Found {len(found_events)} events for task {task_id}")

        # 4. Complete the task
        # Still need Message/Part to construct the completion message
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        return await task_manager.complete_task(task_id, agent_message)

    except InvalidParamsError as ipe:
        logger.error(f"InvalidParamsError processing structured query for task {task_id}: {ipe}")
        raise ipe
    except Exception as e:
        logger.exception(f"Unexpected error processing structured query for task {task_id}: {e}")
        raise InternalError(message=f"An unexpected error occurred during query processing: {e}") from e