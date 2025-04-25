# sa/db/dao.py
"""Data Access Objects (DAOs) for database operations."""

from datetime import datetime
from pydantic import BaseModel, Field
from sqlmodel import SQLModel
from typing import Union
import uuid

from sa.db.models import EventBase, Event, ModifiedInstance


class EventQuery(BaseModel):
    """Parameters for querying events, often derived from NLP."""

    start_time: datetime | None = Field(None, description="Start time for the query range.")
    end_time: datetime | None = Field(None, description="End time for the query range.")
    title_contains: str | None = Field(
        None, description="Substring to search for in event titles."
    )
    # TODO: Add participants, location, etc. if needed for querying


# Model for reading event data via API
class EventRead(EventBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # TODO: Add participant info if needed


# Model for creating events via API
class EventCreate(EventBase):
    # Add any specific creation fields, e.g., participant identifiers
    participant_identifiers: list[str] | None = None


# Model for updating events via API (all fields optional)
class EventUpdate(SQLModel):
    title: str | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    time_zone: str | None = None
    location: str | None = None
    notes: str | None = None
    rrule: str | None = None
    # TODO: How to handle participant updates? Add/remove lists?
    # Field to indicate modification of a specific recurring instance
    original_start_time: datetime | None = None


# Model for deleting events via API (structured delete)
class EventDelete(SQLModel):
    id: uuid.UUID


EventResponse = Union[Event, ModifiedInstance]
TimeRange = tuple[datetime, datetime]

class TaskStatus(BaseModel):
    """Status of an A2A Task."""

    status_message: str | None = None