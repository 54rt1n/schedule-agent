# packages/schedule-a2a/src/schedule_a2a/command/structured_query.py
# Copyright (C) 2025 Martin Bukowski

"""Handles the structured query_events intent."""

import json
from pydantic import ValidationError
from typing import Any
from datetime import datetime, timezone

from schedule_a2a.dto import Task, Message, Part
from schedule_a2a.task_manager import SchedulingTaskManager
from schedule_core.core.logging_config import get_logger
from schedule_core.db.dao import TimeRange
from schedule_core.db.service import DatabaseService
from schedule_core.db.strategies.get_events_in_range import GetEventsInRangeStrategy
from schedule_a2a.exceptions import InvalidParamsError, InternalError
from schedule_core.event.date_utils import DateUtils, InvalidInputError

logger = get_logger(__name__)


async def handle_structured_query(
    task_id: str,
    payload: dict[str, Any],
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    date_utils: DateUtils,
) -> Task:
    """Handles structured queries for event instances, expanding recurring events."""
    logger.info(f"Handling structured query (using GetEventsInRange) for task {task_id}")

    result_message = ""

    try:
        # 1. Extract and Parse/Normalize Time Parameters from Payload
        # Payload keys might be start_time/end_time or start_date/end_date
        start_str = payload.get("start_time") or payload.get("start_date")
        end_str = payload.get("end_time") or payload.get("end_date")
        title_contains = payload.get("title_contains")

        # Use date_utils to parse potentially diverse formats and ensure UTC
        try:
            # Assume parse_nlp_date_range handles None inputs appropriately (uses defaults)
            aware_start_utc, aware_end_utc = date_utils.parse_nlp_date_range(
                start_date_str=start_str,
                end_date_str=end_str
            )
            logger.debug(f"Parsed A2A query range: Start={aware_start_utc}, End={aware_end_utc}")
        except InvalidInputError as pe:
            logger.warning(f"Invalid time format in payload for task {task_id}: {pe}")
            # Re-raise as InvalidParamsError for A2A context
            raise InvalidParamsError(f"Invalid start or end time format in payload: {pe}")

        # Create TimeRange DAO for the strategy
        time_range_dao = TimeRange(
            start_time=aware_start_utc,
            end_time=aware_end_utc
        )

        # 2. Call the GetEventsInRange strategy
        get_events_strategy = GetEventsInRangeStrategy(
            db_service=db_service,
            date_utils=date_utils
        )
        logger.info(f"Calling GetEventsInRangeStrategy for task {task_id}")
        # This returns a list of Event instances
        found_instances = await get_events_strategy.execute(data=time_range_dao)

        # 3. Apply Post-Filtering (title_contains)
        if title_contains and isinstance(title_contains, str):
            filtered_instances = [
                instance for instance in found_instances
                if title_contains.lower() in instance.title.lower()
            ]
            logger.info(f"A2A Query found {len(found_instances)} instances initially, filtered to {len(filtered_instances)} by title '{title_contains}'.")
        else:
            filtered_instances = found_instances
            logger.info(f"A2A Query found {len(found_instances)} instances.")

        # 4. Format the results (List of Event Instances)
        # Use model_dump for reliable serialization of Event model instances
        results_list = [evt.model_dump(mode="json") for evt in filtered_instances]
        
        result_message = json.dumps(results_list)
        # logger.info(f"Found {len(filtered_instances)} event instances for task {task_id}") # Covered above

        # 5. Complete the task
        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        return await task_manager.complete_task(task_id, agent_message)

    except (InvalidParamsError, ValidationError) as ipe:
        # Catch validation errors from DAO or manual checks
        logger.warning(f"InvalidParamsError/ValidationError processing structured query for task {task_id}: {ipe}")

        # Re-raise as InvalidParamsError for consistent A2A error handling
        error_data = getattr(ipe, 'errors', lambda: None)() 
        raise InvalidParamsError(message=str(ipe), data=error_data) from ipe 
    except Exception as e:
        logger.exception(f"Unexpected error processing structured query for task {task_id}: {e}")
        raise InternalError(message=f"An unexpected error occurred during query processing: {e}") from e