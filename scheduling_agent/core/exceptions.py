from typing import Any


class CoreLogicError(Exception):
    """Base class for core logic errors."""



class NLPError(CoreLogicError):
    """Base class for NLP processing errors."""



class LLMError(NLPError):
    """Specific error related to the LLM provider."""



class ConflictError(CoreLogicError):
    """Indicates a scheduling conflict was detected."""

    def __init__(self, message: str = "Scheduling conflict detected", conflicts: list[dict[str, Any]] | None = None):
        super().__init__(message)
        self.conflicts = conflicts if conflicts is not None else []


class ExporterError(CoreLogicError):
    """Raised when an error occurs during calendar exporting."""


class TaskNotFoundError(CoreLogicError):
    """Raised when a requested A2A Task cannot be found in the database."""
    def __init__(self, task_id: str):
        message = f"Task with ID '{task_id}' not found."
        super().__init__(message)
        self.task_id = task_id


class DatabaseError(Exception):
    """Base class for database-related errors."""



class EventNotFoundError(DatabaseError):
    """Raised when a specific event is not found."""

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"Event with ID '{event_id}' not found.")
