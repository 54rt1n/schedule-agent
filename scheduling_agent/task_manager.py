import uuid
import asyncio
from datetime import datetime, timezone, timedelta, time
from enum import Enum
from typing import List, Dict, Optional, Any, AsyncIterable, Union

from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel, Field

from scheduling_agent.logging_config import get_logger

# Import necessary services and components
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.exporters.base import CalendarExporter # Base protocol
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.core.exceptions import ConflictError
# Import Event model for artifacts and EventCreate for validation
from scheduling_agent.db.models import Event, EventCreate
# Import DateUtils for placeholder parsing
from scheduling_agent.core.date_utils import DateUtils
# Import LLMSchedulingParseResult for type hinting
from scheduling_agent.core.schemas import LLMSchedulingParseResult

# Import the canonical A2A types
from .common.types import (
    TaskState,
    Part, # Might need FileContent too if used directly
    FileContent, # Import just in case
    Message,
    Artifact,
    TaskStatus,
    Task,
    TaskSendParams, # For on_send_task
    TaskGetParams, # For future on_get_task
    CancelTaskParams, # For future on_cancel_task
    JSONRPCResponse,
    # Import error types if TaskManager needs to create/return them
    TaskNotFoundError,
    TaskNotCancelableError,
    InternalError,
    InvalidParamsError
)

logger = get_logger(__name__)

# --- Task Manager Implementation ---

class SchedulingTaskManager:
    """Manages the lifecycle and storage of A2A tasks."""

    def __init__(
        self,
        session_factory: sessionmaker, # Now required due to DatabaseService change
        nlp_processor: NLPProcessor,
        db_service: DatabaseService,
        conflict_resolver: ConflictResolver,
        exporters: Optional[Dict[str, CalendarExporter]] = None
    ):
        """Initialize the task manager with dependencies and in-memory storage."""
        self.nlp_processor = nlp_processor
        self.db_service = db_service # Service now needs factory injected where TM is created
        self.conflict_resolver = conflict_resolver
        self.session_factory = session_factory # Store factory if needed directly, or ensure db_service has it
        # Add a basic DateUtils instance for placeholder parsing
        self.date_utils = DateUtils()

        # Default exporters if none provided
        if exporters is None:
            # TODO: DateUtils might need to be initialized/passed differently now
            date_utils = self.db_service.date_utils # Assuming db_service still has date_utils
            self.exporters = {
                "ics": ICalExporter(date_utils=date_utils),
                "org": OrgModeExporter(date_utils=date_utils)
            }
        else:
            self.exporters = exporters

        self._tasks: Dict[str, Task] = {}
        self._task_locks: Dict[str, asyncio.Lock] = {}
        logger.info("SchedulingTaskManager initialized with in-memory storage.")

    async def _get_task_lock(self, task_id: str) -> asyncio.Lock:
        """Gets or creates an asyncio lock for a given task ID."""
        if task_id not in self._task_locks:
            self._task_locks[task_id] = asyncio.Lock()
        return self._task_locks[task_id]

    async def store_task(self, task: Task):
        """Stores or updates a task in memory. Assumes lock is already held."""
        task.updatedAt = datetime.now(timezone.utc)
        self._tasks[task.id] = task
        logger.debug("Stored/Updated task", task_id=task.id, state=task.status.state)

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieves a task by ID."""
        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            logger.debug("Retrieved task", task_id=task_id, found=(task is not None))
            return task.model_copy(deep=True) if task else None # Return a copy

    # --- NEW State Management Methods (Phase 1, Task 2) ---

    async def create_task(self, params: TaskSendParams) -> Task:
        """
        Creates a new task, stores it in memory, and returns it.
        Initial state is set to WORKING.
        """
        task_id = params.id
        session_id = params.sessionId
        logger.info("Creating new task", task_id=task_id, session_id=session_id)

        async with await self._get_task_lock(task_id):
            if task_id in self._tasks:
                # Handle potential ID collision, though UUIDs make this unlikely
                logger.warning("Task ID collision detected", task_id=task_id)
                # Depending on requirements, might raise error or overwrite
                # For now, let's stick to the simple path for now and assume no collision.
                # raise ValueError(f"Task with ID {task_id} already exists.")
                pass # Or maybe just log and proceed to overwrite as store_task does?

            task = Task(
                id=task_id,
                sessionId=session_id,
                params=params, # Store the input parameters
                status=TaskStatus(state=TaskState.WORKING), # Initial state
                artifacts=[],
                history=[],
                createdAt=datetime.now(timezone.utc),
                updatedAt=datetime.now(timezone.utc)
            )
            # Use store_task to handle actual storage and locking
            await self.store_task(task)
            # Return a copy to prevent external modification
            return task.model_copy(deep=True)

    async def update_task_status(self, task_id: str, status: TaskStatus) -> Task:
        """
        Updates the status of a task, adds the old status to history, and returns the updated task.
        Raises TaskNotFoundError if the task doesn't exist.
        """
        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            if not task:
                logger.error("Task not found for status update", task_id=task_id)
                raise TaskNotFoundError(f"Task with ID {task_id} not found.")

            # Add previous status to history
            task.history.append(task.status.model_copy(deep=True))
            # Update status
            task.status = status
            # Update timestamp handled by store_task
            await self.store_task(task)
            logger.debug("Updated task status", task_id=task_id, new_state=status.state)
            return task.model_copy(deep=True)

    async def add_task_artifact(self, task_id: str, artifact: Artifact) -> Task:
        """
        Adds an artifact to a task and returns the updated task.
        Raises TaskNotFoundError if the task doesn't exist.
        """
        async with await self._get_task_lock(task_id):
            task = self._tasks.get(task_id)
            if not task:
                logger.error("Task not found for adding artifact", task_id=task_id)
                raise TaskNotFoundError(f"Task with ID {task_id} not found.")

            task.artifacts.append(artifact)
            # Update timestamp handled by store_task
            await self.store_task(task)
            logger.debug("Added artifact to task", task_id=task_id, artifact_name=artifact.name)
            return task.model_copy(deep=True)

    async def complete_task(self, task_id: str, message: Message, artifact: Optional[Artifact] = None) -> Task:
        """
        Sets task state to COMPLETED, adds message, optionally adds artifact.
        """
        logger.info("Completing task", task_id=task_id)
        # Update status first
        updated_task = await self.update_task_status(task_id, TaskStatus(state=TaskState.COMPLETED, message=message))

        # Add artifact if provided
        if artifact:
            updated_task = await self.add_task_artifact(task_id, artifact)

        return updated_task # Already a copy from add_task_artifact or update_task_status

    async def fail_task(self, task_id: str, message: Message) -> Task:
        """Sets task state to FAILED, adds message."""
        logger.error("Failing task", task_id=task_id, reason=message.parts[0].text if message.parts else "Unknown")
        # Update status
        updated_task = await self.update_task_status(task_id, TaskStatus(state=TaskState.FAILED, message=message))
        return updated_task # Already a copy

    async def require_input_task(self, task_id: str, message: Message, artifact: Optional[Artifact] = None) -> Task:
        """
        Sets task state to INPUT_REQUIRED, adds message, optionally adds artifact.
        """
        logger.info("Setting task to INPUT_REQUIRED", task_id=task_id)
        # Update status first
        updated_task = await self.update_task_status(task_id, TaskStatus(state=TaskState.INPUT_REQUIRED, message=message))

        # Add artifact if provided (e.g., options for input)
        if artifact:
            updated_task = await self.add_task_artifact(task_id, artifact)

        return updated_task # Already a copy

    # --- Placeholder Helper Methods --- #
    def _parse_date_range_from_nlp(self, nlp_result: LLMSchedulingParseResult) -> tuple[datetime, datetime]:
        """Placeholder: Parses start/end datetime from NLP result. Assumes UTC."""
        # Basic placeholder logic - replace with actual parsing
        now = datetime.now(timezone.utc)
        # Use parse_datetime (assume it returns aware datetime or handles timezone)
        start_date = self.date_utils.parse_datetime(nlp_result.date_info.start_date) if nlp_result.date_info.start_date else now
        end_date = self.date_utils.parse_datetime(nlp_result.date_info.end_date) if nlp_result.date_info.end_date else start_date

        # Parse time strings
        try:
            start_time_parsed = datetime.strptime(nlp_result.date_info.specific_time or "00:00:00", "%H:%M:%S").time()
        except ValueError:
            logger.warning("Invalid start time format, defaulting to 00:00:00", time_str=nlp_result.date_info.specific_time)
            start_time_parsed = time(0, 0, 0)

        try:
            end_time_parsed = datetime.strptime(nlp_result.date_info.end_time or "23:59:59", "%H:%M:%S").time()
        except ValueError:
            logger.warning("Invalid end time format, defaulting to 23:59:59", time_str=nlp_result.date_info.end_time)
            end_time_parsed = time(23, 59, 59)

        # Combine date (from potentially aware datetime) and time (naive)
        # Use date() to get the naive date part
        naive_start_dt = datetime.combine(start_date.date(), start_time_parsed)
        naive_end_dt = datetime.combine(end_date.date(), end_time_parsed)

        # Make the combined datetime timezone-aware (UTC)
        # Assume the original dates were intended to be UTC if naive, or keep original tz if aware?
        # For simplicity and based on docstring, let's make them UTC.
        start_dt = naive_start_dt.replace(tzinfo=timezone.utc)
        end_dt = naive_end_dt.replace(tzinfo=timezone.utc)

        # Ensure start_dt is not after end_dt
        if start_dt > end_dt:
            logger.warning("Parsed start_dt is after end_dt, swapping", start=start_dt, end=end_dt)
            start_dt, end_dt = end_dt, start_dt # Basic swap, might need refinement

        return start_dt, end_dt

    def _parse_export_format(self, nlp_result: LLMSchedulingParseResult) -> str:
        """Placeholder: Extracts export format from NLP result or defaults."""
        # TODO: Implement real format extraction (e.g., from text or filters)
        return "ics" # Default to ics for now

    # --- Placeholder methods for A2A JSON-RPC handlers ---

    async def on_send_task(self, request_id: Union[str, int], params: TaskSendParams) -> JSONRPCResponse:
        """Handles the tasks/send request."""
        task_id = params.id
        session_id = params.sessionId
        initial_message = params.message # Already validated as Message by Pydantic

        # NOTE: Task creation is now handled by the state method `create_task`,
        # called from the API handler. This initial creation logic is removed.

        try:
            input_text = next((part.text for part in initial_message.parts if part.type == 'text' and part.text), None)
            input_data = next((part.data for part in initial_message.parts if part.type == 'data'), None)

            final_status_enum = TaskState.FAILED # Default to FAILED
            result_message = "Could not process task." # Default message
            artifacts = [] # Initialize artifacts list
            agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)]) # Default message

            if input_text:
                # --- NLP Logic remains here for now --- #
                nlp_result = await self.nlp_processor.parse_natural_language_query(input_text)
                intent = nlp_result.intent

                if intent == "query_schedule":
                    # ... Query logic remains ...
                    start_time, end_time = self._parse_date_range_from_nlp(nlp_result)
                    events = await self.db_service.get_events_in_range(start=start_time, end=end_time)
                    if events:
                        result_message = f"Found events: {len(events)}"
                        # Define artifact only if events are found
                        artifacts = [Artifact(name="query_results", parts=[Part(type="data", data=[evt.model_dump() for evt in events])])]
                    else:
                        result_message = "No events found in the specified range."
                    final_status_enum = TaskState.COMPLETED # Completed successfully, even if no results
                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])

                elif intent == "export_calendar":
                    # ... Export logic remains ...
                    export_format = self._parse_export_format(nlp_result)
                    start_time, end_time = self._parse_date_range_from_nlp(nlp_result)

                    if export_format not in self.exporters:
                        final_status_enum = TaskState.FAILED
                        result_message = f"Unsupported export format: {export_format}"
                    else:
                        try:
                            events = await self.db_service.get_events_in_range(start=start_time, end=end_time)
                            exporter = self.exporters[export_format]
                            exported_data = exporter.export(events)
                            mime_type = exporter.get_mime_type()
                            file_name = f"calendar_export.{export_format}"

                            # Define artifact only on successful export
                            artifacts = [Artifact(
                                name=file_name,
                                description=f"Calendar export ({export_format})",
                                parts=[Part(type="file", file=FileContent(name=file_name, mimeType=mime_type, bytes=exported_data))] # Use FileContent
                            )]
                            final_status_enum = TaskState.COMPLETED
                            result_message = "Calendar exported successfully."
                        except Exception as export_err:
                            logger.error("Export failed", format=export_format, error=export_err)
                            final_status_enum = TaskState.FAILED
                            result_message = f"Failed to export calendar: {export_err}"

                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])

                else: # Unknown intent
                    # ... Unknown intent logic remains ...
                    final_status_enum = TaskState.FAILED
                    result_message = "Sorry, I didn't understand that request."
                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])

            elif input_data and isinstance(input_data, dict):
                # --- Structured Data Logic --- #
                if 'title' in input_data and ('start_time' in input_data or 'startTime' in input_data):
                    # --- Create Event Success Path REMOVED --- #
                    # --- Conflict Handling logic is being REMOVED --- #
                    try:
                         # Ensure EventCreate data is parsed before calling resolver (Needed for conflict check)
                         # This logic needs moving to the API handler's ConflictError block
                         event_create_data_for_conflict = EventCreate.model_validate(input_data)
                         # Check for conflict *before* attempting creation (or let db_service handle it)
                         # The actual creation attempt is moved to the handler.
                         # We assume the handler calls db_service which raises ConflictError if needed.
                         # So, this try block in TaskManager likely becomes redundant or just holds the ConflictError except.
                         pass # Placeholder - original success/conflict path was here
                    # except ConflictError as ce:
                    #     # --- Conflict Handling (REMOVED) --- #
                    #     try:
                    #         event_create_data_for_conflict = EventCreate.model_validate(input_data)
                    #         alternatives = await self.conflict_resolver.generate_alternative_times(event_create_data_for_conflict, ce.conflicts)
                    #         conflict_info = {
                    #             "message": str(ce),
                    #             "conflicts": [c["event"].model_dump() for c in ce.conflicts if isinstance(c.get("event"), Event)],
                    #             "alternatives": alternatives
                    #         }
                    #         artifacts = [Artifact(name="conflict_info", parts=[Part(type="data", data=conflict_info)])]
                    #         result_message = "Event creation failed due to conflicts. Please provide an alternative time or confirm override."
                    #     except Exception as alt_err:
                    #         logger.error("Failed to generate alternatives after conflict", error=alt_err)
                    #         result_message = f"Event creation failed due to conflicts, and generating alternatives also failed: {alt_err}"
                    #     final_status_enum = TaskState.INPUT_REQUIRED
                    #     agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
                    except Exception as e:
                        # --- Other Exception Handling (REMAINS) --- #
                        # This block now catches any error during the initial validate/pass, including potential validation errors.
                        # We need to refine this, as the main validation/creation is in the handler.
                        logger.error("Error during event data processing (non-conflict)", error=e)
                        final_status_enum = TaskState.FAILED
                        result_message = f"Failed to process event data: {e}"
                        agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
                else:
                    # ... Fallback structured data logic remains ...
                    final_status_enum = TaskState.FAILED
                    result_message = "Could not determine action from structured data."
                    agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])
            else:
                 # ... Fallback unsupported input logic remains ...
                 final_status_enum = TaskState.FAILED
                 result_message = "Unsupported input type."
                 agent_message = Message(role="agent", parts=[Part(type="text", text=result_message)])

            # --- Update Task Status & Return Logic REMOVED --- #
            # This is handled by state methods and the API handler's response creation.
            logger.warning("on_send_task reached end without returning (logic should be moved)")
            raise InternalError(message="TaskManager.on_send_task executed partially - refactoring incomplete.")

        except Exception as e:
             # ... Outer exception handling remains ...
            # This block needs careful review after all logic is moved.
            logger.error("Unexpected error processing task in TaskManager.on_send_task", task_id=task_id, error=e, exc_info=True)
            a2a_error = InternalError(message=f"Internal error processing task in TaskManager: {e}", data=str(e))
            # TaskManager shouldn't create the final JSONRPCResponse
            # Re-raise or return a specific error object?
            # For now, re-raise a standard exception or a BaseA1AError
            raise InternalError(f"Internal error processing task in TaskManager: {e}") from e

    async def on_get_task(self, request_id: Union[str, int], params: TaskGetParams) -> JSONRPCResponse:
        """Handles the tasks/get request."""
        task_id = params.id
        task = await self.get_task(task_id)
        if task:
             # Apply historyLength if needed (logic omitted for brevity)
             return JSONRPCResponse(id=request_id, result=task.model_dump())
        else:
            a2a_error = TaskNotFoundError()
            return JSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error())

    async def on_cancel_task(self, request_id: Union[str, int], params: CancelTaskParams) -> JSONRPCResponse:
        """Handles the tasks/cancel request."""
        task_id = params.id
        task = await self.get_task(task_id)
        if not task:
            a2a_error = TaskNotFoundError()
            return JSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error())
        
        # Check if task can be canceled
        if task.status.state in [TaskState.COMPLETED, TaskState.CANCELED, TaskState.FAILED]:
             a2a_error = TaskNotCancelableError()
             return JSONRPCResponse(id=request_id, error=a2a_error.as_jsonrpc_error())
            
        # Update task status using imported models
        cancel_message = Message(role="agent", parts=[Part(type="text", text="Task canceled by user.")])
        task.status = TaskStatus(state=TaskState.CANCELED, message=cancel_message)
        await self.store_task(task)
        return JSONRPCResponse(id=request_id, result=task.model_dump())

    # --- Add other handlers as needed (sendSubscribe, etc.) ---
