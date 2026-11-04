# packages/schedule-a2a/src/schedule_a2a/command/nlp_request.py
# Copyright (C) 2025 Martin Bukowski

"""Translate text into the structured request envelope or request clarification."""

import json

from schedule_core.core.logging_config import get_logger
from schedule_core.db.service import DatabaseService
from schedule_core.event.conflict_resolver import ConflictResolver
from schedule_core.event.date_utils import DateUtils
from schedule_core.event.exporters.base import CalendarExporter
from schedule_nlp.nlp_processor import NLPProcessor

from schedule_a2a.dto import Message, Part, StructuredPayloadWrapper, Task
from schedule_a2a.task_manager import SchedulingTaskManager

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
) -> Task | StructuredPayloadWrapper:
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
        task = await task_manager.get_task(task_id)
        if task.history and len(task.history) > 1:
            query = json.dumps([entry.model_dump(mode="json") for entry in task.history])
        nlp_result = await nlp_processor.parse_natural_language_query(query)
    except Exception as e:
        logger.exception(f"NLP processor failed for task {task_id}", exc_info=e)
        # Role must be 'user' or 'agent'
        error_message = Message(role="agent", parts=[Part(type="text", text=f"Error processing query: {e}")])
        return await task_manager.fail_task(task_id=task_id, message=error_message)

    if nlp_result.intent == "clarify":
        question = nlp_result.payload.get("message") or "Please provide the missing scheduling details."
        return await task_manager.require_input_task(
            task_id, Message(role="agent", parts=[Part(type="text", text=question)])
        )
    if nlp_result.intent == "unknown":
        return await task_manager.fail_task(
            task_id, Message(role="agent", parts=[Part(type="text", text="Sorry, I didn't understand that request. Could you please rephrase?")])
        )
    return StructuredPayloadWrapper(intent=nlp_result.intent, payload=nlp_result.payload)
