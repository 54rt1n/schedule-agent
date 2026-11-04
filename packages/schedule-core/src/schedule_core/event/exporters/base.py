# sa/event/exporters/base.py
# Copyright (C) 2025 Martin Bukowski

"""Base class for calendar exporters."""

from typing import Any, Protocol

from schedule_core.db.models import Event


class CalendarExporter(Protocol):
    """Defines the interface for calendar export formats."""

    def export(self, events: list[Event], **kwargs: Any) -> str:
        """Exports a list of events to a specific calendar format string.

        Args:
            events: A list of Event objects to export.
            **kwargs: Additional format-specific options.

        Returns:
            A string containing the calendar data in the target format.
        """
        ...
