# sa/db/models/contact.py
# Copyright (C) 2025 Martin Bukowski

"""Models for the Contact table."""

import uuid
from datetime import datetime, UTC

from sqlmodel import Field, SQLModel


# Define the default factory for created_at using naive UTC timestamp
# Consistent with Alembic's handling of server_default
def default_created_at():
    return datetime.now(UTC).replace(tzinfo=None)

# Define the default factory for updated_at using naive UTC timestamp
def default_updated_at():
     return datetime.now(UTC).replace(tzinfo=None)


class Contact(SQLModel, table=True):
    """Represents a contact entry."""

    id: uuid.UUID | None = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )
    name: str = Field(..., index=True, nullable=False)
    email: str | None = Field(default=None, index=True, unique=True)
    phone: str | None = Field(default=None)
    notes: str | None = Field(default=None)

    created_at: datetime = Field(
        default_factory=default_created_at,
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=default_updated_at,
        nullable=False,
    )
