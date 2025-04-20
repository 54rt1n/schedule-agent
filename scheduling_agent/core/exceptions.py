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
