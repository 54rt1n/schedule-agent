# Data Transfer Objects (DTOs) for database interactions 

import uuid
from datetime import datetime
from typing import List, Optional
from sqlmodel import SQLModel, Field

# Import EventBase from the models file as DTOs inherit from it
from .models import EventBase


# Model for reading event data via API
class EventRead(EventBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Add participant info if needed
    # participants: List[ParticipantRead] = []


# Model for creating events via API
class EventCreate(EventBase):
    # Add any specific creation fields, e.g., participant identifiers
    participant_identifiers: Optional[List[str]] = None


# Model for updating events via API (all fields optional)
class EventUpdate(SQLModel):
    title: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    time_zone: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    rrule: Optional[str] = None
    # TODO: How to handle participant updates? Add/remove lists?
    # participant_identifiers_add: Optional[List[str]] = None
    # participant_identifiers_remove: Optional[List[str]] = None 