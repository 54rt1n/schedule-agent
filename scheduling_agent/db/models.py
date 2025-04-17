import uuid
from datetime import datetime, timezone
from typing import List, Optional, TYPE_CHECKING

from sqlalchemy import DateTime, Column
from sqlmodel import Field, Relationship, SQLModel

# Use TYPE_CHECKING to avoid circular imports for relationship type hints
if TYPE_CHECKING:
    # This avoids runtime circular import errors
    from .models import Participant, ExcludedDate, ModifiedInstance

# Define a reusable column type for naive datetimes (assumed UTC in application logic)
NaiveDateTime = Column(DateTime)

class EventBase(SQLModel):
    """Base model for event data (shared properties)."""
    title: str = Field(index=True)
    # Store naive datetimes, assumed to be UTC by application convention
    start_time: datetime = Field(sa_column=Column(DateTime, index=True, nullable=False))
    end_time: datetime = Field(sa_column=Column(DateTime, index=True, nullable=False))
    # Store the IANA timezone identifier associated with the original event time
    time_zone: str = Field(default="UTC")
    location: Optional[str] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    rrule: Optional[str] = Field(default=None, index=True)

class Event(EventBase, table=True):
    """Database model for Events."""
    id: Optional[uuid.UUID] = Field(
        default_factory=uuid.uuid4, primary_key=True, index=True
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        sa_column=Column(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        sa_column=Column(
            DateTime,
            onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
            nullable=False,
        ),
    )

    # Relationships (define with Relationship helper)
    participants: List["Participant"] = Relationship(back_populates="event", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    excluded_dates: List["ExcludedDate"] = Relationship(back_populates="event", sa_relationship_kwargs={"cascade": "all, delete-orphan"})
    modified_instances: List["ModifiedInstance"] = Relationship(back_populates="event", sa_relationship_kwargs={"cascade": "all, delete-orphan"})

# TODO: Define Participant, ExcludedDate, ModifiedInstance models

class Participant(SQLModel, table=True):
    """Placeholder for Participant model."""
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: uuid.UUID = Field(foreign_key="event.id", index=True, nullable=False)
    identifier: str = Field(nullable=False)

    event: Event = Relationship(back_populates="participants")

class ExcludedDate(SQLModel, table=True):
    """Placeholder for ExcludedDate model."""
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: uuid.UUID = Field(foreign_key="event.id", index=True, nullable=False)
    excluded_date: datetime = Field(sa_column=Column(DateTime, nullable=False))

    event: Event = Relationship(back_populates="excluded_dates")

class ModifiedInstance(SQLModel, table=True):
    """Placeholder for ModifiedInstance model."""
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: uuid.UUID = Field(foreign_key="event.id", index=True, nullable=False)
    original_date: datetime = Field(sa_column=Column(DateTime, nullable=False))
    start_time: datetime = Field(sa_column=Column(DateTime, nullable=False))
    end_time: datetime = Field(sa_column=Column(DateTime, nullable=False))
    time_zone: str = Field(nullable=False)
    title: Optional[str] = None
    location: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        sa_column=Column(DateTime, nullable=False)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
        sa_column=Column(
            DateTime,
            onupdate=lambda: datetime.now(timezone.utc).replace(tzinfo=None),
            nullable=False,
        ),
    )

    event: Event = Relationship(back_populates="modified_instances")

# --- API Specific Models (Optional) ---
# Example: Model for reading event data via API
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
    # How to handle participant updates? Add/remove lists?
    # participant_identifiers_add: Optional[List[str]] = None
    # participant_identifiers_remove: Optional[List[str]] = None

# Add Read/Create/Update models if needed for API layer later
# class EventRead(EventBase):
#     id: uuid.UUID
#     created_at: datetime
#     updated_at: datetime

# class EventCreate(EventBase):
#     pass 