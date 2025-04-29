# sa/event/exporters/orgmode_exporter.py
# Copyright (C) 2025 Martin Bukowski

"""Org mode (.org) exporter for events."""

from typing import Any

from sa.event.date_utils import DateUtils
from sa.db.models import Event as DbEvent
from sa.core.logging_config import get_logger

from .base import CalendarExporter

logger = get_logger(__name__)


class OrgModeExporter(CalendarExporter):
    """Exports events to Emacs Org mode format."""

    def __init__(self, date_utils: DateUtils = None):
        self.date_utils = date_utils or DateUtils()

    def export(self, events: list[DbEvent], **kwargs: Any) -> str:
        """Exports a list of events to an Org mode formatted string.

        Args:
            events: A list of Event database objects.
            **kwargs: Optional arguments (e.g., heading_level).

        Returns:
            A string containing the calendar data in Org mode format.
        """
        org_entries = []
        heading_level = kwargs.get("heading_level", 1)
        heading_stars = "*" * heading_level

        # Sort events for chronological output
        events.sort(key=lambda e: e.start_time)

        for event in events:
            try:
                # Convert stored naive UTC to the event's local time zone
                start_local = self.date_utils.convert_from_utc(event.start_time, event.time_zone)
                end_local = self.date_utils.convert_from_utc(event.end_time, event.time_zone)

                # Format timestamp string: <YYYY-MM-DD Day HH:MM-HH:MM>
                # Org mode uses abbreviated day names (Mon, Tue, etc.)
                day_abbr = start_local.strftime("%a")
                time_format = "%Y-%m-%d"  # Date part
                time_str_start = start_local.strftime(f"{time_format} {day_abbr} %H:%M")
                # Include end date only if different from start date
                if start_local.date() == end_local.date():
                    time_str_end = end_local.strftime("%H:%M")
                else:
                    end_day_abbr = end_local.strftime("%a")
                    time_str_end = end_local.strftime(f"{time_format} {end_day_abbr} %H:%M")

                timestamp = f"<{time_str_start}-{time_str_end}>"

                # Start entry
                entry = f"{heading_stars} {event.title}\n"
                entry += f"  {timestamp}\n"

                # Add properties drawer
                entry += "  :PROPERTIES:\n"
                entry += f"  :EVENT_ID: {event.id}\n"
                if event.location:
                    entry += f"  :LOCATION: {event.location}\n"
                if event.time_zone:
                    entry += f"  :TIMEZONE: {event.time_zone}\n"
                # Add participants to properties if they exist
                if hasattr(event, "participants") and event.participants:
                    participant_str = ", ".join([p.identifier for p in event.participants])
                    entry += f"  :PARTICIPANTS: {participant_str}\n"
                if event.rrule:
                    entry += f"  :RRULE: {event.rrule}\n"
                entry += "  :END:\n"

                # Add notes/description
                if event.notes:
                    entry += f"\n{event.notes.strip()}\n"  # Add newline before notes

                org_entries.append(entry)

            except Exception:
                logger.exception(
                    f"Failed to export event {event.id} ('{event.title}') to Org mode"
                )

        return "\n".join(org_entries)  # Separate entries with a newline
