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
    state: str = Field(index=True)
    # Store complex objects as JSON strings
    # Use Text for better compatibility with potentially large JSON strings across DBs
    status_message_json: str | None = Field(default=None, sa_column=Column(Text))
    params_json: str | None = Field(default=None, sa_column=Column(Text))
    artifacts_json: str | None = Field(default=None, sa_column=Column(Text))
    history_json: str | None = Field(default=None, sa_column=Column(Text))
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
