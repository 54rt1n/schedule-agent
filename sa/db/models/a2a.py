# sa/db/models/a2a.py
"""Models for the A2A Task."""

from datetime import UTC, datetime
from sqlalchemy import Column, DateTime, Text
from sqlmodel import Field, SQLModel

# --- A2A Task Persistence ---

class A2ATask(SQLModel, table=True):
    """Database model for storing the state of an A2A Task."""

    __tablename__ = "a2a_task"  # Explicit table name

    id: str = Field(primary_key=True, index=True)
    session_id: str | None = Field(default=None, index=True)
    # Current state from Task.status.state
    state: str = Field(index=True)
    # Optional message from Task.status.message, stored as JSON
    status_message_json: str | None = Field(default=None, sa_column=Column(Text))
    # Optional timestamp from Task.status.timestamp
    status_timestamp: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=False)))
    # Optional list of artifacts from Task.artifacts, stored as JSON
    artifacts_json: str | None = Field(default=None, sa_column=Column(Text))
    # Optional list of messages from Task.history, stored as JSON
    history_json: str | None = Field(default=None, sa_column=Column(Text))
    # Optional metadata from Task.metadata, stored as JSON
    metadata_json: str | None = Field(default=None, sa_column=Column(Text))

    # Internal timestamps for record tracking
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(DateTime(timezone=False), nullable=False),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC).replace(tzinfo=None),
        sa_column=Column(
            DateTime(timezone=False),
            onupdate=lambda: datetime.now(UTC).replace(tzinfo=None),
            nullable=False,
        ),
    )
