from typing import List, Optional

class ConflictError(Exception):
    """Custom exception for scheduling conflicts."""
    def __init__(self, message="Scheduling conflict detected", conflicts: List[dict] = None):
        self.message = message
        self.conflicts = conflicts or []
        super().__init__(self.message)

# Add other core exceptions here as needed
# class ValidationError(Exception): ...
# class NLPError(Exception): ... 