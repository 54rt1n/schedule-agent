# sa/core/exceptions.py
"""Core exceptions for the scheduling agent."""

from typing import Any


class CoreLogicError(Exception):
    """Base class for core logic errors."""
    def __init__(self, message: str, data: Any | None = None):
        self.message = message
        self.data = data


class NLPError(CoreLogicError):
    """Base class for NLP processing errors."""
    def __init__(self, message: str, data: Any | None = None):
        super().__init__(message, data)


class LLMError(NLPError):
    """Specific error related to the LLM provider."""
    def __init__(self, message: str, data: Any | None = None):
        super().__init__(message, data)


class DatabaseError(Exception):
    """Base class for database-related errors."""
    def __init__(self, message: str, data: Any | None = None):
        self.message = message
        self.data = data


class ConflictError(CoreLogicError):
    """Indicates a scheduling conflict was detected."""

    def __init__(self, message: str = "Scheduling conflict detected", conflicts: list[dict[str, Any]] | None = None):
        super().__init__(message, conflicts)


class ExporterError(CoreLogicError):
    """Raised when an error occurs during calendar exporting."""
    def __init__(self, message: str = "Error exporting calendar", data: Any | None = None):
        super().__init__(message, data)


class EventNotFoundError(DatabaseError):
    """Raised when a specific event is not found."""

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"Event with ID '{event_id}' not found.")

class TaskNotFoundError(DatabaseError):
    """Raised when a specific task is not found."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"Task with ID '{task_id}' not found.")
