# sa/event/exporters/ical_exporter.py
"""iCalendar (.ics) exporter for events."""

from datetime import UTC, datetime
from typing import Any

from icalendar import Calendar, vRecur
from icalendar import Event as ICalEvent

from sa.event.date_utils import DateUtils
from sa.db.models import Event as DbEvent
from sa.core.logging_config import get_logger

from .base import CalendarExporter

logger = get_logger(__name__)


class ICalExporter(CalendarExporter):
    """Exports events to the iCalendar (.ics) format."""

    def __init__(self, date_utils: DateUtils = None):
        self.date_utils = date_utils or DateUtils()

    def get_mime_type(self) -> str:
        """Returns the MIME type for iCalendar files."""
        return "text/calendar"

    def export(self, events: list[DbEvent], **kwargs: Any) -> str:
        """Exports a list of events to an iCalendar (.ics) string.

        Args:
            events: A list of Event database objects.
            **kwargs: Optional arguments (e.g., product_id).

        Returns:
            A string containing the calendar data in iCalendar format.
        """
        cal = Calendar()
        cal.add("prodid", kwargs.get("product_id", "-//A2A Scheduling Agent//EN"))
        cal.add("version", "2.0")

        for event in events:
            try:
                ical_event = ICalEvent()
                ical_event.add("summary", event.title)
                ical_event.add("uid", f"{event.id}@scheduling-agent")  # Ensure unique UID

                # Ensure datetimes are timezone-aware for icalendar
                # Database stores naive UTC, convert to aware datetime in the event's timezone
                start_aware = self.date_utils.convert_from_utc(event.start_time, event.time_zone)
                end_aware = self.date_utils.convert_from_utc(event.end_time, event.time_zone)

                ical_event.add("dtstart", start_aware)
                ical_event.add("dtend", end_aware)
                ical_event.add(
                    "dtstamp", datetime.now(UTC)
                )  # Timestamp of export creation

                if event.location:
                    ical_event.add("location", event.location)
                if event.notes:
                    ical_event.add("description", event.notes)

                # Add participants if they exist on the event object
                if hasattr(event, "participants") and event.participants:
                    # Add attendees
                    for participant in event.participants:
                        ical_event.add("attendee", f"MAILTO:{participant.identifier}")
                        # Optional: Add more attendee properties like CN, ROLE, PARTSTAT

                # Add recurrence rule if present
                if event.rrule:
                    # iCalendar library expects RRULE properties as a dict
                    try:
                        rrule_dict = vRecur.from_ical(event.rrule)
                        ical_event.add("rrule", rrule_dict)
                        # TODO: Add EXDATE/RDATE handling if needed based on Event model
                    except ValueError as e:
                        logger.warning(
                            f"Failed to parse RRULE for event {event.id}: '{event.rrule}'. Error: {e}"
                        )

                cal.add_component(ical_event)
            except Exception:
                logger.exception(
                    f"Failed to export event {event.id} ('{event.title}') to iCal"
                )

        return cal.to_ical().decode("utf-8")
