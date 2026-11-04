# sa/db/models/event.py
# Copyright (C) 2025 Martin Bukowski

"""Models for the Event."""

from datetime import UTC, datetime
from sqlalchemy import Column, DateTime
from sqlmodel import Field, Relationship, SQLModel
import uuid

NaiveDateTime = Column(DateTime)


class EventBase(SQLModel):
    """Base model for event data (shared properties)."""
    title: str = Field(index=True)
    start_time: datetime = Field(sa_column=Column(DateTime, index=True, nullable=False))
    end_time: datetime = Field(sa_column=Column(DateTime, index=True, nullable=False))
    time_zone: str = Field(default="UTC")
    location: str | None = Field(default=None)
    notes: str | None = Field(default=None)
    rrule: str | None = Field(default=None, index=True)

    calendar_id: str = Field(default="primary")
    description: str | None = Field(default=None)
    deleted: bool = Field(default=False)


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
