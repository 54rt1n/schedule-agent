from pydantic import ValidationError

from scheduling_agent.a2a.task_manager import SchedulingTaskManager
from scheduling_agent.a2a.types import (
    Artifact,
    InternalError,
    Message,
    Part,
    Task,
)
from scheduling_agent.core.exceptions import DatabaseError
from scheduling_agent.core.schemas import LLMSchedulingParseResult
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.db.schemas import EventQuery
from scheduling_agent.logging_config import get_logger

logger = get_logger(__name__)

async def handle_nlp_query(
    task_id: str,
    nlp_result: LLMSchedulingParseResult,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
) -> Task:
    """Handles the query_events intent from NLP result."""
    entities = nlp_result.filters  # Assuming filters contain query params
    try:
        # Map NLP entities to EventQuery fields
        query_data = {
            "start_time": entities.start_time if hasattr(entities, "start_time") else None,
            "end_time": entities.end_time if hasattr(entities, "end_time") else None,
            "title_contains": entities.title if hasattr(entities, "title") else None,
        }
        query_data = {k: v for k, v in query_data.items() if v is not None}
        event_query = EventQuery.model_validate(query_data)

        # ---- Start Edit: Catch DatabaseError ----
        try:
            queried_events = await db_service.find_events(query=event_query)
        except DatabaseError as dbe:
            logger.exception("Database error during event query", task_id=task_id, error=dbe)
            # Raise InternalError so it's caught by the main handler's generic exception block
            raise InternalError("An internal error occurred while querying the database.") from dbe
        # ---- End Edit ----

        if queried_events:
            result_message = f"Found {len(queried_events)} event(s) matching your query."
            events_data = [event.model_dump(mode="json") for event in queried_events]
            artifact = Artifact(name="queried_events", parts=[Part(type="data", data=events_data)])
        else:
            result_message = "No events found matching your query."
            artifact = None

        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
        final_task = await task_manager.complete_task(task_id, agent_message, artifact)
        return final_task

    except ValidationError as ve:
        logger.exception(
            "Invalid query parameters extracted from NLP",
            task_id=task_id,
            validation_error=ve.errors(),
            extracted_entities=entities,
        )
        clarification_msg = f"I understood you wanted to query events, but I couldn't understand the details. Could you please rephrase your query? (Details: {ve.errors()})"
        agent_message = Message(role="agent", parts=[Part(type="text", text=clarification_msg)])
        final_task = await task_manager.require_input_task(task_id, agent_message)
        return final_task
    # Let other errors propagate to the main NLP handler (_handle_send_nlp_input)
