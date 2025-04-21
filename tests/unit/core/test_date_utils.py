import uuid
from datetime import UTC, datetime, timedelta, time
from zoneinfo import ZoneInfo

import pytest

from scheduling_agent.core.date_utils import DateUtils
from scheduling_agent.db.models import Event, ExcludedDate


@pytest.fixture
def date_utils() -> DateUtils:
    return DateUtils()


def test_parse_datetime_utc(date_utils: DateUtils):
    """Test parsing a datetime string with Z (UTC) timezone."""
    dt_str = "2024-01-01T10:30:00Z"
    parsed_dt = date_utils.parse_datetime(dt_str)
    assert parsed_dt == datetime(2024, 1, 1, 10, 30, 0, tzinfo=UTC)
    assert parsed_dt.tzinfo == UTC


def test_parse_datetime_offset(date_utils: DateUtils):
    """Test parsing a datetime string with a specific offset."""
    dt_str = "2024-01-01T10:30:00-05:00"
    parsed_dt = date_utils.parse_datetime(dt_str)
    expected_utc = datetime(2024, 1, 1, 15, 30, 0, tzinfo=UTC)
    # Check both the original interpretation and UTC conversion
    assert parsed_dt.year == 2024
    assert parsed_dt == expected_utc
    assert parsed_dt.tzinfo == UTC


def test_parse_datetime_naive_assumes_utc(date_utils: DateUtils):
    """Test parsing a naive datetime string assumes UTC."""
    dt_str = "2024-01-01T10:30:00"
    parsed_dt = date_utils.parse_datetime(dt_str)
    assert parsed_dt == datetime(2024, 1, 1, 10, 30, 0, tzinfo=UTC)
    assert parsed_dt.tzinfo == UTC


def test_parse_datetime_invalid(date_utils: DateUtils):
    """Test parsing an invalid datetime string raises ValueError."""
    dt_str = "invalid-date-string"
    with pytest.raises(ValueError, match="Invalid datetime format"):
        date_utils.parse_datetime(dt_str)


def test_normalize_to_utc_aware(date_utils: DateUtils):
    """Test normalizing an already timezone-aware datetime to UTC."""
    dt_aware = datetime(2024, 1, 1, 10, 0, 0, tzinfo=ZoneInfo("America/New_York"))
    dt_utc = date_utils.normalize_to_utc(dt_aware)
    assert dt_utc == datetime(2024, 1, 1, 15, 0, 0, tzinfo=UTC)
    assert dt_utc.tzinfo == UTC


def test_normalize_to_utc_naive_with_tz(date_utils: DateUtils):
    """Test normalizing a naive datetime, assuming a specific timezone."""
    dt_naive = datetime(2024, 1, 1, 10, 0, 0)
    dt_utc = date_utils.normalize_to_utc(dt_naive, tz_name="America/New_York")
    assert dt_utc == datetime(2024, 1, 1, 15, 0, 0, tzinfo=UTC)
    assert dt_utc.tzinfo == UTC


def test_normalize_to_utc_naive_no_tz(date_utils: DateUtils):
    """Test normalizing a naive datetime, assuming UTC if no tz provided."""
    dt_naive = datetime(2024, 1, 1, 10, 0, 0)
    dt_utc = date_utils.normalize_to_utc(dt_naive)  # No tz_name provided
    assert dt_utc == datetime(2024, 1, 1, 10, 0, 0, tzinfo=UTC)
    assert dt_utc.tzinfo == UTC


def test_convert_from_utc(date_utils: DateUtils):
    """Test converting a UTC datetime to a specific timezone."""
    dt_utc = datetime(2024, 1, 1, 15, 0, 0, tzinfo=UTC)
    dt_local = date_utils.convert_from_utc(dt_utc, tz_name="America/New_York")
    assert dt_local.year == 2024
    assert dt_local.month == 1
    assert dt_local.day == 1
    assert dt_local.hour == 10
    assert dt_local.minute == 0
    assert dt_local.tzinfo == ZoneInfo("America/New_York")


def test_convert_from_utc_naive_input(date_utils: DateUtils):
    """Test converting a naive datetime (assumed UTC) to a specific timezone."""
    dt_naive_utc = datetime(2024, 1, 1, 15, 0, 0)
    dt_local = date_utils.convert_from_utc(dt_naive_utc, tz_name="America/New_York")
    assert dt_local.hour == 10
    assert dt_local.tzinfo == ZoneInfo("America/New_York")


def test_convert_from_utc_invalid_tz(date_utils: DateUtils):
    """Test conversion raises error for invalid timezone name."""
    dt_utc = datetime(2024, 1, 1, 15, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="Invalid target timezone"):
        date_utils.convert_from_utc(dt_utc, tz_name="Invalid/Timezone")


# --- Recurrence Expansion Tests ---


def test_expand_recurrence_simple_weekly(date_utils: DateUtils):
    """Test expanding a simple weekly recurrence rule."""
    # Event: Weekly on Monday at 9am UTC for 5 weeks
    start_dt = datetime(2024, 5, 6, 9, 0, 0, tzinfo=UTC)  # First Monday in May
    end_dt = datetime(2024, 5, 6, 10, 0, 0, tzinfo=UTC)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=5"

    base_event_data = {
        "id": uuid.uuid4(),
        "title": "Weekly Meeting",
        "start_time": start_dt.replace(tzinfo=None),  # Store naive UTC
        "end_time": end_dt.replace(tzinfo=None),
        "time_zone": "UTC",
        "rrule": rrule_str,
        # No excluded_dates or modified_instances needed for this test
    }
    # Use model_validate for creating test instance from dict
    # Need to import Event from db models for this
    from scheduling_agent.db.models import Event

    base_event = Event.model_validate(base_event_data)

    # Define range for expansion
    range_start = datetime(2024, 5, 1, 0, 0, 0, tzinfo=UTC)
    range_end = datetime(2024, 6, 10, 0, 0, 0, tzinfo=UTC)

    # --- Act ---
    # Assume expand_recurrence takes base event, range, and optional exceptions
    # For this test, exceptions are empty lists
    # Let's assume expand_recurrence returns a list of simple dicts or EventRead models
    # for now, not full Event DB models, to represent the expanded occurrences.
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=[],
        modified_instances=[],
    )

    # --- Assert ---
    assert len(occurrences) == 5  # COUNT=5

    expected_dates = [
        datetime(2024, 5, 6, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 5, 13, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 5, 20, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 5, 27, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 6, 3, 9, 0, 0, tzinfo=UTC),
    ]

    # Check the start times of the generated occurrences (should be timezone aware UTC)
    # Assuming the returned occurrence object has a 'start_time' attribute
    occurrence_start_times = sorted([occ["start_time"] for occ in occurrences])
    assert occurrence_start_times == expected_dates

    # Check properties of the first occurrence
    first_occurrence = occurrences[0]
    assert first_occurrence["title"] == base_event.title
    assert first_occurrence["start_time"] == expected_dates[0]
    assert first_occurrence["end_time"] == expected_dates[0] + (end_dt - start_dt)
    assert first_occurrence["time_zone"] == base_event.time_zone
    # Check specific instance details
    assert first_occurrence["rrule"] is None  # Occurrences don't have rrule
    assert first_occurrence["base_event_id"] == base_event.id  # Link back to base
    assert (
        first_occurrence["instance_start_time"] == expected_dates[0]
    )  # Identify this specific instance time


def test_expand_recurrence_with_exclusion(date_utils: DateUtils):
    """Test expanding recurrence while excluding specific dates."""
    # Arrange: Daily event for 1 week, exclude the 3rd day
    start_dt = datetime(2024, 7, 1, 9, 0, 0, tzinfo=UTC)
    end_dt = datetime(2024, 7, 1, 10, 0, 0, tzinfo=UTC)
    rrule_str = "FREQ=DAILY;COUNT=7"
    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": "Daily Task",
            "start_time": start_dt.replace(tzinfo=None),
            "end_time": end_dt.replace(tzinfo=None),
            "time_zone": "UTC",
            "rrule": rrule_str,
        }
    )

    # Exclude July 3rd (stored as naive datetime at midnight UTC)
    exclusion_dt = datetime(2024, 7, 3, 0, 0, 0)
    excluded = [ExcludedDate(event_id=base_event.id, excluded_date=exclusion_dt)]

    range_start = datetime(2024, 7, 1, 0, 0, 0, tzinfo=UTC)
    range_end = datetime(2024, 7, 10, 0, 0, 0, tzinfo=UTC)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=excluded,
        modified_instances=[],
    )

    # Assert
    assert len(occurrences) == 6  # 7 total minus 1 excluded
    occurrence_starts = {occ["start_time"].date() for occ in occurrences}
    assert datetime(2024, 7, 1).date() in occurrence_starts
    assert datetime(2024, 7, 2).date() in occurrence_starts
    assert datetime(2024, 7, 3).date() not in occurrence_starts  # Check exclusion
    assert datetime(2024, 7, 4).date() in occurrence_starts
    assert datetime(2024, 7, 5).date() in occurrence_starts
    assert datetime(2024, 7, 6).date() in occurrence_starts
    assert datetime(2024, 7, 7).date() in occurrence_starts


def test_expand_recurrence_with_modification(date_utils: DateUtils):
    """Test expanding recurrence with a modified instance."""
    # Arrange: Weekly event, modify the 2nd instance
    start_dt = datetime(2024, 5, 6, 9, 0, 0, tzinfo=UTC)
    end_dt = datetime(2024, 5, 6, 10, 0, 0, tzinfo=UTC)
    rrule_str = "FREQ=WEEKLY;BYDAY=MO;COUNT=4"
    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": "Weekly Base",
            "start_time": start_dt.replace(tzinfo=None),
            "end_time": end_dt.replace(tzinfo=None),
            "time_zone": "UTC",
            "rrule": rrule_str,
            "location": "Office",
        }
    )

    # Modify the second instance (May 13th)
    original_instance_date = datetime(2024, 5, 13, 0, 0, 0)  # Naive UTC date
    mod_start_dt = datetime(2024, 5, 13, 9, 30, 0)  # Naive UTC time
    mod_end_dt = datetime(2024, 5, 13, 10, 30, 0)
    mod_title = "Modified Weekly"
    mod_location = "Virtual"

    # Need ModifiedInstance model
    from scheduling_agent.db.models import ModifiedInstance

    modified = [
        ModifiedInstance(
            event_id=base_event.id,
            original_date=original_instance_date,
            start_time=mod_start_dt,
            end_time=mod_end_dt,
            time_zone="UTC",  # Specify TZ for the modification
            title=mod_title,
            location=mod_location,
        )
    ]

    range_start = datetime(2024, 5, 1, 0, 0, 0, tzinfo=UTC)
    range_end = datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=[],
        modified_instances=modified,
    )

    # Assert
    assert len(occurrences) == 4
    occurrence_starts = {occ["start_time"] for occ in occurrences}

    # Check the modified instance (May 13th)
    mod_instance_data = next(
        (occ for occ in occurrences if occ["start_time"].date() == datetime(2024, 5, 13).date()),
        None,
    )
    assert mod_instance_data is not None
    assert mod_instance_data["title"] == mod_title
    assert mod_instance_data["location"] == mod_location
    # Start time should be the modified one, normalized to aware UTC
    assert mod_instance_data["start_time"] == mod_start_dt.replace(tzinfo=UTC)
    assert mod_instance_data["end_time"] == mod_end_dt.replace(tzinfo=UTC)
    assert mod_instance_data["modified_instance_id"] is not None

    # Check a non-modified instance (May 6th)
    non_mod_instance_data = next(
        (occ for occ in occurrences if occ["start_time"].date() == datetime(2024, 5, 6).date()),
        None,
    )
    assert non_mod_instance_data is not None
    assert non_mod_instance_data["title"] == base_event.title
    assert non_mod_instance_data["location"] == base_event.location
    assert non_mod_instance_data["start_time"] == start_dt  # Original start time
    assert non_mod_instance_data["modified_instance_id"] is None


def test_expand_recurrence_until(date_utils: DateUtils):
    """Test expanding recurrence with an UNTIL clause."""
    # Arrange: Daily event starting 2024-07-15 9am UTC, repeating until July 20th (exclusive)
    start_dt = datetime(2024, 7, 15, 9, 0, 0, tzinfo=UTC)
    end_dt = datetime(2024, 7, 15, 10, 0, 0, tzinfo=UTC)
    # UNTIL is inclusive according to RFC5545. The time part is important.
    # UNTIL=20240720T000000Z means the last possible *start* time must be <= this value.
    # Since our event starts at 9am, the occurrence on July 19th at 9am is allowed,
    # but the one on July 20th at 9am is *not* because 9am > 0am.
    rrule_str = "FREQ=DAILY;UNTIL=20240720T000000Z"
    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": "Daily Until July 20th",
            "start_time": start_dt.replace(tzinfo=None),
            "end_time": end_dt.replace(tzinfo=None),
            "time_zone": "UTC",
            "rrule": rrule_str,
        }
    )

    range_start = datetime(2024, 7, 1, 0, 0, 0, tzinfo=UTC)
    range_end = datetime(2024, 7, 31, 0, 0, 0, tzinfo=UTC)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=[],
        modified_instances=[],
    )

    # Assert
    assert len(occurrences) == 5  # July 15, 16, 17, 18, 19
    occurrence_starts = sorted([occ["start_time"] for occ in occurrences])

    expected_starts = [
        datetime(2024, 7, 15, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 7, 16, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 7, 17, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 7, 18, 9, 0, 0, tzinfo=UTC),
        datetime(2024, 7, 19, 9, 0, 0, tzinfo=UTC),
    ]
    assert occurrence_starts == expected_starts


def test_expand_recurrence_bysetpos(date_utils: DateUtils):
    """Test expanding recurrence with a BYSETPOS rule (last Friday of month)."""
    # Arrange: Event on the last Friday of the month at 2pm UTC, check over 3 months
    start_dt = datetime(
        2024, 8, 30, 14, 0, 0, tzinfo=UTC
    )  # Initial start must be a valid instance (last Fri of Aug)
    end_dt = datetime(2024, 8, 30, 15, 0, 0, tzinfo=UTC)
    # Last Friday of the month
    rrule_str = "FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1"
    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": "Last Friday Drinks",
            "start_time": start_dt.replace(tzinfo=None),
            "end_time": end_dt.replace(tzinfo=None),
            "time_zone": "UTC",
            "rrule": rrule_str,
        }
    )

    # Range covering Aug, Sep, Oct 2024
    range_start = datetime(2024, 8, 1, 0, 0, 0, tzinfo=UTC)
    range_end = datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start,
        range_end_utc=range_end,
        excluded_dates=[],
        modified_instances=[],
    )

    # Assert
    assert len(occurrences) == 3  # Last Fri of Aug, Sep, Oct
    occurrence_starts = sorted([occ["start_time"] for occ in occurrences])

    # Last Fridays in 2024: Aug 30, Sep 27, Oct 25
    expected_starts = [
        datetime(2024, 8, 30, 14, 0, 0, tzinfo=UTC),
        datetime(2024, 9, 27, 14, 0, 0, tzinfo=UTC),
        datetime(2024, 10, 25, 14, 0, 0, tzinfo=UTC),
    ]
    assert occurrence_starts == expected_starts


# --- DST Transition Tests (Phase 3, Task 4) ---


@pytest.mark.parametrize(
    ("tz_name", "event_local_start", "rrule_str", "range_start_utc", "range_end_utc", "expected_utc_starts"),
    [
        # US/New York: Spring Forward (EDT starts Sunday, March 10, 2024, 2:00 AM EST -> 3:00 AM EDT)
        (
            "America/New_York",
            datetime(2024, 3, 8, 9, 0, 0),  # Event starts Friday 9am EST
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 3, 8, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 3, 13, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 3, 8, 14, 0, 0, tzinfo=UTC),  # Fri 9am EST = 14:00 UTC
                datetime(2024, 3, 9, 14, 0, 0, tzinfo=UTC),  # Sat 9am EST = 14:00 UTC
                datetime(2024, 3, 10, 13, 0, 0, tzinfo=UTC),  # Sun 9am EDT = 13:00 UTC
                datetime(2024, 3, 11, 13, 0, 0, tzinfo=UTC),  # Mon 9am EDT = 13:00 UTC
                datetime(2024, 3, 12, 13, 0, 0, tzinfo=UTC),  # Tue 9am EDT = 13:00 UTC
            ],
        ),
        # US/New York: Fall Back (EST starts Sunday, November 3, 2024, 2:00 AM EDT -> 1:00 AM EST)
        (
            "America/New_York",
            datetime(2024, 11, 1, 9, 0, 0),  # Event starts Friday 9am EDT
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 11, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 11, 6, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 11, 1, 13, 0, 0, tzinfo=UTC),  # Fri 9am EDT = 13:00 UTC
                datetime(2024, 11, 2, 13, 0, 0, tzinfo=UTC),  # Sat 9am EDT = 13:00 UTC
                datetime(2024, 11, 3, 14, 0, 0, tzinfo=UTC),  # Sun 9am EST = 14:00 UTC
                datetime(2024, 11, 4, 14, 0, 0, tzinfo=UTC),  # Mon 9am EST = 14:00 UTC
                datetime(2024, 11, 5, 14, 0, 0, tzinfo=UTC),  # Tue 9am EST = 14:00 UTC
            ],
        ),
        # Europe/London: Spring Forward (BST starts Sunday, 31 March 2024, 1:00 AM GMT -> 2:00 AM BST)
        (
            "Europe/London",
            datetime(2024, 3, 29, 9, 0, 0),  # Event starts Friday 9am GMT
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 3, 29, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 4, 3, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 3, 29, 9, 0, 0, tzinfo=UTC),  # Fri 9am GMT = 09:00 UTC
                datetime(2024, 3, 30, 9, 0, 0, tzinfo=UTC),  # Sat 9am GMT = 09:00 UTC
                datetime(2024, 3, 31, 8, 0, 0, tzinfo=UTC),  # Sun 9am BST = 08:00 UTC
                datetime(2024, 4, 1, 8, 0, 0, tzinfo=UTC),  # Mon 9am BST = 08:00 UTC
                datetime(2024, 4, 2, 8, 0, 0, tzinfo=UTC),  # Tue 9am BST = 08:00 UTC
            ],
        ),
        # Europe/London: Fall Back (GMT starts Sunday, 27 October 2024, 2:00 AM BST -> 1:00 AM GMT)
        (
            "Europe/London",
            datetime(2024, 10, 25, 9, 0, 0),  # Event starts Friday 9am BST
            "FREQ=DAILY;COUNT=5",  # Fri, Sat, Sun, Mon, Tue
            datetime(2024, 10, 25, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 10, 30, 0, 0, 0, tzinfo=UTC),
            [
                datetime(2024, 10, 25, 8, 0, 0, tzinfo=UTC),  # Fri 9am BST = 08:00 UTC
                datetime(2024, 10, 26, 8, 0, 0, tzinfo=UTC),  # Sat 9am BST = 08:00 UTC
                datetime(2024, 10, 27, 9, 0, 0, tzinfo=UTC),  # Sun 9am GMT = 09:00 UTC
                datetime(2024, 10, 28, 9, 0, 0, tzinfo=UTC),  # Mon 9am GMT = 09:00 UTC
                datetime(2024, 10, 29, 9, 0, 0, tzinfo=UTC),  # Tue 9am GMT = 09:00 UTC
            ],
        ),
    ],
)
def test_expand_recurrence_dst_transitions(
    date_utils: DateUtils,
    tz_name: str,
    event_local_start: datetime,
    rrule_str: str,
    range_start_utc: datetime,
    range_end_utc: datetime,
    expected_utc_starts: list[datetime],
):
    """Tests recurrence expansion across Daylight Saving Time transitions."""
    # Create a base event model
    event_duration = timedelta(hours=1)
    event_local_end = event_local_start + event_duration

    # Normalize the local start/end times to naive UTC, like create_event does
    try:
        start_aware_utc = date_utils.normalize_to_utc(event_local_start, tz_name=tz_name)
        end_aware_utc = date_utils.normalize_to_utc(event_local_end, tz_name=tz_name)
        start_naive_utc = start_aware_utc.replace(tzinfo=None)
        end_naive_utc = end_aware_utc.replace(tzinfo=None)
    except Exception as e:
        pytest.fail(f"Failed to normalize test times for tz {tz_name}: {e}")

    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": f"DST Test {tz_name}",
            "start_time": start_naive_utc,  # Use normalized naive UTC
            "end_time": end_naive_utc,  # Use normalized naive UTC
            "time_zone": tz_name,  # Still need original TZ for potential modifications
            "rrule": rrule_str,
        }
    )

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
        excluded_dates=[],
        modified_instances=[],
    )

    # Assert
    occurrence_start_times = sorted([occ["start_time"] for occ in occurrences])

    assert len(occurrence_start_times) == len(expected_utc_starts)
    # Sort both lists before comparing to handle potential minor ordering differences
    assert sorted(occurrence_start_times) == sorted(expected_utc_starts)

    # Verify end times also shifted correctly (optional but good)
    occurrence_end_times = sorted([occ["end_time"] for occ in occurrences])
    expected_utc_ends = [start + event_duration for start in expected_utc_starts]
    assert occurrence_end_times == expected_utc_ends


# Test combining DST, Exclusions, and Modifications
def test_expand_recurrence_dst_with_exclusion(date_utils: DateUtils):
    """Test recurrence over DST with an exclusion after the transition."""
    # Arrange: Daily event at 9am New York time, crossing spring forward
    tz_name = "America/New_York"
    event_local_start = datetime(2024, 3, 8, 9, 0, 0)  # Fri 9am EST
    event_duration = timedelta(hours=1)
    rrule_str = "FREQ=DAILY;COUNT=5"  # Fri, Sat, Sun, Mon, Tue

    # Normalize the local start/end times to naive UTC
    start_aware_utc = date_utils.normalize_to_utc(event_local_start, tz_name=tz_name)
    start_naive_utc = start_aware_utc.replace(tzinfo=None)
    end_naive_utc = (start_aware_utc + event_duration).replace(tzinfo=None)

    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": "DST Exclude Test",
            "start_time": start_naive_utc,
            "end_time": end_naive_utc,
            "time_zone": tz_name,
            "rrule": rrule_str,
        }
    )

    # Exclude Monday, March 11th (which is after the DST transition)
    # Exclusion date stored as naive UTC midnight
    excluded_date_local = datetime(2024, 3, 11, 0, 0, 0)  # Naive local date
    excluded_date_utc_aware = date_utils.normalize_to_utc(excluded_date_local, tz_name=tz_name)
    excluded_date_naive_utc = excluded_date_utc_aware.date()
    # ExcludedDate model expects a datetime, let's use midnight naive UTC for consistency
    excluded_db_date = datetime.combine(excluded_date_naive_utc, datetime.min.time())

    excluded = [ExcludedDate(event_id=base_event.id, excluded_date=excluded_db_date)]

    range_start_utc = datetime(2024, 3, 8, 0, 0, 0, tzinfo=UTC)
    range_end_utc = datetime(2024, 3, 13, 0, 0, 0, tzinfo=UTC)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
        excluded_dates=excluded,
        modified_instances=[],
    )

    # Assert
    assert len(occurrences) == 4  # 5 total minus 1 excluded (Mon Mar 11)
    occurrence_start_times = sorted([occ["start_time"] for occ in occurrences])

    expected_utc_starts = [
        datetime(2024, 3, 8, 14, 0, 0, tzinfo=UTC),  # Fri 9am EST = 14:00 UTC
        datetime(2024, 3, 9, 14, 0, 0, tzinfo=UTC),  # Sat 9am EST = 14:00 UTC
        datetime(2024, 3, 10, 13, 0, 0, tzinfo=UTC),  # Sun 9am EDT = 13:00 UTC
        # Monday March 11th is excluded
        datetime(2024, 3, 12, 13, 0, 0, tzinfo=UTC),  # Tue 9am EDT = 13:00 UTC
    ]
    assert occurrence_start_times == expected_utc_starts


def test_expand_recurrence_dst_with_modification(date_utils: DateUtils):
    """Test recurrence over DST with a modification after the transition."""
    # Arrange: Daily event at 9am New York time, crossing spring forward
    tz_name = "America/New_York"
    event_local_start = datetime(2024, 3, 8, 9, 0, 0)  # Fri 9am EST
    event_duration = timedelta(hours=1)
    rrule_str = "FREQ=DAILY;COUNT=5"  # Fri, Sat, Sun, Mon, Tue

    # Normalize the local start/end times to naive UTC
    start_aware_utc = date_utils.normalize_to_utc(event_local_start, tz_name=tz_name)
    start_naive_utc = start_aware_utc.replace(tzinfo=None)
    end_naive_utc = (start_aware_utc + event_duration).replace(tzinfo=None)

    base_event = Event.model_validate(
        {
            "id": uuid.uuid4(),
            "title": "DST Mod Test Base",
            "start_time": start_naive_utc,
            "end_time": end_naive_utc,
            "time_zone": tz_name,
            "rrule": rrule_str,
        }
    )

    # Modify Monday, March 11th (which is after the DST transition)
    # Original instance time (for lookup) would be 9am EDT = 13:00 UTC
    original_instance_date_local = datetime(2024, 3, 11, 9, 0, 0)  # Local time
    original_instance_date_utc = date_utils.normalize_to_utc(
        original_instance_date_local, tz_name=tz_name
    )
    original_instance_lookup_date = original_instance_date_utc.date()

    # Define the modification details (e.g., push back by 30 mins, new title)
    # Assume modification times are provided as *naive* local times for the modification's date
    mod_start_local = datetime(2024, 3, 11, 9, 30, 0)
    mod_end_local = mod_start_local + event_duration
    mod_title = "Modified DST Event"

    # Need ModifiedInstance model
    from scheduling_agent.db.models import ModifiedInstance

    modified = [
        ModifiedInstance(
            event_id=base_event.id,
            # original_date is the *date* part (naive UTC) for lookup
            original_date=datetime.combine(original_instance_lookup_date, datetime.min.time()),
            start_time=mod_start_local,  # Store naive local time
            end_time=mod_end_local,  # Store naive local time
            time_zone=tz_name,  # Store the TZ context for the modification
            title=mod_title,
        )
    ]

    range_start_utc = datetime(2024, 3, 8, 0, 0, 0, tzinfo=UTC)
    range_end_utc = datetime(2024, 3, 13, 0, 0, 0, tzinfo=UTC)

    # Act
    occurrences = date_utils.expand_recurrence(
        base_event=base_event,
        range_start_utc=range_start_utc,
        range_end_utc=range_end_utc,
        excluded_dates=[],
        modified_instances=modified,
    )

    # Assert
    assert len(occurrences) == 5  # Modification replaces an instance
    occurrence_start_times = sorted([occ["start_time"] for occ in occurrences])

    # Expected UTC start times:
    # Fri Mar 8: 14:00 UTC (9am EST)
    # Sat Mar 9: 14:00 UTC (9am EST)
    # Sun Mar 10: 13:00 UTC (9am EDT) - DST Shift
    # Mon Mar 11: 13:30 UTC (Modified from 9am EDT to 9:30am EDT)
    # Tue Mar 12: 13:00 UTC (9am EDT)
    expected_utc_starts = [
        datetime(2024, 3, 8, 14, 0, 0, tzinfo=UTC),
        datetime(2024, 3, 9, 14, 0, 0, tzinfo=UTC),
        datetime(2024, 3, 10, 13, 0, 0, tzinfo=UTC),
        date_utils.normalize_to_utc(
            mod_start_local, tz_name=tz_name
        ),  # Modified Mon 9:30am EDT -> 13:30 UTC
        datetime(2024, 3, 12, 13, 0, 0, tzinfo=UTC),
    ]
    assert occurrence_start_times == expected_utc_starts

    # Check the modified instance details
    mod_instance_data = next(
        (occ for occ in occurrences if occ["start_time"] == expected_utc_starts[3]), None
    )
    assert mod_instance_data is not None
    assert mod_instance_data["title"] == mod_title
    assert mod_instance_data["modified_instance_id"] is not None


# --- Tests for parse_nlp_date_range --- #

def test_parse_nlp_date_range_defaults(date_utils: DateUtils):
    """Test parsing with default values ('today' to 'next Sunday')."""
    # Note: The exact dates depend on when the test is run.
    # We mainly check that it returns a valid range and times are set correctly.
    start_utc, end_utc = date_utils.parse_nlp_date_range(None, None)

    assert isinstance(start_utc, datetime)
    assert isinstance(end_utc, datetime)
    assert start_utc.tzinfo == UTC
    assert end_utc.tzinfo == UTC

    # Check times are set to start/end of day
    assert start_utc.time() == time.min
    assert end_utc.time() == time(23, 59, 59, 999999)

    # Check start is before end, and roughly 7 days apart (can vary slightly with defaults)
    assert start_utc < end_utc
    assert (end_utc - start_utc) >= timedelta(days=6)
    assert (end_utc - start_utc) < timedelta(days=8)

def test_parse_nlp_date_range_specific_strings(date_utils: DateUtils):
    """Test parsing with specific valid date strings."""
    start_str = "2024-08-15"
    end_str = "August 18, 2024"

    start_utc, end_utc = date_utils.parse_nlp_date_range(start_str, end_str)

    expected_start = datetime(2024, 8, 15, 0, 0, 0, tzinfo=UTC)
    expected_end = datetime(2024, 8, 18, 23, 59, 59, 999999, tzinfo=UTC)

    assert start_utc == expected_start
    assert end_utc == expected_end

def test_parse_nlp_date_range_relative_strings(date_utils: DateUtils):
    """Test parsing with relative date strings like 'tomorrow'."""
    start_str = "tomorrow"
    end_str = "in 3 days"

    start_utc, end_utc = date_utils.parse_nlp_date_range(start_str, end_str)

    today = datetime.now(UTC).date()
    tomorrow = today + timedelta(days=1)
    in_3_days = today + timedelta(days=3)

    expected_start = datetime.combine(tomorrow, time.min, tzinfo=UTC)
    expected_end = datetime.combine(in_3_days, time(23, 59, 59, 999999), tzinfo=UTC)

    assert start_utc == expected_start
    assert end_utc == expected_end

def test_parse_nlp_date_range_start_after_end(date_utils: DateUtils):
    """Test parsing where initial end date is before start date."""
    start_str = "2024-09-10"
    end_str = "2024-09-05" # End date is before start date

    start_utc, end_utc = date_utils.parse_nlp_date_range(start_str, end_str)

    expected_start = datetime(2024, 9, 10, 0, 0, 0, tzinfo=UTC)
    # End date should be adjusted to 7 days after start
    expected_end_date = datetime(2024, 9, 17).date()
    expected_end = datetime.combine(expected_end_date, time(23, 59, 59, 999999), tzinfo=UTC)

    assert start_utc == expected_start
    assert end_utc == expected_end

def test_parse_nlp_date_range_invalid_input(date_utils: DateUtils):
    """Test parsing with invalid date strings falls back gracefully."""
    start_str = "not a date"
    end_str = "also not a date"

    # Should fallback to defaults (today -> today + 7 days)
    start_utc, end_utc = date_utils.parse_nlp_date_range(start_str, end_str)

    assert isinstance(start_utc, datetime)
    assert isinstance(end_utc, datetime)
    assert start_utc.tzinfo == UTC
    assert end_utc.tzinfo == UTC
    assert start_utc < end_utc

def test_parse_nlp_date_range_partial_invalid(date_utils: DateUtils):
    """Test parsing with one valid and one invalid string."""
    start_str = "2024-10-01"
    end_str = "invalid end"

    start_utc, end_utc = date_utils.parse_nlp_date_range(start_str, end_str)

    expected_start = datetime(2024, 10, 1, 0, 0, 0, tzinfo=UTC)
    # End should default to 7 days after the valid start
    expected_end_date = datetime(2024, 10, 8).date()
    expected_end = datetime.combine(expected_end_date, time(23, 59, 59, 999999), tzinfo=UTC)

    assert start_utc == expected_start
    assert end_utc == expected_end
