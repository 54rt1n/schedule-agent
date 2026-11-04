"""Validation for generated local commitments before conversion to an instant."""

from datetime import UTC


def require_existing_time(value):
    restored = value.astimezone(UTC).astimezone(value.tzinfo)
    if restored.replace(tzinfo=None) != value.replace(tzinfo=None):
        raise ValueError(f"Recurrence produces a nonexistent local time: {value.isoformat()}")
