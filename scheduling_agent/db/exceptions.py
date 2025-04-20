"""Custom exceptions related to Database operations."""


class DatabaseError(Exception):
    """Base class for database-related errors."""



class EventNotFoundError(DatabaseError):
    """Raised when a specific event is not found."""

    def __init__(self, event_id: str):
        self.event_id = event_id
        super().__init__(f"Event with ID '{event_id}' not found.")


# Add more specific DB errors as needed (e.g., ConnectionError, QueryError)
