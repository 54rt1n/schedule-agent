# sa/a2a/command/nlp_request.py
"""Handles the query_events intent from NLP result."""

from pydantic import ValidationError

from sa.a2a.task_manager import SchedulingTaskManager
from sa.a2a.dto import Message, Part, Task
from sa.core.logging_config import get_logger
from sa.db.service import DatabaseService
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.event.conflict_resolver import ConflictResolver
from sa.nlp.nlp_processor import NLPProcessor
from .structured_export import handle_structured_export

logger = get_logger(__name__)


async def handle_nlp_request(
    task_id: str,
    message: Message,
    task_manager: SchedulingTaskManager,
    db_service: DatabaseService,
    nlp_processor: NLPProcessor,
    exporters: dict[str, CalendarExporter],
    date_utils: DateUtils,
    conflict_resolver: ConflictResolver,
) -> Task:
    """Handles raw text input from user, determines intent via NLP, and routes to the appropriate command handler."""
    # 1. Extract text query
    if not message.parts or message.parts[0].type != "text" or not message.parts[0].text:
        logger.warning(f"Invalid message format for NLP processing in task {task_id}. Expected text part.")
        # Use fail_task to report the error associated with the task
        # Role must be 'user' or 'agent'
        error_message = Message(role="agent", parts=[Part(type="text", text="Invalid input: Expected a text query.")])
        return await task_manager.fail_task(task_id=task_id, message=error_message)
        # Alternatively, raise InvalidParamsError, but failing the task might be more informative
        # raise InvalidParamsError("Invalid message: Expected a non-empty text part.")

    query = message.parts[0].text
    logger.info(f"Processing NLP query for task {task_id}: '{query[:50]}...'")

    # 2. Call NLP Processor
    try:
        nlp_result = await nlp_processor.parse_natural_language_query(query)
        nlp_result.original_query = query # Store original query for context
    except Exception as e:
        logger.exception(f"NLP processor failed for task {task_id}", exc_info=e)
        # Role must be 'user' or 'agent'
        error_message = Message(role="agent", parts=[Part(type="text", text=f"Error processing query: {e}")])
        return await task_manager.fail_task(task_id=task_id, message=error_message)

    logger.info(f"NLP result for task {task_id}: intent='{nlp_result.intent}', confidence={nlp_result.confidence:.2f}")

    # 3. Route based on intent
    # Add more intent routing as needed
    if nlp_result.intent == "export_calendar":
        logger.debug(f"Routing task {task_id} to handle_structured_export")
        # Construct the data part expected by handle_structured_export
        export_data_part = Part(
            type="data",
            data={
                "date_info": nlp_result.date_info.model_dump() if nlp_result.date_info else {},
                "format": nlp_result.filters.format if nlp_result.filters else None,
                # Add other relevant filters if handle_structured_export uses them
            }
        )
        # Create a new message with this data part
        export_message = Message(role="agent", parts=[export_data_part]) # Role must be user or agent

        return await handle_structured_export(
            task_id=task_id,
            message=export_message, # Pass the constructed message
            task_manager=task_manager,
            db_service=db_service,
            exporters=exporters,
            date_utils=date_utils,
        )
    # Example: elif nlp_result.intent == "query_events":
    #     logger.debug(f"Routing task {task_id} to handle_structured_query_from_nlp")
    #     # Assuming a similar handler exists or will be created
    #     # return await handle_structured_query_from_nlp(...)
    # Add other intents like create_event, delete_event, update_event etc.

    else:
        # Fallback for unknown or unhandled intents
        logger.warning(f"Unhandled NLP intent '{nlp_result.intent}' for task {task_id}")
        # Decide how to handle: fail task, ask for clarification, etc.
        # Role must be 'user' or 'agent'
        fallback_message = Message(
            role="agent",
            parts=[Part(type="text", text=f"Sorry, I didn't understand that request (intent: {nlp_result.intent}). Could you please rephrase?")]
        )
        # Potentially use require_input_task if clarification is desired
        return await task_manager.fail_task(task_id=task_id, message=fallback_message)