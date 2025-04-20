import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Column, DateTime
from sqlmodel import Field, Relationship, SQLModel

# Use TYPE_CHECKING to avoid circular imports for relationship type hints
if TYPE_CHECKING:
    # This avoids runtime circular import errors
    from .models import ExcludedDate, ModifiedInstance, Participant

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
    location: str | None = Field(default=None)
    notes: str | None = Field(default=None)
    rrule: str | None = Field(default=None, index=True)


class Event(EventBase, table=True):
    """Database model for Events."""

    id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(
            DateTime,
            onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
            nullable=False,
        ),
    )

    # Relationships (define with Relationship helper)
    participants: list["Participant"] = Relationship(
        back_populates="event", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    excluded_dates: list["ExcludedDate"] = Relationship(
        back_populates="event", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
    modified_instances: list["ModifiedInstance"] = Relationship(
        back_populates="event", sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )


# TODO: Define Participant, ExcludedDate, ModifiedInstance models


class Participant(SQLModel, table=True):
    """Placeholder for Participant model."""

    id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: uuid.UUID = Field(foreign_key="event.id", index=True, nullable=False)
    identifier: str = Field(nullable=False)

    event: Event = Relationship(back_populates="participants")


class ExcludedDate(SQLModel, table=True):
    """Placeholder for ExcludedDate model."""

    id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: uuid.UUID = Field(foreign_key="event.id", index=True, nullable=False)
    excluded_date: datetime = Field(sa_column=Column(DateTime, nullable=False))

    event: Event = Relationship(back_populates="excluded_dates")


class ModifiedInstance(SQLModel, table=True):
    """Placeholder for ModifiedInstance model."""

    id: uuid.UUID | None = Field(default_factory=uuid.uuid4, primary_key=True)
    event_id: uuid.UUID = Field(foreign_key="event.id", index=True, nullable=False)
    original_date: datetime = Field(sa_column=Column(DateTime, nullable=False))
    start_time: datetime = Field(sa_column=Column(DateTime, nullable=False))
    end_time: datetime = Field(sa_column=Column(DateTime, nullable=False))
    time_zone: str = Field(nullable=False)
    title: str | None = None
    location: str | None = None
    notes: str | None = None
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(DateTime, nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(
            DateTime,
            onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
            nullable=False,
        ),
    )

    event: Event = Relationship(back_populates="modified_instances")


# --- API Specific Models (Optional) ---
# These have been moved to scheduling_agent/db/schemas.py
