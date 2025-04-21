# Data Transfer Objects (DTOs) for database interactions

import uuid
from datetime import datetime

from sqlmodel import SQLModel

# Import EventBase from the models file as DTOs inherit from it
from .models import EventBase


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
