# sa/api/dependencies.py
"""Dependencies for the scheduling agent API."""

from fastapi import Request

from sa.a2a.task_manager import SchedulingTaskManager
from sa.core.logging_config import get_logger
from sa.core.settings import Settings
from sa.db.service import DatabaseService
from sa.event.conflict_resolver import ConflictResolver
from sa.event.date_utils import DateUtils
from sa.event.exporters.base import CalendarExporter
from sa.nlp.llm_provider import LLMProvider
from sa.nlp.nlp_processor import NLPProcessor

logger = get_logger(__name__)

def get_db_service(request: Request) -> DatabaseService:
    """Dependency function to get the DatabaseService instance."""
    db_service = getattr(request.app.state, "db_service", None)
    if db_service is None:
        # This should ideally not happen if lifespan ran correctly
        logger.error("DatabaseService not found in app state!")
        raise RuntimeError("DatabaseService not initialized.")
    return db_service

def get_date_utils(request: Request) -> DateUtils:
    """Dependency function to get the DateUtils instance."""
    date_utils = getattr(request.app.state, "date_utils", None)
    if date_utils is None:
        logger.error("DateUtils not found in app state!")
        raise RuntimeError("DateUtils not initialized.")
    return date_utils

def get_llm_provider(request: Request) -> LLMProvider:
    """Dependency function to get the LLM provider."""
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

def get_exporters(request: Request) -> dict[str, CalendarExporter]:
    """Dependency function to get the available exporters."""
    exporters = getattr(request.app.state, "exporters", None)
    if exporters is None:
        logger.error("Exporters dictionary not found in app state!")
        raise RuntimeError("Exporters not initialized.")
    return exporters

def get_task_manager(request: Request) -> SchedulingTaskManager:
    """Dependency function to get the TaskManager instance from app state."""
    task_manager = getattr(request.app.state, "task_manager", None)
    if task_manager is None:
        logger.error("SchedulingTaskManager not found in app state!")
        raise RuntimeError("SchedulingTaskManager not initialized.")
    return task_manager

def get_conflict_resolver(request: Request) -> ConflictResolver:
    """Dependency function to get the ConflictResolver instance."""
    conflict_resolver = getattr(request.app.state, "conflict_resolver", None)
    if conflict_resolver is None:
        logger.error("ConflictResolver not found in app state!")
        raise RuntimeError("ConflictResolver not initialized.")
    return conflict_resolver

def get_settings(request: Request) -> Settings:
    """Dependency to get the application settings instance."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        logger.error("Settings not found in app state!")
        raise RuntimeError("Settings not initialized.")
    return settings
