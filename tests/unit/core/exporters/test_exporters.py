import uuid
from datetime import datetime, timedelta

import pytest

# Import Calendar for parsing back the result
from icalendar import Calendar

from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.core.exporters.ical_exporter import ICalExporter
from scheduling_agent.core.exporters.orgmode_exporter import OrgModeExporter
from scheduling_agent.db.models import Event, Participant

# --- Fixtures ---


@pytest.fixture(scope="module")
def date_utils() -> DateUtils:
    """Provides a real DateUtils instance."""
    return DateUtils()


@pytest.fixture
def ical_exporter(date_utils: DateUtils) -> ICalExporter:
    """Provides an ICalExporter instance."""
    return ICalExporter(date_utils=date_utils)


@pytest.fixture
def orgmode_exporter(date_utils: DateUtils) -> OrgModeExporter:
    """Provides an OrgModeExporter instance."""
    return OrgModeExporter(date_utils=date_utils)


@pytest.fixture
def sample_event_simple() -> Event:
    """Provides a simple sample Event object (naive UTC)."""
    return Event(
        id=uuid.uuid4(),
        title="Simple Meeting",
        start_time=datetime(2024, 7, 1, 10, 0, 0),  # Stored as naive UTC
        end_time=datetime(2024, 7, 1, 11, 0, 0),
        time_zone="America/New_York",
        created_at=datetime(2024, 6, 30, 12, 0, 0),  # Naive UTC
        updated_at=datetime(2024, 6, 30, 12, 5, 0),  # Naive UTC
    )


@pytest.fixture
def sample_event_detailed() -> Event:
    """Provides a more detailed sample Event object (naive UTC) with participants."""
    event_id = uuid.uuid4()
    participants_data = [
        Participant(id=uuid.uuid4(), event_id=event_id, identifier="alice@example.com"),
        Participant(id=uuid.uuid4(), event_id=event_id, identifier="bob@test.org"),
    ]
    return Event(
        id=event_id,
        title="Detailed Project Sync",
        start_time=datetime(2024, 7, 2, 14, 30, 0),
        end_time=datetime(2024, 7, 2, 15, 30, 0),
        time_zone="Europe/Berlin",
        location="Conference Room B",
        notes="Discuss phase 2 deliverables.\nBring coffee.",
        rrule="FREQ=WEEKLY;BYDAY=TU;COUNT=4",
        created_at=datetime(2024, 7, 1, 8, 0, 0),
        updated_at=datetime(2024, 7, 1, 9, 15, 0),
        participants=participants_data,
    )


@pytest.fixture
def sample_event_midnight_span() -> Event:
    """Provides an event spanning midnight (naive UTC)."""
    return Event(
        id=uuid.uuid4(),
        title="Late Shift",
        start_time=datetime(2024, 7, 3, 22, 0, 0),  # 10 PM UTC
        end_time=datetime(2024, 7, 4, 2, 0, 0),  # 2 AM UTC next day
        time_zone="UTC",  # Keep it simple with UTC
        created_at=datetime(2024, 7, 3, 12, 0, 0),
        updated_at=datetime(2024, 7, 3, 12, 5, 0),
    )


@pytest.fixture
def sample_event_same_day_1() -> Event:
    """Provides the first event for the same-day test (naive UTC)."""
    return Event(
        id=uuid.uuid4(),
        title="Morning Standup",
        start_time=datetime(2024, 7, 5, 9, 0, 0),
        end_time=datetime(2024, 7, 5, 9, 30, 0),
        time_zone="America/Los_Angeles",
        created_at=datetime(2024, 7, 5, 1, 0, 0),
        updated_at=datetime(2024, 7, 5, 1, 5, 0),
    )


@pytest.fixture
def sample_event_same_day_2() -> Event:
    """Provides the second event for the same-day test (naive UTC)."""
    return Event(
        id=uuid.uuid4(),
        title="Afternoon Sync",
        start_time=datetime(2024, 7, 5, 15, 0, 0),
        end_time=datetime(2024, 7, 5, 16, 0, 0),
        time_zone="America/Los_Angeles",  # Same timezone
        created_at=datetime(2024, 7, 5, 2, 0, 0),
        updated_at=datetime(2024, 7, 5, 2, 5, 0),
    )


@pytest.fixture
def sample_event_rrule() -> Event:
    """Provides an event focused on RRULE testing (naive UTC)."""
    return Event(
        id=uuid.uuid4(),
        title="Monthly Report Due",
        start_time=datetime(2024, 8, 1, 9, 0, 0),
        end_time=datetime(2024, 8, 1, 10, 0, 0),
        time_zone="Europe/London",
        rrule="FREQ=MONTHLY;BYMONTHDAY=1;INTERVAL=1",  # First day of every month
        created_at=datetime(2024, 7, 30, 0, 0, 0),
        updated_at=datetime(2024, 7, 30, 0, 5, 0),
    )


# --- iCalExporter Tests ---


def test_ical_export_simple_event(ical_exporter: ICalExporter, sample_event_simple: Event):
    """Test exporting a single simple event to iCal format."""
    events = [sample_event_simple]
    ics_string = ical_exporter.export(events)

    # Basic checks for iCal structure and content
    assert "BEGIN:VCALENDAR" in ics_string
    assert "END:VCALENDAR" in ics_string
    assert "BEGIN:VEVENT" in ics_string
    assert "END:VEVENT" in ics_string
    assert f"UID:{sample_event_simple.id}@scheduling-agent" in ics_string
    assert f"SUMMARY:{sample_event_simple.title}" in ics_string
    # Check if dates are correctly formatted with timezone adjustment
    # NY is UTC-4 in July
    assert "DTSTART;TZID=America/New_York:20240701T060000" in ics_string
    assert "DTEND;TZID=America/New_York:20240701T070000" in ics_string
    assert "LOCATION:" not in ics_string  # No location in simple event
    assert "DESCRIPTION:" not in ics_string  # No notes in simple event
    assert "RRULE:" not in ics_string  # No recurrence in simple event


def test_ical_export_detailed_event(ical_exporter: ICalExporter, sample_event_detailed: Event):
    """Test exporting a single detailed event with location, notes, recurrence, and participants."""
    events = [sample_event_detailed]
    ics_string = ical_exporter.export(events)

    # Parse the generated string back
    parsed_cal = Calendar.from_ical(ics_string)
    exported_event = None
    for component in parsed_cal.walk():
        if component.name == "VEVENT" and component.get("uid") == f"{sample_event_detailed.id}@scheduling-agent":
            exported_event = component
            break

    assert exported_event is not None, "VEVENT component not found or UID mismatch"

    assert exported_event.get("summary") == sample_event_detailed.title
    # Check location and notes (escaping might occur)
    assert exported_event.get("location") == sample_event_detailed.location
    # The library handles unfolding and decoding \n correctly when parsing
    assert exported_event.get("description") == sample_event_detailed.notes
    # Check date/time (parsed datetimes should be timezone-aware)
    assert exported_event.get("dtstart").dt.year == 2024
    assert exported_event.get("dtstart").dt.month == 7
    assert exported_event.get("dtstart").dt.day == 2
    assert exported_event.get("dtstart").dt.hour == 16  # Berlin time (+2 UTC)
    assert exported_event.get("dtstart").dt.minute == 30
    assert exported_event.get("dtstart").dt.tzinfo is not None
    # Check recurrence rule by parsing the vRecur object
    assert "RRULE" in exported_event
    rrule_props = exported_event["RRULE"]
    # Compare against strings, not bytes
    assert rrule_props["FREQ"] == ["WEEKLY"]
    assert rrule_props["BYDAY"] == ["TU"]
    assert rrule_props["COUNT"] == [4]

    # Check attendees
    attendees = exported_event.get("attendee")
    assert attendees is not None
    # Ensure attendees is always a list, even if only one
    if not isinstance(attendees, list):
        attendees = [attendees]
    assert len(attendees) == len(sample_event_detailed.participants)
    expected_attendees = {f"MAILTO:{p.identifier}" for p in sample_event_detailed.participants}
    actual_attendees = {str(a) for a in attendees}  # Convert vCalAddress to string
    assert actual_attendees == expected_attendees


def test_ical_export_multiple_events(
    ical_exporter: ICalExporter, sample_event_simple: Event, sample_event_detailed: Event
):
    """Test exporting multiple events."""
    events = [sample_event_simple, sample_event_detailed]
    ics_string = ical_exporter.export(events)

    assert "BEGIN:VCALENDAR" in ics_string
    assert "END:VCALENDAR" in ics_string
    # Count VEVENT blocks
    assert ics_string.count("BEGIN:VEVENT") == 2
    assert ics_string.count("END:VEVENT") == 2
    # Check UIDs are present
    assert f"UID:{sample_event_simple.id}@scheduling-agent" in ics_string
    assert f"UID:{sample_event_detailed.id}@scheduling-agent" in ics_string


def test_ical_export_multiple_events_same_day(
    ical_exporter: ICalExporter, sample_event_same_day_1: Event, sample_event_same_day_2: Event
):
    """Test exporting multiple events occurring on the same day."""
    events = [sample_event_same_day_1, sample_event_same_day_2]
    ics_string = ical_exporter.export(events)

    # Check structure
    assert "BEGIN:VCALENDAR" in ics_string
    assert "END:VCALENDAR" in ics_string
    assert ics_string.count("BEGIN:VEVENT") == 2
    assert ics_string.count("END:VEVENT") == 2

    # Check UIDs and summaries are present
    assert f"UID:{sample_event_same_day_1.id}@scheduling-agent" in ics_string
    assert f"SUMMARY:{sample_event_same_day_1.title}" in ics_string
    assert f"UID:{sample_event_same_day_2.id}@scheduling-agent" in ics_string
    assert f"SUMMARY:{sample_event_same_day_2.title}" in ics_string


def test_ical_export_midnight_span(ical_exporter: ICalExporter, sample_event_midnight_span: Event):
    """Test exporting an event that spans midnight."""
    events = [sample_event_midnight_span]
    ics_string = ical_exporter.export(events)

    # Parse back and check properties
    parsed_cal = Calendar.from_ical(ics_string)
    exported_event = next((c for c in parsed_cal.walk() if c.name == "VEVENT"), None)

    assert exported_event is not None
    assert exported_event.get("summary") == sample_event_midnight_span.title

    # Check dtstart (should be aware UTC)
    dtstart = exported_event.get("dtstart").dt
    assert isinstance(dtstart, datetime)
    assert dtstart.year == 2024
    assert dtstart.month == 7
    assert dtstart.day == 3
    assert dtstart.hour == 22
    assert dtstart.minute == 0
    # Check that the timezone is effectively UTC (offset is zero)
    assert dtstart.tzinfo is not None
    assert dtstart.utcoffset() == timedelta(0)

    # Check dtend (should be aware UTC on the next day)
    dtend = exported_event.get("dtend").dt
    assert isinstance(dtend, datetime)
    assert dtend.year == 2024
    assert dtend.month == 7
    assert dtend.day == 4
    assert dtend.hour == 2
    assert dtend.minute == 0
    # Check that the timezone is effectively UTC (offset is zero)
    assert dtend.tzinfo is not None
    assert dtend.utcoffset() == timedelta(0)


def test_ical_export_recurrence(ical_exporter: ICalExporter, sample_event_rrule: Event):
    """Test exporting an event specifically focusing on the recurrence rule."""
    events = [sample_event_rrule]
    ics_string = ical_exporter.export(events)

    # Parse back to check RRULE
    parsed_cal = Calendar.from_ical(ics_string)
    exported_event = next((c for c in parsed_cal.walk() if c.name == "VEVENT"), None)
    assert exported_event is not None
    assert "RRULE" in exported_event
    rrule_props = exported_event["RRULE"]
    assert rrule_props["FREQ"] == ["MONTHLY"]
    assert rrule_props["BYMONTHDAY"] == [1]
    assert rrule_props["INTERVAL"] == [1]


# --- OrgModeExporter Tests ---


def test_orgmode_export_simple_event(orgmode_exporter: OrgModeExporter, sample_event_simple: Event):
    """Test exporting a single simple event to Org mode format."""
    events = [sample_event_simple]
    org_string = orgmode_exporter.export(events)

    # Expected format: <YYYY-MM-DD Day HH:MM-HH:MM>
    # NY is UTC-4 in July
    expected_timestamp = "<2024-07-01 Mon 06:00-07:00>"
    assert f"* {sample_event_simple.title}" in org_string
    assert expected_timestamp in org_string
    assert ":PROPERTIES:" in org_string
    assert f":EVENT_ID: {sample_event_simple.id}" in org_string
    assert f":TIMEZONE: {sample_event_simple.time_zone}" in org_string
    assert ":END:" in org_string
    assert ":LOCATION:" not in org_string
    assert ":RRULE:" not in org_string
    # Check that no NOTES property or extra content related to notes exists
    assert ":NOTES:" not in org_string
    assert "\n\n" not in org_string.replace(
        f"* {sample_event_simple.title}\n", ""
    )  # Check for extra newlines where notes would be


def test_orgmode_export_detailed_event(
    orgmode_exporter: OrgModeExporter, sample_event_detailed: Event
):
    """Test exporting a single detailed event with properties, notes, and participants."""
    events = [sample_event_detailed]
    org_string = orgmode_exporter.export(events)

    # Expected format: <YYYY-MM-DD Day HH:MM-HH:MM>
    # Berlin is UTC+2 in July
    expected_timestamp = "<2024-07-02 Tue 16:30-17:30>"
    assert f"* {sample_event_detailed.title}" in org_string
    assert expected_timestamp in org_string
    assert ":PROPERTIES:" in org_string
    assert f":EVENT_ID: {sample_event_detailed.id}" in org_string
    assert f":LOCATION: {sample_event_detailed.location}" in org_string
    assert f":TIMEZONE: {sample_event_detailed.time_zone}" in org_string
    assert f":RRULE: {sample_event_detailed.rrule}" in org_string
    # Check participants property
    expected_participants = ", ".join(
        sorted([p.identifier for p in sample_event_detailed.participants])
    )
    assert f":PARTICIPANTS: {expected_participants}" in org_string
    assert ":END:" in org_string
    assert sample_event_detailed.notes in org_string


def test_orgmode_export_multiple_events(
    orgmode_exporter: OrgModeExporter, sample_event_simple: Event, sample_event_detailed: Event
):
    """Test exporting multiple events, ensuring correct order and separation."""
    events = [sample_event_detailed, sample_event_simple]  # Intentionally out of order
    org_string = orgmode_exporter.export(events)

    # Check that simple event appears first due to sorting
    simple_index = org_string.find(f"* {sample_event_simple.title}")
    detailed_index = org_string.find(f"* {sample_event_detailed.title}")
    assert simple_index != -1
    assert detailed_index != -1
    assert simple_index < detailed_index

    # Check for correct number of entries (separated by newline)
    entries = org_string.strip().split("\n*")  # Split by heading marker
    assert len(entries) == 2
    assert org_string.count(":PROPERTIES:") == 2
    assert org_string.count(":END:") == 2


def test_orgmode_export_multiple_events_same_day(
    orgmode_exporter: OrgModeExporter,
    sample_event_same_day_1: Event,
    sample_event_same_day_2: Event,
):
    """Test exporting multiple events on the same day, checking order."""
    # Ensure events are passed out of order to test sorting
    events = [sample_event_same_day_2, sample_event_same_day_1]
    org_string = orgmode_exporter.export(events)

    # Check order
    idx1 = org_string.find(f"* {sample_event_same_day_1.title}")
    idx2 = org_string.find(f"* {sample_event_same_day_2.title}")
    assert idx1 != -1
    assert idx2 != -1
    assert idx1 < idx2  # Event 1 should appear first

    # Check timestamps are correct (LA is UTC-7 in July)
    # Event 1: 9:00 UTC -> 2:00 PDT
    assert "<2024-07-05 Fri 02:00-02:30>" in org_string
    # Event 2: 15:00 UTC -> 8:00 PDT
    assert "<2024-07-05 Fri 08:00-09:00>" in org_string
    assert org_string.count("* ") == 2  # Check number of top-level entries


def test_orgmode_export_midnight_span(
    orgmode_exporter: OrgModeExporter, sample_event_midnight_span: Event
):
    """Test exporting an event that spans midnight in Org mode."""
    events = [sample_event_midnight_span]
    org_string = orgmode_exporter.export(events)

    # Check timestamp format for midnight span (should show both dates)
    # UTC timezone, so no conversion needed
    expected_timestamp = "<2024-07-03 Wed 22:00-2024-07-04 Thu 02:00>"
    assert f"* {sample_event_midnight_span.title}" in org_string
    assert expected_timestamp in org_string


def test_orgmode_export_recurrence(orgmode_exporter: OrgModeExporter, sample_event_rrule: Event):
    """Test exporting an event focusing on the RRULE property in Org mode."""
    events = [sample_event_rrule]
    org_string = orgmode_exporter.export(events)

    assert f"* {sample_event_rrule.title}" in org_string
    assert ":PROPERTIES:" in org_string
    assert f":RRULE: {sample_event_rrule.rrule}" in org_string
    assert ":END:" in org_string


def test_orgmode_export_heading_level(
    orgmode_exporter: OrgModeExporter, sample_event_simple: Event
):
    """Test using a different heading level for Org mode export."""
    events = [sample_event_simple]
    org_string = orgmode_exporter.export(events, heading_level=3)

    assert f"*** {sample_event_simple.title}" in org_string  # Check for 3 stars
    # Ensure no line starts with exactly '* '
    assert all(not line.startswith("* ") for line in org_string.splitlines())
