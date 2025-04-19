from typing import List, Optional, Dict, Any

class CoreLogicError(Exception):
    """Base class for core logic errors."""
    pass

class NLPError(CoreLogicError):
    """Base class for NLP processing errors."""
    pass

class LLMError(NLPError):
    """Specific error related to the LLM provider."""
    pass

class ConflictError(CoreLogicError):
    """Custom exception for scheduling conflicts."""
    def __init__(self, message="Scheduling conflict detected", conflicts: Optional[List[Dict[str, Any]]] = None):
        self.conflicts = conflicts or []
        super().__init__(message)

class ExporterError(CoreLogicError):
    """Raised when an error occurs during calendar exporting."""
    pass

# Add other core-related exceptions as needed
# class ValidationError(CoreLogicError): ...

# Add other core exceptions here as needed
# class ValidationError(Exception): ...
# class NLPError(Exception): ... 