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
    logger.info(f"Handling structured create for task {task_id}")
    try:
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
            raise InvalidParamsError(message="Could not determine create action from structured data provided.")

    except ConflictError as ce:
        logger.warning(f"Conflict detected during create: {ce}", task_id=task_id)
        # Similar to update, ask user for input
        # Extract conflict details safely
        conflict_details = []
        if isinstance(ce.data, list):
            for conflict in ce.data:
                event_data = conflict.get("event")
                if hasattr(event_data, 'model_dump'):
                    conflict_details.append(event_data.model_dump(mode="json"))
                elif isinstance(event_data, dict):
                    conflict_details.append(event_data)

        agent_message = Message(role="agent", parts=[Part(type="text", text="Could not create event due to scheduling conflicts. Please modify your request or cancel.")])
        artifact_data = {"conflicts": conflict_details}
        conflict_artifact = Artifact(name="conflict_info", parts=[Part(type="data", data=artifact_data)])

        try:
            return await task_manager.require_input_task(task_id, agent_message, conflict_artifact)
        except Exception as report_err:
            logger.exception(
                f"Error reporting conflict for task {task_id}: {report_err}",
                task_id=task_id
            )
            raise InternalError(message="Conflict detected, but an error occurred while reporting them.") from report_err

    except ValidationError as ve:
        logger.warning(f"Invalid data for event creation: {ve.errors()!s}", task_id=task_id)
        raise InvalidParamsError(message="Invalid data provided for event creation.", data=ve.errors()) from ve

    except InvalidParamsError as ipe:
        logger.warning(f"Invalid params error during structured create: {ipe}", task_id=task_id)
        raise ipe

    except Exception as e:
        logger.exception(f"Unexpected error during structured event creation: {e!s}", task_id=task_id)
        raise InternalError(message="An unexpected error occurred during event creation.") from e
