from fastapi import Request  # Import Request for dependency

from scheduling_agent.core.conflict_resolver import ConflictResolver
from scheduling_agent.core.date_utils import DateUtils

# Import exporter types and instances
from scheduling_agent.core.exporters.base import CalendarExporter
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.core.llm_provider import LLMProvider
from scheduling_agent.core.nlp_processor import NLPProcessor
from scheduling_agent.db.service import DatabaseService
from scheduling_agent.logging_config import get_logger  # Import logger
from scheduling_agent.a2a.task_manager import SchedulingTaskManager

logger = get_logger(__name__)  # Initialize logger for this module

# TODO: Implement proper singleton/factory logic, potentially using app state or lifespan context.


# Placeholder instances - these should be managed properly (e.g., singletons)
# Ensure DatabaseService is initialized correctly, possibly using settings
def get_db_service(request: Request) -> DatabaseService:
    db_service = getattr(request.app.state, "db_service", None)
    if db_service is None:
        # This should ideally not happen if lifespan ran correctly
        logger.error("DatabaseService not found in app state!")
        raise RuntimeError("DatabaseService not initialized.")
    return db_service


def get_date_utils() -> DateUtils:
    """Provides an instance of DateUtils."""
    return DateUtils()


def get_llm_provider(request: Request) -> LLMProvider:
    llm_provider = getattr(request.app.state, "llm_provider", None)
    if llm_provider is None:
        logger.error("LLMProvider not found in app state!")
        raise RuntimeError("LLMProvider not initialized.")
    return llm_provider


def get_nlp_processor(request: Request) -> NLPProcessor:
    nlp_processor = getattr(request.app.state, "nlp_processor", None)
    if nlp_processor is None:
        logger.error("NLPProcessor not found in app state!")
        raise RuntimeError("NLPProcessor not initialized.")
    return nlp_processor


def get_conflict_resolver(request: Request) -> ConflictResolver:
    conflict_resolver = getattr(request.app.state, "conflict_resolver", None)
    if conflict_resolver is None:
        logger.error("ConflictResolver not found in app state!")
        raise RuntimeError("ConflictResolver not initialized.")
    return conflict_resolver


# Exporters (Phase 4, Task 2)
def get_exporters(request: Request) -> dict[str, CalendarExporter]:
    """Dependency function to get the available exporters."""
    date_utils = get_date_utils()  # Reuse date utils
    # TODO: Eventually configure which exporters are active via settings
    exporters = getattr(request.app.state, "exporters", None)
    if exporters is None:
        logger.error("Exporters dictionary not found in app state!")
        raise RuntimeError("Exporters not initialized.")
    return {
        "ics": ICalExporter(date_utils=date_utils),
        "org": OrgModeExporter(date_utils=date_utils),
    }


# Task Manager (Primary dependency for RPC)
def get_task_manager(request: Request) -> SchedulingTaskManager:
    """Dependency function to get the TaskManager instance from app state."""
    task_manager = getattr(request.app.state, "task_manager", None)
    if task_manager is None:
        logger.error("SchedulingTaskManager not found in app state!")
        raise RuntimeError("SchedulingTaskManager not initialized.")
    return task_manager
