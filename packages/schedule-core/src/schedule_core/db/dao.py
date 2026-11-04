# sa/db/dao.py
# Copyright (C) 2025 Martin Bukowski

"""Data Access Objects (DAOs) for database operations."""

from datetime import datetime
from pydantic import BaseModel, Field, EmailStr
from sqlmodel import SQLModel
from typing import Union
import uuid

from schedule_core.db.models import EventBase, Event, ModifiedInstance
# No DTO imports needed here anymore

class EventGetById(BaseModel):
    """Parameters for getting an event by its ID."""

    id: uuid.UUID


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
    id: uuid.UUID
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


class GetA2ATaskById(BaseModel):
    """Parameters for getting an A2A task by its ID."""

    id: str # Changed from UUID to str


class TimeRange(BaseModel):
    """Parameters for a time range."""

    start_time: datetime
    end_time: datetime


class A2ATaskCreate(BaseModel):
    """Parameters for creating an initial A2A task record in the database.

    Contains fields corresponding to the A2ATask model, ready for persistence.
    """
    id: str
    session_id: str | None = None
    state: str  # Initial state enum value
    status_message_json: str | None = None # JSON string of initial TaskStatus.message
    status_timestamp: datetime | None = None # Timestamp of initial TaskStatus
    artifacts_json: str | None = None # JSON string of initial Task.artifacts (likely '[]' or null)
    history_json: str | None = None # JSON string of initial Task.history (e.g., '[initial_message]')
    metadata_json: str | None = None # JSON string of initial Task.metadata


class UpdateA2ATaskStatus(BaseModel):
    """Parameters for updating the status and potentially history of an A2A task.

    The Task Manager determines if history needs updating based on the status change.
    """
    id: str
    new_state: str # The new state value (e.g., "working", "completed")
    new_status_message_json: str | None = None # JSON string of the new TaskStatus.message
    new_status_timestamp: datetime | None = None # Timestamp of the new TaskStatus
    new_history_json: str | None = None

# --- Contact DAOs --- #

class ContactGetById(BaseModel):
    """DAO containing the ID of the contact to retrieve."""
    id: uuid.UUID

class ContactCreate(BaseModel):
    """DAO for creating a new contact."""
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None

class ContactRead(BaseModel):
    """DAO for reading contact data."""
    id: uuid.UUID
    name: str
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True} # Allow reading from ORM model

class ContactUpdate(SQLModel):
    """DAO for updating an existing contact. All fields are optional."""
    # Note: We don't include ID here; it's usually passed separately to the strategy/function.
    name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None

class ContactQuery(BaseModel):
    """DAO for querying contacts."""
    email_contains: str | None = None
    name_contains: str | None = None
    email: EmailStr | None = None

class ContactDelete(SQLModel):
    """DAO containing the ID of the contact to delete."""
    id: uuid.UUID
