from functools import lru_cache
from unittest.mock import MagicMock, AsyncMock
from fastapi import Request # Import Request for dependency
from scheduling_agent.task_manager import SchedulingTaskManager
from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.core.llm_provider import LLMProvider
from scheduling_agent.db.service import DatabaseService
# Import exporter types and instances
from scheduling_agent.core.exporters.base import CalendarExporter
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.common.types import Task, TaskStatus, TaskState, TaskSendParams, Message, Artifact
import uuid
from typing import Optional, Dict

# TODO: Implement proper singleton/factory logic, potentially using app state or lifespan context.

# Placeholder instances - these should be managed properly (e.g., singletons)
# Ensure DatabaseService is initialized correctly, possibly using settings
@lru_cache()
def get_db_service() -> DatabaseService:
    # TODO: Get DB URL from settings/environment
    return DatabaseService("schedule_agent.db")

@lru_cache()
def get_date_utils() -> DateUtils:
    return DateUtils()

@lru_cache()
def get_llm_provider() -> LLMProvider:
    # TODO: Configure from settings/environment
    return LLMProvider()

@lru_cache()
def get_nlp_processor() -> NLPProcessor:
    return NLPProcessor(date_utils=get_date_utils(), llm_provider=get_llm_provider())

@lru_cache()
def get_conflict_resolver() -> ConflictResolver:
    return ConflictResolver(db_service=get_db_service(), date_utils=get_date_utils())

# Exporters (Phase 4, Task 2)
@lru_cache()
def get_exporters() -> Dict[str, CalendarExporter]:
    """Dependency function to get the available exporters."""
    date_utils = get_date_utils() # Reuse date utils
    # TODO: Eventually configure which exporters are active via settings
    return {
        "ics": ICalExporter(date_utils=date_utils),
        "org": OrgModeExporter(date_utils=date_utils)
    }

# Task Manager (Primary dependency for RPC)
# Remove @lru_cache and mock implementation
# @lru_cache()
def get_task_manager(request: Request) -> SchedulingTaskManager:
    """Dependency function to get the TaskManager instance from app state."""
    # Retrieve the singleton instance created during lifespan startup
    task_manager = getattr(request.app.state, "task_manager", None)
    if task_manager is None:
        # This should ideally not happen if lifespan context runs correctly
        print("ERROR: TaskManager instance not found in app state!") # Use logger instead
        raise RuntimeError("TaskManager not initialized. Check application lifespan.")
    return task_manager

    # --- REMOVED MOCK IMPLEMENTATION ---
    # print("WARNING: Returning mock Task Manager for dependency testing!")
    # # Use AsyncMock and configure async methods to return awaitable Task objects
    # mock = AsyncMock(spec=SchedulingTaskManager)
    # ... (rest of mock code removed) ...
    # return mock
    # raise NotImplementedError("TaskManager singleton/factory not yet implemented.") 