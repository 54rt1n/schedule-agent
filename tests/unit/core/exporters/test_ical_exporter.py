from datetime import UTC, datetime

import pytest
from icalendar import Calendar

from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.db.models import Event


class TestICalExporter:
    @pytest.fixture
    def date_utils(self):
        return DateUtils()

    @pytest.fixture
    def exporter(self, date_utils):
        return ICalExporter(date_utils=date_utils)

    def test_get_mime_type(self, exporter):
        """Test that the correct MIME type is returned."""
        assert exporter.get_mime_type() == "text/calendar"

    def test_export_single_event_no_tz(self, exporter, date_utils):
        """Test exporting a single simple event with UTC times."""
        event = Event(
            id="evt_1",
            title="Simple Event",
            start_time=datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC),
            end_time=datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC),
            is_recurring=False,
            notes="Some notes",
            location="Office",
            participants=[],
            rrule=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

        ics_data = exporter.export([event])
        assert isinstance(ics_data, str)

        # Basic validation: check if it's a valid iCal string
        try:
            cal = Calendar.from_ical(ics_data)
        except ValueError as e:
            pytest.fail(f"Exported data is not valid iCalendar format: {e}")

        assert len(cal.walk("VEVENT")) == 1
        exported_event = cal.walk("VEVENT")[0]

        assert exported_event.get("summary") == "Simple Event"
        assert exported_event.get("dtstart").dt == datetime(
            2024, 1, 1, 10, 0, 0, tzinfo=UTC
        )
        assert exported_event.get("dtend").dt == datetime(2024, 1, 1, 11, 0, 0, tzinfo=UTC)
        assert exported_event.get("uid") == "evt_1@scheduling-agent"
        assert exported_event.get("description") == "Some notes"
        assert exported_event.get("location") == "Office"
        assert "attendee" not in exported_event
        assert "rrule" not in exported_event

    # TODO: Add more tests:
    # - Event with timezone
    # - Event with participants
    # - Recurring event (RRULE)
    # - All-day event (DTSTART/DTEND as DATE)
    # - Multiple events
    # - Edge cases (e.g., missing optional fields)
